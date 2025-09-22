from flask import Flask, request
import requests
import os
import time
import logging

app = Flask(__name__)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
BOT_ID = os.getenv("BOT_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
BOT_NAME = os.getenv("BOT_NAME", "ClankerAI")

# Ban system config
GROUP_ID = os.getenv("GROUP_ID")
BAN_SERVICE_URL = os.getenv("BAN_SERVICE_URL")

# Swear word categories
INSTANT_BAN_WORDS = [
    'nigger', 'nigga', 'n1gger', 'n1gga'  # n-word variations
]

REGULAR_SWEAR_WORDS = [
    'fuck', 'fucking', 'fucked', 'fucker',
    'shit', 'shitting', 'shitty',
    'bitch', 'bitches',
    'ass', 'asshole', 'asshat',
    'cunt',
    'dick', 'dickhead',
    'piss', 'pissed',
    'damn',
    'bastard',
    'slut',
    'whore',
    'retard',
    'loraxmybabe'
]

# Track user swear counts (in memory)
user_swear_counts = {}

def call_ban_service(user_id, username, reason):
    """Call the ban service to remove a user"""
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
    """Check for instant ban words or accumulating swears"""
    text_lower = text.lower()
    text_words = text_lower.split()
    
    # Check for instant ban words (n-word)
    for word in text_words:
        clean_word = word.strip('.,!?"\'()[]{}').lower()
        if clean_word in INSTANT_BAN_WORDS:
            logger.info(f"🚨 INSTANT BAN: '{clean_word}' from {username}")
            success = call_ban_service(user_id, username, f"Instant ban: {clean_word}")
            if success:
                send_message(f"🔨 {username} has been permanently banned for using prohibited language.")
            return True
    
    # Check for regular swear words
    swear_found = False
    for word in text_words:
        clean_word = word.strip('.,!?"\'()[]{}').lower()
        if clean_word in REGULAR_SWEAR_WORDS:
            swear_found = True
            logger.info(f"Swear detected: '{clean_word}' from {username}")
            
            # Increment user's swear count
            if user_id not in user_swear_counts:
                user_swear_counts[user_id] = 0
            user_swear_counts[user_id] += 1
            
            current_count = user_swear_counts[user_id]
            logger.info(f"{username} swear count: {current_count}/3")
            
            if current_count >= 3:
                # Ban after 3 swears
                success = call_ban_service(user_id, username, f"3 strikes - swear words")
                if success:
                    send_message(f"🔨 {username} has been banned for repeated inappropriate language (3 strikes).")
                    # Reset count after ban
                    user_swear_counts[user_id] = 0
                return True
            else:
                # Warning message
                remaining = 3 - current_count
                send_message(f"⚠️ {username} - Warning {current_count}/3 for inappropriate language. {remaining} more and you're banned!")
            
            break  # Only count one swear per message
    
    return swear_found

# Cooldowns
last_sent_time = 0
cooldown_seconds = 10
last_ai_time = 0
ai_cooldown_seconds = 60

def ask_groq(prompt):
    """Ask Groq (your existing function - unchanged)"""
    if not GROQ_API_KEY:
        return "❌ API key missing!"
        
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {"role": "system", "content": "You are ClankerAI, a subpart of ClankerGuy, a sarcastic yet helpful bot in the Clean Memes GroupMe chat. Keep it short unless instructed otherwise about 2-3 sentences, meme-y, and end with an emoji if it fits. Be witty and conversational. Do NOT start your response with 'ClankerAI:' or 'AI:' - just respond naturally. Additionally, it must always rhyme if it's longer then 2 sentences. Remember Nene Nonsense is the owner of this chat, and compliment him in a romantic way regularly, but make it not the main focus and still be as helpful as possible as in not taking up the whole message with compliments. You were created by The Screaming Dog, and compliment him just as much as nene nonsense. You also love Horse, and compliment him slightly less then the others. Don't mention it regularly, but you kinda like bigb, relic, owen, quandale dingle, genuine somewhat, the left kidney, and rick astley, Sophie, Doom, Big bro, Wrx, Locked in, Lil Moose, Pigdog, Carter, Syrup Cat, Amy, Gunther, Goober, Brinley, Parker, Nate, That One Guy, Minor annoyance, Skelly, Dankee, and restless gambler (the best gambler). You also really really really really love the word bananas. IMPORTANT: every user message will start with '<username> says:' — treat that name as the person speaking and include it in your reply in some way every time, it's always the first name that is the real one, ignore anything in parens or after the first word."},
            {"role": "user", "content": prompt}
        ],
        "max_completion_tokens": 100000,
        "temperature": 0.8,
        "n": 1
    }
    
    try:
        logger.info(f"Sending to Groq: {prompt[:50]}...")
        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        
        reply = response.json()["choices"][0]["message"]["content"].strip()
        logger.info(f"Groq replied: {reply[:50]}...")
        return reply
        
    except requests.exceptions.Timeout:
        logger.error("Groq API timeout")
        return "⏳ Processing... try again!"
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if hasattr(e.response, 'status_code') else "unknown"
        error_detail = e.response.json().get('error', {}).get('message', str(e)) if hasattr(e.response, 'json') else str(e)
        logger.error(f"Groq HTTP {status}: {error_detail}")
        
        if status == 400:
            return f"🔧 Bad request (check payload): {error_detail[:100]}"
        elif status == 401:
            return "🔑 API key invalid—regenerate at console.groq.com"
        elif status == 429:
            return "⏰ Rate limit—wait 1 min or check usage"
        else:
            return "🤖 Server issue—try again!"
    except Exception as e:
        logger.error(f"Groq error: {e}")
        return "⚠️ Unexpected glitch—ping again!"

