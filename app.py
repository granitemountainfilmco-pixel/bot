from flask import Flask, request, jsonify, Blueprint
import requests
import os
import logging
import time
import threading
from fuzzywuzzy import process, fuzz
import urllib.parse
import json
import re
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime, timedelta
import random

app = Flask(__name__)

# --- MERGED CONFIG ---
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")  # Shared: GroupMe API token
GROUP_ID = os.getenv("GROUP_ID")  # Shared: Group ID
BOT_ID = os.getenv("BOT_ID")
BOT_NAME = os.getenv("BOT_NAME", "ClankerBot")
PORT = int(os.getenv("PORT", 5000))
SELF_PING = os.getenv("KEEP_ALIVE_SELF_PING", "true").lower() in ("1", "true", "yes")
ADMIN_IDS = [
    '119189324', '82717917', '124068433', '103258964', '123259855',
    '114848297', '121920211', '134245360', '113819798', '130463543',
    '123142410', '131010920', '133136781', '124453541', '122782552',
    '117776217', '85166615', '114066399', '84254355', '115866991', '124523409',
    '125629030', '124579254', '121097804'
]
JSONBIN_MASTER_KEY = os.getenv("JSONBIN_MASTER_KEY")
JSONBIN_BIN_ID = os.getenv("JSONBIN_BIN_ID")

# Swear word categories
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

# Persistence files
banned_users_file = "banned_users.json"
former_members_file = "former_members.json"
user_swear_counts_file = "user_swear_counts.json"
strikes_file = "user_strikes.json"
daily_counts_file = "daily_message_counts.json"
last_messages_file = "last_messages.json"
system_messages_enabled_file = "system_messages_enabled.json"

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GROUPME_API = "https://api.groupme.com/v3"
API_URL = "https://api.groupme.com/v3"

# In-memory caches
user_swear_counts: Dict[str, int] = {}
banned_users: Dict[str, str] = {}
former_members: Dict[str, str] = {}
user_strikes: Dict[str, int] = {}
muted_users: Dict[str, int] = {}
daily_message_counts: Dict[str, int] = {}
last_message_by_user: Dict[str, str] = {}
daily_counts_date: Optional[str] = None
last_messages_date: Optional[str] = None
system_messages_enabled = True

# Cooldown
last_sent_time = 0.0
last_system_message_time = 0.0
cooldown_seconds = 10

# -----------------------
# JSON Helpers
# -----------------------
def load_json(file_path: str) -> Dict[str, Any]:
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {str(k): v for k, v in data.items()}
                return data
        except Exception as e:
            logger.error(f"Failed to load {file_path}: {e}")
    return {}

def save_json(file_path: str, data: Dict[str, Any]) -> None:
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save {file_path}: {e}")

def load_system_messages_enabled() -> bool:
    data = load_json(system_messages_enabled_file)
    return bool(data.get("enabled", True))

def save_system_messages_enabled(enabled: bool) -> None:
    save_json(system_messages_enabled_file, {"enabled": enabled})

# Initialize
system_messages_enabled = load_system_messages_enabled()
banned_users = load_json(banned_users_file) or {}
user_swear_counts = load_json(user_swear_counts_file) or {}
former_members = load_json(former_members_file) or {}
user_strikes = load_json(strikes_file) or {}

# -----------------------
# Daily Tracking Init
# -----------------------
def _initialize_daily_tracking():
    global daily_message_counts, last_message_by_user, daily_counts_date, last_messages_date
    today = datetime.now().strftime("%Y-%m-%d")
    raw = load_json(daily_counts_file)
    if isinstance(raw, dict) and raw.get("date") == today and isinstance(raw.get("counts"), dict):
        daily_message_counts = {str(k): int(v) for k, v in raw.get("counts", {}).items()}
        daily_counts_date = today
        logger.info(f"Loaded daily_message_counts for {today} ({len(daily_message_counts)} users).")
    else:
        daily_message_counts = {}
        daily_counts_date = today
        save_json(daily_counts_file, {"date": today, "counts": daily_message_counts})
        logger.info("Initialized new daily_message_counts for today.")
    raw2 = load_json(last_messages_file)
    if isinstance(raw2, dict) and raw2.get("date") == today and isinstance(raw2.get("last"), dict):
        last_message_by_user = {str(k): v for k, v in raw2.get("last", {}).items()}
        last_messages_date = today
        logger.info(f"Loaded last_message_by_user for {today} ({len(last_message_by_user)} users).")
    else:
        last_message_by_user = {}
        last_messages_date = today
        save_json(last_messages_file, {"date": today, "last": last_message_by_user})
        logger.info("Initialized new last_message_by_user for today.")

_initialize_daily_tracking()


def extract_last_number(text: str, default: int = 30) -> int:
    """
    Extract the LAST integer from text.
    Examples:
      "!mute @grok 5 minutes" → 5
      "!mute 10" → 10
      "!mute @grok five" → 30 (default)
      "!mute @grok -3" → 30 (enforced min 1)
    """
    # Find all numbers
    numbers = re.findall(r'\d+', text)
    if not numbers:
        return default
    try:
        num = int(numbers[-1])  # last number
        return max(1, num)  # enforce at least 1 min
    except:
        return default

