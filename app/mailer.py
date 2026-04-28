import logging
import smtplib
from email.message import EmailMessage

from app.config import settings
from app.models import OrgSettings

logger = logging.getLogger(__name__)


def email_configured(org_settings: OrgSettings | None = None) -> bool:
    from app.gmail import gmail_configured
    if gmail_configured(org_settings):
        return True
    return smtp_configured(org_settings)


def smtp_configured(org_settings: OrgSettings | None = None) -> bool:
    if org_settings:
        return bool(org_settings.smtp_host and org_settings.smtp_from_email)
    return bool(settings.smtp_host and settings.smtp_from_email)


def send_email(*, to_email: str, subject: str, body: str, org_settings: OrgSettings | None = None) -> tuple[bool, str]:
    logger.info("Email send attempt to=%s subject=%s", to_email, subject)

    if org_settings and org_settings.org and org_settings.org.subscription_status not in {"active", "trialing"}:
        logger.warning("Email send blocked for inactive org_id=%s", org_settings.org_id)
        return False, "Workspace is not active"

    from app.gmail import gmail_configured, gmail_send_email
    if org_settings and gmail_configured(org_settings):
        return gmail_send_email(to_email, subject, body, org_settings)

    if not smtp_configured(org_settings):
        logger.warning("Email send skipped — neither Gmail OAuth nor SMTP is configured")
        return False, "Email not configured"

    host = org_settings.smtp_host if org_settings else settings.smtp_host
    port = org_settings.smtp_port if org_settings else settings.smtp_port
    username = org_settings.smtp_username if org_settings else settings.smtp_username
    password = org_settings.smtp_password if org_settings else settings.smtp_password
    use_tls = org_settings.smtp_use_tls if org_settings else settings.smtp_use_tls
    from_email = org_settings.smtp_from_email if org_settings else settings.smtp_from_email

    message = EmailMessage()
    message["From"] = from_email
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            if use_tls:
                server.starttls()
            if username:
                server.login(username, password)
            server.send_message(message)
        logger.info("SMTP send succeeded to=%s subject=%s", to_email, subject)
        return True, "sent"
    except Exception as exc:  # noqa: BLE001
        logger.exception("SMTP send failed to=%s subject=%s", to_email, subject)
        return False, str(exc)
