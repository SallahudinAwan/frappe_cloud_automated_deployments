#!/usr/bin/env python3
"""
Frappe Cloud → Google Chat Middleware

Root entrypoint kept small; implementation lives in `frappe_cloud_deploy_middleware/`.
"""

import os

from frappe_cloud_deploy_middleware.env import load_env

load_env()

from frappe_cloud_deploy_middleware import create_app
from frappe_cloud_deploy_middleware.db import init_db

app = create_app()


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", "8080"))
    debug = _env_truthy("APP_DEBUG", False) or _env_truthy("FLASK_DEBUG", False)
    app.run(host="0.0.0.0", port=port, debug=debug)
