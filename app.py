from flask import Flask, request, jsonify
from sqlalchemy import create_engine, text
import requests
import json
import os

app = Flask(__name__)

GOOGLE_CHAT_WEBHOOK = os.getenv("GOOGLE_CHAT_WEBHOOK")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# --- SQLAlchemy engine ---
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,    # test connections before using
    pool_recycle=300,      # recycle connections every 5 mins
    pool_size=5,
    max_overflow=10
)

# --- Environment mapping ---
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
    """Create table if not exists and ensure rows for all environments"""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS deployment_lock (
                id VARCHAR(20) PRIMARY KEY,
                state VARCHAR(20) NOT NULL DEFAULT 'idle',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                apps_deployed TEXT
            )
        """))
        for env in ["Staging", "Preview", "Production"]:
            result = conn.execute(
                text("SELECT COUNT(*) FROM deployment_lock WHERE id = :env"),
                {"env": env}
            )
            if result.scalar() == 0:
                conn.execute(
                    text("INSERT INTO deployment_lock (id, state) VALUES (:env, 'idle')"),
                    {"env": env}
                )

def get_state(environment_name):
    with engine.begin() as conn:
        result = conn.execute(
            text("SELECT state, apps_deployed FROM deployment_lock WHERE id = :env"),
            {"env": environment_name}
        )
        row = result.fetchone()
        return (row[0], row[1]) if row else (None, None)

def set_state(environment_name, new_state, apps_deployed=None):
    """Update deployment state; if apps_deployed=None -> set NULL"""
    with engine.begin() as conn:
        if apps_deployed is None:
            conn.execute(
                text("""
                    UPDATE deployment_lock
                    SET state = :state,
                        apps_deployed = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :env
                """),
                {"state": new_state, "env": environment_name}
            )
        else:
            conn.execute(
                text("""
                    UPDATE deployment_lock
                    SET state = :state,
                        apps_deployed = :apps_deployed,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :env
                """),
                {"state": new_state, "apps_deployed": apps_deployed, "env": environment_name}
            )

# --- Routes ---
@app.route("/", methods=["GET"])
def home():
    return "✅ Frappe Cloud → Google Chat Middleware is running!"

@app.route("/status/<env>", methods=["GET"])
def status(env):
    state, apps = get_state(env)
    return jsonify({"environment": env, "state": state, "apps_deployed": apps})

@app.route("/frappe-cloud-webhook", methods=["POST"])
def handle_webhook():
    payload = request.json
    event = payload.get("event", "Unknown Event")
    data = payload.get("data", {})

    environment_name = ""
    if data.get("doctype") == "Bench":
        environment_name = BENCH_ENV_MAP.get(data.get("group"), "")
    elif data.get("doctype") == "Site":
        environment_name = SITE_ENV_MAP.get(data.get("name"), "")

    current_db_state = get_state(environment_name)

    # 🔹 Send update message
    message = f"""📢 *[ {environment_name} ] Frappe Cloud Event*: {event}
{data.get('doctype')}: {data.get('name')}
Status: {data.get('status')}
Modified By: {data.get('modified_by')}
Time: {data.get('modified')}
"""
    if GOOGLE_CHAT_WEBHOOK:
        requests.post(GOOGLE_CHAT_WEBHOOK, json={"text": message})

    # 🔹 Reset lock when site is active again
    if data.get("doctype") == "Site" and data.get("status") == "Active":
        set_state(environment_name, "idle")

        completed_message = f"""✅ *[ {environment_name} ] Deployment Completed*
{data.get('doctype')}: {data.get('name')}
Time: {data.get('modified')}
"""
        apps_info_str = current_db_state[1]
        if apps_info_str:
            completed_message += f"\n*Apps Deployed:* \n{apps_info_str}"

        if GOOGLE_CHAT_WEBHOOK:
            requests.post(GOOGLE_CHAT_WEBHOOK, json={"text": completed_message})

    return "Webhook processed", 200

@app.route("/trigger-workflow/<env>", methods=["POST"])
def trigger_workflow(env):
    state, _ = get_state(env)
    if state == "in_progress":
        return jsonify({"status": "skipped", "message": "Deployment already running"}), 204
    try:
        url = "https://api.github.com/repos/Waseela-Global/frappe_auto_deployments/actions/workflows/187848153/dispatches"
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"
        }
        payload = {"ref": "master"}
        resp = requests.post(url, headers=headers, json=payload)

        if resp.status_code == 204:
            return jsonify({"status": "success", "message": f"{env} Workflow triggered"}), 200
        else:
            return jsonify({
                "status": "error",
                "code": resp.status_code,
                "response": resp.json()
            }), resp.status_code

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8080, debug=True)
