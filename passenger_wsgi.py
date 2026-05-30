import os
import sys
from pathlib import Path

from a2wsgi import ASGIMiddleware


ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("RELINQO_BACKGROUND_SCHEDULER_ENABLED", "false")


def _run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")


if os.environ.get("RELINQO_RUN_MIGRATIONS_ON_STARTUP", "false").lower() in {"1", "true", "yes"}:
    _run_migrations()


from app.main import app as fastapi_app  # noqa: E402

fastapi_wsgi_app = ASGIMiddleware(fastapi_app)


def application(environ, start_response):
    path = environ.get("PATH_INFO", "")
    host = environ.get("HTTP_HOST", environ.get("SERVER_NAME", ""))
    print(f"passenger request path={path!r} host={host!r}", file=sys.stderr, flush=True)

    if path == "/health":
        body = b'{"ok":true,"app":"Relinqo","served_by":"passenger_wsgi"}'
        start_response(
            "200 OK",
            [
                ("content-type", "application/json"),
                ("content-length", str(len(body))),
            ],
        )
        return [body]

    return fastapi_wsgi_app(environ, start_response)
