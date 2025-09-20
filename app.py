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

# Cooldowns
last_sent_time = 0
cooldown_seconds = 10
last_ai_time = 0
ai_cooldown_seconds = 30

def ask_groq(prompt):
    """Ask Groq (free, fast, OpenAI-compatible)"""
    if not GROQ_API_KEY:
        return "❌ API key missing!"
        
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "llama3-8b-8192",  # Free, fast model (Llama 3 8B)
        "messages": [
            {"role": "system", "content": "You are ClankerAI, a sarcastic yet helpful bot in the Clean Memes GroupMe chat. Keep it short (1-2 sentences), meme-y, and end with an emoji if it fits. Be witty and conversational."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 100,
        "temperature": 0.8
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
        if e.response.status_code == 401:
            return "🔑 API key issue—check your Groq key!"
        elif e.response.status_code == 429:
            return "⏰ Rate limit hit—wait a moment!"
        else:
            status = e.response.status_code if hasattr(e.response, 'status_code') else "unknown"
            logger.error(f"Groq HTTP {status}: {e}")
            return "🤖 Server hiccup—try again!"
    except Exception as e:
        logger.error(f"Groq error: {e}")
        return "⚠️ Quick glitch—ping me again!"

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
    """Send AI response with cooldown"""
    global last_ai_time
    now = time.time()
    if now - last_ai_time < ai_cooldown_seconds:
        remaining = int(ai_cooldown_seconds - (now - last_ai_time))
        logger.info(f"AI cooldown: {remaining}s remaining")
        return False
    
    full_message = f"🤖 ClankerAI: {text}"
    success = send_message(full_message)
    if success:
        last_ai_time = now
        logger.info("AI message sent successfully")
    return success

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
        
        # ClankerAI trigger
        if 'clankerai' in text:
            full_text = data['text']
            # Extract prompt after "clankerai"
            if 'clankerai' in full_text:
                prompt = full_text.split('clankerai', 1)[1].strip()
            else:
                prompt = "say something funny"
            
            # Clean up prompt
            if not prompt or prompt in ['!', '?', '', ' ']:
                prompt = f"{sender} just pinged me in the Clean Memes group!"
            
            logger.info(f"ClankerAI triggered by {sender}: {prompt[:50]}...")
            
            # Get AI response
            response = ask_groq(prompt)
            
            # Send with cooldown
            send_ai_message(response)
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
        "groq_key": "SET" if GROQ_API_KEY else "MISSING",
        "groq_status": groq_status,
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
    logger.info(f"Groq Key: {'SET' if GROQ_API_KEY else 'MISSING'}")
    
    # Quick startup test
    if GROQ_API_KEY:
        test = ask_groq("startup test - say hi")
        logger.info(f"Startup test: {test}")
    else:
        logger.error("No Groq API key - ClankerAI won't work!")
    
    port = int(os.getenv("PORT", 5000))
    logger.info(f"🚀 Bot running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
