from flask import Flask, request
import requests
import os
import time
import logging
import threading

app = Flask(__name__)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
BOT_ID = os.getenv("BOT_ID")
BOT_NAME = os.getenv("BOT_NAME", "ClankerBot")

# Ban system config
GROUP_ID = os.getenv("GROUP_ID")
BAN_SERVICE_URL = os.getenv("BAN_SERVICE_URL")

# Swear word categories
INSTANT_BAN_WORDS = [
    'nigger', 'nigga', 'n1gger', 'n1gga', 'nigg', 'n1gg', 'niger', 'niga'
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
    'loraxmybabe'
]

# Track user swear counts (in memory)
user_swear_counts = {}

# Cooldowns
last_sent_time = 0
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
            return True
        else:
            logger.error(f"❌ Ban failed {response.status_code}: {response.text}")
            return False
    except Exception as e:
        logger.error(f"❌ Ban service error: {e}")
        return False

def check_for_violations(text, user_id, username):
    text_lower = text.lower()
    text_words = text_lower.split()
    
    # Instant ban
    for word in text_words:
        clean_word = word.strip('.,!?"\'()[]{}').lower()
        for ban_word in INSTANT_BAN_WORDS:
            if ban_word in clean_word:
                logger.info(f"🚨 INSTANT BAN: '{clean_word}' from {username}")
                success = call_ban_service(user_id, username, f"Instant ban: {clean_word}")
                if success:
                    send_system_message(f"🔨 {username} has been permanently banned for using prohibited language.")
                return True
    
    # Regular swears
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
                send_system_message(f"⚠️ {username} - Warning {current_count}/10 for inappropriate language. {remaining} more and you're banned!")
            break
    return False

def send_system_message(text):
    if not BOT_ID:
        logger.error("No BOT_ID configured - can't send messages")
        return False
    url = "https://api.groupme.com/v3/bots/post"
    payload = {"bot_id": BOT_ID, "text": text}
    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
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

def keep_alive():
    """Send periodic pings to keep bot and ban service awake."""
    bot_health_url = "http://localhost:5000/health"  # Default for local; override with environment variable
    if os.getenv("BOT_HEALTH_URL"):
        bot_health_url = os.getenv("BOT_HEALTH_URL")
    while True:
        try:
            # Ping ban service
            if BAN_SERVICE_URL:
                response = requests.get(f"{BAN_SERVICE_URL}/health", timeout=3)
                logger.info(f"Keep-alive ping to ban service: {response.status_code}")
            else:
                logger.warning("No BAN_SERVICE_URL for keep-alive ping")
        except Exception as e:
            logger.error(f"Keep-alive ping to ban service failed: {e}")
        
        try:
            # Ping bot itself
            response = requests.get(bot_health_url, timeout=3)
            logger.info(f"Keep-alive ping to bot: {response.status_code}")
        except Exception as e:
            logger.error(f"Keep-alive ping to bot failed: {e}")
        
        time.sleep(60)  # Wait 60 seconds before next ping

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
                    send_system_message("GAY")
                elif 'has joined the group' in text_lower:
                    send_system_message("Welcome to Clean Memes, check the rules and announcement topics before chatting!")
                elif 'was removed from the group' in text_lower or 'removed' in text_lower:
                    send_system_message("this could be you if you break the rules, watch it. 👀")
            return '', 200
        
        # Skip non-user messages
        if sender_type not in ['user']:
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
            "tracked_users": len(user_swear_counts)
        },
        "system_triggers": {
            "left": "GAY",
            "joined": "Welcome message",
            "removed": "Rules warning"
        }
    }

# Start keep-alive thread
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    logger.info("🚀 Starting ClankerBot (no AI)")
    logger.info(f"Bot ID: {'SET' if BOT_ID else 'MISSING'}")
    logger.info(f"Bot Name: {BOT_NAME}")
    logger.info(f"Ban System: {'ENABLED' if GROUP_ID and BAN_SERVICE_URL else 'DISABLED'}")
    # Start keep-alive thread
    keep_alive_thread = threading.Thread(target=keep_alive, daemon=True)
    keep_alive_thread.start()
    logger.info("Started keep-alive thread for bot and ban service")
    app.run(host="0.0.0.0", port=port, debug=False)
