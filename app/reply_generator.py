TEMPLATES = {
    "urgent_request": "Hi {name}, thanks for reaching out. We received your urgent request and will review it right away. If you can, reply with your exact address and the best callback number.",
    "quote_request": "Hi {name}, thanks for reaching out. We’d be happy to review your request. Please reply with 1) the service needed, 2) your location, and 3) any helpful photos or timing details.",
    "general_inquiry": "Hi {name}, thanks for reaching out. We received your message and will review it shortly. If helpful, reply with the service you need and your preferred timeline.",
    "existing_customer": "Hi {name}, thanks for the update. We’ve logged your message and will review your account details before following up. If this is urgent, reply with the best callback number.",
}

BOOKING_SUFFIX = "\n\nIf you’d like to book a time, you can pick a slot that works for you here: {booking_url}"


def generate_reply(category: str, sender_name: str | None, booking_url: str | None = None) -> str:
    name = sender_name or "there"
    template = TEMPLATES.get(category, TEMPLATES["general_inquiry"])
    reply = template.format(name=name)
    if booking_url:
        reply += BOOKING_SUFFIX.format(booking_url=booking_url)
    return reply
