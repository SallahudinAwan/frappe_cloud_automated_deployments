"""
Production WSGI entrypoint.

Example:
  gunicorn -w 2 -b 0.0.0.0:8080 wsgi:app
"""

from frappe_cloud_deploy_middleware.env import load_env

load_env()

from frappe_cloud_deploy_middleware import create_app
from frappe_cloud_deploy_middleware.db import init_db

init_db()
app = create_app()
