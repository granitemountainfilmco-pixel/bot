from flask import Flask, request
import requests
import os
import time
import logging
from fuzzywuzzy import process

app = Flask(__name__)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
BOT_ID = os.getenv("BOT_ID")
BOT_NAME = os.getenv("BOT_NAME", "ClankerBot")
GROUP_ID = os.getenv("GROUP_ID")
BAN_SERVICE_URL = os.getenv("BAN_SERVICE_URL")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")  # GroupMe API token for member list
ADMIN_IDS = [
    '119189324', '82717917', '124068433', '103258964', '123259855',
    '114848297', '121920211', '134245360', '113819798', '130463543',
    '123142410', '131010920', '133136781', '124453541', '122782552',
    '117776217', '85166615', '114066399', '84254355', '115866991',
    '125629030', '124579254'
]

# Swear word categories
INSTANT_BAN_WORDS = [
    'nigger', 'nigga', 'n1gger', 'n1gga', 'nigg', 'n1gg', 'nigha'
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
    '67', '6-7', '6 7'
]

# Track user swear counts and banned users (in memory)
user_swear_counts = {}
banned_users = {}     # {user_id: username}
former_members = {}   # {user_id: username} - for anyone removed/left

# Cooldowns
last_sent_time = 0
last_system_message_time = 0
cooldown_seconds = 10

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
        response = requests.get(
            f"https://api.groupme.com/v3/groups/{GROUP_ID}",
            headers={"X-Access-Token": ACCESS_TOKEN},
            timeout=5
        )
        response.raise_for_status()
        members = response.json().get("response", {}).get("members", [])
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

def unban_user(target_alias, sender_name, sender_id, original_text):
    if sender_id not in ADMIN_IDS:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Only admins can use this command")
        logger.warning(f"Non-admin {sender_name} ({sender_id}) attempted unban")
        return False
    if not ACCESS_TOKEN or not GROUP_ID:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Missing ACCESS_TOKEN or GROUP_ID")
        logger.error("Missing ACCESS_TOKEN or GROUP_ID for unban")
        return False

    # Collect candidates from both banned_users and former_members
    candidates = {uname.lower(): (uid, uname) for uid, uname in {**banned_users, **former_members}.items()}
    if not candidates:
        send_system_message(f"> @{sender_name}: {original_text}\nError: No known banned or removed users to unban")
        logger.info("No candidates available for unban")
        return False

    # Fuzzy match with a lower score cutoff to allow more flexibility
    best_match = process.extractOne(target_alias.lower(), list(candidates.keys()), score_cutoff=60)
    if not best_match:
        send_system_message(f"> @{sender_name}: {original_text}\nError: No banned/removed user found matching '{target_alias}' (closest: {process.extractOne(target_alias.lower(), list(candidates.keys()))[0] if candidates else 'none'})")
        logger.info(f"No match for '{target_alias}' in candidates")
        return False

    target_user_id, target_username = candidates[best_match[0]]
    logger.info(f"Attempting to unban {target_username} ({target_user_id})")

    # Check if user is already in the group
    try:
        response = requests.get(
            f"https://api.groupme.com/v3/groups/{GROUP_ID}",
            headers={"X-Access-Token": ACCESS_TOKEN},
            timeout=5
        )
        response.raise_for_status()
        members = response.json().get("response", {}).get("members", [])
        if any(member["user_id"] == target_user_id for member in members):
            send_system_message(f"> @{sender_name}: {original_text}\nError: {target_username} is already in the group")
            logger.info(f"Unban failed: {target_username} ({target_user_id}) already in group")
            return False
    except Exception as e:
        logger.error(f"Error checking group members: {e}")
        send_system_message(f"> @{sender_name}: {original_text}\nError: Failed to verify group membership for {target_username}")
        return False

    # Attempt to re-add user
    url = f"https://api.groupme.com/v3/groups/{GROUP_ID}/members/add"
    payload = {"members": [{"user_id": target_user_id, "nickname": target_username}]}
    headers = {"X-Access-Token": ACCESS_TOKEN}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=5)
        if response.status_code == 202:
            # For /members/add, 202 indicates the request was accepted
            logger.info(f"Unban request accepted for {target_username} ({target_user_id})")
            send_system_message(f"> @{sender_name}: {original_text}\n✅ {target_username} has been unbanned and re-added to the group")
            banned_users.pop(target_user_id, None)
            former_members.pop(target_user_id, None)
            return True
        elif response.status_code == 400:
            error_msg = response.json().get("response", {}).get("errors", ["Unknown error"])[0]
            send_system_message(f"> @{sender_name}: {original_text}\nError: Failed to unban {target_username} - {error_msg}")
            logger.error(f"Unban failed: {error_msg}")
            return False
        else:
            send_system_message(f"> @{sender_name}: {original_text}\nError: Failed to unban {target_username} (status {response.status_code})")
            logger.error(f"Unban failed with status {response.status_code}: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Unban error for {target_username} ({target_user_id}): {e}")
        send_system_message(f"> @{sender_name}: {original_text}\nError: Failed to unban {target_username} - {str(e)}")
        return False

