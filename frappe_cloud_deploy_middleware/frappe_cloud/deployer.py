import json
import os

import requests
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from ..config import GOOGLE_CHAT_TIMEOUT_SECONDS, PRESS_API_TIMEOUT_SECONDS


def ensure_deployment_lock_table(engine) -> None:
    """Ensure the deployment tracking table and environment rows exist."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
            CREATE TABLE IF NOT EXISTS deployment_lock (
                id VARCHAR(20) PRIMARY KEY,
                state VARCHAR(20) NOT NULL DEFAULT 'idle',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                apps_deployed JSONB,
                current_deploy_candidate VARCHAR(100),
                chat_thread_id TEXT
            )
        """
            )
        )
        for env_id in ["Staging", "Preview", "Production"]:
            exists = conn.execute(text("SELECT COUNT(*) FROM deployment_lock WHERE id=:id"), {"id": env_id}).scalar()
            if not exists:
                conn.execute(text("INSERT INTO deployment_lock (id, state) VALUES (:id, 'idle')"), {"id": env_id})


def update_deployment_state(engine, deploy_env: str, new_state, apps_payload=None, deploy_candidate=None, chat_thread_id=None) -> None:
    """Update deployment state and metadata in DB."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE deployment_lock
                SET state = :state,
                    apps_deployed = :apps_deployed,
                    current_deploy_candidate = :deploy_candidate,
                    chat_thread_id = :chat_thread_id,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """
            ),
            {
                "id": deploy_env.capitalize(),
                "state": new_state,
                "apps_deployed": json.dumps(apps_payload) if apps_payload else None,
                "deploy_candidate": deploy_candidate,
                "chat_thread_id": chat_thread_id,
            },
        )


