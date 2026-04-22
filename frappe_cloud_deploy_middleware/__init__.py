"""
Frappe Cloud automated deployment webhook middleware.

This package holds the modularized implementation behind the root `app.py` and
`auto_deploy.py` entrypoints.
"""

from .app_factory import create_app

__all__ = ["create_app"]

