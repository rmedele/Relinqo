import os
import sys
from pathlib import Path

from a2wsgi import ASGIMiddleware


ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))


def _run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")


if os.environ.get("RELINQO_RUN_MIGRATIONS_ON_STARTUP", "false").lower() in {"1", "true", "yes"}:
    _run_migrations()


from app.main import app as fastapi_app  # noqa: E402

application = ASGIMiddleware(fastapi_app)
