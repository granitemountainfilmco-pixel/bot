from flask import Flask, request
import requests
import os
import time
import logging

app = Flask(__name__)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# GroupMe API settings (from Render environment variables)
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
BOT_ID = os.getenv("BOT_ID")

# Global rate limit tracker
last_sent_time = 0
cooldown_seconds = 10  # global cooldown

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    logger.info(f"Received data: {data}")

    if not data or 'text' not in data:
        logger.info("Empty payload or no text, ignoring")
        return '', 200

    message_text = data['text'].lower()
    logger.info(f"Processing message: {message_text}")

    if 'clean memes' in message_text:
        send_message("We're the best!")
    elif 'wsg' in message_text:
        send_message("God is good")
    elif 'has left the group' in message_text:
        send_message("GAY")
    elif 'wsg chat just got back from band practice' in message_text:
        send_message("band kid? crazy.")
    elif 'has joined the group' in message_text:
        send_message("Welcome to Clean Memes, check the rules and announcement topics before chatting!")
    elif 'https:' in message_text:
        send_message("Delete this, links are not allowed, admins have been notified")
    
    return '', 200

def send_message(text):
    """Send a message via GroupMe Bot API with global cooldown"""
    global last_sent_time
    now = time.time()

    if now - last_sent_time < cooldown_seconds:
        logger.info(f"Cooldown active, skipping message: {text}")
        return

    url = "https://api.groupme.com/v3/bots/post"
    payload = {
        "bot_id": BOT_ID,
        "text": text
    }
    try:
        logger.info(f"Sending message: {text}")
        response = requests.post(url, json=payload)
        response.raise_for_status()
        logger.info("Message sent successfully")
        last_sent_time = now  # update cooldown timer
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending message: {e}")

@app.route('/groups', methods=['GET'])
def groups():
    url = "https://api.groupme.com/v3/groups"
    headers = {"X-Access-Token": ACCESS_TOKEN}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}, 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
