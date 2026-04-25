import logging
import os
import subprocess
import sys

from flask import Blueprint, jsonify, request

from .services import detect_deploy_failure
from ..db import get_state
from ..security import require_shared_secret

log = logging.getLogger(__name__)

bp = Blueprint("deployment", __name__)


@bp.route("/status/<env>", methods=["GET"])
def get_deployment_status(env: str):
    """
    Returns the current deployment_lock state for the requested environment.
    """
    auth_error = require_shared_secret(request, "status", "DEPLOY_STATUS_TOKEN")
    if auth_error:
        return auth_error

    state, apps, candidate, chat_thread_id = get_state(env)
    return jsonify(
        {
            "environment": env,
            "state": state,
            "apps_deployed": apps,
            "current_deploy_candidate": candidate,
            "chat_thread_id": chat_thread_id,
        }
    )


@bp.route("/trigger-workflow/<env>", methods=["POST"])
def trigger_deployment_workflow(env: str):
    try:
        auth_error = require_shared_secret(request, "trigger-workflow", "DEPLOY_WORKFLOW_TOKEN")
        if auth_error:
            return auth_error

        state, _, _, _ = get_state(env)
        if state == "in_progress":
            return jsonify({"status": "skipped", "message": "Deployment already running"}), 204

        data = request.get_json(force=True, silent=True) or {}
        # Clone current environment and inject DEPLOY_ENV
        env_vars = os.environ.copy()
        env_vars["DEPLOY_ENV"] = env.lower()  # 👈 inject here
        env_vars["ALLOWED_APPS_FROM_WORKFLOW"] = data.get("allowed_apps", "")

        process = subprocess.Popen(
            [sys.executable, "auto_deploy.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env_vars,  # 👈 pass it here
        )

        for line in process.stdout:
            log.info(line.strip())

        process.wait()

        return jsonify({"status": "success", "message": f"Deployment started for {env}"}), 200

    except Exception as e:
        log.exception("Error triggering deployment workflow for env=%s", env)
        return jsonify({"status": "error", "message": str(e)}), 500


# ------------------------
# Deploy failure checker (previously check_deploy_failure)
# ------------------------
@bp.route("/check-deploy-failure/<env>", methods=["GET"])
def check_deployment_failure(env: str):
    auth_error = require_shared_secret(request, "check-deploy-failure", "DEPLOY_STATUS_TOKEN")
    if auth_error:
        return auth_error

    payload, status_code = detect_deploy_failure(env)
    return jsonify(payload), status_code