# -----------------------
# Ban Functions
# -----------------------
def get_user_membership_id(user_id):
    try:
        url = f"{GROUPME_API}/groups/{GROUP_ID}?token={ACCESS_TOKEN}"
        response = requests.get(url, timeout=8)
        if response.status_code == 401:
            logger.error("ACCESS TOKEN INVALID OR EXPIRED - Regenerate your token at https://dev.groupme.com/!")
            return None
        elif response.status_code != 200:
            logger.error(f"Failed to get group info: {response.status_code} - {response.text}")
            return None
        group_data = response.json()
        members = group_data.get('response', {}).get('members', [])
        for member in members:
            if str(member.get('user_id')) == str(user_id):
                membership_id = member.get('id')
                logger.info(f"Found membership ID {membership_id} for user {user_id}")
                return membership_id
        logger.warning(f"User {user_id} not found in group members")
        return None
    except Exception as e:
        logger.exception(f"Error getting membership ID for {user_id}: {e}")
        return None

def _find_replied_message(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """GroupMe puts the original message in an attachment of type "reply"."""
    for att in data.get("attachments", []):
        if att.get("type") == "reply":
            return att.get("reply_to") or att
    return None


def _delete_message_by_id(msg_id: str) -> bool:
    """DELETE /conversations/:group_id/messages/:message_id – correct endpoint."""
    url = f"{GROUPME_API}/conversations/{GROUP_ID}/messages/{msg_id}"
    try:
        r = requests.delete(url, params={"token": ACCESS_TOKEN}, timeout=8)
        if r.status_code == 204:
            logger.info(f"Deleted message {msg_id}")
            return True
        logger.error(f"Delete failed {msg_id}: {r.status_code} {r.text}")
        return False
    except Exception as e:
        logger.exception(f"Delete error {msg_id}: {e}")
        return False
        
def ban_user(user_id, username, reason):
    try:
        membership_id = get_user_membership_id(user_id)
        if not membership_id:
            logger.warning(f"Cannot ban {username} ({user_id}) — membership id not found")
            return False
        url = f"{GROUPME_API}/groups/{GROUP_ID}/members/{membership_id}/remove?token={ACCESS_TOKEN}"
        response = requests.post(url, timeout=8)
        if response.status_code == 200:
            logger.info(f"Successfully banned {username} ({user_id}) - {reason}")
            return True
        else:
            logger.error(f"Ban failed {response.status_code}: {response.text}")
            return False
    except Exception as e:
        logger.exception(f"Ban error for {username} ({user_id}): {e}")
        return False

def call_ban_service(user_id: str, username: str, reason: str) -> bool:
    success = ban_user(user_id, username, reason)
    if success:
        banned_users[str(user_id)] = username
        save_json(banned_users_file, banned_users)
        user_swear_counts.pop(str(user_id), None)
        save_json(user_swear_counts_file, user_swear_counts)
    return success

# -------------------------------------------------
# DAILY 100 COIN INFLATION — EVERY 24 HOURS
# -------------------------------------------------
def daily_coin_inflation_worker():
    """Runs every 24 hours: +100 coins to every player with a balance."""
    logger.info("Daily inflation thread started.")
    while True:
        try:
            # Wait until next 12:00 AM UTC (or any time you prefer)
            now = datetime.utcnow()
            next_run = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            sleep_seconds = (next_run - now).total_seconds()
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

            # === GIVE 100 COINS TO EVERYONE ===
            if "balances" not in game_data:
                game_data["balances"] = {}
            
            affected = 0
            for uid in list(game_data["balances"].keys()):
                game_data["balances"][uid] += 100
                affected += 1

            # Also give to anyone who has ever minted (even if balance was 0)
            for uid in game_data.get("has_minted", set()):
                uid = str(uid)
                game_data["balances"][uid] = game_data["balances"].get(uid, 0) + 100
                affected += 1

            save_game_data(game_data)
            logger.info(f"Daily inflation: +100 coins to {affected} players.")
            
            # Optional: Announce in group
            send_message("**DAILY DROP** — Everyone gets **+100 MemeCoins**! Check `!balance`")

        except Exception as e:
            logger.error(f"Inflation worker error: {e}")
            time.sleep(3600)  # Wait 1h on error

# Start the thread
inflation_thread = threading.Thread(target=daily_coin_inflation_worker, daemon=True)
inflation_thread.start()

# -----------------------
# Message Deletion (Community API)
# -----------------------
def delete_message(message_id: str) -> bool:
    if not ACCESS_TOKEN or not GROUP_ID:
        logger.error("Missing ACCESS_TOKEN or GROUP_ID for message deletion")
        return False
    url = f"{GROUPME_API}/conversations/{GROUP_ID}/messages/{message_id}?token={ACCESS_TOKEN}"
    try:
        response = requests.delete(url, timeout=8)
        if response.status_code == 204:
            logger.info(f"Deleted message {message_id}")
            return True
        else:
            logger.error(f"Delete failed for {message_id}: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.exception(f"Delete message error for {message_id}: {e}")
        return False

# -----------------------
# Group API Helpers
# -----------------------
def get_group_members() -> List[Dict[str, Any]]:
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

def google_search(query: str) -> str:
    if not query or len(query.strip()) < 3:
        return "Invalid query—try something longer!"
    encoded_query = urllib.parse.quote(query.strip(), safe='')
    headers = {"User-Agent": "Mozilla/5.0 (compatible; GoogleSearchBot/1.0)"}
    try:
        summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded_query}"
        r = requests.get(summary_url, timeout=8, headers=headers)
        if r.status_code == 404:
            search_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={encoded_query}&format=json&utf8=1"
            s = requests.get(search_url, timeout=8, headers=headers)
            s.raise_for_status()
            results = s.json().get("query", {}).get("search", [])
            if not results:
                return "No relevant Wikipedia article found."
            best_title = results[0]["title"]
            encoded_best = urllib.parse.quote(best_title, safe='')
            r = requests.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded_best}", timeout=8, headers=headers)
        r.raise_for_status()
        data = r.json()
        extract = data.get("extract", "").strip()
        if not extract:
            return "No summary available for this topic on Wikipedia."
        sentences = extract.split('. ')
        summary = '. '.join(sentences[:2])
        if not summary.endswith('.'):
            summary += '.'
        return f"Quick answer: {summary} (Source: Wikipedia)"
    except Exception as e:
        logger.error(f"Wikipedia search error for '{query}': {e}")
        return f"Search failed—{e.__class__.__name__}: {e}"

