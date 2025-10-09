from flask import Flask, request
import requests
import os
import time
import logging
from fuzzywuzzy import process
import json
import re
from typing import Dict, Any, Optional, Tuple, List

app = Flask(__name__)

# -----------------------
# Logging
# -----------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------
# Config (env)
# -----------------------
BOT_ID = os.getenv("BOT_ID")
BOT_NAME = os.getenv("BOT_NAME", "ClankerBot")
GROUP_ID = os.getenv("GROUP_ID")
BAN_SERVICE_URL = os.getenv("BAN_SERVICE_URL")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")  # GroupMe API token for member list and add
API_URL = "https://api.groupme.com/v3"

# Admin user IDs (string IDs)
ADMIN_IDS = [
    '119189324', '82717917', '124068433', '103258964', '123259855',
    '114848297', '121920211', '134245360', '113819798', '130463543',
    '123142410', '131010920', '133136781', '124453541', '122782552',
    '117776217', '85166615', '114066399', '84254355', '115866991', '124523409',
    '125629030', '124579254'
]

# -----------------------
# Swear word categories
# -----------------------
INSTANT_BAN_WORDS = [
    'nigger', 'nigga', 'n1gger', 'n1gga', 'nigg', 'n1gg', 'nigha',
]

REGULAR_SWEAR_WORDS = [
    'fuck', 'fucking', 'fucked', 'fucker',
    'shit', 'shitting', 'shitty',
    'bitch', 'bitches',
    'ass', 'asshole', 'asshat',
    'cunt',
    'dick', 'dickhead',
    'damn',
    'bastard',
    'slut',
    'whore',
    'retard',
    'wtf',
    'nevergonnagiveyouupnevergonnaletyoudown',
    '67', '6-7', '6 7', 'bullshit', 'maggot'
]

# -----------------------
# Persistence files
# -----------------------
banned_users_file = "banned_users.json"            # { user_id_str: username }
former_members_file = "former_members.json"        # { user_id_str_or_key: nickname }
user_swear_counts_file = "user_swear_counts.json"  # { user_id_str: int }
strikes_file = "user_strikes.json"                 # { user_id_str: int }

# In-memory caches
user_swear_counts: Dict[str, int] = {}
banned_users: Dict[str, str] = {}
former_members: Dict[str, str] = {}
user_strikes: Dict[str, int] = {}

# -----------------------
# Helpers for JSON load/save
# -----------------------
def load_json(file_path: str) -> Dict[str, Any]:
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Ensure keys are strings
                if isinstance(data, dict):
                    return {str(k): v for k, v in data.items()}
                return {}
        except Exception as e:
            logger.error(f"Failed to load {file_path}: {e}")
    return {}

def save_json(file_path: str, data: Dict[str, Any]) -> None:
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save {file_path}: {e}")

# Load persisted data (ensure string keys)
banned_users = load_json(banned_users_file)
user_swear_counts = load_json(user_swear_counts_file)
former_members = load_json(former_members_file)
user_strikes = load_json(strikes_file)

# -----------------------
# Cooldown / rate-limiting
# -----------------------
last_sent_time = 0.0
last_system_message_time = 0.0
cooldown_seconds = 10

# -----------------------
# Group API helpers
# -----------------------
def get_group_members() -> List[Dict[str, Any]]:
    """Return list of group members (each dict has keys like nickname, user_id)."""
    if not ACCESS_TOKEN or not GROUP_ID:
        logger.error("Missing ACCESS_TOKEN or GROUP_ID for group members")
        return []
    try:
        response = requests.get(
            f"{API_URL}/groups/{GROUP_ID}",
            headers={"X-Access-Token": ACCESS_TOKEN},
            timeout=8
        )
        response.raise_for_status()
        return response.json().get("response", {}).get("members", [])
    except Exception as e:
        logger.error(f"Failed to get group members: {e}")
        return []

