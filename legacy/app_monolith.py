#!/usr/bin/env python3
"""
LEGACY (monolithic) implementation.

Frappe Cloud → Google Chat Middleware

This single-file Flask app listens for:
 - GitHub webhooks (pull_request, workflow_run)
 - Frappe Cloud webhooks (Deploy Candidate Build, Bench, Site, etc.)
 - Provides helper endpoints to trigger/check deployments

Features / improvements in this refactor:
 - Clearer function names and docstrings
 - Structured logging instead of print()
 - Safer JSON parsing and defensive checks
 - Proper DB handling with SQLAlchemy
 - Utilities to convert HTML -> plain text for messages
 - Truncation/escaping for long tracebacks
 - Google Chat card builders refined and validated
"""

from datetime import datetime
import html
import json
import logging
import os
import re
from zoneinfo import ZoneInfo

import auto_deploy
import subprocess
import sys

import requests
from flask import Flask, jsonify, request
from sqlalchemy import create_engine, text

# ------------------------
# Configuration / Logging
# ------------------------
app = Flask(__name__)

# Environment variables (required / optional)
GOOGLE_CHAT_WEBHOOK = os.getenv("GOOGLE_CHAT_WEBHOOK")
GOOGLE_CHAT_WEBHOOK_TESTING = os.getenv("GOOGLE_CHAT_WEBHOOK_TESTING")  # keep existing name for compatibility
GOOGLE_CHAT_WEBHOOK_GITHUB = os.getenv("GOOGLE_CHAT_WEBHOOK_GITHUB")  # keep existing name for compatibility
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
FC_API_KEY = os.getenv("FC_API_KEY")
FC_API_SECRET = os.getenv("FC_API_SECRET")

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ------------------------
# Database Engine (SQLAlchemy)
# ------------------------
# Keep behavior similar: use connection pooling but safe defaults
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    # Note: original had pool_recycle/pool_size; keep moderate defaults
    pool_recycle=300,
    pool_size=5,
    max_overflow=10
)

# ------------------------
# Environment mappings & Allowed statuses
# ------------------------
SITE_ENV_MAP = {
    # NOTE: placeholder values (do not use in production).
    "your-staging-site.example.com": "Staging",
    "your-preview-site.example.com": "Preview",
    "your-production-site.example.com": "Production",
    "your-v16-site.example.com": "Version16",
}

BENCH_ENV_MAP = {
    # NOTE: placeholder values (do not use in production).
    "bench-staging-id": "Staging",
    "bench-preview-id": "Preview",
    "bench-production-id": "Production",
    "bench-v16-id": "Version16"
}

ALLOWED_STATUS_MAP = {
    # Allowed statuses for each doctype — used to filter webhook noise
    "Bench": {"Installing", "Updating", "Active", "Broken"},
    "Site": {"Pending", "Installing", "Updating", "Active", "Inactive", "Broken", "Archived", "Suspended"},
    "Deploy Candidate Build": {"Draft", "Scheduled", "Running", "Success", "Failure"},
}

ENV_ICONS = {
    "Staging": "https://cdn-icons-png.freepik.com/512/6562/6562824.png?uid=R218549038&ga=GA1.1.1901556257.1760382396",
    "Preview": "https://cdn-icons-png.freepik.com/512/6561/6561218.png?uid=R218549038&ga=GA1.1.1901556257.1760382396",
    "Production": "https://cdn-icons-png.freepik.com/512/6561/6561171.png?uid=R218549038&ga=GA1.1.1901556257.1760382396",
    "Version16": "https://cdn-icons-png.freepik.com/512/16695/16695467.png?ga=GA1.1.1901556257.1760382396"
}    


