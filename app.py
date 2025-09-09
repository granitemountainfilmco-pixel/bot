from flask import Flask, request
import requests
import os

app = Flask(__name__)

# GroupMe API settings (use environment variables in Render if possible)
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "gLQYFRJ45TWBhuDlAGFlU3t1132qDJGrA3vUQ6rx")
BOT_ID = os.getenv("BOT_ID", "b735f26b7a373dfbd15c49f29d")


@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()

    # Validate incoming webhook
    if not data or 'text' not in data:
        return '', 200

    # Avoid bot loops
    if data.get("sender_type") == "bot":
        return '', 200

    # Check incoming message text
    message_text = data['text'].lower()
    if 'clean memes' in message_text:
        send_message("We're the best!")

    return '', 200


def send_message(text):
    """Send a message via GroupMe Bot API"""
    url = "https://api.groupme.com/v3/bots/post"
    payload = {
        "bot_id": BOT_ID,
        "text": text
    }
    # ✅ No headers needed here, bot posts only need bot_id
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error sending message: {e}")


def get_groups():
    """Example of an authenticated API call using the access token"""
    url = "https://api.groupme.com/v3/groups"
    headers = {"X-Access-Token": ACCESS_TOKEN}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching groups: {e}")
        return None


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