# -----------------------
# Fuzzy Member Search
# -----------------------
def fuzzy_find_member(target_alias: str) -> Optional[Tuple[str, str]]:
    if not target_alias or len(target_alias.strip()) < 2:
        return None
    target_clean = target_alias.strip().lower()
    target_words = target_clean.split()
    target_length = len(target_clean)

    def contains_all_words(nickname: str, target_words: List[str]) -> bool:
        nick_words = nickname.lower().split()
        return all(word in nick_words for word in target_words)

    if target_alias.isdigit():
        members = get_group_members()
        for m in members:
            if str(m.get("user_id")) == target_alias:
                return (str(m.get("user_id")), m.get("nickname") or "Unknown")
        if target_alias in former_members:
            return (str(target_alias), former_members[target_alias])
        if target_alias in banned_users:
            return (str(target_alias), banned_users[target_alias])

    members = get_group_members()
    nicknames = [m.get("nickname", "") for m in members if m.get("nickname")]
    for m in members:
        nick = m.get("nickname", "")
        if nick.lower() == target_clean:
            return (str(m.get("user_id")), nick or "Unknown")

    nick_lower_list = [n.lower() for n in nicknames if n]
    if nick_lower_list:
        match = process.extractOne(target_clean, nick_lower_list, score_cutoff=90, scorer=fuzz.token_sort_ratio)
        if match:
            matched_lower, score = match
            index = nick_lower_list.index(matched_lower)
            matched_nickname = nicknames[index]
            matched_length = len(matched_nickname)
            length_ratio = min(matched_length, target_length) / max(matched_length, target_length)
            if (contains_all_words(matched_nickname, target_words) and
                    (length_ratio >= 0.7 or score >= 95)):
                for m in members:
                    if m.get("nickname", "").lower() == matched_lower:
                        return (str(m.get("user_id")), m.get("nickname") or "Unknown")

    for uid, nick in former_members.items():
        if nick.lower() == target_clean:
            return (str(uid), nick)

    if former_members:
        former_nicks = list(former_members.values())
        former_lower = [n.lower() for n in former_nicks]
        match = process.extractOne(target_clean, former_lower, score_cutoff=90, scorer=fuzz.token_sort_ratio)
        if match:
            matched_lower, score = match
            index = former_lower.index(matched_lower)
            matched_nickname = former_nicks[index]
            matched_length = len(matched_nickname)
            length_ratio = min(matched_length, target_length) / max(matched_length, target_length)
            if (contains_all_words(matched_nickname, target_words) and
                    (length_ratio >= 0.7 or score >= 95)):
                for k, v in former_members.items():
                    if v.lower() == matched_lower:
                        return (str(k), v)

    for uid, uname in banned_users.items():
        if target_clean in uname.lower() or target_clean == uid:
            return (str(uid), uname)

    if len(target_words) > 1:
        return None
    return None

# -----------------------
# Admin Commands
# -----------------------
def get_user_id(target_alias: str, sender_name: str, sender_id: str, original_text: str) -> bool:
    if str(sender_id) not in ADMIN_IDS:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Only admins can use this command")
        return False
    if not ACCESS_TOKEN or not GROUP_ID:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Missing ACCESS_TOKEN or GROUP_ID")
        return False
    result = fuzzy_find_member(target_alias)
    if not result:
        send_system_message(f"> @{sender_name}: {original_text}\nNo user found matching '{target_alias}'")
        return False
    user_id, nickname = result
    send_system_message(f"> @{sender_name}: {original_text}\n{nickname}'s user_id is {user_id}")
    return True

