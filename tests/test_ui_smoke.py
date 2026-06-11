from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from app.main import app


ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "app" / "ui"
HTML_FILES = sorted(UI_DIR.glob("*.html"))
README = ROOT / "README.md"
ENV_EXAMPLE = ROOT / ".env.example"


def _clean(value: str | None) -> str:
    return " ".join((value or "").split())


class HtmlCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.controls: list[dict] = []
        self.fields: list[dict] = []
        self.assets: list[dict] = []
        self.titles: list[str] = []
        self._stack: list[tuple[str, int | None]] = []
        self._label_stack: list[dict] = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        control_idx = None
        if tag == "label":
            self._label_stack.append({"text": "", "field_indices": []})
        if tag in {"a", "button"}:
            self.controls.append({"tag": tag, "attrs": attr, "text": ""})
            control_idx = len(self.controls) - 1
        if tag in {"input", "select", "textarea"}:
            label_text = " ".join(label["text"] for label in self._label_stack)
            self.fields.append({"tag": tag, "attrs": attr, "label_text": label_text})
            field_idx = len(self.fields) - 1
            for label in self._label_stack:
                label["field_indices"].append(field_idx)
        if tag in {"link", "script", "img", "source"}:
            value = attr.get("href") or attr.get("src")
            if value:
                self.assets.append({"tag": tag, "attrs": attr, "value": value})
        if tag == "title":
            self._in_title = True
        self._stack.append((tag, control_idx))

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "label" and self._label_stack:
            self._label_stack.pop()
        for idx in range(len(self._stack) - 1, -1, -1):
            if self._stack[idx][0] == tag:
                del self._stack[idx:]
                break

    def handle_data(self, data):
        if self._in_title:
            self.titles.append(data)
        for label in self._label_stack:
            label["text"] += data
            for field_idx in label["field_indices"]:
                self.fields[field_idx]["label_text"] += data
        for _, control_idx in self._stack:
            if control_idx is not None:
                self.controls[control_idx]["text"] += data


def _parse(path: Path) -> HtmlCollector:
    parser = HtmlCollector()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


def _control_cases():
    for path in HTML_FILES:
        for idx, control in enumerate(_parse(path).controls, start=1):
            attrs = control["attrs"]
            label = (
                attrs.get("id")
                or attrs.get("data-action")
                or attrs.get("data-outcome")
                or _clean(control["text"])[:30]
                or str(idx)
            )
            yield pytest.param(path, idx, control, id=f"{path.name}:{control['tag']}:{label}")


def _field_cases():
    for path in HTML_FILES:
        html = path.read_text(encoding="utf-8")
        for idx, field in enumerate(_parse(path).fields, start=1):
            attrs = field["attrs"]
            label = attrs.get("id") or attrs.get("name") or str(idx)
            yield pytest.param(path, html, field, id=f"{path.name}:{field['tag']}:{label}")


def _asset_cases():
    for path in HTML_FILES:
        for asset in _parse(path).assets:
            value = asset["value"]
            if value.startswith("/ui/"):
                yield pytest.param(path, asset, id=f"{path.name}:{value}")


@pytest.mark.parametrize("path", HTML_FILES, ids=lambda p: p.name)
def test_ui_pages_have_title_viewport_and_stylesheet(path):
    html = path.read_text(encoding="utf-8")
    parser = _parse(path)

    assert _clean(" ".join(parser.titles)), f"{path.name} is missing a title"
    assert 'name="viewport"' in html, f"{path.name} is missing a responsive viewport"
    assert "/ui/styles.css" in html, f"{path.name} is missing the shared stylesheet"


@pytest.mark.parametrize("path", HTML_FILES, ids=lambda p: p.name)
def test_ui_pages_include_relinqo_favicons(path):
    html = path.read_text(encoding="utf-8")

    assert 'href="/ui/favicon.svg' in html, f"{path.name} is missing the SVG favicon"
    assert 'href="/favicon.ico' in html, f"{path.name} is missing the ICO fallback favicon"
    assert 'href="/ui/apple-touch-icon.png' in html, f"{path.name} is missing the Apple touch icon"


@pytest.mark.parametrize(
    "route,content_type",
    [
        ("/favicon.ico", "image/x-icon"),
        ("/favicon.svg", "image/svg+xml"),
        ("/apple-touch-icon.png", "image/png"),
    ],
)
def test_root_favicon_assets_render(route, content_type):
    response = TestClient(app).get(route)

    assert response.status_code == 200
    assert content_type in response.headers["content-type"]
    assert response.content


@pytest.mark.parametrize("path", HTML_FILES, ids=lambda p: p.name)
def test_ui_pages_do_not_ship_placeholder_copy(path):
    html = path.read_text(encoding="utf-8").lower()
    banned = ["todo:", "fixme", "lorem ipsum", "click here", "coming soon"]
    assert not any(term in html for term in banned), path.name


def test_marketing_page_focuses_on_missed_lead_recovery():
    html = (UI_DIR / "marketing.html").read_text(encoding="utf-8")

    assert "Stop losing trade jobs to missed calls and slow replies." in html
    assert "Get a free lead leak audit" in html
    assert "Most trade companies do not need more leads" in html