def get_group_share_url() -> Optional[str]:
    if not ACCESS_TOKEN or not GROUP_ID:
        return None
    try:
        response = requests.get(
            f"{API_URL}/groups/{GROUP_ID}",
            headers={"X-Access-Token": ACCESS_TOKEN},
            timeout=8
        )
        response.raise_for_status()
        return response.json().get("response", {}).get("share_url")
    except Exception as e:
        logger.error(f"Failed to get group share URL: {e}")
        return None

# -----------------------
# Ban service / ban action
# -----------------------
def call_ban_service(user_id: str, username: str, reason: str) -> bool:
    """
    Call external ban service. If successful, persist user into banned_users.
    Always use string for user_id keys.
    """
    if not BAN_SERVICE_URL:
        logger.warning("No BAN_SERVICE_URL configured - can't ban users")
        return False
    payload = {
        'user_id': str(user_id),
        'username': username,
        'reason': reason
    }
    try:
        response = requests.post(f"{BAN_SERVICE_URL}/ban", json=payload, timeout=8)
        if response.status_code == 200:
            logger.info(f"✅ Banned {username} ({user_id}): {reason}")
            banned_users[str(user_id)] = username
            save_json(banned_users_file, banned_users)
            # clear any local counters
            user_swear_counts.pop(str(user_id), None)
            save_json(user_swear_counts_file, user_swear_counts)
            return True
        else:
            logger.error(f"❌ Ban failed {response.status_code}: {response.text}")
            return False
    except Exception as e:
        logger.error(f"❌ Ban service error: {e}")
        return False

# -----------------------
# Utility: fuzzy find a member by alias among current and former members
# -----------------------
def fuzzy_find_member(target_alias: str) -> Optional[Tuple[str, str]]:
    """
    Returns tuple (user_id_str, nickname) if found, otherwise None.
    Tries active members first, then former_members fallback.
    Uses fuzzy matching with a score cutoff to tolerate small differences.
    """
    if not target_alias:
        return None
    members = get_group_members()
    nicknames = [m.get("nickname", "") for m in members]
    nick_lower_list = [n.lower() for n in nicknames if n]
    best = None
    if nick_lower_list:
        match = process.extractOne(target_alias.lower(), nick_lower_list, score_cutoff=80)
        if match:
            matched_lower = match[0]
            # find the original member with that nickname (case-insensitive)
            for m in members:
                if m.get("nickname", "").lower() == matched_lower:
                    return (str(m.get("user_id")), m.get("nickname"))
    # Fallback: check exact direct match by ID
    if target_alias.isdigit():
        tid = str(target_alias)
        for m in members:
            if str(m.get("user_id")) == tid:
                return (tid, m.get("nickname"))
    # Fallback: search former_members by fuzzy
    if former_members:
        former_nicks = list(former_members.values())
        former_lower = [n.lower() for n in former_nicks]
        match = process.extractOne(target_alias.lower(), former_lower, score_cutoff=75)
        if match:
            matched_lower = match[0]
            for k, v in former_members.items():
                if v.lower() == matched_lower:
                    return (str(k), v)
    # last-ditch: if target_alias exactly matches a stored banned username
    for uid, uname in banned_users.items():
        if target_alias.lower() in uname.lower() or target_alias == uid:
            return (str(uid), uname)
    return None

# -----------------------
# User ID helper (for admin use)
# -----------------------
def get_user_id(target_alias: str, sender_name: str, sender_id: str, original_text: str) -> bool:
    """
    Admin-only helper command that finds a user's user_id and posts system message.
    """
    if str(sender_id) not in ADMIN_IDS:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Only admins can use this command")
        return False
    if not ACCESS_TOKEN or not GROUP_ID:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Missing ACCESS_TOKEN or GROUP_ID")
        logger.error("Missing ACCESS_TOKEN or GROUP_ID for user_id query")
        return False

    try:
        result = fuzzy_find_member(target_alias)
        if not result:
            send_system_message(f"> @{sender_name}: {original_text}\nNo user found matching '{target_alias}'")
            return False
        user_id, nickname = result
        send_system_message(f"> @{sender_name}: {original_text}\n{nickname}'s user_id is {user_id}")
        return True
    except Exception as e:
        logger.error(f"User ID query error: {e}")
        send_system_message(f"> @{sender_name}: {original_text}\nError: Failed to fetch user_id")
        return False