def unban_user(target_alias: str, sender: str, sender_id: str, full_text: str) -> None:
    try:
        if str(sender_id) not in ADMIN_IDS:
            send_system_message(f"> @{sender}: {full_text}\nError: Only admins can use this command")
            return
        if not ACCESS_TOKEN or not GROUP_ID:
            send_system_message(f"> @{sender}: {full_text}\nError: Missing ACCESS_TOKEN or GROUP_ID")
            return
        match_user_id = None
        match_nickname = None
        fuzzy_result = fuzzy_find_member(target_alias)
        if fuzzy_result:
            match_user_id, match_nickname = fuzzy_result
        if not match_user_id:
            for uid, uname in banned_users.items():
                if target_alias.lower() in uname.lower() or str(uid) == target_alias:
                    match_user_id = str(uid)
                    match_nickname = uname
                    break
        if not match_user_id:
            send_system_message(f"> @{sender}: {full_text}\nNo record of '{target_alias}' in banned list or group members.")
            return
        nickname = match_nickname or "Member"
        match_user_id = str(match_user_id)
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
                time.sleep(3)
                new_members = get_group_members()
                return (any(str(m.get("user_id")) == match_user_id for m in new_members), 0)
            results_id = data.get('response', {}).get('results_id')
            if not results_id:
                time.sleep(3)
                new_members = get_group_members()
                return (any(str(m.get("user_id")) == match_user_id for m in new_members), 0)
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
                            logger.info(f"User found in poll results for {nick_to_add}")
                            return True, 0
                    except Exception as e:
                        logger.error(f"Failed parsing poll JSON: {e}")
                elif poll_resp.status_code == 404:
                    break
                elif poll_resp.status_code == 503:
                    continue
            return False, 408

        success, status_code = attempt_add(safe_name)
        if success:
            send_system_message(f"> @{sender}: {full_text}\n{nickname} re-added to the group.")
            banned_users.pop(match_user_id, None)
            save_json(banned_users_file, banned_users)
            user_swear_counts.pop(match_user_id, None)
            save_json(user_swear_counts_file, user_swear_counts)
            user_strikes.pop(match_user_id, None)
            save_json(strikes_file, user_strikes)
            former_members.pop(match_user_id, None)
            save_json(former_members_file, former_members)
            return
        time.sleep(6)
        members_after = get_group_members()
        if any(str(m.get("user_id")) == match_user_id for m in members_after):
            send_system_message(f"> @{sender}: {full_text}\n{nickname} re-added to the group.")
            banned_users.pop(match_user_id, None)
            save_json(banned_users_file, banned_users)
            user_swear_counts.pop(match_user_id, None)
            save_json(user_swear_counts_file, user_swear_counts)
            user_strikes.pop(match_user_id, None)
            save_json(strikes_file, user_strikes)
            former_members.pop(match_user_id, None)
            save_json(former_members_file, former_members)
            return
        retry_name = re.sub(r"[^A-Za-z0-9]", "", safe_name)[:20] or "Member"
        logger.warning(f"Primary unban failed; retrying with '{retry_name}'.")
        success, _ = attempt_add(retry_name)
        if success:
            send_system_message(f"> @{sender}: {full_text}\n{retry_name} re-added after retry.")
            banned_users.pop(match_user_id, None)
            save_json(banned_users_file, banned_users)
            user_swear_counts.pop(match_user_id, None)
            save_json(user_swear_counts_file, user_swear_counts)
            user_strikes.pop(match_user_id, None)
            save_json(strikes_file, user_strikes)
            former_members.pop(match_user_id, None)
            save_json(former_members_file, former_members)
            return
        share_url = get_group_share_url()
        error_msg = f"GroupMe sync delay, cooldown, or API failure (code {status_code})"
        fallback_msg = f"Could not re-add {nickname}. {error_msg}. "
        if share_url:
            fallback_msg += f"Send them: {share_url}"
        send_system_message(f"> @{sender}: {full_text}\n{fallback_msg}")
    except Exception as e:
        logger.error(f"unban_user error: {e}")
        send_system_message(f"> @{sender}: {full_text}\nError unbanning '{target_alias}': {str(e)}")

def ban_user_command(target_alias: str, sender_name: str, sender_id: str, original_text: str) -> bool:
    if str(sender_id) not in ADMIN_IDS:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Only admins can use this command")
        return False
    if not ACCESS_TOKEN or not GROUP_ID:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Missing ACCESS_TOKEN or GROUP_ID")
        return False
    result = fuzzy_find_member(target_alias)
    if not result:
        send_system_message(f"> @{sender_name}: {original_text}\nNo user found matching '{target_alias}'")
        return False
    target_user_id, target_username = result
    success = call_ban_service(target_user_id, target_username, "Admin ban command")
    if success:
        send_system_message(f"> @{sender_name}: {original_text}\n{target_username} has been permanently banned by admin command.")
    else:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Failed to ban {target_username}")
    return success

def record_strike(target_alias: str, admin_name: str, admin_id: str, original_text: str) -> None:
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
    count = user_strikes[user_id]
    logger.info(f"Recorded strike for {nickname} ({user_id}) — total strikes: {count}")
    send_system_message(f"> @{admin_name}: {original_text}\nStrike recorded for {nickname} ({user_id}). Total strikes: {count}")

