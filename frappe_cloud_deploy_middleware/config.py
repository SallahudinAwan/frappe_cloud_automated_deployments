import json
import logging
import os
from typing import Dict, Optional

log = logging.getLogger(__name__)


def _get_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("Invalid %s=%r; using default %s", name, raw, default)
        return default


def _get_json_env_map(name: str) -> Optional[Dict[str, str]]:
    """
    Read a mapping from an env var containing a JSON object.

    Example:
      SITE_ENV_MAP_JSON='{"your-site.example.com":"Staging"}'
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        log.warning("Invalid JSON in %s; ignoring override", name)
        return None

    if not isinstance(payload, dict):
        log.warning("%s must be a JSON object; ignoring override", name)
        return None

    cleaned: Dict[str, str] = {}
    for key, value in payload.items():
        if isinstance(key, str) and isinstance(value, str):
            cleaned[key] = value
        else:
            log.warning("%s contains non-string key/value; skipping an entry", name)

    return cleaned or None


# Environment variables (required / optional)
GOOGLE_CHAT_WEBHOOK = os.getenv("GOOGLE_CHAT_WEBHOOK")
GOOGLE_CHAT_WEBHOOK_TESTING = os.getenv("GOOGLE_CHAT_WEBHOOK_TESTING")  # keep existing name for compatibility
GOOGLE_CHAT_WEBHOOK_GITHUB = os.getenv("GOOGLE_CHAT_WEBHOOK_GITHUB")  # keep existing name for compatibility
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
FC_API_KEY = os.getenv("FC_API_KEY")
FC_API_SECRET = os.getenv("FC_API_SECRET")

# Environment mappings & Allowed statuses
SITE_ENV_MAP = {
    # NOTE: placeholder values (do not use in production).
    # Set SITE_ENV_MAP_JSON (recommended) or update this mapping before running.
    "your-staging-site.example.com": "Staging",
    "your-preview-site.example.com": "Preview",
    "your-production-site.example.com": "Production",
    "your-v16-site.example.com": "Version16",
}

BENCH_ENV_MAP = {
    # NOTE: placeholder values (do not use in production).
    # Set BENCH_ENV_MAP_JSON (recommended) or update this mapping before running.
    "bench-staging-id": "Staging",
    "bench-preview-id": "Preview",
    "bench-production-id": "Production",
    "bench-v16-id": "Version16",
}

SITE_ENV_MAP = _get_json_env_map("SITE_ENV_MAP_JSON") or SITE_ENV_MAP
BENCH_ENV_MAP = _get_json_env_map("BENCH_ENV_MAP_JSON") or BENCH_ENV_MAP

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
    "Version16": "https://cdn-icons-png.freepik.com/512/16695/16695467.png?ga=GA1.1.1901556257.1760382396",
}

# HTTP timeouts (seconds)
PRESS_API_TIMEOUT_SECONDS = _get_float_env("PRESS_API_TIMEOUT_SECONDS", 30.0)
GITHUB_API_TIMEOUT_SECONDS = _get_float_env("GITHUB_API_TIMEOUT_SECONDS", 15.0)
GOOGLE_CHAT_TIMEOUT_SECONDS = _get_float_env("GOOGLE_CHAT_TIMEOUT_SECONDS", 10.0)