# -----------------------
# Unban / Re-add user logic (robust)
# -----------------------
def unban_user(target_alias: str, sender: str, sender_id: str, full_text: str) -> None:
    """
    Attempt to unban (re-add) a user who was previously banned by the bot.
    Uses ACCESS_TOKEN for /members/add. Provides diagnostic messages and fallbacks.
    """
    try:
        if str(sender_id) not in ADMIN_IDS:
            send_system_message(f"> @{sender}: {full_text}\nError: Only admins can use this command")
            return

        if not ACCESS_TOKEN or not GROUP_ID:
            send_system_message(f"> @{sender}: {full_text}\nError: Missing ACCESS_TOKEN or GROUP_ID")
            logger.error("Missing ACCESS_TOKEN or GROUP_ID for unban")
            return

        # find user id from various places: banned_users, fuzzy matching, former_members
        match_user_id = None
        match_nickname = None

        # direct fuzzy lookup (works for current, former, banned)
        fuzzy_result = fuzzy_find_member(target_alias)
        if fuzzy_result:
            match_user_id, match_nickname = fuzzy_result

        # If not found via fuzzy, check banned_users mapping for substring or numeric id
        if not match_user_id:
            for uid, uname in banned_users.items():
                if target_alias.lower() in uname.lower() or str(uid) == target_alias:
                    match_user_id = str(uid)
                    match_nickname = uname
                    break

        if not match_user_id:
            send_system_message(f"> @{sender}: {full_text}\n⚠️ No record of '{target_alias}' in banned list or group members.")
            return

        nickname = match_nickname or "Member"
        match_user_id = str(match_user_id)

        # sanitize nickname for add payload
        safe_name = re.sub(r"[^A-Za-z0-9 _\\-]", "", nickname)[:30] or "User"

        logger.info(f"Attempting to unban {nickname} ({match_user_id})")

        def attempt_add(nick_to_add: str) -> Tuple[bool, int]:
            payload = {"members": [{"nickname": nick_to_add, "user_id": match_user_id}]}
            try:
                resp = requests.post(
                    f"{API_URL}/groups/{GROUP_ID}/members/add",
                    headers={"X-Access-Token": ACCESS_TOKEN},
                    json=payload,
                    timeout=12
                )
            except Exception as e:
                logger.error(f"HTTP add attempt error: {e}")
                return False, 0

            if resp.status_code not in (200, 202):
                logger.warning(f"Unban HTTP error {resp.status_code}: {resp.text}")
                return False, resp.status_code

            try:
                data = resp.json()
            except Exception:
                # If we can't parse JSON, treat as possible success but verify
                time.sleep(3)
                new_members = get_group_members()
                return (any(str(m.get("user_id")) == match_user_id for m in new_members), 0)

            results_id = data.get('response', {}).get('results_id')
            if not results_id:
                # No results_id; maybe a synchronous add or silent success: check directly
                logger.info("No results_id present in response; checking group members directly")
                time.sleep(3)
                new_members = get_group_members()
                return (any(str(m.get("user_id")) == match_user_id for m in new_members), 0)

            # Poll for results (increased attempts and delays to be resilient)
            max_polls = 15
            for attempt in range(max_polls):
                sleep_time = 2 if attempt > 0 else 1
                time.sleep(sleep_time)
                try:
                    poll_resp = requests.get(
                        f"{API_URL}/groups/{GROUP_ID}/members/results/{results_id}",
                        headers={"X-Access-Token": ACCESS_TOKEN},
                        timeout=8
                    )
                except Exception as e:
                    logger.error(f"Poll HTTP error on attempt {attempt + 1}: {e}")
                    continue

                if poll_resp.status_code == 200:
                    try:
                        results_data = poll_resp.json().get('response', {})
                        added_members = results_data.get('members', [])
                        if any(str(m.get('user_id')) == match_user_id for m in added_members):
                            logger.info(f"✅ User found in poll results for {nick_to_add}")
                            return True, 0
                        else:
                            logger.debug(f"Poll {attempt + 1} returned 200 but user {match_user_id} not in added members")
                    except Exception as e:
                        logger.error(f"Failed parsing poll JSON: {e}")
                elif poll_resp.status_code == 404:
                    logger.warning("Results expired (404)")
                    break
                elif poll_resp.status_code == 503:
                    logger.warning("Service temporarily unavailable (503) during poll")
                    continue
                else:
                    logger.warning(f"Unexpected poll status {poll_resp.status_code}")
                    # continue to try
            return False, 408

        # Primary add attempt
        success, status_code = attempt_add(safe_name)
        if success:
            send_system_message(f"> @{sender}: {full_text}\n✅ {nickname} re-added to the group.")
            banned_users.pop(match_user_id, None)
            save_json(banned_users_file, banned_users)
            user_swear_counts.pop(match_user_id, None)
            save_json(user_swear_counts_file, user_swear_counts)
            user_strikes.pop(match_user_id, None)
            save_json(strikes_file, user_strikes)
            # Also remove from former_members if present
            former_members.pop(match_user_id, None)
            save_json(former_members_file, former_members)
            return

        # Check group directly after primary attempt
        time.sleep(6)
        members_after_primary = get_group_members()
        if any(str(m.get("user_id")) == match_user_id for m in members_after_primary):
            logger.info(f"✅ User added despite omitted from poll results: {nickname}")
            send_system_message(f"> @{sender}: {full_text}\n✅ {nickname} re-added to the group.")
            banned_users.pop(match_user_id, None)
            save_json(banned_users_file, banned_users)
            user_swear_counts.pop(match_user_id, None)
            save_json(user_swear_counts_file, user_swear_counts)
            user_strikes.pop(match_user_id, None)
            save_json(strikes_file, user_strikes)
            former_members.pop(match_user_id, None)
            save_json(former_members_file, former_members)
            return

        # Retry with simplified nickname
        retry_name = re.sub(r"[^A-Za-z0-9]", "", safe_name)[:20] or "Member"
        logger.warning(f"Primary unban failed (code {status_code}); retrying using '{retry_name}'.")
        success, status_code = attempt_add(retry_name)
        if success:
            send_system_message(f"> @{sender}: {full_text}\n✅ {retry_name} re-added after retry.")
            banned_users.pop(match_user_id, None)
            save_json(banned_users_file, banned_users)
            user_swear_counts.pop(match_user_id, None)
            save_json(user_swear_counts_file, user_swear_counts)
            user_strikes.pop(match_user_id, None)
            save_json(strikes_file, user_strikes)
            former_members.pop(match_user_id, None)
            save_json(former_members_file, former_members)
            return

        # Final attempt: check once more, then fallback message
        time.sleep(8)
        members_after_retry = get_group_members()
        if any(str(m.get("user_id")) == match_user_id for m in members_after_retry):
            logger.info(f"✅ User added on retry despite omitted from poll: {nickname}")
            send_system_message(f"> @{sender}: {full_text}\n✅ {nickname} re-added after retry.")
            banned_users.pop(match_user_id, None)
            save_json(banned_users_file, banned_users)
            user_swear_counts.pop(match_user_id, None)
            save_json(user_swear_counts_file, user_swear_counts)
            user_strikes.pop(match_user_id, None)
            save_json(strikes_file, user_strikes)
            former_members.pop(match_user_id, None)
            save_json(former_members_file, former_members)
            return

        # Give admin a helpful fallback
        share_url = get_group_share_url()
        error_msg = f"GroupMe sync delay, recent removal cooldown, user privacy settings, or API silent failure (code {status_code})"
        logger.warning(f"⚠️ Unban failed even after retry and final checks: {nickname} ({match_user_id}). {error_msg}")
        fallback_msg = f"⚠️ Could not automatically re-add {nickname}. {error_msg}. "
        if share_url:
            fallback_msg += f"Please share this link with {nickname}: {share_url}"
        else:
            fallback_msg += "Please re-add manually via the GroupMe app."
        send_system_message(f"> @{sender}: {full_text}\n{fallback_msg}")
    except Exception as e:
        logger.error(f"unban_user unexpected error: {e}")
        send_system_message(f"> @{sender}: {full_text}\n❌ Error while unbanning '{target_alias}': {str(e)}")

