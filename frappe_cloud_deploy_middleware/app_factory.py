import logging
import os

from flask import Flask


def create_app() -> Flask:
    """
    Flask application factory.
    """
    _configure_logging()
    app = Flask(__name__)

    from .home import bp as home_bp
    from .github.webhooks import bp as github_bp
    from .frappe_cloud.deployment import bp as deployment_bp
    from .frappe_cloud.webhooks import bp as frappe_cloud_bp

    app.register_blueprint(home_bp)
    app.register_blueprint(github_bp)
    app.register_blueprint(frappe_cloud_bp)
    app.register_blueprint(deployment_bp)

    return app


def _configure_logging() -> None:
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s: %(message)s")
