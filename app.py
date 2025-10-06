from flask import Flask, request
import requests
import os
import time
import logging
from fuzzywuzzy import process
import json
import re

app = Flask(__name__)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
BOT_ID = os.getenv("BOT_ID")
BOT_NAME = os.getenv("BOT_NAME", "ClankerBot")
GROUP_ID = os.getenv("GROUP_ID")
BAN_SERVICE_URL = os.getenv("BAN_SERVICE_URL")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")  # GroupMe API token for member list and add
API_URL = "https://api.groupme.com/v3"
ADMIN_IDS = [
    '119189324', '82717917', '124068433', '103258964', '123259855',
    '114848297', '121920211', '134245360', '113819798', '130463543',
    '123142410', '131010920', '133136781', '124453541', '122782552',
    '117776217', '85166615', '114066399', '84254355', '115866991', '124523409',
    '125629030', '124579254'
]

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

# Track user swear counts and banned users (persisted)
banned_users_file = "banned_users.json"
former_members_file = "former_members.json"
user_swear_counts_file = "user_swear_counts.json"
user_swear_counts = {}
banned_users = {}
former_members = {}

# Load persisted data
def load_json(file_path):
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load {file_path}: {e}")
    return {}

def save_json(file_path, data):
    try:
        with open(file_path, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Failed to save {file_path}: {e}")

banned_users = load_json(banned_users_file)
user_swear_counts = load_json(user_swear_counts_file)
former_members = load_json(former_members_file)

# Cooldowns
last_sent_time = 0
last_system_message_time = 0
cooldown_seconds = 10

def get_group_members():
    if not ACCESS_TOKEN or not GROUP_ID:
        logger.error("Missing ACCESS_TOKEN or GROUP_ID for group members")
        return []
    try:
        response = requests.get(
            f"{API_URL}/groups/{GROUP_ID}",
            headers={"X-Access-Token": ACCESS_TOKEN},
            timeout=5
        )
        response.raise_for_status()
        return response.json().get("response", {}).get("members", [])
    except Exception as e:
        logger.error(f"Failed to get group members: {e}")
        return []

def get_group_share_url():
    if not ACCESS_TOKEN or not GROUP_ID:
        return None
    try:
        response = requests.get(
            f"{API_URL}/groups/{GROUP_ID}",
            headers={"X-Access-Token": ACCESS_TOKEN},
            timeout=5
        )
        response.raise_for_status()
        return response.json().get("response", {}).get("share_url")
    except Exception as e:
        logger.error(f"Failed to get group share URL: {e}")
        return None

def call_ban_service(user_id, username, reason):
    if not BAN_SERVICE_URL:
        logger.warning("No BAN_SERVICE_URL configured - can't ban users")
        return False
    payload = {
        'user_id': user_id,
        'username': username,
        'reason': reason
    }
    try:
        response = requests.post(f"{BAN_SERVICE_URL}/ban", json=payload, timeout=5)
        if response.status_code == 200:
            logger.info(f"✅ Banned {username} ({user_id}): {reason}")
            banned_users[user_id] = username
            save_json(banned_users_file, banned_users)
            user_swear_counts.pop(user_id, None)
            save_json(user_swear_counts_file, user_swear_counts)
            return True
        else:
            logger.error(f"❌ Ban failed {response.status_code}: {response.text}")
            return False
    except Exception as e:
        logger.error(f"❌ Ban service error: {e}")
        return False

def get_user_id(target_alias, sender_name, sender_id, original_text):
    if sender_id not in ADMIN_IDS:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Only admins can use this command")
        return False
    if not ACCESS_TOKEN or not GROUP_ID:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Missing ACCESS_TOKEN or GROUP_ID")
        logger.error("Missing ACCESS_TOKEN or GROUP_ID for user_id query")
        return False
    
    try:
        members = get_group_members()
        if not members:
            send_system_message(f"> @{sender_name}: {original_text}\nError: No members found")
            return False
        
        aliases = [member["nickname"] for member in members]
        best_match = process.extractOne(target_alias.lower(), [a.lower() for a in aliases], score_cutoff=80)
        if not best_match:
            send_system_message(f"> @{sender_name}: {original_text}\nNo user found matching '{target_alias}'")
            return False
        
        for member in members:
            if member["nickname"].lower() == best_match[0].lower():
                user_id = member["user_id"]
                send_system_message(f"> @{sender_name}: {original_text}\n{member['nickname']}'s user_id is {user_id}")
                return True
        
        send_system_message(f"> @{sender_name}: {original_text}\nError: Could not retrieve user_id")
        return False
    except Exception as e:
        logger.error(f"User ID query error: {e}")
        send_system_message(f"> @{sender_name}: {original_text}\nError: Failed to fetch user_id")
        return False

def unban_user(target_alias, sender, sender_id, full_text):
    """
    Attempt to unban (re-add) a user who was banned by the bot.
    Uses ACCESS_TOKEN of a real admin for /members/add.
    Includes automatic retry and clearer diagnostics.
    """
    if sender_id not in ADMIN_IDS:
        send_system_message(f"> @{sender}: {full_text}\nError: Only admins can use this command")
        return

    if not ACCESS_TOKEN or not GROUP_ID:
        send_system_message(f"> @{sender}: {full_text}\nError: Missing ACCESS_TOKEN or GROUP_ID")
        logger.error("Missing ACCESS_TOKEN or GROUP_ID for unban")
        return

    try:
        # Check group size
        members = get_group_members()
        if len(members) >= 5000:
            send_system_message(f"> @{sender}: {full_text}\n⚠️ Cannot unban: Group has reached maximum 5000 members.")
            return

        # Find user record in banned_users {user_id: username}
        match_user_id = None
        match_nickname = None
        for uid, uname in banned_users.items():
            if target_alias.lower() in uname.lower() or str(uid) == target_alias:
                match_user_id = uid
                match_nickname = uname
                break

        if not match_user_id:
            send_system_message(f"> @{sender}: {full_text}\n⚠️ No record of '{target_alias}' in banned list.")
            return

        nickname = match_nickname
        safe_name = re.sub(r"[^A-Za-z0-9 _\\-]", "", nickname)[:30] or "User"

        logger.info(f"Attempting to unban {nickname} ({match_user_id})")

        def attempt_add(nick_to_add, is_retry=False):
            payload = {"members": [{"nickname": nick_to_add, "user_id": match_user_id}]}
            resp = requests.post(
                f"{API_URL}/groups/{GROUP_ID}/members/add",
                headers={"X-Access-Token": ACCESS_TOKEN},
                json=payload,
                timeout=10
            )

            if resp.status_code not in (200, 202):
                logger.warning(f"Unban HTTP error {resp.status_code}: {resp.text}")
                return False, resp.status_code

            try:
                data = resp.json()
                results_id = data.get('response', {}).get('results_id')
                if not results_id:
                    # Fallback to sync check
                    logger.warning("No results_id, falling back to sync check")
                    time.sleep(3)
                    new_members = get_group_members()
                    return any(str(m.get("user_id")) == str(match_user_id) for m in new_members), 0
            except Exception as e:
                logger.error(f"Failed to parse add response: {e}")
                return False, 500

            # Poll for results
            max_polls = 15  # Increased for longer delays
            poll_successful = False
            for attempt in range(max_polls):
                sleep_time = 3 if attempt > 0 else 1  # Slightly longer initial and progressive
                time.sleep(sleep_time)
                try:
                    poll_resp = requests.get(
                        f"{API_URL}/groups/{GROUP_ID}/members/results/{results_id}",
                        headers={"X-Access-Token": ACCESS_TOKEN},
                        timeout=5
                    )
                    if poll_resp.status_code == 503:
                        continue
                    elif poll_resp.status_code == 404:
                        logger.warning("Results expired")
                        break
                    elif poll_resp.status_code == 200:
                        results_data = poll_resp.json().get('response', {})
                        added_members = results_data.get('members', [])
                        logger.info(f"Poll {attempt + 1} response members count: {len(added_members)}")  # Log count
                        logger.debug(f"Full poll response: {poll_resp.json()}")  # Detailed log
                        if any(str(m.get('user_id')) == str(match_user_id) for m in added_members):
                            logger.info(f"✅ User found in poll results for {nick_to_add}")
                            poll_successful = True
                            break
                        else:
                            logger.warning(f"Poll {attempt + 1} returned 200 but user {match_user_id} not in added members")
                    else:
                        logger.warning(f"Poll {attempt + 1} unexpected status: {poll_resp.status_code}")
                        break
                except Exception as e:
                    logger.error(f"Poll attempt {attempt + 1} error: {e}")
                    if attempt < max_polls - 1:
                        continue

            if not poll_successful:
                if attempt == max_polls - 1:
                    logger.warning("Polling timed out without finding user in results")
                return False, 408

            # If poll successful (user in results), double-check in group
            time.sleep(5)  # Longer sleep for sync
            final_members = get_group_members()
            if any(str(m.get("user_id")) == str(match_user_id) for m in final_members):
                logger.info(f"✅ Confirmed in group after poll success for {nick_to_add}")
                return True, 0
            else:
                logger.warning("User in poll but not in final group members; possible sync delay")
                return False, 200

        # Primary attempt
        success, status_code = attempt_add(safe_name, False)
        if success:
            send_system_message(f"> @{sender}: {full_text}\n✅ {nickname} re-added to the group.")
            banned_users.pop(match_user_id, None)
            save_json(banned_users_file, banned_users)
            user_swear_counts.pop(match_user_id, None)
            save_json(user_swear_counts_file, user_swear_counts)
            return

        # Always check group after primary (for omitted cases)
        time.sleep(10)  # Extra wait for potential omitted add
        members_after_primary = get_group_members()
        if any(str(m.get("user_id")) == str(match_user_id) for m in members_after_primary):
            logger.info(f"✅ User added despite omitted from poll results: {nickname}")
            send_system_message(f"> @{sender}: {full_text}\n✅ {nickname} re-added to the group.")
            banned_users.pop(match_user_id, None)
            save_json(banned_users_file, banned_users)
            user_swear_counts.pop(match_user_id, None)
            save_json(user_swear_counts_file, user_swear_counts)
            return

        # Retry with simplified nickname
        logger.warning(f"Primary unban failed (code {status_code}); retrying once.")
        retry_name = re.sub(r"[^A-Za-z0-9]", "", safe_name)[:20] or "Member"
        success, status_code = attempt_add(retry_name, True)

        if success:
            send_system_message(f"> @{sender}: {full_text}\n✅ {retry_name} re-added after retry.")
            banned_users.pop(match_user_id, None)
            save_json(banned_users_file, banned_users)
            user_swear_counts.pop(match_user_id, None)
            save_json(user_swear_counts_file, user_swear_counts)
            return

        # Always check group after retry
        time.sleep(10)
        members_after_retry = get_group_members()
        if any(str(m.get("user_id")) == str(match_user_id) for m in members_after_retry):
            logger.info(f"✅ User added on retry despite omitted from poll: {nickname}")
            send_system_message(f"> @{sender}: {full_text}\n✅ {nickname} re-added after retry.")
            banned_users.pop(match_user_id, None)
            save_json(banned_users_file, banned_users)
            user_swear_counts.pop(match_user_id, None)
            save_json(user_swear_counts_file, user_swear_counts)
            return

        # Final failure: Provide share URL
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
        logger.error(f"unban_user error: {e}")
        send_system_message(f"> @{sender}: {full_text}\n❌ Error while unbanning {target_alias}: {str(e)}")

# --- BAN LOGIC (unchanged except for persistence) ---
def ban_user(target_alias, sender_name, sender_id, original_text):
    if sender_id not in ADMIN_IDS:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Only admins can use this command")
        return False
    if not ACCESS_TOKEN or not GROUP_ID:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Missing ACCESS_TOKEN or GROUP_ID")
        logger.error("Missing ACCESS_TOKEN or GROUP_ID for ban")
        return False

    try:
        members = get_group_members()
        if not members:
            send_system_message(f"> @{sender_name}: {original_text}\nError: No members found")
            return False

        aliases = [member["nickname"] for member in members]
        best_match = process.extractOne(target_alias.lower(), [a.lower() for a in aliases], score_cutoff=80)
        if not best_match:
            send_system_message(f"> @{sender_name}: {original_text}\nNo user found matching '{target_alias}'")
            return False

        for member in members:
            if member["nickname"].lower() == best_match[0].lower():
                target_user_id = member["user_id"]
                target_username = member["nickname"]
                success = call_ban_service(target_user_id, target_username, "Admin ban command")
                if success:
                    send_system_message(f"> @{sender_name}: {original_text}\n🔨 {target_username} has been permanently banned by admin command.")
                else:
                    send_system_message(f"> @{sender_name}: {original_text}\nError: Failed to ban {target_username}")
                return success

        send_system_message(f"> @{sender_name}: {original_text}\nError: Could not retrieve user for banning")
        return False
    except Exception as e:
        logger.error(f"Ban command error: {e}")
        send_system_message(f"> @{sender_name}: {original_text}\nError: Failed to ban '{target_alias}'")
        return False

# --- VIOLATION CHECKS ---
def check_for_violations(text, user_id, username):
    text_lower = text.lower()
    text_words = text_lower.split()
    
    for word in text_words:
        clean_word = word.strip('.,!?"\'()[]{}').lower()
        if clean_word in INSTANT_BAN_WORDS:
            logger.info(f"🚨 INSTANT BAN: '{clean_word}' from {username}")
            success = call_ban_service(user_id, username, f"Instant ban: {clean_word}")
            if success:
                send_system_message(f"🔨 {username} has been permanently banned for using prohibited language.")
            return True
    
    for word in text_words:
        clean_word = word.strip('.,!?"\'()[]{}').lower()
        if clean_word in REGULAR_SWEAR_WORDS:
            if user_id not in user_swear_counts:
                user_swear_counts[user_id] = 0
            user_swear_counts[user_id] += 1
            save_json(user_swear_counts_file, user_swear_counts)
            current_count = user_swear_counts[user_id]
            logger.info(f"{username} swear count: {current_count}/10")
            
            if current_count >= 10:
                success = call_ban_service(user_id, username, f"10 strikes - swear words")
                if success:
                    send_system_message(f"🔨 {username} has been banned for repeated inappropriate language (10 strikes).")
                    user_swear_counts[user_id] = 0
                    save_json(user_swear_counts_file, user_swear_counts)
                return True
            else:
                remaining = 10 - current_count
                send_system_message(f"⚠️ {username} ({user_id}) - Warning {current_count}/10 for inappropriate language. {remaining} more and you're banned!")
            break
    return False

# --- MESSAGE SENDING ---
def send_system_message(text):
    global last_system_message_time
    if not BOT_ID:
        logger.error("No BOT_ID configured - can't send messages")
        return False
    
    is_strike_message = "Warning" in text or "banned" in text
    now = time.time()
    if not is_strike_message and now - last_system_message_time < cooldown_seconds:
        logger.info("System message cooldown active")
        return False

    url = "https://api.groupme.com/v3/bots/post"
    payload = {"bot_id": BOT_ID, "text": text}
    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
        if not is_strike_message:
            last_system_message_time = now
        logger.info(f"System message sent: {text[:30]}...")
        return True
    except Exception as e:
        logger.error(f"GroupMe system send error: {e}")
        return False

def send_message(text):
    global last_sent_time
    now = time.time()
    if now - last_sent_time < cooldown_seconds:
        logger.info("Regular cooldown active")
        return False
    if not BOT_ID:
        logger.error("No BOT_ID configured - can't send messages")
        return False
    url = "https://api.groupme.com/v3/bots/post"
    payload = {"bot_id": BOT_ID, "text": text}
    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
        last_sent_time = now
        logger.info(f"Regular message sent: {text[:30]}...")
        return True
    except Exception as e:
        logger.error(f"GroupMe send error: {e}")
        return False

def is_system_message(data):
    sender_type = data.get('sender_type')
    sender_name = data.get('name', '').lower()
    if sender_type == "system":
        return True
    system_senders = ['groupme', 'system', '']
    if sender_name in system_senders:
        return True
    return False

def is_real_system_event(text_lower):
    system_patterns = [
        'has joined the group',
        'has left the group',
        'was added to the group',
        'was removed from the group',
        'removed',
        'added'
    ]
    return any(pattern in text_lower for pattern in system_patterns)

# --- WEBHOOK ---
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data or 'text' not in data:
            return '', 200
        text = data['text']
        sender_type = data.get('sender_type')
        sender = data.get('name', 'Someone')
        user_id = data.get('user_id')
        text_lower = text.lower()
        attachments = data.get("attachments", [])
        
        # SYSTEM MESSAGE HANDLING
        if sender_type == "system" or is_system_message(data):
            if is_real_system_event(text_lower):
                if 'has left the group' in text_lower:
                    former_members[user_id or f"ghost-{sender}"] = sender
                    save_json(former_members_file, former_members)
                    send_system_message("GAY")
                elif 'has joined the group' in text_lower:
                    send_system_message("Welcome to Clean Memes, check the rules and announcement topics before chatting!")
                elif 'was removed from the group' in text_lower or 'removed' in text_lower:
                    former_members[user_id or f"ghost-{sender}"] = sender
                    save_json(former_members_file, former_members)
                    send_system_message("this could be you if you break the rules, watch it. 👀")
            return '', 200
        
        if sender_type not in ['user']:
            return '', 200
        
        # COMMANDS
        if text_lower.startswith('!unban '):
            target_alias = text_lower.replace('!unban ', '').strip()
            if target_alias:
                unban_user(target_alias, sender, user_id, text)
            return '', 200
        
        if text_lower.startswith('!ban '):
            target_alias = text_lower.replace('!ban ', '').strip()
            if target_alias:
                ban_user(target_alias, sender, user_id, text)
            return '', 200
        
        if text_lower.startswith('!userid ') or ('what is' in text_lower and 'user id' in text_lower):
            target_alias = text_lower.replace('!userid ', '').strip()
            if 'what is' in text_lower:
                target_alias = text_lower.split('user id')[0].replace('what is', '').strip()
            if target_alias:
                get_user_id(target_alias, sender, user_id, text)
            return '', 200
        
        # Ban checks
        if user_id and text:
            check_for_violations(text, user_id, sender)
        
        # Triggers
        if 'clean memes' in text_lower:
            send_message("We're the best!")
        elif 'wsg' in text_lower:
            send_message("God is good")
        elif 'cooper is my pookie' in text_lower:
            send_message("me too bro")
        elif 'https:' in text and not any(att.get("type") == "video" for att in attachments):
            send_message("Delete this, links are not allowed, admins have been notified")
        
        return '', 200
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return '', 500

# --- RESET COUNT ---
@app.route('/reset-count', methods=['POST'])
def reset_count():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        if not user_id:
            return {"error": "user_id required"}, 400
        old_count = user_swear_counts.get(user_id, 0)
        user_swear_counts[user_id] = 0
        save_json(user_swear_counts_file, user_swear_counts)
        return {"success": True, "old_count": old_count, "new_count": 0}
    except Exception as e:
        logger.error(f"Reset count error: {e}")
        return {"error": str(e)}, 500

# --- HEALTH ---
@app.route('/health', methods=['GET'])
def health():
    return {
        "status": "healthy" if BOT_ID else "missing config",
        "bot_id": BOT_ID[:8] + "..." if BOT_ID else "MISSING",
        "bot_name": BOT_NAME,
        "ban_system": {
            "enabled": bool(GROUP_ID and BAN_SERVICE_URL),
            "group_id": GROUP_ID or "MISSING",
            "ban_service_url": BAN_SERVICE_URL or "MISSING",
            "instant_ban_words": len(INSTANT_BAN_WORDS),
            "regular_swear_words": len(REGULAR_SWEAR_WORDS),
            "tracked_users": len(user_swear_counts),
            "banned_users": len(banned_users),
            "former_members": len(former_members)
        },
        "system_triggers": {
            "left": "GAY",
            "joined": "Welcome message",
            "removed": "Rules warning"
        }
    }

# --- START BOT ---
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    logger.info("🚀 Starting ClankerBot (no AI)")
    logger.info(f"Bot ID: {'SET' if BOT_ID else 'MISSING'}")
    logger.info(f"Bot Name: {BOT_NAME}")
    logger.info(f"Ban System: {'ENABLED' if GROUP_ID and BAN_SERVICE_URL else 'DISABLED'}")
    app.run(host="0.0.0.0", port=port, debug=False)