# -----------------------
# Ban command (admin)
# -----------------------
def ban_user(target_alias: str, sender_name: str, sender_id: str, original_text: str) -> bool:
    if str(sender_id) not in ADMIN_IDS:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Only admins can use this command")
        return False
    if not ACCESS_TOKEN or not GROUP_ID:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Missing ACCESS_TOKEN or GROUP_ID")
        logger.error("Missing ACCESS_TOKEN or GROUP_ID for ban")
        return False

    try:
        result = fuzzy_find_member(target_alias)
        if not result:
            send_system_message(f"> @{sender_name}: {original_text}\nNo user found matching '{target_alias}'")
            return False
        target_user_id, target_username = result
        success = call_ban_service(target_user_id, target_username, "Admin ban command")
        if success:
            send_system_message(f"> @{sender_name}: {original_text}\n🔨 {target_username} has been permanently banned by admin command.")
        else:
            send_system_message(f"> @{sender_name}: {original_text}\nError: Failed to ban {target_username}")
        return success
    except Exception as e:
        logger.error(f"Ban command error: {e}")
        send_system_message(f"> @{sender_name}: {original_text}\nError: Failed to ban '{target_alias}'")
        return False

# -----------------------
# Strikes feature (new)
# -----------------------
def record_strike(target_alias: str, admin_name: str, admin_id: str, original_text: str) -> None:
    """
    Admin-only: record a strike against a user.
    - Finds the user by fuzzy match among members/former/banned.
    - Persists strikes to file.
    - Sends system message acknowledging the strike and current totals.
    """
    if str(admin_id) not in ADMIN_IDS:
        send_system_message(f"> @{admin_name}: {original_text}\nError: Only admins can issue 'strike' commands.")
        return

    result = fuzzy_find_member(target_alias)
    if not result:
        send_system_message(f"> @{admin_name}: {original_text}\nNo user found matching '{target_alias}'.")
        return

    user_id, nickname = result
    user_id = str(user_id)
    if user_id not in user_strikes:
        user_strikes[user_id] = 0
    user_strikes[user_id] += 1
    save_json(strikes_file, user_strikes)

    # Also keep a mirrored "swear count" optionally? We leave swear counts separate.
    count = user_strikes[user_id]
    logger.info(f"Recorded strike for {nickname} ({user_id}) — total strikes: {count}")
    send_system_message(f"> @{admin_name}: {original_text}\n⚠️ Strike recorded for {nickname} ({user_id}). Total strikes: {count}")

