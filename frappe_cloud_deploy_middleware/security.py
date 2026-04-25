import hashlib
import hmac
import os
from typing import Optional

from flask import Request, jsonify


def _extract_supplied_secret(request: Request) -> str:
    """
    Extract caller-provided secret from common locations.
    Priority: Authorization Bearer, then X-Webhook-Token, then token query param.
    """
    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()

    header_token = request.headers.get("X-Webhook-Token", "").strip()
    if header_token:
        return header_token

    return request.args.get("token", "").strip()


def require_shared_secret(request: Request, endpoint_name: str, env_var_name: str):
    """
    Enforce a shared secret for inbound requests.
    Returns None when authorized, otherwise a Flask error response tuple.
    """
    expected_secret = os.getenv(env_var_name, "").strip() or os.getenv("INBOUND_SHARED_TOKEN", "").strip()
    if not expected_secret:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": (
                        f"{endpoint_name} authentication is misconfigured: "
                        f"set {env_var_name} or INBOUND_SHARED_TOKEN"
                    ),
                }
            ),
            503,
        )

    supplied_secret = _extract_supplied_secret(request)
    if not supplied_secret or not hmac.compare_digest(supplied_secret, expected_secret):
        return jsonify({"status": "unauthorized", "message": f"Invalid authentication for {endpoint_name}"}), 401

    return None


def _is_valid_github_signature(body: bytes, secret: str, signature_header: Optional[str]) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    received = signature_header.split("=", 1)[1].strip()
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(received, digest)


def require_github_auth(request: Request, endpoint_name: str):
    """
    Enforce GitHub webhook auth.
    Preferred: X-Hub-Signature-256 with GITHUB_WEBHOOK_SECRET.
    Fallback: shared secret from GITHUB_WEBHOOK_TOKEN.
    Returns None when authorized, otherwise a Flask error response tuple.
    """
    body = request.get_data(cache=True)
    signature = request.headers.get("X-Hub-Signature-256")
    signature_secret = os.getenv("GITHUB_WEBHOOK_SECRET", "").strip()

    if signature_secret:
        if _is_valid_github_signature(body, signature_secret, signature):
            return None
        return jsonify({"status": "unauthorized", "message": f"Invalid GitHub signature for {endpoint_name}"}), 401

    return require_shared_secret(request, endpoint_name, "GITHUB_WEBHOOK_TOKEN")