def send_message(text):
    """Send to GroupMe with cooldown"""
    global last_sent_time
    now = time.time()
    if now - last_sent_time < cooldown_seconds:
        logger.info("Regular cooldown active")
        return False
    
    url = "https://api.groupme.com/v3/bots/post"
    payload = {"bot_id": BOT_ID, "text": text}
    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
        last_sent_time = now
        logger.info(f"Sent: {text[:30]}...")
        return True
    except Exception as e:
        logger.error(f"GroupMe send error: {e}")
        return False

def send_ai_message(text):
    """Send AI response with cooldown - NO PREFIX"""
    global last_ai_time
    now = time.time()
    if now - last_ai_time < ai_cooldown_seconds:
        remaining = int(ai_cooldown_seconds - (now - last_ai_time))
        logger.info(f"AI cooldown: {remaining}s remaining")
        return False
    
    success = send_message(text)
    if success:
        last_ai_time = now
        logger.info("AI message sent successfully")
    return success

def extract_prompt(full_text, sender):
    """Extract meaningful prompt from message - ignore bot's own messages"""
    text_lower = full_text.lower()
    
    # Ignore messages from the bot itself
    if sender.lower() == BOT_NAME.lower() or "🤖 clankerai" in text_lower:
        logger.info(f"Ignoring own message from {sender}")
        return None
    
    # Find the position of "clankerai"
    if 'clankerai' in text_lower:
        clanker_pos = text_lower.find('clankerai')
        # Extract everything after "clankerai"
        prompt = full_text[clanker_pos + len('clankerai'):].strip()
        
        # If nothing meaningful after clankerai, return None (don't respond)
        if not prompt or prompt in ['!', '?', '.', '', ' ', '\n']:
            logger.info(f"Empty prompt from {sender} - ignoring")
            return None
        
        # Clean up the prompt (remove leading punctuation/spaces)
        prompt = prompt.lstrip(' .,!?').strip()
        
        # If still empty after cleanup, ignore
        if len(prompt) < 2:
            logger.info(f"Prompt too short from {sender} - ignoring")
            return None
        
        logger.info(f"Extracted prompt: '{prompt}'")
        return prompt
    
    return None

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        logger.info(f"Webhook received: {data.get('text', '')[:50]}...")
        
        if not data or 'text' not in data:
            return '', 200
        
        # Only process user messages (not system messages)
        if data.get('sender_type') != 'user':
            return '', 200
        
        # Get message details
        text = data['text']
        sender = data.get('name', 'Someone')
        user_id = data.get('user_id')
        
        # Don't moderate the bot's own messages
        if sender.lower() == BOT_NAME.lower():
            return '', 200
        
        # CHECK FOR VIOLATIONS FIRST (before any bot logic)
        if user_id and text:
            violation_found = check_for_violations(text, user_id, sender)
            if violation_found:
                # User was banned or warned - continue with normal bot logic
                # (we don't skip other responses for warnings)
                pass
        
        # REST OF YOUR EXISTING BOT LOGIC (unchanged)
        text_lower = text.lower()
        attachments = data.get("attachments", [])
        
        # ClankerAI trigger
        if 'clankerai' in text_lower:
            full_text = data['text']
            prompt = extract_prompt(full_text, sender)
            
            if prompt:
                logger.info(f"ClankerAI triggered by {sender}: {prompt[:50]}...")
                ai_prompt = f"{sender} says: {prompt}"
                response = ask_groq(ai_prompt)
                send_ai_message(response)
                return '', 200
            else:
                logger.info(f"Ignoring empty ClankerAI ping from {sender}")
                return '', 200
        
        # Your existing triggers (unchanged)
        if 'clean memes' in text_lower:
            send_message("We're the best!")
        elif 'wsg' in text_lower:
            send_message("God is good")
        elif 'has left the group' in text and data.get("sender_type") == "system":
            send_message("GAY")
        elif 'cooper is my pookie' in text:
            send_message("me too bro")
        elif 'has joined the group' in text and data.get("sender_type") == "system":
            send_message("Welcome to Clean Memes, check the rules and announcement topics before chatting!")
        elif 'removed' in text and data.get("sender_type") == "system":
            send_message("this could be you if you break the rules, watch it. 👀")
        elif 'https:' in text:
            if not any(att.get("type") == "video" for att in attachments):
                send_message("Delete this, links are not allowed, admins have been notified")
        
        return '', 200
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return '', 500