def get_strikes_report(target_alias: str, requester_name: str, requester_id: str, original_text: str) -> None:
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
    send_system_message(f"> @{requester_name}: {original_text}\n{nickname} ({user_id}) has {count} strike(s).")

# -----------------------
# Violation Detection with Deletion
# -----------------------
def check_for_violations(text: str, user_id: str, username: str, message_id: str) -> bool:
    """
    Check for muted users first, then swears/instant bans.
    Deletes message + sends warning if muted or violation.
    """
    uid = str(user_id)
    now = time.time()
    deleted = False

    # === MUTE CHECK: Auto-delete if user is muted ===
    # === MUTE ENFORCEMENT ===
    if uid in muted_users and now < muted_users[uid]:
        minutes_left = int((muted_users[uid] - now) / 60) + 1
        # Delete the message using the correct API
        _delete_message_by_id(message_id)
        # Warn once per mute (not on every message)
        if not hasattr(check_for_violations, "_mute_warned"):
            check_for_violations._mute_warned = {}
        if uid not in check_for_violations._mute_warned:
            send_system_message(
                f"{username} is **muted** – {minutes_left} minute(s) left. Your messages are being deleted."
            )
            check_for_violations._mute_warned[uid] = True
        logger.info(f"MUTED MSG DELETED: {username} ({uid}), {minutes_left}m left")
        deleted = True
        return deleted

    # === Clean expired mutes ===
    expired = [u for u, until in list(muted_users.items()) if now >= until]
    for u in expired:
        del muted_users[u]
    if expired:
        logger.info(f"Cleaned {len(expired)} expired mutes")

    # === Instant Ban Words ===
    text_lower = text.lower()
    text_words = text_lower.split()
    for word in text_words:
        clean_word = word.strip('.,!?"\'()[]{}').lower()
        if clean_word in INSTANT_BAN_WORDS:
            logger.info(f"INSTANT BAN: '{clean_word}' from {username} (msg {message_id})")
            delete_message(message_id)
            success = call_ban_service(uid, username, f"Instant ban: {clean_word}")
            if success:
                send_system_message(f"{username} has been permanently banned for using prohibited language. (Message deleted)")
            deleted = True
            return True

    # === Regular Swear Words (Strike System) ===
    for word in text_words:
        clean_word = word.strip('.,!?"\'()[]{}').lower()
        if clean_word in REGULAR_SWEAR_WORDS:
            if uid not in user_swear_counts:
                user_swear_counts[uid] = 0
            user_swear_counts[uid] += 1
            save_json(user_swear_counts_file, user_swear_counts)
            current_count = user_swear_counts[uid]
            logger.info(f"{username} swear count: {current_count}/10 (msg {message_id})")
            
            delete_message(message_id)
            
            if current_count >= 10:
                success = call_ban_service(uid, username, f"10 strikes - swear words")
                if success:
                    send_system_message(f"{username} has been banned for repeated inappropriate language (10 strikes). (Message deleted)")
                    user_swear_counts[uid] = 0
                    save_json(user_swear_counts_file, user_swear_counts)
                deleted = True
                return True
            else:
                remaining = 10 - current_count
                send_system_message(f"{username} ({uid}) - Warning {current_count}/10 for inappropriate language. {remaining} more and you're banned! (Message deleted)")
            deleted = True
            break  # Only one swear per message

    return deleted

# -----------------------
# Message Sending
# -----------------------
def send_system_message(text: str) -> bool:
    global last_system_message_time, system_messages_enabled
    if not BOT_ID:
        logger.error("No BOT_ID configured")
        return False
    is_strike_or_ban = any(k in text for k in ["Warning", "banned", "Strike", "ban", "deleted"])
    if not is_strike_or_ban and not system_messages_enabled:
        return False
    now = time.time()
    if not is_strike_or_ban and now - last_system_message_time < cooldown_seconds:
        return False
    url = "https://api.groupme.com/v3/bots/post"
    payload = {"bot_id": BOT_ID, "text": text}
    try:
        response = requests.post(url, json=payload, timeout=8)
        response.raise_for_status()
        if not is_strike_or_ban:
            last_system_message_time = now
        logger.info(f"System message sent: {text[:80]}")
        return True
    except Exception as e:
        logger.error(f"GroupMe send error: {e}")
        return False

def send_message(text: str) -> bool:
    global last_sent_time, system_messages_enabled
    if not system_messages_enabled:
        return False
    now = time.time()
    if now - last_sent_time < cooldown_seconds:
        return False
    if not BOT_ID:
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

def send_dm(recipient_id: str, text: str) -> bool:
    url = f"{GROUPME_API}/direct_messages"
    payload = {
        "direct_message": {
            "recipient_id": recipient_id,
            "source_guid": str(int(time.time() * 1000)),
            "text": text
        }
    }
    try:
        response = requests.post(url, params={"token": ACCESS_TOKEN}, json=payload, timeout=8)
        if response.status_code == 201:
            logger.info(f"DM sent to {recipient_id}: {text[:30]}")
            return True
        else:
            logger.error(f"DM failed {response.status_code}: {response.text}")
            return False
    except Exception as e:
        logger.error(f"DM error: {e}")
        return False