def get_strikes_report(target_alias: str, requester_name: str, requester_id: str, original_text: str) -> None:
    """
    Admin-only: report the strike count for a user.
    """
    if str(requester_id) not in ADMIN_IDS:
        send_system_message(f"> @{requester_name}: {original_text}\nError: Only admins can use '!strikes' command.")
        return

    result = fuzzy_find_member(target_alias)
    if not result:
        send_system_message(f"> @{requester_name}: {original_text}\nNo user found matching '{target_alias}'.")
        return

    user_id, nickname = result
    user_id = str(user_id)
    count = int(user_strikes.get(user_id, 0))
    send_system_message(f"> @{requester_name}: {original_text}\n📊 {nickname} ({user_id}) has {count} strike(s).")

# -----------------------
# Violation detection (swear checks)
# -----------------------
def check_for_violations(text: str, user_id: str, username: str) -> bool:
    text_lower = text.lower()
    text_words = text_lower.split()

    # Instant ban words first
    for word in text_words:
        clean_word = word.strip('.,!?"\'()[]{}').lower()
        if clean_word in INSTANT_BAN_WORDS:
            logger.info(f"🚨 INSTANT BAN: '{clean_word}' from {username}")
            success = call_ban_service(user_id, username, f"Instant ban: {clean_word}")
            if success:
                send_system_message(f"🔨 {username} has been permanently banned for using prohibited language.")
            return True

    # Regular swear words -> increase swear count and warn / ban at threshold
    for word in text_words:
        clean_word = word.strip('.,!?"\'()[]{}').lower()
        if clean_word in REGULAR_SWEAR_WORDS:
            uid = str(user_id)
            if uid not in user_swear_counts:
                user_swear_counts[uid] = 0
            user_swear_counts[uid] += 1
            save_json(user_swear_counts_file, user_swear_counts)
            current_count = user_swear_counts[uid]
            logger.info(f"{username} swear count: {current_count}/10")

            if current_count >= 10:
                success = call_ban_service(uid, username, f"10 strikes - swear words")
                if success:
                    send_system_message(f"🔨 {username} has been banned for repeated inappropriate language (10 strikes).")
                    # reset their count locally
                    user_swear_counts[uid] = 0
                    save_json(user_swear_counts_file, user_swear_counts)
                return True
            else:
                remaining = 10 - current_count
                send_system_message(f"⚠️ {username} ({uid}) - Warning {current_count}/10 for inappropriate language. {remaining} more and you're banned!")
            break
    return False

