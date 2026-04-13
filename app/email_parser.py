import re
from email import policy
from email.parser import Parser
from email.utils import parseaddr
from typing import Any

PHONE_RE = re.compile(r"(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})")
LOCATION_RE = re.compile(r"\b(?:in|at|near)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,3})")
NAME_RE = re.compile(r"(?:my name is|this is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", re.IGNORECASE)


def parse_lead_fields(payload: dict[str, Any]) -> dict[str, Any]:
    body = payload.get("body", "") or ""
    sender_name = payload.get("sender_name")
    if not sender_name:
        name_match = NAME_RE.search(body)
        sender_name = name_match.group(1) if name_match else None

    phone_match = PHONE_RE.search(body)
    location_match = LOCATION_RE.search(body)

    return {
        "sender_name": sender_name,
        "phone": phone_match.group(0) if phone_match else None,
        "location": location_match.group(1) if location_match else None,
    }


def parse_forwarded_email(raw_email: str) -> dict[str, str | None]:
    message = Parser(policy=policy.default).parsestr(raw_email)
    sender_name, sender_email = parseaddr(message.get("From", ""))
    subject = message.get("Subject", "") or ""

    body = _extract_body(message)
    if not body:
        body = raw_email

    return {
        "sender_name": sender_name or None,
        "sender_email": sender_email or None,
        "subject": subject.strip() or None,
        "body": body.strip() or raw_email.strip(),
    }


def _extract_body(message) -> str:
    if message.is_multipart():
        parts = []
        for part in message.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain" and not part.get_filename():
                try:
                    parts.append(part.get_content().strip())
                except Exception:  # noqa: BLE001
                    continue
        return "\n\n".join(part for part in parts if part)

    try:
        return message.get_content().strip()
    except Exception:  # noqa: BLE001
        return ""
