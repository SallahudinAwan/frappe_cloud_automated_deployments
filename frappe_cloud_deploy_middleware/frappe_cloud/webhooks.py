import logging

import requests
from flask import Blueprint, jsonify, request

from .cards import build_card_normal, build_card_success
from .services import check_site_update_status, detect_deploy_failure
from ..config import (
    ALLOWED_STATUS_MAP,
    BENCH_ENV_MAP,
    ENV_ICONS,
    GOOGLE_CHAT_TIMEOUT_SECONDS,
    GOOGLE_CHAT_WEBHOOK,
    SITE_ENV_MAP,
)
from ..db import get_state, set_state

log = logging.getLogger(__name__)

bp = Blueprint("frappe_cloud", __name__)


@bp.route("/frappe-cloud-webhook", methods=["POST"])
def handle_frappe_cloud_webhook():
    """
    Main Frappe Cloud webhook handler.
    - Filters out uninteresting statuses
    - Maintains deployment_lock state
    - Posts updates to Google Chat
    """
    try:
        payload = request.get_json(force=True)
        event = payload.get("event", "Unknown Event")
        data = payload.get("data", {}) or {}

        doctype = data.get("doctype")
        status_val = data.get("status")
        doc_name = data.get("name")

        log.info("Frappe Cloud webhook event=%s doctype=%s status=%s name=%s", event, doctype, status_val, doc_name)

        # Filter out statuses we don't want to show
        allowed_statuses = ALLOWED_STATUS_MAP.get(doctype)
        if allowed_statuses is not None and status_val not in allowed_statuses:
            return jsonify({"status": "skipped", "reason": f"{doctype} status '{status_val}' not allowed"}), 200

        # Determine environment name from bench/group or site name
        environment_name = ""
        if doctype in ("Bench", "Deploy Candidate Build"):
            environment_name = BENCH_ENV_MAP.get(data.get("group"), "")
        elif doctype == "Site":
            environment_name = SITE_ENV_MAP.get(data.get("name"), "")

        current_db_state = get_state(environment_name)

        # If a deploy candidate build arrives and no deployment is in progress, create thread and set lock
        if current_db_state[0] == "idle" and doctype == "Deploy Candidate Build":
            # post a manual-deploy card and capture chat thread id from response
            image_url = ENV_ICONS.get(environment_name, "https://cdn-icons-png.freepik.com/512/6562/6562824.png")
            manual_card = {
                "cardsV2": [
                    {
                        "cardId": "frappe-cloud-deploy-start-manual",
                        "card": {
                            "header": {
                                "title": f"🚀 [{environment_name}] Manual/Retry Deployment Alert",
                                "subtitle": "Deployment Started 🔄",
                                "imageUrl": image_url,
                                "imageType": "CIRCLE",
                            }
                        },
                    }
                ]
            }
            if GOOGLE_CHAT_WEBHOOK:
                res = requests.post(GOOGLE_CHAT_WEBHOOK, json=manual_card, timeout=GOOGLE_CHAT_TIMEOUT_SECONDS)
                res_json = res.json() if res.content else {}
                chat_thread_id = res_json.get("thread", {}).get("name")
                # Save new lock state
                set_state(environment_name, "in_progress", None, data.get("name"), chat_thread_id)
                current_db_state = get_state(environment_name)
            else:
                log.warning("GOOGLE_CHAT_WEBHOOK not set; manual deploy card not sent")

        # Send a normal update (uses existing chat_thread_id if any)
        if GOOGLE_CHAT_WEBHOOK:
            thread_id = current_db_state[3] or ""  # may be None
            card_message = build_card_normal(environment_name, event, data, thread_id)
            resp = requests.post(
                GOOGLE_CHAT_WEBHOOK + "&messageReplyOption=REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD",
                json=card_message,
                timeout=GOOGLE_CHAT_TIMEOUT_SECONDS,
            )
            log.info("Posted normal card: %s %s", resp.status_code, resp.text)

        # If Site becomes Active, reset lock and send success card if apps info exists
        if data.get("doctype") == "Site" and data.get("status") == "Active":
            if check_site_update_status(data.get("name"), environment_name):
                apps_info_str = current_db_state[1]
                log.info("Apps: %s", apps_info_str)
                if GOOGLE_CHAT_WEBHOOK:
                    card_message = build_card_success(environment_name, data, apps_info_str)
                    resp = requests.post(GOOGLE_CHAT_WEBHOOK, json=card_message, timeout=GOOGLE_CHAT_TIMEOUT_SECONDS)
                    log.info("Posted success card: %s %s", resp.status_code, resp.text)
                    set_state(environment_name, "idle", None, None, None)

        # If Deploy Candidate Build failed, trigger deploy-failure checker to post detailed failure
        if doctype == "Deploy Candidate Build" and status_val == "Failure":
            try:
                failure_payload, _ = detect_deploy_failure(environment_name)
                set_state(environment_name, "idle", None, None, None)
                log.info("detect_deploy_failure result: %s", failure_payload)
            except Exception:
                log.exception("Error while checking deploy failure")

        return "Webhook processed", 200

    except Exception as exc:
        log.exception("Error handling Frappe Cloud webhook")
        return jsonify({"status": "error", "message": str(exc)}), 500

