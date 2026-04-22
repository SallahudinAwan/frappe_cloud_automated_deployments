import logging

import requests
from flask import Blueprint, jsonify, request

from .cards import github_pr_card
from ..config import (
    GITHUB_API_TIMEOUT_SECONDS,
    GOOGLE_CHAT_TIMEOUT_SECONDS,
    GOOGLE_CHAT_WEBHOOK_GITHUB,
    GOOGLE_CHAT_WEBHOOK_TESTING,
    GITHUB_TOKEN,
)
from ..db import get_github_db_state, get_thread_id_from_repo_and_branch, insert_github_db_state
from ..utils import to_pakistan_time

log = logging.getLogger(__name__)

bp = Blueprint("github", __name__)


@bp.route("/github-webhook-v2", methods=["POST"])
def handle_github_webhook_v2():
    """
    Handle GitHub webhooks. Supports:
    - pull_request (opened/closed) -> sends a textual notification
    - workflow_run (failures/success) -> sends a textual notification in PR thread if possible
    """
    try:
        event = request.headers.get("X-GitHub-Event", "unknown")
        payload = request.get_json(force=True)
        # ----------------------------
        # Pull Request Handling
        # ----------------------------
        if event == "pull_request":
            action = payload.get("action")
            pr = payload.get("pull_request", {})
            repo = payload.get("repository", {})

            if action in ("opened", "closed"):
                is_merged = pr.get("merged", False)
                status_text = "Merged ✅" if is_merged else ("Closed ❌" if action == "closed" else "Opened 🟢")

                actor = payload.get("sender", {}).get("login", "unknown")
                time_raw = pr.get("merged_at") or pr.get("closed_at") or pr.get("created_at")
                time = to_pakistan_time(time_raw)
                pr_title = pr.get("title", "")
                from_branch = pr.get("head", {}).get("ref", "")
                to_branch = pr.get("base", {}).get("ref", "")
                pr_url = pr.get("html_url", "")
                repo_name = repo.get("full_name", "")
                github_card = github_pr_card(
                    repo_name, "Pull Request " + status_text, pr_title, from_branch, to_branch, actor, time, pr_url
                )

                if action == "opened":
                    res = requests.post(
                        GOOGLE_CHAT_WEBHOOK_TESTING, json=github_card, timeout=GOOGLE_CHAT_TIMEOUT_SECONDS
                    ).json()
                    insert_github_db_state(pr.get("id"), res.get("thread", {}).get("name"), repo_name, to_branch)
                else:
                    _, thread_id = get_github_db_state(str(pr.get("id")))
                    github_card["thread"] = {"name": thread_id}
                    requests.post(
                        GOOGLE_CHAT_WEBHOOK_TESTING + "&messageReplyOption=REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD",
                        json=github_card,
                        timeout=GOOGLE_CHAT_TIMEOUT_SECONDS,
                    )
        elif event == "workflow_run":
            run = payload.get("workflow_run", {})
            workflow_name = payload.get("workflow", {}).get("name", "Unknown Workflow")
            conclusion = run.get("conclusion")

            if conclusion in ("failure", "success"):
                actor = run.get("actor", {}).get("login", "unknown")
                repo = payload.get("repository", {}).get("full_name", "")
                url = run.get("html_url", "")

                utc_time = run.get("updated_at") or run.get("created_at")
                time = to_pakistan_time(utc_time)

                pr_info = run.get("pull_requests", [])
                thread_id = None

                if pr_info:
                    # Workflow directly linked to PR
                    pr_id = pr_info[0].get("id")
                    _, thread_id = get_github_db_state(str(pr_id))
                else:
                    # Workflow triggered by push, use latest thread for repo
                    branch_name = run.get("head_branch")
                    _, thread_id = get_thread_id_from_repo_and_branch(repo, branch_name)

                success_or_failure = ""
                if conclusion == "failure":
                    success_or_failure = "🚨 Workflow Failed"
                elif conclusion == "success":
                    success_or_failure = "✅ Workflow Successful"

                message = {
                    "cardsV2": [
                        {
                            "cardId": f"github-workflow-{run.get('id')}",
                            "card": {
                                "header": {
                                    "title": f"{workflow_name} - {repo}",
                                    "subtitle": success_or_failure,
                                    "imageUrl": "https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png",
                                    "imageType": "CIRCLE",
                                },
                                "sections": [
                                    {
                                        "widgets": [
                                            {"decoratedText": {"topLabel": "Status", "text": conclusion.capitalize()}},
                                            {"decoratedText": {"topLabel": "Triggered By", "text": actor}},
                                            {"decoratedText": {"topLabel": "Time", "text": time}},
                                            {"decoratedText": {"topLabel": "URL", "text": ""}},
                                            {
                                                "buttonList": {
                                                    "buttons": [
                                                        {
                                                            "text": url,
                                                            "onClick": {"openLink": {"url": url}},
                                                        }
                                                    ]
                                                }
                                            },
                                        ]
                                    }
                                ],
                            },
                        }
                    ]
                }

                # Post to thread if available
                if thread_id:
                    message["thread"] = {"name": thread_id}
                    requests.post(
                        GOOGLE_CHAT_WEBHOOK_TESTING + "&messageReplyOption=REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD",
                        json=message,
                        timeout=GOOGLE_CHAT_TIMEOUT_SECONDS,
                    )
                else:
                    requests.post(GOOGLE_CHAT_WEBHOOK_TESTING, json=message, timeout=GOOGLE_CHAT_TIMEOUT_SECONDS)

        return jsonify({"status": "ok"}), 200
    except Exception as exc:
        log.exception("Error handling GitHub webhook")
        return jsonify({"status": "error", "message": str(exc)}), 500


