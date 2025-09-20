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
XAI_API_KEY = os.getenv("XAI_API_KEY")

# Cooldowns
last_sent_time = 0
cooldown_seconds = 10
last_ai_time = 0
ai_cooldown_seconds = 30

def ask_grok(prompt):
    """Ask Grok (that's me!)"""
    url = "https://api.x.ai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "grok-beta",
        "messages": [{"role": "user", "content": f"Clean Memes group chat: {prompt}"}],
        "max_tokens": 100,
        "temperature": 0.8
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Grok error: {e}")
        return "🤖 I'm having a circuit moment—try again!"

def send_message(text):
    """Send to GroupMe with cooldown"""
    global last_sent_time
    now = time.time()
    if now - last_sent_time < cooldown_seconds:
        return False
    
    url = "https://api.groupme.com/v3/bots/post"
    payload = {"bot_id": BOT_ID, "text": text}
    try:
        requests.post(url, json=payload)
        last_sent_time = now
        return True
    except:
        return False

def send_ai_message(text):
    """Send AI response with cooldown"""
    global last_ai_time
    now = time.time()
    if now - last_ai_time < ai_cooldown_seconds:
        return False
    
    success = send_message(f"🤖 ClankerAI: {text}")
    if success:
        last_ai_time = now
    return success

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    if not data or 'text' not in data:
        return '', 200
    
    text = data['text'].lower()
    sender = data.get('name', 'Someone')
    
    # ClankerAI trigger
    if 'clankerai' in text:
        full_text = data['text']
        prompt = full_text.split('clankerai', 1)[1].strip() if 'clankerai' in full_text else "say something funny"
        if not prompt or prompt in ['!', '?', '']:
            prompt = f"{sender} just pinged me!"
        
        logger.info(f"ClankerAI: {prompt}")
        response = ask_grok(prompt)
        send_ai_message(response)
        return '', 200
    
    # Your existing triggers
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
    elif 'https:' in text and not any(att.get("type") == "video" for att in data.get("attachments", [])):
        send_message("Delete this, links are not allowed, admins have been notified")
    
    return '', 200

@app.route('/health', methods=['GET'])
def health():
    return {
        "status": "OK" if BOT_ID and XAI_API_KEY else "missing config",
        "grok_ready": bool(XAI_API_KEY),
        "daily_limit": "10k tokens (~100 messages)"
    }

if __name__ == "__main__":
    logger.info("🚀 ClankerAI (powered by Grok) starting...")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
