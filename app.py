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
                send_system_message(f"🔨 {username} has been permanently banned for using prohibited language.")
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
            logger.info(f"{username} swear count: {current_count}/10")
            
            if current_count >= 10:
                # Ban after 10 swears
                success = call_ban_service(user_id, username, f"10 strikes - swear words")
                if success:
                    send_system_message(f"🔨 {username} has been banned for repeated inappropriate language (10 strikes).")
                    # Reset count after ban
                    user_swear_counts[user_id] = 0
                return True
            else:
                # Warning message
                remaining = 10 - current_count
                send_system_message(f"⚠️ {username} - Warning {current_count}/10 for inappropriate language. {remaining} more and you're banned!")
            
            break  # Only count one swear per message
    
    return swear_found

# Cooldowns
last_sent_time = 0
cooldown_seconds = 10
last_ai_time = 0
ai_cooldown_seconds = 60

def send_system_message(text):
    """Send system messages without cooldown (main chat only)"""
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
    """Send to GroupMe with cooldown (main chat only)"""
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

def send_ai_message(text):
    """Send AI response with cooldown (in subtopic 'clean memes chat memes')"""
    global last_ai_time
    now = time.time()
    if now - last_ai_time < ai_cooldown_seconds:
        remaining = int(ai_cooldown_seconds - (now - last_ai_time))
        logger.info(f"AI cooldown: {remaining}s remaining")
        return False

    if not BOT_ID:
        logger.error("No BOT_ID configured - can't send AI messages")
        return False

    url = "https://api.groupme.com/v3/bots/post"
    payload = {
        "bot_id": BOT_ID,
        "text": text,
        "attachments": [
            {
                "type": "topic",
                "topic": "clean memes chat memes"
            }
        ]
    }
    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
        last_ai_time = now
        logger.info(f"AI message sent in subtopic 'clean memes chat memes': {text[:30]}...")
        return True
    except Exception as e:
        logger.error(f"GroupMe AI send error: {e}")
        return False

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

def is_system_message(data):
    """
    Check if this is a GroupMe system message based on API docs
    System messages have sender_type: "system" and specific sender names
    """
    sender_type = data.get('sender_type')
    sender_name = data.get('name', '').lower()
    
    # GroupMe system messages have sender_type "system"
    if sender_type == "system":
        return True
    
    # Also check for system-like sender names (fallback)
    system_senders = ['groupme', 'system', '']
    if sender_name in system_senders:
        return True
    
    return False

def is_real_system_event(text_lower):
    """
    Check if this is a real GroupMe system event (not user typing the same text)
    Based on GroupMe API system message patterns
    """
    # Exact matches for GroupMe system message formats
    system_patterns = [
        'has joined the group',
        'has left the group',
        'was added to the group',
        'was removed from the group',
        'removed',
        'added'
    ]
    
    for pattern in system_patterns:
        if pattern in text_lower:
            return True
    return False

