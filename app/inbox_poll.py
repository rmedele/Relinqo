import email
import imaplib
import logging
from email.header import decode_header, make_header
from email.utils import parseaddr

from sqlalchemy.orm import Session

from app.models import InboxMessage, OrgSettings
from app.schemas import LeadIngestRequest

logger = logging.getLogger(__name__)


def inbox_poll_configured(org_settings: OrgSettings | None = None) -> bool:
    from app.gmail import gmail_configured
    if org_settings and gmail_configured(org_settings):
        return True
    if org_settings:
        return bool(org_settings.imap_username and org_settings.imap_password and org_settings.imap_host)
    from app.config import settings
    return bool(settings.imap_username and settings.imap_password and settings.imap_host)


def poll_inbox(db: Session, org_id: int | None = None, org_settings: OrgSettings | None = None, limit: int = 10) -> dict:
    if not inbox_poll_configured(org_settings):
        return {"ok": False, "error": "IMAP not configured"}

    from app.gmail import gmail_configured, gmail_poll_inbox
    if org_settings and gmail_configured(org_settings) and org_id is not None:
        return gmail_poll_inbox(db, org_id, org_settings, limit)

    if org_settings:
        host = org_settings.imap_host
        port = org_settings.imap_port
        username = org_settings.imap_username
        password = org_settings.imap_password
        mailbox = org_settings.imap_mailbox
        search_criteria = org_settings.imap_search_criteria
    else:
        from app.config import settings
        host = settings.imap_host
        port = settings.imap_port
        username = settings.imap_username
        password = settings.imap_password
        mailbox = settings.imap_mailbox
        search_criteria = settings.imap_search_criteria

    mail = imaplib.IMAP4_SSL(host, port)
    try:
        mail.login(username, password)
        mail.select(mailbox)
        status, data = mail.search(None, search_criteria)
        if status != "OK":
            return {"ok": False, "error": "IMAP search failed"}

        ids = [item for item in data[0].split() if item][-limit:]
        created = []
        skipped = 0

        for msg_num in ids:
            status, msg_data = mail.fetch(msg_num, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue

            raw_bytes = msg_data[0][1]
            message = email.message_from_bytes(raw_bytes)
            external_id = message.get("Message-ID") or f"imap-{msg_num.decode()}"

            exists = db.query(InboxMessage).filter(InboxMessage.external_id == external_id).first()
            if exists:
                skipped += 1
                continue

            parsed = _parse_message(message)

            from app.routes.leads import ingest_lead
            lead = ingest_lead(
                LeadIngestRequest(
                    source="forwarded_email",
                    sender_name=parsed["sender_name"],
                    sender_email=parsed["sender_email"],
                    subject=parsed["subject"],
                    body=parsed["body"],
                ),
                db,
                org_id=org_id,
                org_settings=org_settings,
            )
            db.add(InboxMessage(org_id=org_id or lead.org_id, source="imap", external_id=external_id, lead_id=lead.id))
            db.commit()
            created.append(lead.id)

        return {"ok": True, "created": len(created), "skipped": skipped, "lead_ids": created}
    finally:
        try:
            mail.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            mail.logout()
        except Exception:  # noqa: BLE001
            pass


def _parse_message(message) -> dict[str, str | None]:
    sender_name, sender_email = parseaddr(message.get("From", ""))
    subject = str(make_header(decode_header(message.get("Subject", "")))) if message.get("Subject") else None
    body = _extract_body(message)

    return {
        "sender_name": sender_name or None,
        "sender_email": sender_email,
        "subject": subject,
        "body": body or "(empty email body)",
    }


def _extract_body(message) -> str:
    if message.is_multipart():
        parts = []
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "")
            if content_type == "text/plain" and "attachment" not in disposition.lower():
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                if payload:
                    parts.append(payload.decode(charset, errors="replace").strip())
        return "\n\n".join(part for part in parts if part)

    payload = message.get_payload(decode=True)
    if not payload:
        return ""
    charset = message.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace").strip()
