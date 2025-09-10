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

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    logger.info(f"Received data: {data}")

    # Ignore empty payloads
    if not data or 'text' not in data:
        logger.info("Empty payload or no text, ignoring")
        return '', 200

    # Check for trigger words
    message_text = data['text'].lower()
    logger.info(f"Processing message: {message_text}")
    if 'clean memes' in message_text:
        logger.info("Trigger: clean memes")
        send_message("We're the best!")
    elif 'wsg' in message_text:
        logger.info("Trigger: wsg")
        send_message("God is good")
    elif 'bye' in message_text:
        logger.info("Trigger: bye")
        send_message("cya")
    elif 'has left the group' in message_text:
        logger.info("Trigger: has left the group")
        send_message("GAY")
    elif 'wsg chat just got back from band practice' in message_text:
        logger.info("Trigger: band practice")
        send_message("band kid? crazy.")
    elif 'has joined the group' in message_text:
        logger.info("Trigger: has joined the group")
        send_message("Welcome to Clean Memes, check the rules and announcement topics before chatting!")
    elif 'www.' in message_text:
        logger.info("Trigger: www.")
        send_message("Delete this, links are not allowed, admins have been notified")
    
    return '', 200

def send_message(text):
    """Send a message via GroupMe Bot API"""
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
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending message: {e}")

# Example of using access token for API calls if needed
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
