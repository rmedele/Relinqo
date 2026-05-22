"""Launch-night safety probes for the public Relinqo deployment.

This script intentionally avoids authenticated flows and avoids mutating
production data. It verifies the public boundary: health, auth gating, webhook
signature behavior, and obvious committed secret values in tracked files.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin


SECRET_VALUE_RE = re.compile(
    r"(sk_(?:live|test)_[A-Za-z0-9]{8,}|whsec_[A-Za-z0-9]{8,}|"
    r"AC[0-9a-fA-F]{32}|AIza[0-9A-Za-z_-]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]+PRIVATE KEY-----)"
)


@dataclass(frozen=True)
class Probe:
    method: str
    path: str
    expected_status: set[int]
    description: str
    body: bytes = b""
    content_type: str = "application/json"
    must_not_contain: tuple[str, ...] = ()
    fail_if_detail_contains: tuple[str, ...] = ()


@dataclass
class Result:
    ok: bool
    label: str
    detail: str


@dataclass
class Response:
    status: int | None
    body: bytes
    content_type: str
    error: str | None = None

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> dict | None:
        try:
            parsed = json.loads(self.text())
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None


PROBES = [
    Probe("GET", "/health", {200}, "FastAPI health is alive"),
    Probe("GET", "/auth/rescue", {404}, "temporary rescue backdoor is gone"),
    Probe("GET", "/leads", {401}, "lead API requires auth", must_not_contain=("sender_email", "recommended_reply")),
    Probe("GET", "/stats", {401}, "stats API requires auth", must_not_contain=("won_revenue", "pipeline_value")),
    Probe("GET", "/settings", {401}, "settings page shell requires auth"),
    Probe("GET", "/pipeline", {401}, "pipeline page shell requires auth"),
    Probe("GET", "/templates", {401}, "templates page shell requires auth"),
    Probe("GET", "/", {200}, "marketing page is public"),
    Probe("GET", "/book-demo", {200}, "demo booking page is public"),
    Probe("GET", "/demo", {200}, "live demo page is public"),
    Probe("POST", "/twilio/voice/incoming", {403}, "Twilio voice webhook rejects missing signature"),
    Probe("POST", "/sms/webhook", {403}, "Twilio SMS webhook rejects missing signature"),
    Probe("POST", "/sms/status", {403}, "Twilio SMS status webhook rejects missing signature"),
    Probe(
        "POST",
        "/stripe/webhook",
        {400},
        "Stripe webhook rejects unsigned requests and has a webhook secret configured",
        fail_if_detail_contains=("not configured", "webhook secret is not configured"),
    ),
]


def fetch(base_url: str, probe: Probe) -> Response:
    url = urljoin(base_url.rstrip("/") + "/", probe.path.lstrip("/"))
    headers = {
        "Accept": "application/json,text/html;q=0.9",
        "User-Agent": "relinqo-launch-night-check/1.0",
    }
    data = probe.body if probe.method != "GET" else None
    if data:
        headers["Content-Type"] = probe.content_type

    request = urllib.request.Request(url, data=data, headers=headers, method=probe.method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=15) as response:
            return Response(
                status=response.status,
                body=response.read(8192),
                content_type=response.headers.get("content-type", ""),
            )
    except urllib.error.HTTPError as exc:
        return Response(
            status=exc.code,
            body=exc.read(8192),
            content_type=exc.headers.get("content-type", ""),
        )
    except urllib.error.URLError as exc:
        fallback = fetch_with_curl(base_url, probe)
        if fallback.status is not None:
            return fallback
        return Response(status=None, body=b"", content_type="", error=str(exc.reason))


def fetch_with_curl(base_url: str, probe: Probe) -> Response:
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        return Response(status=None, body=b"", content_type="", error="curl is unavailable")

    url = urljoin(base_url.rstrip("/") + "/", probe.path.lstrip("/"))
    command = [
        curl,
        "-sS",
        "--noproxy",
        "*",
        "-X",
        probe.method,
        "-H",
        "Accept: application/json,text/html;q=0.9",
        "-H",
        "User-Agent: relinqo-launch-night-check/1.0",
    ]
    if probe.body:
        command.extend(["-H", f"Content-Type: {probe.content_type}", "--data-binary", probe.body.decode("utf-8")])
    command.extend([
        "-w",
        "\n---RELINQO_STATUS:%{http_code}\n---RELINQO_CONTENT_TYPE:%{content_type}\n",
        url,
    ])

    completed = subprocess.run(
        command,
        text=False,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        error = completed.stderr.decode("utf-8", errors="replace").strip()
        return Response(status=None, body=b"", content_type="", error=error or f"curl exited {completed.returncode}")

    output = completed.stdout
    status_marker = b"\n---RELINQO_STATUS:"
    type_marker = b"\n---RELINQO_CONTENT_TYPE:"
    status_index = output.rfind(status_marker)
    type_index = output.rfind(type_marker)
    if status_index < 0 or type_index < 0 or type_index < status_index:
        return Response(status=None, body=b"", content_type="", error="could not parse curl response")

    body = output[:status_index]
    raw_status = output[status_index + len(status_marker):type_index].strip()
    raw_type = output[type_index + len(type_marker):].strip()
    try:
        status = int(raw_status.decode("ascii"))
    except ValueError:
        return Response(status=None, body=b"", content_type="", error="could not parse curl status")
    return Response(status=status, body=body, content_type=raw_type.decode("utf-8", errors="replace"))


def check_probe(base_url: str, probe: Probe) -> Result:
    response = fetch(base_url, probe)
    label = f"{probe.method} {probe.path}"
    if response.status is None:
        return Result(False, label, f"could not connect: {response.error or 'unknown network error'}")
    if response.status not in probe.expected_status:
        return Result(False, label, f"expected {sorted(probe.expected_status)}, got {response.status}: {response.text()[:180]!r}")

    text = response.text()
    lowered = text.lower()
    for needle in probe.must_not_contain:
        if needle.lower() in lowered:
            return Result(False, label, f"response unexpectedly contained {needle!r}")

    detail = ""
    parsed = response.json()
    if parsed and "detail" in parsed:
        detail = str(parsed["detail"])
        for needle in probe.fail_if_detail_contains:
            if needle.lower() in detail.lower():
                return Result(False, label, detail)

    suffix = f" ({detail})" if detail else ""
    return Result(True, label, f"{probe.description}{suffix}")


def iter_tracked_files(repo_root: Path) -> Iterable[Path]:
    completed = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=True,
    )
    for raw in completed.stdout.splitlines():
        if raw:
            yield repo_root / raw


def check_committed_secret_values(repo_root: Path) -> list[Result]:
    findings: list[str] = []
    for path in iter_tracked_files(repo_root):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if SECRET_VALUE_RE.search(line):
                rel = path.relative_to(repo_root)
                findings.append(f"{rel}:{line_no}")

    if findings:
        return [Result(False, "tracked secret values", "potential secret values found at " + ", ".join(findings[:10]))]
    return [Result(True, "tracked secret values", "no obvious live/test secret values found")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run public launch-night safety checks.")
    parser.add_argument("--base-url", default="https://www.relinqo.com")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    results = [check_probe(args.base_url, probe) for probe in PROBES]
    results.extend(check_committed_secret_values(repo_root))

    ok = True
    for result in results:
        ok = ok and result.ok
        status = "OK  " if result.ok else "FAIL"
        print(f"{status} {result.label}: {result.detail}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
