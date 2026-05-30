import io
import json
import sys

from passenger_wsgi import application


def call_wsgi(path: str, method: str = "GET", body: bytes = b"", content_type: str = ""):
    seen = {}

    def start_response(status, headers, exc_info=None):
        seen["status"] = status
        seen["headers"] = dict(headers)

    environ = {
        "REQUEST_METHOD": method,
        "SCRIPT_NAME": "",
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "SERVER_NAME": "www.relinqo.com",
        "SERVER_PORT": "443",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "HTTP_HOST": "www.relinqo.com",
        "HTTP_ACCEPT": "*/*",
        "REMOTE_ADDR": "127.0.0.1",
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "https",
        "wsgi.input": io.BytesIO(body),
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": False,
        "wsgi.multiprocess": True,
        "wsgi.run_once": False,
    }
    if content_type:
        environ["CONTENT_TYPE"] = content_type

    response_body = b"".join(application(environ, start_response))
    return seen["status"], seen["headers"], response_body


def test_passenger_wsgi_serves_public_routes():
    cases = [
        ("/", b"AI speed-to-lead"),
        ("/website-widget", b'data-workspace="reese-plumbing"'),
        ("/api/widget/embed.js", b"data-relinqo-widget"),
        ("/health", b'"ok":true'),
    ]

    for path, expected in cases:
        status, headers, body = call_wsgi(path)
        assert status == "200 OK"
        assert expected in body
        assert int(headers["content-length"]) == len(body)


def test_passenger_wsgi_posts_widget_json_body():
    payload = json.dumps(
        {
            "workspace": "missing-workspace",
            "token": "bad",
            "email": "test@example.com",
            "message": "This is a test lead from the WSGI adapter.",
        }
    ).encode("utf-8")

    status, headers, body = call_wsgi(
        "/api/public/widget/lead",
        method="POST",
        body=payload,
        content_type="text/plain;charset=UTF-8",
    )

    assert status == "404 Not Found"
    assert headers["access-control-allow-origin"] == "*"
    assert b"Widget workspace not found" in body