# -----------------------
# Message sending helpers
# -----------------------
def send_system_message(text: str) -> bool:
    """
    Send a message through configured bot. Honours cooldown for non-strike messages.
    """
    global last_system_message_time
    if not BOT_ID:
        logger.error("No BOT_ID configured - can't send messages")
        return False

    # Consider messages that include 'Warning' or 'banned' or 'Strike' as strike-like and ignore cooldown
    is_strike_message = any(k in text for k in ["Warning", "banned", "Strike", "Strike recorded", "strike", "🔨", "⚠️"])
    now = time.time()
    if not is_strike_message and now - last_system_message_time < cooldown_seconds:
        logger.info("System message cooldown active")
        return False

    url = "https://api.groupme.com/v3/bots/post"
    payload = {"bot_id": BOT_ID, "text": text}
    try:
        response = requests.post(url, json=payload, timeout=8)
        response.raise_for_status()
        if not is_strike_message:
            last_system_message_time = now
        logger.info(f"System message sent: {text[:80]}")
        return True
    except Exception as e:
        logger.error(f"GroupMe system send error: {e}")
        return False

def send_message(text: str) -> bool:
    global last_sent_time
    now = time.time()
    if now - last_sent_time < cooldown_seconds:
        logger.info("Regular message cooldown active")
        return False
    if not BOT_ID:
        logger.error("No BOT_ID configured - can't send messages")
        return False
    url = "https://api.groupme.com/v3/bots/post"
    payload = {"bot_id": BOT_ID, "text": text}
    try:
        response = requests.post(url, json=payload, timeout=8)
        response.raise_for_status()
        last_sent_time = now
        logger.info(f"Regular message sent: {text[:80]}")
        return True
    except Exception as e:
        logger.error(f"GroupMe send error: {e}")
        return False

