from scripts.check_deployment import ProbeResult, analyze_probe_results


def probe(path: str, status: int, content_type: str, body: str) -> ProbeResult:
    return ProbeResult(path=path, status=status, content_type=content_type, body=body.encode("utf-8"))


def test_deployment_check_accepts_fastapi_backend():
    ok, lines = analyze_probe_results({
        "/health": probe("/health", 200, "application/json", '{"ok": true, "app": "reqlinqo"}'),
        "/auth/me": probe("/auth/me", 401, "application/json", '{"detail": "Not authenticated"}'),
        "/auth/login": probe("/auth/login", 401, "application/json", '{"detail": "Invalid credentials"}'),
        "/api/health": probe("/api/health", 404, "application/json", '{"detail": "Not Found"}'),
        "/api/auth/login": probe("/api/auth/login", 404, "application/json", '{"detail": "Not Found"}'),
    })

    assert ok is True
    assert any("/health returned the FastAPI health JSON" in line for line in lines)


def test_deployment_check_flags_static_shell_and_other_api():
    ok, lines = analyze_probe_results({
        "/health": probe("/health", 200, "text/html", "<!doctype html><div id=\"root\"></div>"),
        "/auth/me": probe("/auth/me", 200, "text/html", "<!doctype html><div id=\"root\"></div>"),
        "/auth/login": probe("/auth/login", 404, "text/html", "<!doctype html><div id=\"root\"></div>"),
        "/api/health": probe("/api/health", 200, "application/json", '{"status": "ok", "uptime": 123}'),
        "/api/auth/login": probe(
            "/api/auth/login",
            401,
            "application/json",
            '{"error": {"message": "Invalid credentials", "code": "AUTH_INVALID"}}',
        ),
    })

    assert ok is False
    assert any("static app shell" in line for line in lines)
    assert any("separate API service" in line for line in lines)
    assert any("deployed frontend/backend are mismatched" in line for line in lines)
