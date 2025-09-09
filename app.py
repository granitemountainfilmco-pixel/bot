from flask import Flask, request
import requests
import json
import os

app = Flask(__name__)

# GroupMe API settings (hardcoded as requested)
ACCESS_TOKEN = "gLQYFRJ45TWBhuDlAGFlU3t1132qDJGrA3vUQ6rx"
BOT_ID = "b735f26b7a373dfbd15c49f29d"

@app.route('/webhook', methods=['POST'])
def webhook():
    # Parse incoming message
    data = request.get_json()
    if not data or 'text' not in data:
        return '', 200

    # Check if message contains "clean memes" (case-insensitive)
    message_text = data['text'].lower()
    if 'clean memes' in message_text:
        # Send response
        send_message("We're the best!")
    
    return '', 200

def send_message(text):
    url = "https://api.groupme.com/v3/bots/post"
    payload = {
        "bot_id": BOT_ID,
        "text": text
    }
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error sending message: {e}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))