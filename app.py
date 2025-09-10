from flask import Flask, request
import requests
import json


app = Flask(__name__)

GOOGLE_CHAT_WEBHOOK = "https://chat.googleapis.com/v1/spaces/AAQA4gwdpHQ/messages?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI&token=LImmQc57oGth_ybsOB3cw4PQuuaNr2NOi9W-sdkNISs"

# 🔹 Site mapping (still hardcoded exact matches)
SITE_ENV_MAP = {
    "waseela.frappe.cloud": "Staging",
    "waseela-os-preview.s.frappe.cloud": "Preview",
    "waseela-os-production.s.frappe.cloud": "Production"
}

BENCH_ENV_MAP = {
    "bench-17853": "Staging",
    "bench-25861": "Preview",
    "bench-25568": "Production"
}

@app.route("/", methods=["GET"])
def home():
    return "✅ Frappe Cloud → Google Chat Middleware is running!"

@app.route("/frappe-cloud-webhook", methods=["POST"])
def handle_webhook():
    payload = request.json
    print(payload)
    event = payload.get("event", "Unknown Event")
    data = payload.get("data", {})
    json_string = json.dumps(data)
    environment_name = ""
    if data.get('doctype') == "Bench":
        environment_name = BENCH_ENV_MAP[data.get('group')]
    elif data.get('doctype') == "Site":
        environment_name = SITE_ENV_MAP[data.get('name')]

    # Format message nicely
    message = f"""📢 *[ {environment_name} ] Frappe Cloud Event*: {event}
        
{data.get('doctype')}: {data.get('name')}
status: {data.get('status')}
Modified By: {data.get('modified_by')}
Time: {data.get('modified')}
    """
    print(message)
    requests.post(GOOGLE_CHAT_WEBHOOK, json={"text": message})
    
    if data.get('doctype') == "Site" and data.get('status') == "Active":
        # Format message nicely
        completed_message = f"""📢 *[ {environment_name} ] Frappe Cloud Event*: Deployment Completed ✅
            
{data.get('doctype')}: {data.get('name')}
Time: {data.get('modified')}
        """
        requests.post(GOOGLE_CHAT_WEBHOOK, json={"text": completed_message})
    
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