def ban_user(target_alias, sender_name, sender_id, original_text):
    if sender_id not in ADMIN_IDS:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Only admins can use this command")
        return False
    if not ACCESS_TOKEN or not GROUP_ID:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Missing ACCESS_TOKEN or GROUP_ID")
        logger.error("Missing ACCESS_TOKEN or GROUP_ID for ban")
        return False

    try:
        response = requests.get(
            f"https://api.groupme.com/v3/groups/{GROUP_ID}",
            headers={"X-Access-Token": ACCESS_TOKEN},
            timeout=5
        )
        response.raise_for_status()
        members = response.json().get("response", {}).get("members", [])
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
            current_count = user_swear_counts[user_id]
            logger.info(f"{username} swear count: {current_count}/10")
            
            if current_count >= 10:
                success = call_ban_service(user_id, username, f"10 strikes - swear words")
                if success:
                    send_system_message(f"🔨 {username} has been banned for repeated inappropriate language (10 strikes).")
                    user_swear_counts[user_id] = 0
                return True
            else:
                remaining = 10 - current_count
                send_system_message(f"⚠️ {username} ({user_id}) - Warning {current_count}/10 for inappropriate language. {remaining} more and you're banned!")
            break
    return False

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
                    send_system_message("GAY")
                elif 'has joined the group' in text_lower:
                    send_system_message("Welcome to Clean Memes, check the rules and announcement topics before chatting!")
                elif 'was removed from the group' in text_lower or 'removed' in text_lower:
                    former_members[user_id or f"ghost-{sender}"] = sender
                    send_system_message("this could be you if you break the rules, watch it. 👀")
            return '', 200
        
        # Skip non-user messages
        if sender_type not in ['user']:
            return '', 200
        
        # Unban command
        if text_lower.startswith('!unban '):
            target_alias = text_lower.replace('!unban ', '').strip()
            if target_alias:
                unban_user(target_alias, sender, user_id, text)
            return '', 200
        
        # Ban command
        if text_lower.startswith('!ban '):
            target_alias = text_lower.replace('!ban ', '').strip()
            if target_alias:
                ban_user(target_alias, sender, user_id, text)
            return '', 200
        
        # User ID query
        if text_lower.startswith('!userid ') or 'what is' in text_lower and 'user id' in text_lower:
            target_alias = text_lower.replace('!userid ', '').strip()
            if 'what is' in text_lower:
                target_alias = text_lower.split('user id')[0].replace('what is', '').strip()
            if target_alias:
                get_user_id(target_alias, sender, user_id, text)
            return '', 200
        
        # Ban checks
        if user_id and text:
            check_for_violations(text, user_id, sender)
        
        # User triggers
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

@app.route('/reset-count', methods=['POST'])
def reset_count():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        if not user_id:
            return {"error": "user_id required"}, 400
        old_count = user_swear_counts.get(user_id, 0)
        user_swear_counts[user_id] = 0
        return {"success": True, "old_count": old_count, "new_count": 0}
    except Exception as e:
        logger.error(f"Reset count error: {e}")
        return {"error": str(e)}, 500

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

# Start bot
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    logger.info("🚀 Starting ClankerBot (no AI)")
    logger.info(f"Bot ID: {'SET' if BOT_ID else 'MISSING'}")
    logger.info(f"Bot Name: {BOT_NAME}")
    logger.info(f"Ban System: {'ENABLED' if GROUP_ID and BAN_SERVICE_URL else 'DISABLED'}")
    app.run(host="0.0.0.0", port=port, debug=False)
