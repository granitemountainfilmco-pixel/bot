from flask import Flask, request
import requests
import os

app = Flask(__name__)

# GroupMe API settings (from Render environment variables)
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
BOT_ID = os.getenv("BOT_ID")

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()

    # Ignore empty payloads
    if not data or 'text' not in data:
        return '', 200

    # Avoid infinite loops
    if data.get("sender_type") == "bot":
        return '', 200

    # Check for trigger words
    message_text = data['text'].lower()
    if 'clean memes' in message_text:
        send_message("We're the best!")
    elif 'wsg' in message_text:
        send_message("God is good")
    elif 'bye' in message_text:
        send_message("https://uploads.dailydot.com/2024/12/cat-laughing-4.jpg?auto=compress&fm=pjpg")

    return '', 200

def send_message(text):
    """Send a message via GroupMe Bot API"""
    url = "https://api.groupme.com/v3/bots/post"
    payload = {
        "bot_id": BOT_ID,
        "text": text
    }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error sending message: {e}")

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