from flask import Flask, request, jsonify
from sqlalchemy import create_engine, text
import requests
import json
import os

app = Flask(__name__)

GOOGLE_CHAT_WEBHOOK = os.getenv("GOOGLE_CHAT_WEBHOOK")
GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN")

DATABASE_URL = os.getenv("DATABASE_URL")  # default to SQLite

# 🔹 SQLAlchemy engine
engine = create_engine(DATABASE_URL, echo=False, future=True)

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

# --- DB Helpers ---
def init_db():
    """Create table if not exists and ensure one row exists"""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS deployment_lock (
                id VARCHAR(20) PRIMARY KEY,
                state VARCHAR(20) NOT NULL DEFAULT 'idle',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                apps_deployed VARCHAR(10000)
            )
        """))
        result = conn.execute(text("SELECT COUNT(*) FROM deployment_lock"))
        if result.scalar() == 0:
            conn.execute(text("INSERT INTO deployment_lock (id, state) VALUES ('Staging', 'idle')"))

def get_state(environment_name):
    with engine.begin() as conn:
        result = conn.execute(
            text("SELECT state, apps_deployed FROM deployment_lock WHERE id = :env"),
            {"env": environment_name}
        )
        row = result.fetchone()
        if row:
            return row  # (state, apps_deployed)
        return None, None

def set_state(new_state):
    with engine.begin() as conn:
        conn.execute(text("UPDATE deployment_lock SET state=:state, updated_at=CURRENT_TIMESTAMP WHERE id='Staging'"),
                     {"state": new_state})

@app.route("/", methods=["GET"])
def home():
    return "✅ Frappe Cloud → Google Chat Middleware is running!"

@app.route("/status", methods=["GET"])
def status():
    state = get_state("Staging")[0]
    return jsonify({"deployment_state": state})

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
        set_state("idle")
        
        # Format message nicely
        completed_message = f"""📢 *[ {environment_name} ] Frappe Cloud Event*: Deployment Completed ✅
            
{data.get('doctype')}: {data.get('name')}
Time: {data.get('modified')}
        """
        
        current_db_state = get_state(environment_name) 
        apps_info_str = current_db_state[1]
        # Add apps only if they exist
        if apps_info_str:
            completed_message += f"""\n*Apps Deployed:*  
{apps_info_str}
"""
        requests.post(GOOGLE_CHAT_WEBHOOK, json={"text": completed_message})
        
    return "Message Send Successfully!!!", 200


@app.route("/trigger-workflow", methods=["POST"])
def trigger_workflow():
    state = get_state("Staging")[0]
    if state == "in_progress":
        return jsonify({"status": "skipped", "message": "Deployment already running"}), 200
    try:
        url = "https://api.github.com/repos/Waseela-Global/frappe_auto_deployments/actions/workflows/187848153/dispatches"
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"
        }
        payload = {"ref": "master"}
        resp = requests.post(url, headers=headers, json=payload)
        if resp.status_code == 204:
            return jsonify({"status": "success", "message": "Workflow triggered"}), 200
        else:
            return jsonify({
                "status": "error",
                "code": resp.status_code,
                "response": resp.json()
            }), resp.status_code

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    init_db()  # ensure table exists
    app.run(host="0.0.0.0", port=8080, debug=True)