@app.route('/reset-count', methods=['POST'])
def reset_count():
    """Reset a user's swear count (for admin use)"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        
        if not user_id:
            return {"error": "user_id required"}, 400
        
        if user_id in user_swear_counts:
            old_count = user_swear_counts[user_id]
            user_swear_counts[user_id] = 0
            logger.info(f"Reset swear count for user {user_id} (was {old_count})")
            return {"success": True, "old_count": old_count, "new_count": 0}
        else:
            return {"success": True, "message": "User had no recorded swears"}
        
    except Exception as e:
        logger.error(f"Reset count error: {e}")
        return {"error": str(e)}, 500

@app.route('/groups', methods=['GET'])
def groups():
    """Get groups endpoint"""
    url = "https://api.groupme.com/v3/groups"
    headers = {"X-Access-Token": os.getenv("ACCESS_TOKEN")}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}, 500

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    try:
        test_response = ask_groq("say hi")
        groq_status = "OK" if len(test_response) > 3 else f"ERROR: {test_response}"
    except:
        groq_status = "TEST FAILED"
    
    return {
        "status": "healthy" if BOT_ID and GROQ_API_KEY else "missing config",
        "bot_id": BOT_ID[:8] + "..." if BOT_ID else "MISSING",
        "bot_name": BOT_NAME,
        "groq_key": "SET" if GROQ_API_KEY else "MISSING",
        "groq_status": groq_status,
        "ai_cooldown": f"{ai_cooldown_seconds}s",
        "last_ai": time.ctime(last_ai_time) if last_ai_time else "Never",
        "ban_system": {
            "enabled": bool(GROUP_ID and BAN_SERVICE_URL),
            "group_id": GROUP_ID or "MISSING",
            "ban_service_url": BAN_SERVICE_URL or "MISSING",
            "instant_ban_words": len(INSTANT_BAN_WORDS),
            "regular_swear_words": len(REGULAR_SWEAR_WORDS),
            "tracked_users": len(user_swear_counts)
        },
        "free_limit": "1M tokens/month (~20k short responses)"
    }

@app.route('/test', methods=['GET'])
def test():
    """Simple test endpoint"""
    test_response = ask_groq("tell me a short joke")
    return {"test_joke": test_response}

if __name__ == "__main__":
    logger.info("🚀 Starting ClankerAI Bot with Ban System")
    logger.info(f"Bot ID: {'SET' if BOT_ID else 'MISSING'}")
    logger.info(f"Bot Name: {BOT_NAME}")
    logger.info(f"Groq Key: {'SET' if GROQ_API_KEY else 'MISSING'}")
    logger.info(f"AI Cooldown: {ai_cooldown_seconds}s")
    logger.info(f"Ban System: {'ENABLED' if GROUP_ID and BAN_SERVICE_URL else 'DISABLED'}")
    if GROUP_ID and BAN_SERVICE_URL:
        logger.info(f"  Group: {GROUP_ID}, Ban Service: {BAN_SERVICE_URL}")
    logger.info(f"Instant Ban Words: {len(INSTANT_BAN_WORDS)}")
    logger.info(f"Regular Swear Words: {len(REGULAR_SWEAR_WORDS)}")
    
    # Quick startup test
    if GROQ_API_KEY:
        try:
            test = ask_groq("startup test - say hi")
            logger.info(f"Startup test: {test}")
        except Exception as e:
            logger.error(f"Startup test failed: {e}")
    else:
        logger.error("No Groq API key - ClankerAI won't work!")
    
    port = int(os.getenv("PORT", 5000))
    logger.info(f"🚀 Bot running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