# ------------------------
# DB helpers
# ------------------------
def init_db() -> None:
    """
    Create the `deployment_lock` table if not exists and ensure rows for Staging/Preview/Production.
    """
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS deployment_lock (
                id VARCHAR(20) PRIMARY KEY,
                state VARCHAR(20) NOT NULL DEFAULT 'idle',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                apps_deployed JSONB,
                current_deploy_candidate VARCHAR(100),
                chat_thread_id TEXT
            )
        """))
        for env in ["Staging", "Preview", "Production"]:
            result = conn.execute(text("SELECT COUNT(*) FROM deployment_lock WHERE id = :env"), {"env": env})
            if result.scalar() == 0:
                conn.execute(text("INSERT INTO deployment_lock (id, state) VALUES (:env, 'idle')"), {"env": env})
    log.info("DB initialized / ensured deployment_lock rows")


def get_state(environment_name: str):
    """
    Return tuple (state, apps_deployed, current_deploy_candidate, chat_thread_id)
    or (None, None, None, None) if missing.
    """
    with engine.begin() as conn:
        result = conn.execute(
            text("SELECT state, apps_deployed, current_deploy_candidate, chat_thread_id FROM deployment_lock WHERE id = :env"),
            {"env": environment_name}
        )
        row = result.fetchone()
        if not row:
            return (None, None, None, None)
        return (row[0], row[1], row[2], row[3])


def set_state(environment_name: str, new_state: str, apps_deployed=None, deploy_candidate=None, chat_thread_id=None) -> None:
    """
    Update deployment_lock row. apps_deployed may be JSON-serializable or None.
    """
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE deployment_lock
                SET state = :state,
                    apps_deployed = :apps_deployed,
                    current_deploy_candidate = :deploy_candidate,
                    updated_at = CURRENT_TIMESTAMP,
                    chat_thread_id = :chat_thread_id
                WHERE id = :env
            """),
            {
                "state": new_state,
                "apps_deployed": json.dumps(apps_deployed) if apps_deployed is not None else None,
                "deploy_candidate": deploy_candidate,
                "env": environment_name,
                "chat_thread_id": chat_thread_id
            }
        )
    log.info("set_state(%s -> %s) chat_thread=%s", environment_name, new_state, chat_thread_id)


def get_github_db_state(pr_id: str):
    with engine.begin() as conn:
        result = conn.execute(
            text("SELECT pr_id, google_thread_id FROM github_db WHERE pr_id = :pr_id"),
            {"pr_id": pr_id}
        )
        row = result.fetchone()
        if not row:
            return (None, None)
        log.info("set_pr_id(%s) chat_thread=%s", row[0], row[1])
        return (row[0], row[1])

def get_thread_id_from_repo_and_branch(repo_name: str, branch_name: str):
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                SELECT pr_id, google_thread_id 
                FROM github_db 
                WHERE repo_name = :repo_name 
                ORDER BY id DESC
                LIMIT 1
            """),
            {"repo_name": repo_name}
        )

        row = result.fetchone()
        if not row:
            return (None, None)

        log.info("LATEST pr_id(%s) chat_thread=%s", row[0], row[1])
        return (row[0], row[1])

def insert_github_db_state(pr_id: int, google_thread_id: str, repo_name: str,to_branch: str):
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO github_db (pr_id, google_thread_id,repo_name,branch_name)
                VALUES (:pr_id, :google_thread_id,:repo_name,:branch_name)
            """),
            {"pr_id": f"{pr_id}", "google_thread_id": google_thread_id,"repo_name":repo_name,"branch_name":to_branch}
        )
        log.info("set_pr_id(%s) chat_thread=%s repo_name=%s branch_name=%s", pr_id, google_thread_id,repo_name,to_branch)