def get_subtopic_from_attachments(data):
    """
    Robustly check incoming message attachments for a topic/subtopic.
    GroupMe may present a topic attachment with type 'topic' and either 'topic' or 'name' keys.
    """
    attachments = data.get("attachments", []) or []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        if att.get("type") == "topic":
            # att could have 'topic' or 'name' depending on payload
            return att.get("topic") or att.get("name") or None
        # some payloads nest info under 'payload' or similar
        payload = att.get("payload")
        if isinstance(payload, dict):
            if payload.get("type") == "topic":
                return payload.get("topic") or payload.get("name") or None
    return None

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        logger.info(f"Webhook received: {data.get('text', '')[:50]}...")
        logger.info(f"Sender type: {data.get('sender_type')}, Sender: {data.get('name')}")
        
        if not data or 'text' not in data:
            return '', 200
        
        # Get all message details
        text = data['text']
        sender_type = data.get('sender_type')
        sender = data.get('name', 'Someone')
        user_id = data.get('user_id')
        text_lower = text.lower()
        attachments = data.get("attachments", [])
        
        # Determine subtopic (if any)
        subtopic = get_subtopic_from_attachments(data)
        if subtopic:
            logger.info(f"Message subtopic detected: {subtopic}")
        
        logger.info(f"Processing - Type: {sender_type}, Sender: {sender}, Text: {text[:50]}...")
        
        # SYSTEM MESSAGE HANDLING - GroupMe API docs say sender_type = "system"
        if sender_type == "system" or is_system_message(data):
            logger.info(f"Processing SYSTEM message from {sender}: {text}")
            
            # Only trigger for real GroupMe system events
            if is_real_system_event(text_lower):
                if 'has left the group' in text_lower:
                    send_system_message("GAY")
                    logger.info("✅ SYSTEM TRIGGER: User left - sent 'GAY'")
                elif 'has joined the group' in text_lower:
                    send_system_message("Welcome to Clean Memes, check the rules and announcement topics before chatting!")
                    logger.info("✅ SYSTEM TRIGGER: User joined - sent welcome message")
                elif 'was removed from the group' in text_lower or 'removed' in text_lower:
                    send_system_message("this could be you if you break the rules, watch it. 👀")
                    logger.info("✅ SYSTEM TRIGGER: User removed - sent warning message")
                else:
                    logger.info(f"Unhandled system event: {text}")
            else:
                logger.info(f"Non-event system message: {text}")
            return '', 200
        
        # Skip non-user messages (system messages already handled)
        if sender_type not in ['user']:
            logger.info(f"Skipping non-user message: {sender_type}")
            return '', 200
        
        # Don't process bot's own messages
        if sender.lower() == BOT_NAME.lower():
            logger.info(f"Ignoring own message from {sender}")
            return '', 200
        
        # PREVENT USERS FROM TRIGGERING SYSTEM RESPONSES
        # Check if user message mimics system events
        if is_real_system_event(text_lower):
            logger.info(f"🚫 User {sender} tried to trigger system event: {text[:50]}...")
            return '', 200
        
        # BAN SYSTEM CHECKS - ONLY FOR REAL USER MESSAGES
        if user_id and text:
            violation_found = check_for_violations(text, user_id, sender)
            if violation_found:
                logger.info(f"Violation handled for {sender}")
                # Continue with bot logic even after violation
        
        # --- CLANKERAI TRIGGER: ONLY ALLOWED IN subtopic 'clean memes chat memes' ---
        # Use the same prompt extraction logic as before but gated by subtopic
        if 'clankerai' in text_lower:
            # Extract prompt (function handles ignoring bot's own and empty prompts)
            full_text = data['text']
            prompt = extract_prompt(full_text, sender)
            
            # Only allow ClankerAI to respond if message is inside the designated subtopic
            allowed_subtopic = "clean memes chat memes"
            if subtopic and subtopic.lower() == allowed_subtopic:
                if prompt:
                    logger.info(f"🤖 ClankerAI triggered by {sender} in subtopic '{subtopic}': {prompt[:50]}...")
                    ai_prompt = f"{sender} says: {prompt}"
                    response = ask_groq(ai_prompt)
                    send_ai_message(response)
                    logger.info(f"✅ AI response sent for {sender} in subtopic '{subtopic}'")
                else:
                    logger.info(f"⚠️ Ignoring empty ClankerAI ping from {sender} in subtopic")
                return '', 200
            else:
                # If clankerai used outside the allowed subtopic, politely redirect in MAIN chat
                logger.info(f"⛔ ClankerAI attempt by {sender} outside '{allowed_subtopic}' (subtopic: {subtopic})")
                send_message("⚠️ Use 'clankerai' in the 'clean memes chat memes' subtopic.")
                return '', 200
        
        # OTHER USER TRIGGERS (main chat)
        if 'clean memes' in text_lower:
            send_message("We're the best!")
            logger.info(f"✅ Trigger: 'clean memes' from {sender}")
        elif 'wsg' in text_lower:
            send_message("God is good")
            logger.info(f"✅ Trigger: 'wsg' from {sender}")
        elif 'cooper is my pookie' in text:
            send_message("me too bro")
            logger.info(f"✅ Trigger: 'cooper is my pookie' from {sender}")
        elif 'https:' in text:
            if not any(att.get("type") == "video" for att in attachments):
                send_message("Delete this, links are not allowed, admins have been notified")
                logger.info(f"✅ Trigger: Link detected from {sender}")
        
        logger.info(f"Message processed from {sender}: {text[:30]}...")
        return '', 200
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        logger.error(f"Error context - Data: {data if 'data' in locals() else 'No data'}")
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
    headers = {"X-Access-Token": os.getenv("GROUPME_ACCESS_TOKEN")}
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
        "system_triggers": {
            "enabled": True,
            "sender_type_check": "system",
            "events": {
                "left": "GAY",
                "joined": "Welcome message", 
                "removed": "Rules warning"
            }
        },
        "free_limit": "1M tokens/month (~20k short responses)"
    }