def fetch_bench_info(headers, bench_name: str):
    """Fetch the release group (bench) info from Frappe Cloud."""
    url = "https://cloud.frappe.io/api/method/press.api.client.get"
    payload = {"doctype": "Release Group", "name": bench_name}
    print(payload)
    resp = requests.post(url, headers=headers, json=payload, timeout=PRESS_API_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()["message"]


def build_deploy_start_card(deploy_env: str, bench_name: str, site_name: str, apps):
    """Construct a Google Chat card when deployment starts."""
    env_icons = {
        "staging": "https://cdn-icons-png.freepik.com/512/6562/6562824.png",
        "preview": "https://cdn-icons-png.freepik.com/512/6561/6561218.png",
        "production": "https://cdn-icons-png.freepik.com/512/6561/6561171.png",
    }

    image_url = env_icons.get(deploy_env, "https://cdn-icons-png.freepik.com/512/6562/6562824.png")
    card = {
        "cardsV2": [
            {
                "cardId": "frappe-cloud-deploy-start",
                "card": {
                    "header": {
                        "title": f"🚀 [ {deploy_env.capitalize()} ] Automated Deployment Alert",
                        "subtitle": "Deployment Started 🔄",
                        "imageUrl": image_url,
                        "imageType": "CIRCLE",
                    },
                    "sections": [
                        {
                            "widgets": [
                                {"decoratedText": {"topLabel": "Bench Name", "text": bench_name}},
                                {"decoratedText": {"topLabel": "Site Name", "text": site_name}},
                            ]
                        }
                    ],
                },
            }
        ]
    }

    # Add "Apps Deployed" only if apps exist
    if apps:
        card["cardsV2"][0]["card"]["sections"].append(
            {
                "header": "Apps Deployed",
                "collapsible": True,
                "uncollapsibleWidgetsCount": 2,
                "widgets": [
                    widget
                    for app in apps
                    for widget in [
                        {"decoratedText": {"topLabel": app["app"].capitalize(), "text": ""}},
                        {
                            "buttonList": {
                                "buttons": [
                                    {
                                        "text": app["last Commit Message"],
                                        "onClick": {
                                            "openLink": {
                                                "url": app["repo"].rstrip("/") + "/commit/" + app["Last Commit Hash"]
                                            }
                                        },
                                    }
                                ]
                            }
                        },
                    ]
                ],
            }
        )

    return card


def post_google_chat_card(google_chat_webhook: str, card_payload):
    """Send a Card message to Google Chat and return the API response."""
    resp = requests.post(google_chat_webhook, json=card_payload, timeout=GOOGLE_CHAT_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()


def trigger_deployment(headers, google_chat_webhook: str, engine, deploy_env: str, bench_name: str, apps, apps_info, sites):
    """
    Trigger a deployment (simulation) and send Google Chat notification.
    In production, replace with Press API endpoint.
    """
    url = "https://cloud.frappe.io/api/method/press.api.bench.deploy_and_update"
    payload = {"name": bench_name, "apps": apps, "sites": sites, "run_will_fail_check": True}
    resp = requests.post(url, headers=headers, json=payload, timeout=PRESS_API_TIMEOUT_SECONDS)
    resp.raise_for_status()

    print(f"🔄 Triggering deployment for {bench_name} ...")
    print(json.dumps(payload, indent=2))

    # --- Send Google Chat card ---
    card_payload = build_deploy_start_card(deploy_env, bench_name, sites[0]["name"], apps_info)
    res_data = post_google_chat_card(google_chat_webhook, card_payload)

    deployment_payload = resp.json()
    # deployment_payload = {"message":"ABC"}  #for testing

    # --- Save deployment progress in DB ---
    update_deployment_state(
        engine=engine,
        deploy_env=deploy_env,
        new_state="in_progress",
        apps_payload=apps_info,
        deploy_candidate=deployment_payload.get("message"),  # replace with Press API result
        chat_thread_id=res_data.get("thread", {}).get("name"),
    )

    print("✅ Deployment message sent successfully!")
    return deployment_payload


def main():
    """Main deployment handler entrypoint."""
    api_key = os.getenv("FC_API_KEY")
    api_secret = os.getenv("FC_API_SECRET")
    deploy_env = os.getenv("DEPLOY_ENV", "staging").lower()  # staging | preview | production
    google_chat_webhook = os.getenv("GOOGLE_CHAT_WEBHOOK")
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL is required (env var DATABASE_URL is not set).")
    if not api_key or not api_secret:
        raise RuntimeError("FC_API_KEY and FC_API_SECRET are required.")
    if not google_chat_webhook:
        raise RuntimeError("GOOGLE_CHAT_WEBHOOK is required.")

    engine = create_engine(database_url, pool_pre_ping=True, poolclass=NullPool)

    env_config = {
        "staging": {
            "bench": os.getenv("STAGING_BENCH_NAME", "bench-XXXXX"),
            "allowed_apps": set(app.strip() for app in os.getenv("STAGING_ALLOWED_APPS", "").split(",") if app.strip()),
        },
        "preview": {
            "bench": os.getenv("PREVIEW_BENCH_NAME", "bench-XXXXX"),
            "allowed_apps": set(app.strip() for app in os.getenv("PREVIEW_ALLOWED_APPS", "").split(",") if app.strip()),
        },
        "production": {
            "bench": os.getenv("PROD_BENCH_NAME", "bench-XXXXX"),
            "allowed_apps": set(app.strip() for app in os.getenv("PROD_ALLOWED_APPS", "").split(",") if app.strip()),
        },
        "version16": {
            "bench": os.getenv("VERSION16_BENCH_NAME", "bench-XXXXX"),
            "allowed_apps": set(app.strip() for app in os.getenv("VERSION16_ALLOWED_APPS", "").split(",") if app.strip()),
        },
    }

    bench_name = env_config[deploy_env]["bench"]

    allowed_apps_from_workflow = os.getenv("ALLOWED_APPS_FROM_WORKFLOW", "")
    if allowed_apps_from_workflow:
        allowed_apps = {app.strip() for app in allowed_apps_from_workflow.split(",") if app.strip()}
        print(f"🔹 Using allowed apps from workflow input: {allowed_apps}")
    else:
        allowed_apps = env_config[deploy_env]["allowed_apps"]
        print(f"🔹 Using allowed apps from environment: {allowed_apps}")

    headers = {"Authorization": f"token {api_key}:{api_secret}", "Content-Type": "application/json"}

    ensure_deployment_lock_table(engine)
    env_id = deploy_env.capitalize()
    print(f"🌍 Starting deployment for {env_id}")

    bench_info = fetch_bench_info(headers, bench_name)
    deploy_info = bench_info["deploy_information"]

    # --- Safety Checks ---
    if deploy_info.get("deploy_in_progress"):
        print(f"⚠️ {env_id}: Deployment already in progress. Exiting.")
        return

    if not deploy_info.get("update_available"):
        print(f"✅ {env_id}: No updates available. Exiting.")
        return

    # --- Build list of apps to deploy ---
    apps_to_deploy, apps_info = [], []
    for app_info in deploy_info["apps"]:
        if not app_info.get("update_available"):
            continue
        if app_info["name"] not in allowed_apps:
            continue

        latest_release = app_info["next_release"]
        release_obj = next((r for r in app_info["releases"] if r["name"] == latest_release), None)
        if not release_obj:
            continue

        apps_to_deploy.append(
            {"app": app_info["name"], "source": release_obj["source"], "release": release_obj["name"], "hash": release_obj["hash"]}
        )
        apps_info.append(
            {
                "app": app_info["name"],
                "last Commit Message": release_obj["message"],
                "Last Commit Hash": release_obj["hash"],
                "repo": app_info.get("repository_url", ""),
            }
        )

    if not apps_to_deploy:
        print(f"⚠️ {env_id}: Updates flagged, but no deployable apps found.")
        return

    print(f"🧩 Apps to deploy: {[a['app'] for a in apps_to_deploy]}")

    # --- Trigger Deployment ---
    sites = deploy_info["sites"]
    deploy_resp = trigger_deployment(headers, google_chat_webhook, engine, deploy_env, bench_name, apps_to_deploy, apps_info, sites)

    print(f"✅ Deployment triggered successfully:\n{json.dumps(deploy_resp, indent=2)}")


if __name__ == "__main__":
    main()
