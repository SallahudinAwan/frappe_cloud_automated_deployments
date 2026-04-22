from flask import Blueprint

bp = Blueprint("home", __name__)


@bp.route("/", methods=["GET"])
def health_check():
    return "✅ Frappe Cloud → Google Chat Middleware is running!"

