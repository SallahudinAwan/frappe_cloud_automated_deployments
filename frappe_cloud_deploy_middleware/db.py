import json
import logging

from sqlalchemy import create_engine, text

from .config import DATABASE_URL

log = logging.getLogger(__name__)

# Fail fast with a clear error if required configuration is missing.
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required (env var DATABASE_URL is not set).")

# Database Engine (SQLAlchemy)
# Keep behavior similar: use connection pooling but safe defaults
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    # Note: original had pool_recycle/pool_size; keep moderate defaults
    pool_recycle=300,
    pool_size=5,
    max_overflow=10,
)


def init_db() -> None:
    """
    Create the `deployment_lock` table if not exists and ensure rows for Staging/Preview/Production.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                """
            CREATE TABLE IF NOT EXISTS deployment_lock (
                id VARCHAR(20) PRIMARY KEY,
                state VARCHAR(20) NOT NULL DEFAULT 'idle',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                apps_deployed JSONB,
                current_deploy_candidate VARCHAR(100),
                chat_thread_id TEXT
            )
        """
            )
        )
        for env in ["Staging", "Preview", "Production"]:
            result = conn.execute(text("SELECT COUNT(*) FROM deployment_lock WHERE id = :env"), {"env": env})
            if result.scalar() == 0:
                conn.execute(text("INSERT INTO deployment_lock (id, state) VALUES (:env, 'idle')"), {"env": env})
    log.info("DB initialized / ensured deployment_lock rows")


def get_state(environment_name: str):
    """
    Return tuple (state, apps_deployed, current_deploy_candidate, chat_thread_id)
    or (None, None, None, None) if missing.
    """
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "SELECT state, apps_deployed, current_deploy_candidate, chat_thread_id FROM deployment_lock WHERE id = :env"
            ),
            {"env": environment_name},
        )
        row = result.fetchone()
        if not row:
            return (None, None, None, None)
        return (row[0], row[1], row[2], row[3])


def set_state(
    environment_name: str, new_state: str, apps_deployed=None, deploy_candidate=None, chat_thread_id=None
) -> None:
    """
    Update deployment_lock row. apps_deployed may be JSON-serializable or None.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE deployment_lock
                SET state = :state,
                    apps_deployed = :apps_deployed,
                    current_deploy_candidate = :deploy_candidate,
                    updated_at = CURRENT_TIMESTAMP,
                    chat_thread_id = :chat_thread_id
                WHERE id = :env
            """
            ),
            {
                "state": new_state,
                "apps_deployed": json.dumps(apps_deployed) if apps_deployed is not None else None,
                "deploy_candidate": deploy_candidate,
                "env": environment_name,
                "chat_thread_id": chat_thread_id,
            },
        )
    log.info("set_state(%s -> %s) chat_thread=%s", environment_name, new_state, chat_thread_id)


def get_github_db_state(pr_id: str):
    with engine.begin() as conn:
        result = conn.execute(text("SELECT pr_id, google_thread_id FROM github_db WHERE pr_id = :pr_id"), {"pr_id": pr_id})
        row = result.fetchone()
        if not row:
            return (None, None)
        log.info("set_pr_id(%s) chat_thread=%s", row[0], row[1])
        return (row[0], row[1])


def get_thread_id_from_repo_and_branch(repo_name: str, branch_name: str):
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                SELECT pr_id, google_thread_id 
                FROM github_db 
                WHERE repo_name = :repo_name 
                ORDER BY id DESC
                LIMIT 1
            """
            ),
            {"repo_name": repo_name},
        )

        row = result.fetchone()
        if not row:
            return (None, None)

        log.info("LATEST pr_id(%s) chat_thread=%s", row[0], row[1])
        return (row[0], row[1])


def insert_github_db_state(pr_id: int, google_thread_id: str, repo_name: str, to_branch: str):
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO github_db (pr_id, google_thread_id,repo_name,branch_name)
                VALUES (:pr_id, :google_thread_id,:repo_name,:branch_name)
            """
            ),
            {"pr_id": f"{pr_id}", "google_thread_id": google_thread_id, "repo_name": repo_name, "branch_name": to_branch},
        )
        log.info("set_pr_id(%s) chat_thread=%s repo_name=%s branch_name=%s", pr_id, google_thread_id, repo_name, to_branch)


def set_chat_thread(environment_name: str, chat_thread_id=None) -> None:
    """
    Update deployment_lock row. apps_deployed may be JSON-serializable or None.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE deployment_lock
                SET chat_thread_id = :chat_thread_id
                WHERE id = :env
            """
            ),
            {
                "env": environment_name,
                "chat_thread_id": chat_thread_id,
            },
        )
    log.info("set_chat_thread(%s) chat_thread=%s", environment_name, chat_thread_id)
