import logging

import requests

from .cards import build_card_failure, build_card_failure_detailed
from ..config import FC_API_KEY, FC_API_SECRET, GOOGLE_CHAT_TIMEOUT_SECONDS, GOOGLE_CHAT_WEBHOOK, PRESS_API_TIMEOUT_SECONDS
from ..db import get_state, set_chat_thread
from ..utils import format_failure_message

log = logging.getLogger(__name__)


def check_site_update_status(site_name: str, env: str):
    """
    Checks if the given Frappe Cloud site has any updates pending.
    If updates are available, sends a Google Chat alert.
    """

    api_url = "https://cloud.frappe.io/api/method/press.api.client.get"
    payload = {
        "doctype": "Site",
        "name": site_name,
    }

    headers = {
        "Authorization": f"token {FC_API_KEY}:{FC_API_SECRET}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(api_url, json=payload, headers=headers, timeout=PRESS_API_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()

        update_info = data.get("message", {}).get("update_information", {})
        update_available = update_info.get("update_available")

        if update_available:
            # 🚨 Site requires manual update
            message = {
                "cardsV2": [
                    {
                        "cardId": "site-update-alert",
                        "card": {
                            "header": {
                                "title": f"⚠️ [{env}] Site Update Required",
                                "subtitle": f"Site: {site_name}",
                                "imageUrl": "https://cdn-icons-png.flaticon.com/128/595/595067.png",
                                "imageType": "CIRCLE",
                            },
                            "sections": [
                                {
                                    "widgets": [
                                        {"decoratedText": {"topLabel": "Status", "text": "🚨 Site has pending updates"}},
                                        {
                                            "decoratedText": {
                                                "topLabel": "Action Required",
                                                "text": "Please manually update this site on Frappe Cloud.",
                                            }
                                        },
                                    ]
                                }
                            ],
                        },
                    }
                ]
            }

            # Send to Google Chat
            try:
                res = requests.post(GOOGLE_CHAT_WEBHOOK, json=message, timeout=GOOGLE_CHAT_TIMEOUT_SECONDS)
                res_json = res.json() if res.content else {}
                chat_thread_id = res_json.get("thread", {}).get("name")
                set_chat_thread(env, chat_thread_id)
                log.warning("[%s] Site %r requires manual update. Alert sent to Google Chat.", env, site_name)
            except Exception:
                log.exception("[%s] Failed to send Google Chat alert for site %r", env, site_name)

            return False

        log.info("[%s] Site %r is up to date.", env, site_name)
        return True

    except requests.exceptions.RequestException as e:
        log.error("[%s] Failed to check update status for site %r: %s", env, site_name, e)
        return False
    except Exception:
        log.exception("[%s] Unexpected error while checking site update status for %r", env, site_name)
        return False


def detect_deploy_failure(env: str):
    """
    Inspect Frappe Cloud notifications & candidate build to find failure details,
    post a detailed failure card to Google Chat, and return (payload, status_code).
    """
    try:
        state, apps, candidate, chat_thread_id = get_state(env)
        if state != "in_progress" or not candidate:
            return {"status": "idle", "message": "No active deployment to check"}, 202

        headers = {"Authorization": f"token {FC_API_KEY}:{FC_API_SECRET}", "Content-Type": "application/json"}

        # Step 1: look for Press Notification errors for the candidate
        url_list = "https://cloud.frappe.io/api/method/press.api.client.get_list"
        payload_list = {
            "doctype": "Press Notification",
            "fields": ["title", "name"],
            "filters": {
                "document_type": "Deploy Candidate Build",
                "document_name": candidate,
                "is_actionable": True,
                "class": "Error",
            },
            "limit": 20,
        }
        resp = requests.post(url_list, headers=headers, json=payload_list, timeout=PRESS_API_TIMEOUT_SECONDS)
        resp.raise_for_status()
        notifications = resp.json().get("message", [])

        if notifications:
            notif_id = notifications[0]["name"]
            url_get = "https://cloud.frappe.io/api/method/press.api.client.get"
            payload_get = {"doctype": "Press Notification", "name": notif_id}
            resp2 = requests.post(url_get, headers=headers, json=payload_get, timeout=PRESS_API_TIMEOUT_SECONDS)
            resp2.raise_for_status()
            notif_detail = resp2.json().get("message", {})

            title = notif_detail.get("title")
            html_message = notif_detail.get("message")
            traceback_info = notif_detail.get("traceback", "")

            # Build plain-text error summary and detailed failure card
            error_text = format_failure_message(env, candidate, title, html_message, traceback_info)
            failure_card = build_card_failure_detailed(env.capitalize(), candidate, title, html_message, traceback_info, apps or [])

            if GOOGLE_CHAT_WEBHOOK:
                res = requests.post(GOOGLE_CHAT_WEBHOOK, json=failure_card, timeout=GOOGLE_CHAT_TIMEOUT_SECONDS)
                try:
                    log.info("Posted failure card: %s", res.json())
                except ValueError:
                    log.info("Posted failure card (no json returned): status %s", res.status_code)

            return {"status": "failure", "error": error_text}, 202

        # Step 2: fallback check candidate build steps/status
        url_candidate = "https://cloud.frappe.io/api/method/press.api.client.get"
        payload_candidate = {"doctype": "Deploy Candidate Build", "name": candidate}
        resp3 = requests.post(url_candidate, headers=headers, json=payload_candidate, timeout=PRESS_API_TIMEOUT_SECONDS)
        resp3.raise_for_status()
        candidate_info = resp3.json().get("message", {})

        status_val = candidate_info.get("status")
        steps = candidate_info.get("build_steps", [])

        failed_step = None
        for step in steps:
            if step.get("status") == "Failure":
                failed_step = f"{step.get('stage')} → {step.get('step')}"
                break

        if status_val == "Failure":
            failure_card = build_card_failure(env.capitalize(), candidate, failed_step or "unknown (check logs)", apps or [])
            if GOOGLE_CHAT_WEBHOOK:
                requests.post(GOOGLE_CHAT_WEBHOOK, json=failure_card, timeout=GOOGLE_CHAT_TIMEOUT_SECONDS)
            return {"status": "failure", "error": "Candidate build failure detected"}, 202

        return {"status": "Normal", "error": "No Error Detected Yet"}, 200

    except Exception as exc:
        log.exception("Error checking deploy failure")
        return {"status": "error", "message": str(exc)}, 500