# Utility to find the recent merged PR for a branch
def find_recent_pr_for_branch(repo, branch):
    try:
        url = f"https://api.github.com/repos/{repo}/pulls?state=closed&base={branch}&sort=updated&direction=desc"
        headers = {"Authorization": f"token {GITHUB_TOKEN}"}
        resp = requests.get(url, headers=headers, timeout=GITHUB_API_TIMEOUT_SECONDS)
        resp.raise_for_status()
        for pr in resp.json():
            if pr.get("merged_at"):
                return pr.get("id")
        return None
    except Exception:
        log.exception("Error finding recent PR for repo=%s base=%s", repo, branch)
        return None


@bp.route("/github-webhook", methods=["POST"])
def handle_github_webhook():
    """
    Handle GitHub webhooks. Supports:
      - pull_request (opened/closed) -> sends a textual notification
      - workflow_run (failures) -> sends a textual notification
    """
    try:
        event = request.headers.get("X-GitHub-Event", "unknown")
        payload = request.get_json(force=True)

        if event == "pull_request":
            action = payload.get("action")
            pr = payload.get("pull_request", {})
            repo = payload.get("repository", {})

            if action in ("opened", "closed"):
                is_merged = pr.get("merged", False)
                status_text = "Merged ✅" if is_merged else ("Closed ❌" if action == "closed" else "Opened 🟢")

                actor = payload.get("sender", {}).get("login", "unknown")
                time_raw = pr.get("merged_at") or pr.get("closed_at") or pr.get("created_at")
                time = to_pakistan_time(time_raw)
                pr_title = pr.get("title", "")
                from_branch = pr.get("head", {}).get("ref", "")
                to_branch = pr.get("base", {}).get("ref", "")
                pr_url = pr.get("html_url", "")
                repo_name = repo.get("full_name", "")

                message = (
                    f"🔔 *Pull Request {status_text}*\n"
                    f"📌 *Title*: {pr_title}\n"
                    f"🔀 *Branch*: {from_branch} → {to_branch}\n"
                    f"👤 *Actor*: {actor}\n"
                    f"🗓️ *Time*: {time}\n"
                    f"📂 *Repository*: {repo_name}\n"
                    f"🔗 {pr_url}"
                )
                if GOOGLE_CHAT_WEBHOOK_GITHUB:
                    requests.post(
                        GOOGLE_CHAT_WEBHOOK_GITHUB, json={"text": message}, timeout=GOOGLE_CHAT_TIMEOUT_SECONDS
                    )
                else:
                    log.info("No testing webhook set; PR notification skipped.")

        elif event == "workflow_run":
            run = payload.get("workflow_run", {})
            workflow_name = payload.get("workflow", {}).get("name", "Unknown Workflow")
            conclusion = run.get("conclusion")

            if conclusion in ("failure", "success"):
                actor = run.get("actor", {}).get("login", "unknown")
                repo = payload.get("repository", {}).get("full_name", "")
                url = run.get("html_url", "")

                utc_time = run.get("updated_at") or run.get("created_at")
                time = to_pakistan_time(utc_time)

                pr_info = run.get("pull_requests", [])
                if pr_info:
                    pr_number = pr_info[0].get("number")
                    pr_url = f"https://github.com/{repo}/pull/{pr_number}"
                    pr_title = pr_info[0].get("title", f"PR #{pr_number}")
                    pr_text = f"\n📌 *PR*: [{pr_title}]({pr_url})"
                else:
                    pr_text = ""
                success_or_failure = ""
                if conclusion == "failure":
                    success_or_failure = "🚨 *Workflow Failed*\n"
                elif conclusion == "success":
                    success_or_failure = "✅ *Workflow Successful*\n"
                message = (
                    f"{success_or_failure}"
                    f"⚙️ *Workflow*: {workflow_name}\n"
                    f"📂 *Repository*: {repo}\n"
                    f"👤 *Triggered By*: {actor}\n"
                    f"🗓️ *Time*: {time}\n"
                    f"🔗 {url}"
                    f"{pr_text}"
                )
                if GOOGLE_CHAT_WEBHOOK_GITHUB:
                    requests.post(
                        GOOGLE_CHAT_WEBHOOK_GITHUB, json={"text": message}, timeout=GOOGLE_CHAT_TIMEOUT_SECONDS
                    )
                else:
                    log.info("No testing webhook set; workflow failure notification skipped.")

        return jsonify({"status": "ok"}), 200

    except Exception as exc:
        log.exception("Error handling GitHub webhook")
        return jsonify({"status": "error", "message": str(exc)}), 500

