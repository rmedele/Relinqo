"""Smoke-test a deployed reqlinqo backend.

This intentionally avoids creating accounts or mutating production data. It
checks that the public domain is serving this FastAPI app, not a static app
shell or a different API mounted under /api.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urljoin


@dataclass(frozen=True)
class ProbeResult:
    path: str
    status: int | None
    content_type: str
    body: bytes
    error: str | None = None

    def json_body(self) -> object | None:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None


def _looks_like_html(result: ProbeResult) -> bool:
    head = result.body[:200].lstrip().lower()
    return "text/html" in result.content_type.lower() or head.startswith((b"<!doctype html", b"<html"))


def _json_dict(result: ProbeResult) -> dict | None:
    parsed = result.json_body()
    return parsed if isinstance(parsed, dict) else None


def fetch_path(base_url: str, path: str, *, method: str = "GET", payload: dict | None = None) -> ProbeResult:
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    data = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "reqlinqo-deployment-check/1.0",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=10) as response:
            return ProbeResult(
                path=path,
                status=response.status,
                content_type=response.headers.get("content-type", ""),
                body=response.read(4096),
            )
    except urllib.error.HTTPError as exc:
        return ProbeResult(
            path=path,
            status=exc.code,
            content_type=exc.headers.get("content-type", ""),
            body=exc.read(4096),
        )
    except urllib.error.URLError as exc:
        return ProbeResult(path=path, status=None, content_type="", body=b"", error=str(exc.reason))


def analyze_probe_results(results: Mapping[str, ProbeResult]) -> tuple[bool, list[str]]:
    lines: list[str] = []
    ok = True

    health = results["/health"]
    health_json = _json_dict(health)
    if health.status is None:
        ok = False
        lines.append(f"FAIL /health could not connect: {health.error or 'unknown network error'}.")
    elif health.status == 200 and health_json and health_json.get("ok") is True and "app" in health_json:
        lines.append("OK   /health returned the FastAPI health JSON.")
    else:
        ok = False
        if _looks_like_html(health):
            lines.append("FAIL /health returned HTML, so the domain is hitting a static app shell.")
        else:
            lines.append(f"FAIL /health expected FastAPI JSON, got status={health.status} body={health.body[:120]!r}.")

    auth_me = results["/auth/me"]
    auth_me_json = _json_dict(auth_me)
    if auth_me.status is None:
        ok = False
        lines.append(f"FAIL /auth/me could not connect: {auth_me.error or 'unknown network error'}.")
    elif auth_me.status == 401 and auth_me_json and auth_me_json.get("detail") == "Not authenticated":
        lines.append("OK   /auth/me returned the expected unauthenticated FastAPI JSON.")
    else:
        ok = False
        if _looks_like_html(auth_me):
            lines.append("FAIL /auth/me returned HTML, so auth requests are not reaching FastAPI.")
        else:
            lines.append(f"FAIL /auth/me expected 401 JSON, got status={auth_me.status} body={auth_me.body[:120]!r}.")

    auth_login = results["/auth/login"]
    auth_login_json = _json_dict(auth_login)
    if auth_login.status is None:
        ok = False
        lines.append(f"FAIL /auth/login could not connect: {auth_login.error or 'unknown network error'}.")
    elif auth_login.status == 401 and auth_login_json and auth_login_json.get("detail") == "Invalid credentials":
        lines.append("OK   /auth/login parsed JSON and rejected the smoke-test credentials.")
    else:
        ok = False
        if _looks_like_html(auth_login):
            lines.append("FAIL /auth/login returned HTML, so login POSTs are being swallowed before FastAPI.")
        else:
            lines.append(f"FAIL /auth/login expected 401 JSON, got status={auth_login.status} body={auth_login.body[:120]!r}.")

    api_health = results.get("/api/health")
    api_auth = results.get("/api/auth/login")
    api_health_json = _json_dict(api_health) if api_health else None
    api_auth_json = _json_dict(api_auth) if api_auth else None
    if (
        not ok
        and api_health
        and api_health.status == 200
        and api_health_json
        and api_health_json.get("status") == "ok"
    ):
        lines.append("NOTE /api/health is alive with a non-FastAPI shape. This looks like a separate API service.")
    if (
        not ok
        and api_auth
        and api_auth_json
        and isinstance(api_auth_json.get("error"), dict)
        and api_auth_json["error"].get("code") in {"AUTH_INVALID", "VALIDATION", "NOT_FOUND"}
    ):
        lines.append("NOTE /api/auth/login is not this repo's session-auth endpoint. The deployed frontend/backend are mismatched.")

    return ok, lines


def run_checks(base_url: str) -> tuple[bool, list[str]]:
    results = {
        "/health": fetch_path(base_url, "/health"),
        "/auth/me": fetch_path(base_url, "/auth/me"),
        "/auth/login": fetch_path(
            base_url,
            "/auth/login",
            method="POST",
            payload={"email": "deployment-check@example.com", "password": "DefinitelyWrong123"},
        ),
        "/api/health": fetch_path(base_url, "/api/health"),
        "/api/auth/login": fetch_path(
            base_url,
            "/api/auth/login",
            method="POST",
            payload={"identifier": "deployment-check@example.com", "password": "DefinitelyWrong123"},
        ),
    }
    return analyze_probe_results(results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that a public URL is serving the reqlinqo FastAPI backend.")
    parser.add_argument("base_url", help="Production base URL, for example https://app.leadrelayapp.com")
    args = parser.parse_args(argv)

    ok, lines = run_checks(args.base_url)
    for line in lines:
        print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