# -----------------------
# System Message Detection
# -----------------------
def is_system_message(data: Dict[str, Any]) -> bool:
    sender_type = data.get('sender_type')
    sender_name = data.get('name', '').lower()
    if sender_type == "system":
        return True
    if sender_name in ['groupme', 'system', '']:
        return True
    return False

def is_real_system_event(text_lower: str) -> bool:
    patterns = [
        'has joined the group', 'has left the group',
        'was added to the group', 'was removed from the group',
        'removed', 'added'
    ]
    return any(pattern in text_lower for pattern in patterns)

# -----------------------
# Daily Leaderboard
# -----------------------
def _ensure_today_keys():
    global daily_message_counts, last_message_by_user, daily_counts_date, last_messages_date
    today = datetime.now().strftime("%Y-%m-%d")
    if daily_counts_date != today:
        daily_message_counts = {}
        daily_counts_date = today
        save_json(daily_counts_file, {"date": today, "counts": daily_message_counts})
        logger.info("Reset daily_message_counts for new day.")
    if last_messages_date != today:
        last_message_by_user = {}
        last_messages_date = today
        save_json(last_messages_file, {"date": today, "last": last_message_by_user})
        logger.info("Reset last_message_by_user for new day.")

def increment_user_message_count(user_id: str, username: str, text: str) -> None:
    try:
        _ensure_today_keys()
        uid = str(user_id)
        last_text = last_message_by_user.get(uid)
        normalized = (text or "").strip()
        if last_text == normalized:
            return
        last_message_by_user[uid] = normalized
        daily_message_counts[uid] = int(daily_message_counts.get(uid, 0)) + 1
        save_json(last_messages_file, {"date": daily_counts_date, "last": last_message_by_user})
        save_json(daily_counts_file, {"date": daily_counts_date, "counts": daily_message_counts})
    except Exception as e:
        logger.error(f"Error incrementing count: {e}")

def _build_leaderboard_message(top_n: int = 3) -> str:
    try:
        _ensure_today_keys()
        if not daily_message_counts:
            return "Daily Unemployed Leaders:\nNo messages recorded today."
        members = get_group_members()
        id_to_nick = {str(m.get("user_id")): m.get("nickname") for m in members if m.get("user_id")}
        fallback = {str(k): v for k, v in former_members.items()}
        sorted_items = sorted(daily_message_counts.items(), key=lambda kv: (-int(kv[1]), kv[0]))
        lines = ["Daily Unemployed Leaders:"]
        rank = 1
        for uid, cnt in sorted_items[:top_n]:
            name = id_to_nick.get(uid) or fallback.get(uid) or f"User {uid}"
            lines.append(f"{rank}. {name} ({cnt})")
            rank += 1
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Error building leaderboard: {e}")
        return "Daily Unemployed Leaders:\nError."

def _reset_daily_counts():
    global daily_message_counts, last_message_by_user, daily_counts_date, last_messages_date
    today = datetime.now().strftime("%Y-%m-%d")
    daily_message_counts = {}
    last_message_by_user = {}
    daily_counts_date = today
    last_messages_date = today
    save_json(daily_counts_file, {"date": today, "counts": daily_message_counts})
    save_json(last_messages_file, {"date": today, "last": last_message_by_user})

def _seconds_until_next_8pm():
    now = datetime.now()
    target = now.replace(hour=20, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return max(0, (target - now).total_seconds())

def daily_leaderboard_worker():
    logger.info("Leaderboard thread started.")
    while True:
        try:
            secs = _seconds_until_next_8pm()
            while secs > 0:
                sleep_chunk = min(secs, 300)
                time.sleep(sleep_chunk)
                secs -= sleep_chunk
            msg = _build_leaderboard_message()
            if send_message(msg):
                logger.info("Posted daily leaderboard.")
            else:
                logger.warning("Failed to post leaderboard.")
            _reset_daily_counts()
            time.sleep(5)
        except Exception as e:
            logger.error(f"Leaderboard worker error: {e}")
            time.sleep(60)

def start_leaderboard_thread_once():
    if not getattr(start_leaderboard_thread_once, "_started", False):
        t = threading.Thread(target=daily_leaderboard_worker, daemon=True)
        t.start()
        start_leaderboard_thread_once._started = True
        logger.info("Leaderboard thread initialized.")

# -----------------------
# Jsonbin Helpers
# -----------------------
def load_game_data() -> Dict[str, Any]:
    if not JSONBIN_MASTER_KEY or not JSONBIN_BIN_ID:
        logger.error("Missing JSONBIN creds")
        return {}
    url = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}/latest"
    headers = {"X-Master-Key": JSONBIN_MASTER_KEY}
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        resp.raise_for_status()
        data = resp.json().get("record", {})
        data["has_minted"] = set(data.get("has_minted", []))
        return data
    except Exception as e:
        logger.error(f"Jsonbin load error: {e}")
        return {}

def save_game_data(data: Dict[str, Any]) -> bool:
    if not JSONBIN_MASTER_KEY or not JSONBIN_BIN_ID:
        return False
    save_data = data.copy()
    save_data["has_minted"] = list(save_data["has_minted"])
    url = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"
    headers = {"X-Master-Key": JSONBIN_MASTER_KEY, "Content-Type": "application/json"}
    try:
        resp = requests.put(url, headers=headers, json=save_data, timeout=5)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Jsonbin save error: {e}")
        return False

