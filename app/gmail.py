import base64
import logging
from datetime import datetime, timezone
from email.mime.text import MIMEText

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from app.models import InboxMessage, OrgSettings
from app.schemas import LeadIngestRequest

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def gmail_configured(org_settings: OrgSettings | None) -> bool:
    if not org_settings:
        return False
    return bool(org_settings.google_oauth_refresh_token)


def _get_credentials(org_settings: OrgSettings, db: Session | None = None) -> Credentials:
    from app.config import settings

    creds = Credentials(
        token=org_settings.google_oauth_access_token or None,
        refresh_token=org_settings.google_oauth_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=SCOPES,
    )

    if creds.expired or not creds.valid:
        creds.refresh(GoogleAuthRequest())
        org_settings.google_oauth_access_token = creds.token
        org_settings.google_oauth_token_expires_at = creds.expiry.replace(tzinfo=timezone.utc) if creds.expiry else None
        if db:
            db.commit()

    return creds


def get_gmail_service(org_settings: OrgSettings, db: Session | None = None):
    creds = _get_credentials(org_settings, db)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def gmail_poll_inbox(db: Session, org_id: int, org_settings: OrgSettings, limit: int = 10) -> dict:
    try:
        service = get_gmail_service(org_settings, db)
        results = service.users().messages().list(
            userId="me", q="is:unread", maxResults=limit
        ).execute()

        messages = results.get("messages", [])
        if not messages:
            return {"ok": True, "created": 0, "skipped": 0, "lead_ids": []}

        created = []
        skipped = 0

        for msg_ref in messages:
            msg_id = msg_ref["id"]
            external_id = f"gmail-{msg_id}"

            exists = db.query(InboxMessage).filter(InboxMessage.external_id == external_id).first()
            if exists:
                skipped += 1
                continue

            msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
            parsed = _parse_gmail_message(service, msg, msg_id)

            if not parsed["sender_email"]:
                skipped += 1
                continue

            from app.routes.leads import ingest_lead
            lead = ingest_lead(
                LeadIngestRequest(
                    source="gmail_api",
                    sender_name=parsed["sender_name"],
                    sender_email=parsed["sender_email"],
                    subject=parsed["subject"],
                    body=parsed["body"],
                ),
                db,
                org_id=org_id,
                org_settings=org_settings,
                attachments=parsed.get("attachments"),
            )
            db.add(InboxMessage(org_id=org_id, source="gmail_api", external_id=external_id, lead_id=lead.id))
            db.commit()
            created.append(lead.id)

            # Mark as read in Gmail
            try:
                service.users().messages().modify(
                    userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}
                ).execute()
            except Exception:
                logger.warning("Failed to mark message %s as read", msg_id)

        return {"ok": True, "created": len(created), "skipped": skipped, "lead_ids": created}

    except Exception:
        logger.exception("Gmail poll failed")
        return {"ok": False, "error": "Gmail API poll failed"}


def gmail_send_email(
    to_email: str, subject: str, body: str, org_settings: OrgSettings, db: Session | None = None
) -> tuple[bool, str]:
    try:
        service = get_gmail_service(org_settings, db)

        message = MIMEText(body)
        message["to"] = to_email
        message["subject"] = subject
        if org_settings.smtp_from_email:
            message["from"] = org_settings.smtp_from_email
        elif org_settings.google_oauth_email:
            message["from"] = org_settings.google_oauth_email

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return True, "sent"

    except Exception:
        logger.exception("Gmail send failed")
        return False, "Gmail API send failed"


IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_PHOTO_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_PHOTOS_PER_EMAIL = 3


def _extract_image_attachments(service, msg: dict, msg_id: str) -> list[dict]:
    """Extract image attachments from a Gmail message."""
    attachments = []
    _walk_parts_for_images(service, msg.get("payload", {}), msg_id, attachments)
    return attachments[:MAX_PHOTOS_PER_EMAIL]


def _walk_parts_for_images(service, payload: dict, msg_id: str, attachments: list) -> None:
    """Recursively walk message parts to find image attachments."""
    mime_type = payload.get("mimeType", "")

    if mime_type in IMAGE_MIME_TYPES:
        filename = payload.get("filename") or "image.jpg"
        body = payload.get("body", {})
        data = None

        if body.get("data"):
            # Small inline image
            raw = base64.urlsafe_b64decode(body["data"])
            if len(raw) <= MAX_PHOTO_SIZE:
                data = raw
        elif body.get("attachmentId"):
            # Large attachment — fetch separately
            try:
                att = service.users().messages().attachments().get(
                    userId="me", messageId=msg_id, id=body["attachmentId"]
                ).execute()
                raw = base64.urlsafe_b64decode(att["data"])
                if len(raw) <= MAX_PHOTO_SIZE:
                    data = raw
            except Exception:
                logger.warning("Failed to fetch attachment %s from message %s", body["attachmentId"], msg_id)

        if data and len(attachments) < MAX_PHOTOS_PER_EMAIL:
            attachments.append({"filename": filename, "mime_type": mime_type, "data": data})

    for part in payload.get("parts", []):
        _walk_parts_for_images(service, part, msg_id, attachments)


def _parse_gmail_message(service, msg: dict, msg_id: str) -> dict[str, str | None]:
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}

    sender_name = None
    sender_email = None
    from_header = headers.get("from", "")
    if "<" in from_header and ">" in from_header:
        sender_name = from_header[:from_header.index("<")].strip().strip('"')
        sender_email = from_header[from_header.index("<") + 1:from_header.index(">")]
    else:
        sender_email = from_header.strip()

    subject = headers.get("subject")
    body = _extract_gmail_body(msg.get("payload", {}))
    attachments = _extract_image_attachments(service, msg, msg_id)

    return {
        "sender_name": sender_name or None,
        "sender_email": sender_email or None,
        "subject": subject,
        "body": body or "(empty email body)",
        "attachments": attachments,
    }


def _extract_gmail_body(payload: dict) -> str:
    # Try to get plain text body directly
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    # Walk multipart parts
    parts = payload.get("parts", [])
    for part in parts:
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")

    # Recurse into nested multipart
    for part in parts:
        if part.get("mimeType", "").startswith("multipart/"):
            result = _extract_gmail_body(part)
            if result:
                return result

    return ""