@app.route('/test', methods=['GET'])
def test():
    """Simple test endpoint"""
    test_response = ask_groq("tell me a short joke")
    return {"test_joke": test_response}

@app.route('/test-system', methods=['POST'])
def test_system():
    """Test system message handling with GroupMe API format"""
    try:
        test_data = request.get_json() or {}
        test_text = test_data.get('text', 'John Doe has left the group')
        test_type = test_data.get('sender_type', 'system')
        test_sender = test_data.get('name', 'GroupMe')
        
        logger.info(f"🧪 Testing system message: '{test_text}' (type: {test_type}, sender: {test_sender})")
        
        # Create mock data structure
        mock_data = {
            'text': test_text,
            'sender_type': test_type,
            'name': test_sender
        }
        
        # Test the system detection
        is_system = is_system_message(mock_data)
        is_event = is_real_system_event(test_text.lower())
        
        logger.info(f"System detection: {is_system}, Event detection: {is_event}")
        
        if test_type == "system" and is_event:
            text_lower = test_text.lower()
            if 'has left the group' in text_lower:
                result = send_system_message("GAY")
                return {"result": "User left trigger fired", "sent": result, "detection": {"is_system": is_system, "is_event": is_event}}
            elif 'has joined the group' in text_lower:
                result = send_system_message("Welcome to Clean Memes, check the rules and announcement topics before chatting!")
                return {"result": "User joined trigger fired", "sent": result, "detection": {"is_system": is_system, "is_event": is_event}}
            elif 'was removed from the group' in text_lower or 'removed' in text_lower:
                result = send_system_message("this could be you if you break the rules, watch it. 👀")
                return {"result": "User removed trigger fired", "sent": result, "detection": {"is_system": is_system, "is_event": is_event}}
            else:
                return {"result": "System message detected but no trigger matched", "detection": {"is_system": is_system, "is_event": is_event}}
        else:
            return {"result": f"Test failed - System: {is_system}, Event: {is_event}", "expected_type": "system"}
            
    except Exception as e:
        logger.error(f"System test error: {e}")
        return {"error": str(e)}, 500

if __name__ == "__main__":
    logger.info("🚀 Starting ClankerAI Bot with Enhanced System Message Handling")
    logger.info(f"Bot ID: {'SET' if BOT_ID else 'MISSING'}")
    logger.info(f"Bot Name: {BOT_NAME}")
    logger.info(f"Groq Key: {'SET' if GROQ_API_KEY else 'MISSING'}")
    logger.info(f"AI Cooldown: {ai_cooldown_seconds}s")
    logger.info(f"Ban System: {'ENABLED' if GROUP_ID and BAN_SERVICE_URL else 'DISABLED'}")
    if GROUP_ID and BAN_SERVICE_URL:
        logger.info(f"  Group: {GROUP_ID}, Ban Service: {BAN_SERVICE_URL}")
    logger.info(f"Instant Ban Words: {len(INSTANT_BAN_WORDS)}")
    logger.info(f"Regular Swear Words: {len(REGULAR_SWEAR_WORDS)}")
    
    logger.info("📋 SYSTEM MESSAGE TRIGGERS (GroupMe API):")
    logger.info("  sender_type: 'system' + 'has left the group' → 'GAY'")
    logger.info("  sender_type: 'system' + 'has joined the group' → Welcome message")
    logger.info("  sender_type: 'system' + 'removed' → Rules warning")
    logger.info("  User messages with these phrases → IGNORED for system triggers")
    
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
    logger.info("🧪 Test endpoints:")
    logger.info("  POST /test-system {'text': 'John Doe has left the group', 'sender_type': 'system', 'name': 'GroupMe'}")
    logger.info("  GET /health")
    app.run(host="0.0.0.0", port=port, debug=False)