# Game Data Init
game_data = load_game_data() or {
    "nfts": {},
    "balances": {},
    "has_minted": set(),
    "pending_rarity": {}
}
next_nft_id = max([int(k) for k in game_data["nfts"]], default=0) + 1

# -----------------------
# Get Message Likes
# -----------------------
def get_message_likes(message_id: str) -> int:
    url = f"{GROUPME_API}/groups/{GROUP_ID}/messages/{message_id}?token={ACCESS_TOKEN}"
    try:
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200:
            msg = resp.json().get('response', {}).get('message', {})
            return len(msg.get('favorited_by', []))
        else:
            logger.error(f"Get likes failed: {resp.status_code}")
            return 0
    except Exception as e:
        logger.error(f"Get likes error: {e}")
        return 0

# -----------------------
# Rarity Update Thread
# -----------------------
def rarity_update_worker():
    while True:
        try:
            now = time.time()
            updated = False
            for nft_id, info in list(game_data["pending_rarity"].items()):
                if now - info["mint_time"] >= 86400:  # 24 hours
                    likes = get_message_likes(info["message_id"])
                    rarity = min(10, max(1, likes // 2 + 1))  # e.g., 0 likes =1, 2=2, 20+=10
                    game_data["nfts"][nft_id]["rarity"] = rarity
                    game_data["nfts"][nft_id]["price"] = rarity * 50  # Update price
                    send_dm(game_data["nfts"][nft_id]["owner"], f"Your MemeNFT #{nft_id} rarity updated to {rarity} based on {likes} likes!")
                    del game_data["pending_rarity"][nft_id]
                    updated = True
            if updated:
                save_game_data(game_data)
        except Exception as e:
            logger.error(f"Rarity worker error: {e}")
        time.sleep(3600)  # Check hourly

# Start Thread
t_rarity = threading.Thread(target=rarity_update_worker, daemon=True)
t_rarity.start()

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        global system_messages_enabled, game_data, next_nft_id, pending_actions
        data = request.get_json()
        if not data:
            return '', 200

        text = data.get('text', '') or ''
        sender_type = data.get('sender_type')
        sender = data.get('name', 'Someone')
        user_id = str(data.get('user_id')) if data.get('user_id') is not None else None
        message_id = data.get('id')
        text_lower = text.lower()
        attachments = data.get("attachments", [])
        is_dm = 'group_id' not in data or not data['group_id']

        # === SYSTEM MESSAGES (Join / Leave / Ban) ===
        if sender_type == "system" or is_system_message(data):
            if is_real_system_event(text_lower):
                if 'has left the group' in text_lower or 'was removed from the group' in text_lower:
                    key = user_id or f"ghost-{sender}"
                    former_members[str(key)] = sender
                    save_json(former_members_file, former_members)

                    # 1 in 10 chance to say "GAY"
                    if random.randint(1, 10) == 1:
                        send_system_message("GAY")
#                elif 'has joined the group' in text_lower:
#                    send_system_message("Welcome to Clean Memes, check the rules and announcement topics before chatting!")
#                elif 'has rejoined the group' in text_lower:
#                    send_system_message("oh look who's back")
            return '', 200
        if sender_type not in ['user']:
            return '', 200

        # === VIOLATION CHECK (Swear Filter, Mute, Ban) ===
        if user_id and text and message_id:
            deleted = check_for_violations(text, user_id, sender, str(message_id))
            if deleted:
                return '', 200

        # === DAILY MESSAGE COUNT ===
        if user_id and text:
            increment_user_message_count(user_id, sender, text)

        if text_lower.startswith('!mute'):
            if str(user_id) not in ADMIN_IDS:
                send_system_message(f"> @{sender}: {text}\nOnly admins can use !mute")
                return '', 200

            replied = _find_replied_message(data)
            if replied:
                target_id = str(replied.get("user_id"))
                target_nick = replied.get("name", "Unknown")
                # Extract LAST number from entire command text
                minutes = extract_last_number(text, 30)
            else:
                parts = text.split()
                if len(parts) < 2:
                    send_system_message("> Usage: `!mute` (reply) **or** `!mute @User [minutes]`")
                    return '', 200
                target_name = parts[1].lstrip('@')
                # Extract LAST number from entire text (after splitting)
                minutes = extract_last_number(text, 30)
                target = fuzzy_find_member(target_name)
                if not target:
                    send_system_message(f"> @{sender}: User **{target_name}** not found")
                    return '', 200
                target_id, target_nick = target

            # Apply mute
            muted_until = time.time() + minutes * 60
            muted_users[target_id] = muted_until
            send_system_message(
                f"{target_nick} (`{target_id}`) has been **muted** for **{minutes}** minute(s)."
            )
            logger.info(f"Muted {target_nick} ({target_id}) for {minutes}m")
            return '', 200
            
        if text_lower.startswith('!unmute '):
            if str(user_id) not in ADMIN_IDS:
                send_system_message(f"> @{sender}: Only admins can use !unmute")
                return '', 200
            target_name = text[len('!unmute '):].strip().lstrip('@')
            target = fuzzy_find_member(target_name)
            if not target:
                send_system_message(f"> @{sender}: User not found")
                return '', 200
            target_id, _ = target
            if target_id in muted_users:
                del muted_users[target_id]
                send_system_message(f"{target_name} has been unmuted.")
            else:
                send_system_message(f"{target_name} was not muted.")
            return '', 200

        if text_lower.startswith('!ban '):
            if str(user_id) not in ADMIN_IDS:
                send_system_message(f"> @{sender}: Only admins can use !ban")
                return '', 200
            target_name = text[len('!ban '):].strip().lstrip('@')
            ban_user_command(target_name, sender, user_id, text)
            return '', 200

        if text_lower.startswith('!unban '):
            if str(user_id) not in ADMIN_IDS:
                send_system_message(f"> @{sender}: Only admins can use !unban")
                return '', 200
            target_name = text[len('!unban '):].strip().lstrip('@')
            unban_user(target_name, sender, user_id, text)
            return '', 200

        if text_lower.startswith('!delete'):
            if str(user_id) not in ADMIN_IDS:
                send_system_message(f"> @{sender}: {text}\nOnly admins can use !delete")
                return '', 200

            # 1. Delete replied-to message
            replied = _find_replied_message(data)
            if replied and replied.get("id"):
                if _delete_message_by_id(replied["id"]):
                    send_system_message(f"Message {replied['id']} (replied-to) deleted by @{sender}.")
                else:
                    send_system_message(f"Failed to delete replied-to message {replied['id']}.")
                return '', 200

            # 2. Fallback: explicit ID
            parts = text.split()
            if len(parts) < 2:
                send_system_message("> Usage: `!delete` (reply) **or** `!delete <MESSAGE_ID>`")
                return '', 200

            msg_id = parts[1]
            if _delete_message_by_id(msg_id):
                send_system_message(f"Message {msg_id} deleted by @{sender}.")
            else:
                send_system_message(f"Failed to delete message {msg_id}.")
            return '', 200
    
        if text_lower.startswith('!getid '):
            target_name = text[len('!getid '):].strip().lstrip('@')
            get_user_id(target_name, sender, user_id, text)
            return '', 200

        if text_lower.startswith('!strike '):
            if str(user_id) not in ADMIN_IDS:
                send_system_message(f"> @{sender}: Only admins can issue strikes")
                return '', 200
            target_name = text[len('!strike '):].strip().lstrip('@')
            record_strike(target_name, sender, user_id, text)
            return '', 200

        if text_lower.startswith('!strikes '):
            if str(user_id) not in ADMIN_IDS:
                send_system_message(f"> @{sender}: Only admins can view strikes")
                return '', 200
            target_name = text[len('!strikes '):].strip().lstrip('@')
            get_strikes_report(target_name, sender, user_id, text)
            return '', 200

        if text_lower == '!system on' and str(user_id) in ADMIN_IDS:
            system_messages_enabled = True
            save_system_messages_enabled(True)
            send_system_message("System messages **ENABLED** by admin.")
            return '', 200

        if text_lower == '!system off' and str(user_id) in ADMIN_IDS:
            system_messages_enabled = False
            save_system_messages_enabled(False)
            send_system_message("System messages **DISABLED** by admin.")
            return '', 200

        if 'clean memes' in text_lower:
            send_message("We're the best!")
        elif 'wsg' in text_lower:
            send_message("God is good")
        elif '!kill' in text_lower:
            send_message("Error: Only God himself can use this command")
        elif 'cooper is my pookie' in text_lower:
            send_message("me too bro")
        elif 'wrx is tall' in text_lower:
            send_message("Wrx considers a chihuahua to be a large dog")
        elif 'https:' in text and not any(att.get("type") == "video" for att in attachments):
            send_message("Delete this, links are not allowed, admins have been notified")
        elif 'france' in text_lower:
            send_message("please censor that to fr*nce")
        elif 'french' in text_lower:
            send_message("please censor that to fr*nch")


        if text_lower.strip() == '!leaderboard':
            msg = _build_leaderboard_message()
            send_message(msg)
            return '', 200

        return '', 200

    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return '', 500
        

# -----------------------
# Global Pending Actions (for DM trade replies)
# -----------------------
pending_actions = {}  # {user_id: {"action": "...", ...}}

# -----------------------
# Start Leaderboard Thread (Daily Unemployed)
# -----------------------
start_leaderboard_thread_once()

# -----------------------
# Keep-Alive Ping (Prevents Render/Heroku Sleep)
# -----------------------
def keep_alive():
    if not SELF_PING:
        return
    def ping():
        while True:
            try:
                requests.get(f"https://{request.host}", timeout=5)
                logger.info("Self-ping sent to keep alive.")
            except:
                pass
            time.sleep(600)  # 10 minutes
    t = threading.Thread(target=ping, daemon=True)
    t.start()

# -----------------------
# Flask App Run
# -----------------------
if __name__ == "__main__":
    keep_alive()
    try:
        port = int(os.environ.get("PORT", 5000))
        logger.info(f"Starting Flask app on port {port}")
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Failed to start server: {e}")
