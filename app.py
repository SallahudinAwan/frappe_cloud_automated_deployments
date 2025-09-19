from flask import Flask, request, jsonify
from sqlalchemy import create_engine, text
import requests
import json
import os
import re
import html

app = Flask(__name__)

GOOGLE_CHAT_WEBHOOK = os.getenv("GOOGLE_CHAT_WEBHOOK")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
FC_API_KEY = os.getenv("FC_API_KEY")
FC_API_SECRET = os.getenv("FC_API_SECRET")

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
                apps_deployed TEXT,
                current_deploy_candidate VARCHAR(100)
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
            text("SELECT state, apps_deployed, current_deploy_candidate FROM deployment_lock WHERE id = :env"),
            {"env": environment_name}
        )
        row = result.fetchone()
        return (row[0], row[1], row[2]) if row else (None, None, None)

def set_state(environment_name, new_state, apps_deployed=None, deploy_candidate=None):
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE deployment_lock
                SET state = :state,
                    apps_deployed = :apps_deployed,
                    current_deploy_candidate = :deploy_candidate,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :env
            """),
            {
                "state": new_state,
                "apps_deployed": apps_deployed,
                "deploy_candidate": deploy_candidate,
                "env": environment_name
            }
        )

# --- Routes ---
@app.route("/", methods=["GET"])
def home():
    return "✅ Frappe Cloud → Google Chat Middleware is running!"

@app.route("/status/<env>", methods=["GET"])
def status(env):
    state, apps, candidate = get_state(env)
    return jsonify({"environment": env, "state": state, "apps_deployed": apps, "current_deploy_candidate": candidate})

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
        set_state(environment_name, "idle", None, None)

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
    state, _, _ = get_state(env)
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


def html_to_plain_text(html_content: str) -> str:
    """
    Very small HTML -> plain text converter:
    - unescapes HTML entities
    - replaces <p>, <br>, <li> with newlines
    - removes all other tags
    - collapses multiple newlines/spaces
    """
    if not html_content:
        return ""

    # unescape entities like &lt; &amp;
    text = html.unescape(html_content)

    # replace block tags with newlines so paragraphs become separate lines
    # add newlines around tags we want to preserve as structure
    text = re.sub(r'(?i)</?(p|div|br|li|ul|ol|h[1-6])[^>]*>', '\n', text)

    # remove any remaining tags
    text = re.sub(r'<[^>]+>', '', text)

    # normalize whitespace & newlines: collapse more than 2 newlines into 2
    text = re.sub(r'\r\n?', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    # collapse multiple spaces
    text = re.sub(r'[ \t]{2,}', ' ', text)

    # strip leading/trailing whitespace and ensure tidy paragraphs
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]  # drop empty lines
    return "\n\n".join(lines)


def format_failure_message(env, candidate, title, html_message, traceback_text,
                           max_traceback_chars=2000):
    """
    Build a clean text message for chat:
    - html_message -> converted to plain text
    - traceback_text -> truncated and put in a code block
    Returns a string ready to post to Google Chat (JSON {"text": message}).
    """
    plain_msg = html_to_plain_text(html_message)
    tb = traceback_text or ""
    tb = tb.strip()

    # Truncate traceback: keep head and tail if very large
    if len(tb) > max_traceback_chars:
        half = max_traceback_chars // 2
        tb = tb[:half] + "\n\n...[truncated]...\n\n" + tb[-half:]

    # Escape triple backticks in traceback (so we can safely wrap in a code block)
    tb = tb.replace("```", "`​``")  # insert zero-width char to avoid breaking

    # Build message
    parts = []
    parts.append(f"❌ *[{env}]* Deployment Failed")
    parts.append(f"*Deploy Candidate:* `{candidate}`")
    if title:
        parts.append(f"*Error:* {title}")
    if plain_msg:
        parts.append("\n*Details:*\n" + plain_msg)
    if tb:
        parts.append("\n*Traceback:*\n```\n" + tb + "\n```")

    # Join with blank line between sections
    message = "\n\n".join(parts)
    return message


# --- New API ---
@app.route("/check-deploy-failure/<env>", methods=["GET"])
def check_deploy_failure(env):
    try:
        state, _, candidate = get_state(env)
        if state != "in_progress" or not candidate:
            return jsonify({"status": "idle", "message": "No active deployment to check"}), 200

        # Step 1: Check Press Notifications
        url_list = "https://frappecloud.com/api/method/press.api.client.get_list"
        headers = {
            "Authorization": f"token {FC_API_KEY}:{FC_API_SECRET}",
            "Content-Type": "application/json"
        }
        payload_list = {
            "doctype": "Press Notification",
            "fields": ["title", "name"],
            "filters": {
                "document_type": "Deploy Candidate Build",
                "document_name": candidate,
                "is_actionable": True,
                "class": "Error"
            },
            "limit": 20
        }

        resp = requests.post(url_list, headers=headers, json=payload_list)
        resp.raise_for_status()
        notifications = resp.json().get("message", [])

        if not notifications:
            return jsonify({"status": "in_progress", "message": "No errors detected yet"}), 200

        notif_id = notifications[0]["name"]

        # Step 2: Fetch full error details
        url_get = "https://frappecloud.com/api/method/press.api.client.get"
        payload_get = {"doctype": "Press Notification", "name": notif_id}
        resp2 = requests.post(url_get, headers=headers, json=payload_get)
        resp2.raise_for_status()
        notif_detail = resp2.json().get("message", {})
        
        title = notif_detail.get("title")
        html_message = notif_detail.get("message")
        traceback_info = notif_detail.get("traceback", "")

        # Step 3: Reset lock
        set_state(env, "idle", None, None)
        chat_text = format_failure_message(env, candidate, title, html_message, traceback_info)

        if GOOGLE_CHAT_WEBHOOK:
            requests.post(GOOGLE_CHAT_WEBHOOK, json={"text": chat_text})

        return jsonify({"status": "failure", "error": chat_text}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8080, debug=True)
