from ..config import ENV_ICONS
from ..utils import html_to_plain_text


def build_card_success(env: str, data: dict, apps: list):
    """
    Card shown when deployment completed successfully.
    """
    doctype_name = data.get("doctype")
    name = data.get("name")
    time = data.get("modified")

    image_url = ENV_ICONS.get(env, "https://cdn-icons-png.freepik.com/512/6562/6562824.png")
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
                                            "url": app.get("repo", "").rstrip("/")
                                            + "/commit/"
                                            + app.get("Last Commit Hash", "")
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
                        "imageUrl": image_url,
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
    image_url = ENV_ICONS.get(env, "https://cdn-icons-png.freepik.com/512/6562/6562824.png")

    return {
        "cardsV2": [
            {
                "cardId": "frappe-cloud-deploy-failed",
                "card": {
                    "header": {
                        "title": f"🚀 [{env}] Deployment Failed",
                        "subtitle": f"❌ Candidate: {candidate}",
                        "imageUrl": image_url,
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
                                                    "onClick": {
                                                        "openLink": {
                                                            "url": app.get("repo", "").rstrip("/")
                                                            + "/commit/"
                                                            + app.get("Last Commit Hash", "")
                                                        }
                                                    },
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
        tb = tb[: max_tb // 2] + "\n\n...[truncated]...\n\n" + tb[-max_tb // 2 :]

    # Convert html_message to plain text and escape
    plain_msg = html_to_plain_text(html_message)
    image_url = ENV_ICONS.get(env, "https://cdn-icons-png.freepik.com/512/6562/6562824.png")

    return {
        "cardsV2": [
            {
                "cardId": "frappe-cloud-deploy-failure-detailed",
                "card": {
                    "header": {
                        "title": f"🚀 [{env}] Deployment Failed",
                        "subtitle": f"❌ Candidate: {candidate}",
                        "imageUrl": image_url,
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
                        {"header": "Traceback:", "widgets": [{"textParagraph": {"text": f"<pre>{tb}</pre>"}}]},
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
                                                    "onClick": {
                                                        "openLink": {
                                                            "url": app.get("repo", "").rstrip("/")
                                                            + "/commit/"
                                                            + app.get("Last Commit Hash", "")
                                                        }
                                                    },
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

