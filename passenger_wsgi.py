import asyncio
import os
import sys
import traceback
from http import HTTPStatus
from pathlib import Path


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


def _read_body(environ) -> bytes:
    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
    except ValueError:
        length = 0
    if length <= 0:
        return b""
    return environ["wsgi.input"].read(length)


def _asgi_headers(environ) -> list[tuple[bytes, bytes]]:
    headers: list[tuple[bytes, bytes]] = []
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            name = key[5:].lower().replace("_", "-").encode("latin-1")
            headers.append((name, str(value).encode("latin-1")))
    for key, name in (("CONTENT_TYPE", b"content-type"), ("CONTENT_LENGTH", b"content-length")):
        value = environ.get(key)
        if value:
            headers.append((name, str(value).encode("latin-1")))
    return headers


def _scope(environ) -> dict:
    server_port = environ.get("SERVER_PORT")
    try:
        port = int(server_port) if server_port else None
    except ValueError:
        port = None
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.1"},
        "http_version": environ.get("SERVER_PROTOCOL", "HTTP/1.1").split("/", 1)[-1],
        "method": environ.get("REQUEST_METHOD", "GET"),
        "scheme": environ.get("wsgi.url_scheme", "http"),
        "path": environ.get("PATH_INFO") or "/",
        "raw_path": (environ.get("PATH_INFO") or "/").encode("latin-1"),
        "query_string": (environ.get("QUERY_STRING") or "").encode("latin-1"),
        "root_path": environ.get("SCRIPT_NAME", ""),
        "headers": _asgi_headers(environ),
        "client": (environ.get("REMOTE_ADDR") or "", 0),
        "server": (environ.get("SERVER_NAME") or "", port),
    }


async def _call_asgi(environ) -> tuple[str, list[tuple[str, str]], bytes]:
    body = _read_body(environ)
    scope = _scope(environ)
    status_code = 500
    response_headers: list[tuple[str, str]] = []
    chunks: list[bytes] = []
    sent_request = False

    async def receive():
        nonlocal sent_request
        if sent_request:
            return {"type": "http.disconnect"}
        sent_request = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        nonlocal status_code, response_headers
        if message["type"] == "http.response.start":
            status_code = int(message["status"])
            response_headers = [
                (key.decode("latin-1"), value.decode("latin-1"))
                for key, value in message.get("headers", [])
            ]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await fastapi_app(scope, receive, send)
    response_body = b"".join(chunks)
    if scope["method"] == "HEAD":
        response_body = b""
    if not any(key.lower() == "content-length" for key, _ in response_headers):
        response_headers.append(("content-length", str(len(response_body))))
    try:
        phrase = HTTPStatus(status_code).phrase
    except ValueError:
        phrase = "Unknown"
    status = f"{status_code} {phrase}"
    return status, response_headers, response_body


def application(environ, start_response):
    method = environ.get("REQUEST_METHOD", "")
    path = environ.get("PATH_INFO", "")
    host = environ.get("HTTP_HOST", environ.get("SERVER_NAME", ""))
    print(
        f"passenger request method={method!r} path={path!r} host={host!r} "
        f"content_length={environ.get('CONTENT_LENGTH')!r}",
        file=sys.stderr,
        flush=True,
    )
    try:
        status, headers, body = asyncio.run(_call_asgi(environ))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        body = b"Internal Server Error"
        status = "500 Internal Server Error"
        headers = [("content-type", "text/plain; charset=utf-8"), ("content-length", str(len(body)))]
    start_response(status, headers)
    return [body]
