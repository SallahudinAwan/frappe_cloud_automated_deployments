from flask import Flask, request
import requests

app = Flask(__name__)

GOOGLE_CHAT_WEBHOOK = "https://chat.googleapis.com/v1/spaces/AAQA4gwdpHQ/messages?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI&token=LImmQc57oGth_ybsOB3cw4PQuuaNr2NOi9W-sdkNISs"

@app.route("/", methods=["GET"])
def home():
    return "✅ Frappe Cloud → Google Chat Middleware is running!"

@app.route("/frappe-cloud-webhook", methods=["POST"])
def handle_webhook():
    payload = request.json
    print(payload)
    event = payload.get("event", "Unknown Event")
    data = payload.get("data", {})

    # Format message nicely
    message = f"""
📢 *Frappe Cloud Event*: {event}

🌐 Site: {data.get('site')}
🔄 Change Type: {data.get('type')}
👤 Modified By: {data.get('modified_by')}
🕒 Time: {data.get('timestamp')}
    """

    requests.post(GOOGLE_CHAT_WEBHOOK, json={"text": message})
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