def test_public_pages_keep_audit_first_ctas():
    for filename in ["marketing.html", "book-demo.html", "live-demo.html"]:
        html = (UI_DIR / filename).read_text(encoding="utf-8")

        assert "Create account" not in html
        assert "Sign up" not in html
        assert "Start free trial" not in html
        assert "Get a free lead leak audit" in html or "Book my free audit" in html


def test_demo_page_hides_internal_setup_language_from_public_copy():
    html = (UI_DIR / "live-demo.html").read_text(encoding="utf-8")

    assert "Instant simulator available now. Live phone/email tests are available during a guided walkthrough." in html
    assert "Not configured" not in html
    assert "Email forwarder endpoint" not in html
    assert "Twilio SMS webhook" not in html
    assert "Twilio voice webhook" not in html


def test_register_page_positions_pilot_codes_for_trade_owners():
    html = (UI_DIR / "register.html").read_text(encoding="utf-8")

    assert "Create your Relinqo workspace" in html
    assert "14-day pilot code" in html
    assert 'name="trial_code"' in html
    assert "Need a pilot code? Book a free lead leak audit." in html
    assert "Catch missed calls, emails, forms, and after-hours inquiries" in html
    assert "Linear / Notion-style" not in html
    assert "product restraint" not in html
    assert "editorial edge" not in html


@pytest.mark.parametrize("path,idx,control", list(_control_cases()))
def test_buttons_and_links_have_clear_purpose(path, idx, control):
    attrs = control["attrs"]
    label = _clean(
        control["text"]
        or attrs.get("aria-label")
        or attrs.get("title")
        or attrs.get("value")
    )
    label = label or _clean(attrs.get("aria-label") or attrs.get("title"))

    assert label, f"{path.name} control #{idx} has no visible or accessible name"
    assert len(label) >= 2, f"{path.name} control #{idx} label is too short: {label!r}"
    assert label.lower() not in {"button", "link", "submit", "click"}, (
        f"{path.name} control #{idx} label is too generic: {label!r}"
    )

    if control["tag"] == "a":
        href = attrs.get("href")
        assert href is not None, f"{path.name} link #{idx} is missing href"
        if href == "#":
            assert attrs.get("id") or attrs.get("aria-disabled") == "true", (
                f"{path.name} placeholder link #{idx} needs an id or disabled state"
            )


@pytest.mark.parametrize("path,html,field", list(_field_cases()))
def test_form_fields_have_names_and_labels(path, html, field):
    attrs = field["attrs"]
    if attrs.get("type") == "hidden":
        return

    identity = attrs.get("id") or attrs.get("name")
    assert identity, f"{path.name} {field['tag']} is missing id/name"

    has_label = False
    if attrs.get("aria-label") or attrs.get("placeholder") or attrs.get("title"):
        has_label = True
    if attrs.get("id") and f'for="{attrs["id"]}"' in html:
        has_label = True
    if _clean(field.get("label_text")):
        has_label = True
    if field["tag"] == "select" and attrs.get("id") and attrs.get("id") in html:
        has_label = True

    assert has_label, f"{path.name} field {identity!r} needs label/placeholder/aria-label"


@pytest.mark.parametrize("path,asset", list(_asset_cases()))
def test_local_ui_assets_referenced_by_pages_exist(path, asset):
    parsed = urlparse(asset["value"])
    target = UI_DIR / parsed.path.removeprefix("/ui/")
    assert target.exists(), f"{path.name} references missing asset {asset['value']}"


@pytest.mark.parametrize(
    "route,expected",
    [
        ("/", "Stop losing trade jobs to missed calls and slow replies."),
        ("/demo", "liveDemoForm"),
        ("/book-demo", 'action="/contact"'),
        ("/website-widget", 'data-workspace="reese-plumbing"'),
        ("/login", "Sign In"),
        ("/register", "Create workspace"),
        ("/forgot-password", "Send Reset Link"),
        ("/reset-password", "Reset Password"),
    ],
)
def test_public_and_auth_pages_render(route, expected):
    response = TestClient(app).get(route)
    assert response.status_code == 200
    assert expected in response.text


@pytest.mark.parametrize(
    "phrase",
    [
        "Register a workspace",
        "Connect Gmail",
        "Start or bypass billing",
        "Configure missed-call rescue",
        "Set weekly booking availability",
        "Send a test lead",
        "Move a lead to Won",
        "Reviews on autopilot",
        "Run the full test suite",
        "Doteasy/cPanel production notes",
        "MySQL",
        "Stripe test card",
        "Twilio webhooks",
    ],
)
def test_readme_documents_new_user_setup_steps(phrase):
    text = README.read_text(encoding="utf-8")
    assert phrase.lower() in text.lower()


@pytest.mark.parametrize(
    "env_name",
    [
        "APP_ENV",
        "APP_PORT",
        "PUBLIC_BASE_URL",
        "DATABASE_URL",
        "SESSION_SECRET",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "LLM_PROVIDER",
        "LLM_API_KEY",
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_FROM_NUMBER",
        "SMS_ALERT_TO_NUMBER",
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "BILLING_ADMIN_TOKEN",
    ],
)
def test_env_example_lists_required_setup_variables(env_name):
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert f"{env_name}=" in text