# -----------------------
# System message heuristics
# -----------------------
def is_system_message(data: Dict[str, Any]) -> bool:
    sender_type = data.get('sender_type')
    sender_name = data.get('name', '').lower()
    if sender_type == "system":
        return True
    system_senders = ['groupme', 'system', '']
    if sender_name in system_senders:
        return True
    return False

def is_real_system_event(text_lower: str) -> bool:
    system_patterns = [
        'has joined the group',
        'has left the group',
        'was added to the group',
        'was removed from the group',
        'removed',
        'added'
    ]
    return any(pattern in text_lower for pattern in system_patterns)

# -----------------------
# Webhook: message handling
# -----------------------
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return '', 200

        # If message has no text (e.g., attachments-only), ignore for most commands but still handle system events
        text = data.get('text', '') or ''
        sender_type = data.get('sender_type')
        sender = data.get('name', 'Someone')
        user_id = str(data.get('user_id')) if data.get('user_id') is not None else None
        text_lower = text.lower()
        attachments = data.get("attachments", [])

        # --- SYSTEM EVENTS ---
        if sender_type == "system" or is_system_message(data):
            if is_real_system_event(text_lower):
                if 'has left the group' in text_lower:
                    # store former member by id if present, otherwise by a generated ghost key
                    key = user_id or f"ghost-{sender}"
                    former_members[str(key)] = sender
                    save_json(former_members_file, former_members)
                    # existing behavior: send "GAY" (user had this, kept for behavior parity)
                    send_system_message("GAY")
                elif 'has joined the group' in text_lower:
                    send_system_message("Welcome to Clean Memes, check the rules and announcement topics before chatting!")
                elif 'was removed from the group' in text_lower or 'removed' in text_lower:
                    key = user_id or f"ghost-{sender}"
                    former_members[str(key)] = sender
                    save_json(former_members_file, former_members)
                    send_system_message("this could be you if you break the rules, watch it. 👀")
            return '', 200

        # Only process user messages beyond this point
        if sender_type not in ['user']:
            return '', 200

        # ---------------------
        # COMMAND HANDLING
        # ---------------------
        # Standard admin commands: !unban, !ban, !userid
        if text_lower.startswith('!unban '):
            target_alias = text[len('!unban '):].strip()
            if target_alias:
                unban_user(target_alias, sender, user_id, text)
            return '', 200

        if text_lower.startswith('!ban '):
            target_alias = text[len('!ban '):].strip()
            if target_alias:
                ban_user(target_alias, sender, user_id, text)
            return '', 200

        if text_lower.startswith('#dismantle '):
            target_alias = text[len('#dismantle'):].strip()
            if target_alias:
                ban_user(target_alias, sender, user_id, text)
            return '', 200


        if text_lower.startswith('!userid ') or ('what is' in text_lower and 'user id' in text_lower):
            target_alias = text[len('!userid '):].strip() if text_lower.startswith('!userid ') else None
            if 'what is' in text_lower and 'user id' in text_lower:
                # e.g., "what is <name>'s user id" or "what is <name> user id"
                # try to extract the name part
                try:
                    idx = text_lower.index('user id')
                    maybe = text[:idx].replace('what is', '').strip()
                    target_alias = maybe
                except Exception:
                    target_alias = None
            if target_alias:
                get_user_id(target_alias, sender, user_id, text)
            return '', 200

        # New strikes API:
        # Admins can message: strike @username
        # And request: !strikes <username>
        # We'll accept slight variations:
        # - "strike @name"
        # - "strike name"
        # - "!strikes name"
        # - "!strikes @name"
        # - Also accept leading mention-style like "@name strike" (but not required)
        # For simplicity, check common patterns:
        stripped = text.strip()

        # Pattern: starts with 'strike ' (admin command)
        if re.match(r'^\s*strike\s+@?(\S+)', stripped, flags=re.I):
            # extract the alias after "strike"
            m = re.match(r'^\s*strike\s+@?(\S+)', stripped, flags=re.I)
            if m:
                alias = m.group(1).strip()
                # remove any trailing punctuation
                alias = alias.strip('.,!?:;')
                record_strike(alias, sender, user_id, text)
            else:
                send_system_message(f"> @{sender}: {text}\nUsage: strike @username")
            return '', 200

        # Pattern: admin typed '!strikes '
        if text_lower.startswith('!strikes '):
            alias = text[len('!strikes '):].strip()
            if alias:
                # remove leading @ if present
                alias = alias.lstrip('@').strip()
                get_strikes_report(alias, sender, user_id, text)
            return '', 200

        # Also allow '!strikes' with ID only
        if text_lower.startswith('!strikes') and len(text_lower.split()) == 1:
            send_system_message(f"> @{sender}: {text}\nUsage: !strikes <username or id>")
            return '', 200

        # ---------------------
        # VIOLATION CHECKS (swear words etc.)
        # ---------------------
        if user_id and text:
            check_for_violations(text, user_id, sender)

        # ---------------------
        # TRIGGERS / AUTORESPONSES
        # ---------------------
        if 'clean memes' in text_lower:
            send_message("We're the best!")
        elif 'wsg' in text_lower:
            send_message("God is good")
        elif 'cooper is my pookie' in text_lower:
            send_message("me too bro")
        elif 'https:' in text and not any(att.get("type") == "video" for att in attachments):
            send_message("Delete this, links are not allowed, admins have been notified")
        elif 'france' in text_lower:
            send_message("please censor that to fr*nce")
        elif 'french' in text_lower:
            send_message("please censor that to fr*nch")


        return '', 200

    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return '', 500

