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
    '125629030'
]

# Swear word categories
INSTANT_BAN_WORDS = ['nigger', 'nigga', 'n1gger', 'n1gga', 'nigg', 'n1gg']
REGULAR_SWEAR_WORDS = [
    'fuck','fucking','fucked','fucker',
    'shit','shitting','shitty',
    'bitch','bitches',
    'ass','asshole','asshat',
    'cunt','dick','dickhead',
    'damn','bastard','slut','whore',
    'retard','wtf',
    'nevergonnagiveyouupnevergonnaletyoudown',
    '67'
]

# Track user swear counts and banned users (in memory)
user_swear_counts = {}
banned_users = {}     # {user_id: username}
former_members = {}   # {user_id: username}

# Member cache
member_cache = {}     # nickname.lower() -> user_id
id_to_name = {}       # user_id -> nickname

# Cooldowns
last_sent_time = 0
last_system_message_time = 0
cooldown_seconds = 10

# -----------------------
# Member cache utilities
# -----------------------

def refresh_member_cache():
    if not ACCESS_TOKEN or not GROUP_ID:
        return
    try:
        r = requests.get(
            f"https://api.groupme.com/v3/groups/{GROUP_ID}",
            headers={"X-Access-Token": ACCESS_TOKEN}, timeout=5
        )
        r.raise_for_status()
        members = r.json().get("response", {}).get("members", [])
        for m in members:
            nick, uid = m.get("nickname"), m.get("user_id")
            if nick and uid:
                member_cache[nick.lower()] = uid
                id_to_name[uid] = nick
    except Exception as e:
        logger.debug(f"refresh_member_cache failed: {e}")

def remember_member(nickname, user_id):
    if nickname and user_id:
        member_cache[nickname.lower()] = user_id
        id_to_name[user_id] = nickname

# -----------------------
# Ban + Unban helpers
# -----------------------

def call_ban_service(user_id, username):
    if not BAN_SERVICE_URL:
        logger.warning("No BAN_SERVICE_URL configured - can't ban users")
        return False
    try:
        response = requests.post(f"{BAN_SERVICE_URL}/ban", json={
            'user_id': user_id, 'username': username
        }, timeout=5)
        if response.status_code == 200:
            banned_users[user_id] = username
            former_members[user_id] = username
            logger.info(f"✅ Banned {username} ({user_id})")
            return True
        logger.error(f"❌ Ban failed {response.status_code}: {response.text}")
        return False
    except Exception as e:
        logger.error(f"❌ Ban service error: {e}")
        return False

def unban_user(target_alias, sender_name, sender_id, original_text):
    if sender_id not in ADMIN_IDS:
        send_system_message(f"> @{sender_name}: {original_text}\nError: Only admins can use this command")
        return False

    # Build candidate pool
    candidates = {}
    for uid, uname in {**banned_users, **former_members}.items():
        if uname:
            candidates[uname.lower()] = (uid, uname)
        candidates[str(uid)] = (uid, uname)

    if not candidates:
        send_system_message(f"> @{sender_name}: {original_text}\nError: No known banned/removed users to unban")
        return False

    lookup_key = target_alias.strip()
    key_lower = lookup_key.lower()

    if key_lower in candidates:
        target_user_id, target_username = candidates[key_lower]
    elif lookup_key in candidates:
        target_user_id, target_username = candidates[lookup_key]
    else:
        nickname_keys = [k for k in candidates.keys() if not k.isdigit()]
        if not nickname_keys:
            send_system_message(f"> @{sender_name}: {original_text}\nError: No nickname records to match")
            return False
        best = process.extractOne(key_lower, nickname_keys)
        if not best or best[1] < 70:
            send_system_message(f"> @{sender_name}: {original_text}\nError: No match for '{target_alias}'")
            return False
        matched_nick = best[0]
        target_user_id, target_username = candidates[matched_nick]

    # Re-add via GroupMe API
    try:
        url = f"https://api.groupme.com/v3/groups/{GROUP_ID}/members/add"
        payload = {"members": [{"user_id": target_user_id, "nickname": target_username}]}
        headers = {"X-Access-Token": ACCESS_TOKEN}
        r = requests.post(url, json=payload, headers=headers, timeout=5)
        r.raise_for_status()
        send_system_message(f"> @{sender_name}: {original_text}\n✅ {target_username} has been unbanned and re-added")
        banned_users.pop(target_user_id, None)
        former_members.pop(target_user_id, None)
        logger.info(f"✅ Unbanned {target_username} ({target_user_id})")
        return True
    except Exception as e:
        logger.error(f"❌ Unban error: {e}")
        send_system_message(f"> @{sender_name}: {original_text}\nError: Failed to unban '{target_alias}'")
        return False

