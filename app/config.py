from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Global application settings
    app_name: str = "relinqo"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    public_base_url: str = "http://127.0.0.1:8080"
    database_url: str = "sqlite:///./data/leadrelay.db"
    session_secret: str = "change-me-to-random-secret"
    forwarded_allow_ips: str = "127.0.0.1"  # IPs allowed to set X-Forwarded-* headers
    llm_provider: str = "none"
    llm_api_key: str = ""

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""

    # Legacy per-org fields — kept for migration seeding and fallback.
    # New orgs configure these via OrgSettings in the database.
    review_username: str = "admin"
    review_password: str = "change-me"
    forwarding_token: str = "change-forwarding-token"
    inbox_poll_enabled: bool = False
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"
    imap_search_criteria: str = 'UNSEEN'
    human_review: bool = True
    owner_alert_email: str = "owner@example.com"
    digest_to_email: str = "owner@example.com"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_from_email: str = "alerts@example.com"
    auto_send_confidence_threshold: float = 0.85
    default_timezone: str = "America/Edmonton"
    business_name: str = ""
    business_services: str = ""
    business_area: str = ""
    business_hours: str = "Mon-Fri 8am-5pm"
    business_phone: str = ""
    business_tone: str = "friendly and professional"
    business_reply_signature: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    sms_alert_to_number: str = ""

    # Public demo contact surface
    demo_inbox_email: str = ""
    demo_phone_number: str = ""
    demo_forwarding_token: str = ""

    # Stripe Billing
    stripe_publishable_key: str = ""
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_id: str = ""
    stripe_price_amount_cents: int = 19900
    stripe_price_currency: str = "usd"
    stripe_product_name: str = "relinqo Full Service"
    stripe_trial_period_days: int = 14
    billing_admin_token: str = ""
    billing_enforced: bool = True

    @field_validator("public_base_url", mode="before")
    @classmethod
    def normalize_public_base_url(cls, value: str) -> str:
        raw = str(value or "").strip().rstrip("/")
        if not raw:
            return raw

        if "://" not in raw:
            authority = raw.split("/", 1)[0].lower()
            is_local = (
                authority == "localhost"
                or authority.startswith("localhost:")
                or authority == "127.0.0.1"
                or authority.startswith("127.0.0.1:")
                or authority == "0.0.0.0"
                or authority.startswith("0.0.0.0:")
                or authority == "[::1]"
                or authority.startswith("[::1]:")
            )
            scheme = "http" if is_local else "https"
            raw = f"{scheme}://{raw}"

        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("PUBLIC_BASE_URL must be an absolute HTTP(S) URL")
        return raw

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()


def normalized_database_url(url: str | None = None) -> str:
    """Return a SQLAlchemy-compatible database URL.

    Some hosts expose Postgres URLs as postgres://..., while SQLAlchemy expects
    postgresql://... or postgresql+psycopg://....
    """
    value = url or settings.database_url
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+psycopg://", 1)
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    return value
