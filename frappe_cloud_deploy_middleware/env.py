import os
from typing import Optional


def load_env(env_file: Optional[str] = None, override: bool = False) -> None:
    """
    Load environment variables from a `.env` file (local/dev convenience).

    - No-op if `python-dotenv` is not installed or the file doesn't exist.
    - Uses `ENV_FILE` env var if provided, otherwise defaults to `.env`.
    """
    target = env_file or os.getenv("ENV_FILE") or ".env"

    try:
        from dotenv import find_dotenv, load_dotenv
    except Exception:
        # Keep dependency optional at runtime; production can rely on real env vars.
        return

    dotenv_path = find_dotenv(target, usecwd=True) or target
    if not os.path.exists(dotenv_path):
        return

    load_dotenv(dotenv_path=dotenv_path, override=override)