def set_chat_thread(environment_name: str, chat_thread_id=None) -> None:
    """
    Update deployment_lock row. apps_deployed may be JSON-serializable or None.
    """
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE deployment_lock
                SET chat_thread_id = :chat_thread_id
                WHERE id = :env
            """),
            {
                "env": environment_name,
                "chat_thread_id": chat_thread_id,
            }
        )
    log.info("set_chat_thread(%s) chat_thread=%s", environment_name, chat_thread_id)

# ------------------------
# Utility functions
# ------------------------
def to_pakistan_time(utc_time_str: str) -> str:
    """
    Convert ISO utc time string (with Z) to Pakistan timezone formatted string.
    """
    if not utc_time_str:
        return ""
    dt_utc = datetime.fromisoformat(utc_time_str.replace("Z", "+00:00"))
    dt_pkt = dt_utc.astimezone(ZoneInfo("Asia/Karachi"))
    return dt_pkt.strftime("%Y-%m-%d %H:%M:%S")


def html_to_plain_text(html_content: str) -> str:
    """
    Convert small HTML snippets to plain text preserving paragraphs.
    - unescape entities
    - convert <p>, <br>, <li>, header tags to newlines
    - remove remaining tags
    - collapse whitespace and return tidy paragraphs
    """
    if not html_content:
        return ""
    text = html.unescape(html_content)
    # Convert block tags to newlines
    text = re.sub(r'(?i)</?(p|div|br|li|ul|ol|h[1-6])[^>]*>', '\n', text)
    # Remove remaining tags
    text = re.sub(r'<[^>]+>', '', text)
    # Normalize line endings and collapse multiple blank lines
    text = re.sub(r'\r\n?', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Collapse consecutive spaces
    text = re.sub(r'[ \t]{2,}', ' ', text)
    # Strip and keep non-empty lines
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n\n".join(lines)


def format_failure_message(env: str, candidate: str, title: str, html_message: str, traceback_text: str, max_traceback_chars: int = 2000) -> str:
    """
    Build a clean textual message summarizing the failure for plain-text notifications.
    This returns markdown-like text with a code block for traceback.
    """
    plain_msg = html_to_plain_text(html_message)
    tb = (traceback_text or "").strip()

    # If traceback is large, keep head and tail with a truncated marker
    if len(tb) > max_traceback_chars:
        half = max_traceback_chars // 2
        tb = tb[:half] + "\n\n...[truncated]...\n\n" + tb[-half:]

    # Escape triple backticks in the traceback to avoid breaking code fences
    tb = tb.replace("```", "`\u200b``")  # insert zero-width char

    parts = []
    if title:
        parts.append(f"*Error:* {title}")
    if plain_msg:
        parts.append("\n*Details:*\n" + plain_msg)
    if tb:
        parts.append("\n*Traceback:*\n```\n" + tb + "\n```")

    return "\n\n".join(parts)


# ------------------------
# Google Chat card builders
# ------------------------
def build_card_success(env: str, data: dict, apps: list):
    """
    Card shown when deployment completed successfully.
    """
    doctype_name = data.get("doctype")
    name = data.get("name")
    time = data.get("modified")

    imageURL = ENV_ICONS.get(env, "https://cdn-icons-png.freepik.com/512/6562/6562824.png")
    # Base card sections
    sections = [
        {
            "widgets": [
                {"decoratedText": {"topLabel": doctype_name, "text": name}},
                {"decoratedText": {"topLabel": "Time", "text": time}},
            ]
        }
    ]

    # Add "Apps Deployed" section only if apps exist
    if apps and len(apps) > 0:
        app_section = {
            "header": "Apps Deployed",
            "collapsible": True,
            "uncollapsibleWidgetsCount": 2,
            "widgets": [
                widget
                for app in apps
                for widget in [
                    {"decoratedText": {"topLabel": app.get("app", ""), "text": ""}},
                    {
                        "buttonList": {
                            "buttons": [
                                {
                                    "text": app.get("last Commit Message", "View Commit"),
                                    "onClick": {
                                        "openLink": {
                                            "url": app.get("repo", "").rstrip("/") + "/commit/" + app.get("Last Commit Hash", "")
                                        }
                                    },
                                }
                            ]
                        }
                    },
                ]
            ],
        }
        sections.append(app_section)

    return {
        "cardsV2": [
            {
                "cardId": "frappe-cloud-deploy-success",
                "card": {
                    "header": {
                        "title": f"🚀 [{env}] Automated Deployment Alert",
                        "subtitle": "Deployment Completed ✅",
                        "imageUrl": imageURL,
                        "imageType": "CIRCLE",
                    },
                    "sections": sections,
                },
            }
        ],
    }

def build_card_normal(env: str, event: str, data: dict, thread_id: str):
    """
    Generic card for normal events (Bench / Site updates).
    """
    doctype_name = data.get("doctype")
    name = data.get("name")
    status = data.get("status")
    modified_by = data.get("modified_by")
    time = data.get("modified")
    return {
        "thread": {"name": thread_id},
        "cardsV2": [
            {
                "cardId": "frappe-cloud-normal",
                "card": {
                    "header": {
                        "title": f"[{env}] Frappe Cloud",
                        "subtitle": event,
                        "imageUrl": "https://cdn.brandfetch.io/idUkiQgw2e/w/400/h/400/theme/dark/icon.png?c=1dxbfHSJFAPEGdCLU4o5B",
                        "imageType": "CIRCLE",
                    },
                    "sections": [
                        {
                            "widgets": [
                                {"decoratedText": {"topLabel": doctype_name, "text": name}},
                                {"decoratedText": {"topLabel": "Status", "text": status}},
                                {"decoratedText": {"topLabel": "Time", "text": time}},
                                {"decoratedText": {"topLabel": "Modified By", "text": modified_by}},
                            ]
                        }
                    ],
                },
            }
        ],
    }


def build_card_failure(env: str, candidate: str, failed_step: str, apps: list):
    """
    Simple failure card listing the failed step and apps.
    """
    imageURL = ENV_ICONS.get(env, "https://cdn-icons-png.freepik.com/512/6562/6562824.png")
    
    return {
        "cardsV2": [
            {
                "cardId": "frappe-cloud-deploy-failed",
                "card": {
                    "header": {
                        "title": f"🚀 [{env}] Deployment Failed",
                        "subtitle": f"❌ Candidate: {candidate}",
                        "imageUrl": imageURL,
                        "imageType": "CIRCLE",
                    },
                    "sections": [
                        {"widgets": [{"decoratedText": {"topLabel": "Failed at step", "text": failed_step}}]},
                        {
                            "header": "Apps Deployment Failed",
                            "collapsible": True,
                            "uncollapsibleWidgetsCount": 2,
                            "widgets": [
                                widget
                                for app in apps
                                for widget in [
                                    {"decoratedText": {"topLabel": app["app"], "text": ""}},
                                    {
                                        "buttonList": {
                                            "buttons": [
                                                {
                                                    "text": app.get("last Commit Message", ""),
                                                    "onClick": {"openLink": {"url": app.get("repo", "").rstrip("/") + "/commit/" + app.get("Last Commit Hash", "")}}
                                                }
                                            ]
                                        }
                                    },
                                ]
                            ],
                        },
                    ],
                },
            }
        ]
    }


def build_card_failure_detailed(env: str, candidate: str, title: str, html_message: str, traceback_info: str, apps: list):
    """
    Detailed failure card: shows error summary and a 'pre' formatted traceback block.
    Uses textParagraph with <pre> to preserve formatting.
    """
    # Ensure traceback isn't huge (avoid exceeding payload limits)
    max_tb = 5000
    tb = traceback_info or ""
    if len(tb) > max_tb:
        tb = tb[:max_tb // 2] + "\n\n...[truncated]...\n\n" + tb[-max_tb // 2 :]

    # Convert html_message to plain text and escape
    plain_msg = html_to_plain_text(html_message)
    imageURL = ENV_ICONS.get(env, "https://cdn-icons-png.freepik.com/512/6562/6562824.png")
            
    return {
        "cardsV2": [
            {
                "cardId": "frappe-cloud-deploy-failure-detailed",
                "card": {
                    "header": {
                        "title": f"🚀 [{env}] Deployment Failed",
                        "subtitle": f"❌ Candidate: {candidate}",
                        "imageUrl": imageURL,
                        "imageType": "CIRCLE",
                    },
                    "sections": [
                        {
                            "widgets": [
                                {
                                    "textParagraph": {
                                        "text": (
                                            f"<b>Error:</b> {title or 'Unknown'}<br><br>"
                                            f"<b>Details:</b><br>{plain_msg}<br><br>"
                                            f"To rectify this, please fix the issue mentioned below and push a new update."
                                        )
                                    }
                                }
                            ]
                        },
                        {
                            "header": "Traceback:",
                            "widgets": [
                                {
                                    "textParagraph": {
                                        "text": f"<pre>{tb}</pre>"
                                    }
                                }
                            ]
                        },
                        {
                            "header": "Apps Deployment Failed",
                            "collapsible": True,
                            "uncollapsibleWidgetsCount": 2,
                            "widgets": [
                                widget
                                for app in apps
                                for widget in [
                                    {"decoratedText": {"topLabel": app["app"], "text": ""}},
                                    {
                                        "buttonList": {
                                            "buttons": [
                                                {
                                                    "text": app.get("last Commit Message", ""),
                                                    "onClick": {"openLink": {"url": app.get("repo", "").rstrip("/") + "/commit/" + app.get("Last Commit Hash", "")}}
                                                }
                                            ]
                                        }
                                    },
                                ]
                            ],
                        },
                    ],
                },
            }
        ]
    }


# ------------------------
# Flask routes (webhooks, endpoints)
# ------------------------
@app.route("/", methods=["GET"])
def home():
    return "✅ Frappe Cloud → Google Chat Middleware is running!"


def github_pr_card(repo_name,status_text,pr_title,from_branch,to_branch,actor,time,pr_url):
    card = {
        "cardsV2": [
            {
                "cardId": "github-pr-start",
                "card": {
                    "header": {
                        "title": f"{repo_name}",
                        "subtitle": status_text,
                        "imageUrl": "https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png",
                        "imageType": "CIRCLE"
                    },
                    "sections": [
                        {
                            "widgets": [
                                {"decoratedText": {"topLabel": "PR Title", "text": f"{pr_title}"}},
                                {"decoratedText": {"topLabel": "From to Branch", "text": f"{from_branch} to {to_branch}"}},
                                {"decoratedText": {"topLabel": "Actor", "text": actor}},
                                {"decoratedText": {"topLabel": "Time", "text": time}},
                            ]
                        },
                        {
                            "widgets": [
                                {"decoratedText": {"topLabel": "PR Link", "text": ""}},
                                {
                                    "buttonList": {
                                        "buttons": [
                                            {
                                                "text": pr_url,
                                                "onClick": {
                                                    "openLink": {
                                                        "url": pr_url
                                                    }
                                                }
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    ]
                }
            }
        ]
    }

    return card


def github_workflow_card(repo_name,status_text,pr_title,from_branch,to_branch,actor,time,pr_url):
    card = {
        "cardsV2": [
            {
                "cardId": "github-pr-start",
                "card": {
                    "header": {
                        "title": f"{repo_name}",
                        "subtitle": status_text,
                        "imageUrl": "https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png",
                        "imageType": "CIRCLE"
                    },
                    "sections": [
                        {
                            "widgets": [
                                {"decoratedText": {"topLabel": "PR Title", "text": f"{pr_title}"}},
                                {"decoratedText": {"topLabel": "From to Branch", "text": f"{from_branch} to {to_branch}"}},
                                {"decoratedText": {"topLabel": "Actor", "text": actor}},
                                {"decoratedText": {"topLabel": "Time", "text": time}},
                            ]
                        },
                        {
                            "widgets": [
                                {"decoratedText": {"topLabel": "PR Link", "text": ""}},
                                {
                                    "buttonList": {
                                        "buttons": [
                                            {
                                                "text": pr_url,
                                                "onClick": {
                                                    "openLink": {
                                                        "url": pr_url
                                                    }
                                                }
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    ]
                }
            }
        ]
    }

    return card

@app.route("/github-webhook-v2", methods=["POST"])
def github_webhook_v2():
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
                github_card = github_pr_card(repo_name,"Pull Request "+status_text,pr_title,from_branch,to_branch,actor,time,pr_url)

                if action == "opened":
                    res = requests.post(GOOGLE_CHAT_WEBHOOK_TESTING, json=github_card).json()
                    insert_github_db_state(pr.get("id"),res.get("thread", {}).get("name"),repo_name,to_branch)
                else:
                    pr_id,thread_id = get_github_db_state(str(pr.get("id")))
                    github_card["thread"] = {"name": thread_id}
                    requests.post(GOOGLE_CHAT_WEBHOOK_TESTING + "&messageReplyOption=REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD", json=github_card)
        elif event == "workflow_run":
            run = payload.get("workflow_run", {})
            workflow_name = payload.get("workflow", {}).get("name", "Unknown Workflow")
            conclusion = run.get("conclusion")
            status = run.get("status")

            if conclusion in ("failure","success"):
                actor = run.get("actor", {}).get("login", "unknown")
                repo = payload.get("repository", {}).get("full_name", "")
                url = run.get("html_url", "")

                utc_time = run.get("updated_at") or run.get("created_at")
                time = to_pakistan_time(utc_time)

                pr_info = run.get("pull_requests", [])
                thread_id = None
                pr_text = ""

                if pr_info:
                    # Workflow directly linked to PR
                    pr_id = pr_info[0].get("id")
                    pr_number = pr_info[0].get("number")
                    pr_title = pr_info[0].get("title", f"PR #{pr_number}")
                    pr_url = f"https://github.com/{repo}/pull/{pr_number}"
                    _, thread_id = get_github_db_state(str(pr_id))
                    pr_text = f"\n📌 *PR*: [{pr_title}]({pr_url})"

                else:
                    # Workflow triggered by push, try to find recent merged PR for branch
                    branch_name = run.get("head_branch")
                    pr_id,thread_id = get_thread_id_from_repo_and_branch(repo,branch_name)
                        
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
                                    {"widgets": [
                                        {"decoratedText": {"topLabel": "Status", "text": conclusion.capitalize()}},
                                        {"decoratedText": {"topLabel": "Triggered By", "text": actor}},
                                        {"decoratedText": {"topLabel": "Time", "text": time}},
                                        {"decoratedText": {"topLabel": "URL", "text": ""}},
                                        {
                                            "buttonList": {
                                                "buttons": [
                                                    {
                                                        "text": url,
                                                        "onClick": {
                                                            "openLink": {
                                                                "url": url
                                                            }
                                                        }
                                                    }
                                                ]
                                            }
                                        }
                                    ]}
                                ]
                            }
                        }
                    ]
                }

                # Post to thread if available
                if thread_id:
                    message["thread"] = {"name": thread_id}
                    requests.post(GOOGLE_CHAT_WEBHOOK_TESTING + "&messageReplyOption=REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD", json=message)
                else:
                    requests.post(GOOGLE_CHAT_WEBHOOK_TESTING, json=message)

        return jsonify({"status": "ok"}), 200
    except Exception as exc:
        log.exception("Error handling GitHub webhook")
        return jsonify({"status": "error", "message": str(exc)}), 500

# Utility to find the recent merged PR for a branch
def find_recent_pr_for_branch(repo, branch):
    try:
        url = f"https://api.github.com/repos/{repo}/pulls?state=closed&base={branch}&sort=updated&direction=desc"
        headers = {"Authorization": f"token {GITHUB_TOKEN}"}
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        for pr in resp.json():
            if pr.get("merged_at"):
                return pr.get("id")
        return None
    except Exception as e:
        log.exception(f"Error finding recent PR for {repo}/{branch}: {e}")
        return None

@app.route("/github-webhook", methods=["POST"])
def github_webhook():
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
                    requests.post(GOOGLE_CHAT_WEBHOOK_GITHUB, json={"text": message})
                else:
                    log.info("No testing webhook set; PR notification skipped.")

        elif event == "workflow_run":
            run = payload.get("workflow_run", {})
            workflow_name = payload.get("workflow", {}).get("name", "Unknown Workflow")
            conclusion = run.get("conclusion")
            status = run.get("status")

            if conclusion in ("failure","success"):
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
                    requests.post(GOOGLE_CHAT_WEBHOOK_GITHUB, json={"text": message})
                else:
                    log.info("No testing webhook set; workflow failure notification skipped.")

        return jsonify({"status": "ok"}), 200

    except Exception as exc:
        log.exception("Error handling GitHub webhook")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/status/<env>", methods=["GET"])
def status(env):
    """
    Returns the current deployment_lock state for the requested environment.
    """
    state, apps, candidate, chat_thread_id = get_state(env)
    return jsonify({
        "environment": env,
        "state": state,
        "apps_deployed": apps,
        "current_deploy_candidate": candidate,
        "chat_thread_id": chat_thread_id
    })

def check_site_update_status(site_name: str, env: str):
    """
    Checks if the given Frappe Cloud site has any updates pending.
    If updates are available, sends a Google Chat alert.
    """

    api_url = "https://cloud.frappe.io/api/method/press.api.client.get"
    payload = {
        "doctype": "Site",
        "name": site_name
    }
    
    headers = {
        "Authorization": f"token {FC_API_KEY}:{FC_API_SECRET}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(api_url, data=json.dumps(payload), headers=headers, timeout=15)
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
                                "imageType": "CIRCLE"
                            },
                            "sections": [
                                {
                                    "widgets": [
                                        {
                                            "decoratedText": {
                                                "topLabel": "Status",
                                                "text": "🚨 Site has pending updates"
                                            }
                                        },
                                        {
                                            "decoratedText": {
                                                "topLabel": "Action Required",
                                                "text": "Please manually update this site on Frappe Cloud."
                                            }
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }

            # Send to Google Chat
            try:
                res = requests.post(GOOGLE_CHAT_WEBHOOK, json=message, timeout=10)
                res_json = res.json() if res.content else {}
                chat_thread_id = res_json.get("thread", {}).get("name")
                set_chat_thread(env,chat_thread_id)
                log.warning(f"[{env}] Site '{site_name}' requires manual update. Alert sent to Google Chat.")
            except Exception as e:
                log.error(f"[{env}] Failed to send Google Chat alert for site '{site_name}': {e}")

            return False

        else:
            log.info(f"[{env}] Site '{site_name}' is up to date.")
            return True

    except requests.exceptions.RequestException as e:
        log.error(f"[{env}] Failed to check update status for site '{site_name}': {e}")
        return False
    except Exception as e:
        log.exception(f"[{env}] Unexpected error while checking site update status for '{site_name}': {e}")
        return False


@app.route("/frappe-cloud-webhook", methods=["POST"])
def handle_webhook():
    """
    Main Frappe Cloud webhook handler.
    - Filters out uninteresting statuses
    - Maintains deployment_lock state
    - Posts updates to Google Chat
    """
    try:
        payload = request.get_json(force=True)
        log.info(payload)
        event = payload.get("event", "Unknown Event")
        data = payload.get("data", {}) or {}

        doctype = data.get("doctype")
        status_val = data.get("status")

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
            imageURL = ENV_ICONS.get(environment_name, "https://cdn-icons-png.freepik.com/512/6562/6562824.png")         
            manual_card = {
                "cardsV2": [
                    {
                        "cardId": "frappe-cloud-deploy-start-manual",
                        "card": {
                            "header": {
                                "title": f"🚀 [{environment_name}] Manual/Retry Deployment Alert",
                                "subtitle": "Deployment Started 🔄",
                                "imageUrl": imageURL,
                                "imageType": "CIRCLE",
                            }
                        }
                    }
                ]
            }
            if GOOGLE_CHAT_WEBHOOK:
                res = requests.post(GOOGLE_CHAT_WEBHOOK, json=manual_card)
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
            resp = requests.post(GOOGLE_CHAT_WEBHOOK + "&messageReplyOption=REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD", json=card_message)
            log.info("Posted normal card: %s %s", resp.status_code, resp.text)

        # If Site becomes Active, reset lock and send success card if apps info exists
        if data.get("doctype") == "Site" and data.get("status") == "Active":
            if check_site_update_status(data.get("name"), environment_name):
                apps_info_str = current_db_state[1]
                log.info("Apps: %s", apps_info_str)
                if GOOGLE_CHAT_WEBHOOK:
                    card_message = build_card_success(environment_name, data, apps_info_str)
                    resp = requests.post(GOOGLE_CHAT_WEBHOOK, json=card_message)
                    log.info("Posted success card: %s %s", resp.status_code, resp.text)
                    set_state(environment_name, "idle", None, None, None)

        # If Deploy Candidate Build failed, trigger check_deploy_failure to post detailed failure
        if doctype == "Deploy Candidate Build" and status_val == "Failure":
            # Run check_deploy_failure logic and post the failure card (function returns JSON response)
            try:
                check_resp = check_deploy_failure(environment_name)
                set_state(environment_name, "idle", None, None, None)
                log.info("check_deploy_failure result: %s", check_resp.get_json() if hasattr(check_resp, "get_json") else check_resp)
            except Exception:
                log.exception("Error while checking deploy failure")
        return "Webhook processed", 200

    except Exception as exc:
        log.exception("Error handling Frappe Cloud webhook")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/trigger-workflow/<env>", methods=["POST"])
def trigger_workflow(env):
    try:
        state, _, _, _ = get_state(env)
        if state == "in_progress":
            return jsonify({"status": "skipped", "message": "Deployment already running"}), 204

        data = request.get_json(force=True, silent=True) or {}
        # Clone current environment and inject DEPLOY_ENV
        env_vars = os.environ.copy()
        env_vars["DEPLOY_ENV"] = env.lower()  # 👈 inject here
        env_vars["ALLOWED_APPS_FROM_WORKFLOW"] = data.get("allowed_apps","")
        
        process = subprocess.Popen(
            [sys.executable, "auto_deploy.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env_vars  # 👈 pass it here
        )

        for line in process.stdout:
            log.info(line.strip())

        process.wait()

        return jsonify({"status": "success", "message": f"Deployment started for {env}"}), 200

    except Exception as e:
        log.info("❌ Error triggering deployment:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

# ------------------------
# Deploy failure checker (previously check_deploy_failure)
# ------------------------
@app.route("/check-deploy-failure/<env>", methods=["GET"])
def check_deploy_failure(env):
    """
    Inspect Frappe Cloud notifications & candidate build to find failure details,
    post a detailed failure card to Google Chat, and return JSON summarizing the detection.
    """
    try:
        state, apps, candidate, chat_thread_id = get_state(env)
        if state != "in_progress" or not candidate:
            return jsonify({"status": "idle", "message": "No active deployment to check"}), 202

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
        resp = requests.post(url_list, headers=headers, json=payload_list)
        resp.raise_for_status()
        notifications = resp.json().get("message", [])

        if notifications:
            notif_id = notifications[0]["name"]
            url_get = "https://cloud.frappe.io/api/method/press.api.client.get"
            payload_get = {"doctype": "Press Notification", "name": notif_id}
            resp2 = requests.post(url_get, headers=headers, json=payload_get)
            resp2.raise_for_status()
            notif_detail = resp2.json().get("message", {})

            title = notif_detail.get("title")
            html_message = notif_detail.get("message")
            traceback_info = notif_detail.get("traceback", "")

            # Build plain-text error summary and detailed failure card
            error_text = format_failure_message(env, candidate, title, html_message, traceback_info)
            failure_card = build_card_failure_detailed(env.capitalize(), candidate, title, html_message, traceback_info, apps or [])

            if GOOGLE_CHAT_WEBHOOK:
                res = requests.post(GOOGLE_CHAT_WEBHOOK, json=failure_card)
                try:
                    log.info("Posted failure card: %s", res.json())
                except ValueError:
                    log.info("Posted failure card (no json returned): status %s", res.status_code)

            return jsonify({"status": "failure", "error": error_text}), 202

        # Step 2: fallback check candidate build steps/status
        url_candidate = "https://cloud.frappe.io/api/method/press.api.client.get"
        payload_candidate = {"doctype": "Deploy Candidate Build", "name": candidate}
        resp3 = requests.post(url_candidate, headers=headers, json=payload_candidate)
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
                requests.post(GOOGLE_CHAT_WEBHOOK, json=failure_card)
            return jsonify({"status": "failure", "error": "Candidate build failure detected"}), 202

        return jsonify({"status": "Normal", "error": "No Error Detected Yet"}), 200

    except Exception as exc:
        log.exception("Error checking deploy failure")
        return jsonify({"status": "error", "message": str(exc)}), 500


# ------------------------
# App entrypoint
# ------------------------
if __name__ == "__main__":
    init_db()
    # Keep debug True for local dev parity with original; you can set to False in prod
    app.run(host="0.0.0.0", port=8080, debug=True)
