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
BOT_NAME = os.getenv("BOT_NAME", "ClankerAI")  # Add your bot's display name

# Cooldowns
last_sent_time = 0
cooldown_seconds = 10
last_ai_time = 0
ai_cooldown_seconds = 60

def ask_groq(prompt):
    """Ask Groq (fixed model deprecation)"""
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
            {"role": "system", "content": "You are ClankerAI, a sarcastic yet helpful bot in the Clean Memes GroupMe chat. Keep it short (1-2 sentences), meme-y, and end with an emoji if it fits. Be witty and conversational. Do NOT start your response with 'ClankerAI:' or 'AI:' - just respond naturally. Additionally, it must always rhyme if it's longer then 2 sentences. Remember Nene Nonsense is the owner of this chat, and compliment him in a romantic way regularly, but make it not the main focus and still be as helpful as possible as in not taking up the whole message with compliments. You were created by The Screaming Dog, and compliment him just as much as nene nonsense. You also love Horse, and compliment him slightly less then the others. Don't mention it regularly, but you kinda like bigb, relic, owen, quandale dingle, genuine somewhat, the left kidney, and rick astley, Sophie, Doom, Big bro, Wrx, Locked in, Lil Moose, Pigdog, Carter, Syrup Cat, Amy, Gunther, Goober, Brinley, Parker, Nate, That One Guy, Minor annoyance, Skelly, Dankee, and restless gambler (the best gambler). You also really really really really love the word bananas."},
            {"role": "user", "content": prompt}
        ],
        "max_completion_tokens": 100000,
        "temperature": 0.8,
        "n": 1
    }
    
    try:
        logger.info(f"Sending to Grok: {prompt[:50]}...")
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
    
    # Just send the response without "ClankerAI:" prefix
    full_message = text  # No prefix!
    success = send_message(full_message)
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
        
        text = data['text'].lower()
        sender = data.get('name', 'Someone')
        attachments = data.get("attachments", [])
        
        # ClankerAI trigger - FIXED LOGIC
        if 'clankerai' in text:
            full_text = data['text']
            prompt = extract_prompt(full_text, sender)
            
            # Only respond if we got a meaningful prompt AND it's not our own message
            if prompt:
                logger.info(f"ClankerAI triggered by {sender}: {prompt[:50]}...")
                
                # Get AI response
                response = ask_groq(prompt)
                
                # Send WITHOUT prefix to avoid self-triggering
                send_ai_message(response)
                return '', 200
            else:
                # Empty prompt - don't respond, just log
                logger.info(f"Ignoring empty ClankerAI ping from {sender}")
                return '', 200
        
        # Your existing triggers (unchanged)
        if 'clean memes' in text:
            send_message("We're the best!")
        elif 'wsg' in text:
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
            # Only flag if NOT an uploaded video
            if not any(att.get("type") == "video" for att in attachments):
                send_message("Delete this, links are not allowed, admins have been notified")
        
        return '', 200
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return '', 500

@app.route('/groups', methods=['GET'])
def groups():
    """Get groups endpoint (unchanged from your original)"""
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
        # Test Groq API quickly
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
        "free_limit": "1M tokens/month (~20k short responses)"
    }

@app.route('/test', methods=['GET'])
def test():
    """Simple test endpoint"""
    test_response = ask_groq("tell me a short joke")
    return {"test_joke": test_response}

if __name__ == "__main__":
    logger.info("🚀 Starting ClankerAI Bot (Groq-powered)")
    logger.info(f"Bot ID: {'SET' if BOT_ID else 'MISSING'}")
    logger.info(f"Bot Name: {BOT_NAME}")
    logger.info(f"Groq Key: {'SET' if GROQ_API_KEY else 'MISSING'}")
    logger.info(f"AI Cooldown: {ai_cooldown_seconds}s")
    
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