# -----------------------
# Moderation / messaging
# -----------------------

def check_for_violations(text, user_id, username):
    text_lower = text.lower()
    words = [w.strip('.,!?"\'()[]{}').lower() for w in text_lower.split()]

    for w in words:
        if w in INSTANT_BAN_WORDS:
            logger.info(f"🚨 INSTANT BAN: {w} from {username}")
            if call_ban_service(user_id, username):
                send_system_message(f"🔨 {username} has been permanently banned.")
            return True

    for w in words:
        if w in REGULAR_SWEAR_WORDS:
            user_swear_counts[user_id] = user_swear_counts.get(user_id, 0) + 1
            current = user_swear_counts[user_id]
            if current >= 10:
                if call_ban_service(user_id, username):
                    send_system_message(f"🔨 {username} has been banned for repeated language (10 strikes).")
                    user_swear_counts[user_id] = 0
                return True
            else:
                send_system_message(f"⚠️ {username} - Warning {current}/10. {10-current} more and you're banned!")
            break
    return False

def send_system_message(text):
    global last_system_message_time
    if not BOT_ID:
        return False
    now = time.time()
    if now - last_system_message_time < cooldown_seconds:
        return False
    url = "https://api.groupme.com/v3/bots/post"
    payload = {"bot_id": BOT_ID, "text": text}
    try:
        requests.post(url, json=payload, timeout=5).raise_for_status()
        last_system_message_time = now
        return True
    except Exception:
        return False

def send_message(text):
    global last_sent_time
    now = time.time()
    if now - last_sent_time < cooldown_seconds:
        return False
    if not BOT_ID:
        return False
    url = "https://api.groupme.com/v3/bots/post"
    payload = {"bot_id": BOT_ID, "text": text}
    try:
        requests.post(url, json=payload, timeout=5).raise_for_status()
        last_sent_time = now
        return True
    except Exception:
        return False

# -----------------------
# Webhook
# -----------------------

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data or 'text' not in data:
            return '', 200
        text, sender_type = data['text'], data.get('sender_type')
        sender, user_id = data.get('name','Someone'), data.get('user_id')
        text_lower = text.lower()
        attachments = data.get("attachments", [])

        if sender_type == 'user' and sender and user_id:
            remember_member(sender, user_id)

        # Commands
        if text_lower.startswith('!ban '):
            if user_id not in ADMIN_IDS:
                send_system_message(f"> @{sender}: {text}\nError: Only admins can use !ban")
                return '', 200
            target_alias = text.split(maxsplit=1)[1].strip()
            refresh_member_cache()
            if not member_cache:
                send_system_message(f"> @{sender}: {text}\nError: No members found")
                return '', 200
            aliases = list(member_cache.keys())
            best = process.extractOne(target_alias.lower(), aliases, score_cutoff=80)
            if not best:
                send_system_message(f"> @{sender}: {text}\nNo user found matching '{target_alias}'")
                return '', 200
            matched_nick = best[0]
            target_user_id = member_cache.get(matched_nick)
            target_username = id_to_name.get(target_user_id, matched_nick)
            if target_user_id and call_ban_service(target_user_id, target_username):
                send_system_message(f"🔨 {target_username} has been banned.")
            return '', 200

        if text_lower.startswith('!unban '):
            target_alias = text_lower.replace('!unban ', '').strip()
            if target_alias:
                unban_user(target_alias, sender, user_id, text)
            return '', 200

        if text_lower.startswith('!userid '):
            target_alias = text_lower.replace('!userid ', '').strip()
            if target_alias:
                refresh_member_cache()
                if member_cache:
                    best = process.extractOne(target_alias.lower(), list(member_cache.keys()), score_cutoff=80)
                    if best:
                        uid = member_cache[best[0]]
                        uname = id_to_name.get(uid, best[0])
                        send_system_message(f"> @{sender}: {text}\n{uname}'s user_id is {uid}")
            return '', 200

        # Moderation
        if user_id and text:
            check_for_violations(text, user_id, sender)

        # Fun triggers
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

# -----------------------
# Health + reset
# -----------------------

@app.route('/reset-count', methods=['POST'])
def reset_count():
    try:
        user_id = request.get_json().get('user_id')
        if not user_id:
            return {"error":"user_id required"}, 400
        old_count = user_swear_counts.get(user_id,0)
        user_swear_counts[user_id] = 0
        return {"success":True,"old_count":old_count,"new_count":0}
    except Exception as e:
        return {"error":str(e)},500

@app.route('/health', methods=['GET'])
def health():
    return {"status":"healthy" if BOT_ID else "missing config"}

# -----------------------
# Main
# -----------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    logger.info("🚀 Starting ClankerBot with ban/unban")
    app.run(host="0.0.0.0", port=port, debug=False)