# -----------------------
# Admin HTTP helper to reset counts (existing)
# -----------------------
@app.route('/reset-count', methods=['POST'])
def reset_count():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        if not user_id:
            return {"error": "user_id required"}, 400
        old_count = int(user_swear_counts.get(str(user_id), 0))
        user_swear_counts[str(user_id)] = 0
        save_json(user_swear_counts_file, user_swear_counts)
        return {"success": True, "old_count": old_count, "new_count": 0}
    except Exception as e:
        logger.error(f"Reset count error: {e}")
        return {"error": str(e)}, 500

# -----------------------
# Health endpoint
# -----------------------
@app.route('/health', methods=['GET'])
def health():
    bot_id_brief = (BOT_ID[:8] + "...") if BOT_ID else "MISSING"
    return {
        "status": "healthy" if BOT_ID else "missing config",
        "bot_id": bot_id_brief,
        "bot_name": BOT_NAME,
        "ban_system": {
            "enabled": bool(GROUP_ID and BAN_SERVICE_URL),
            "group_id": GROUP_ID or "MISSING",
            "ban_service_url": BAN_SERVICE_URL or "MISSING",
            "instant_ban_words": len(INSTANT_BAN_WORDS),
            "regular_swear_words": len(REGULAR_SWEAR_WORDS),
            "tracked_users": len(user_swear_counts),
            "banned_users": len(banned_users),
            "former_members": len(former_members),
            "strikes_tracked": len(user_strikes)
        },
        "system_triggers": {
            "left": "GAY",
            "joined": "Welcome message",
            "removed": "Rules warning"
        }
    }

# -----------------------
# Boot
# -----------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    logger.info("🚀 Starting ClankerBot (no AI)")
    logger.info(f"Bot ID: {'SET' if BOT_ID else 'MISSING'}")
    logger.info(f"Bot Name: {BOT_NAME}")
    logger.info(f"Ban System: {'ENABLED' if GROUP_ID and BAN_SERVICE_URL else 'DISABLED'}")
    logger.info(f"Loaded banned_users: {len(banned_users)} entries")
    logger.info(f"Loaded user_swear_counts: {len(user_swear_counts)} entries")
    logger.info(f"Loaded user_strikes: {len(user_strikes)} entries")
    app.run(host="0.0.0.0", port=port, debug=False)
