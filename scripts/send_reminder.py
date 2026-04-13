#!/usr/bin/env python3
"""One-shot script to send a reminder email via the app's SMTP config."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.mailer import send_email

send_email(
    to_email="reesemedele@gmail.com",
    subject="Reminder: Fill in Twilio credentials for LeadRelay",
    body=(
        "Hey Reese,\n\n"
        "Reminder to fill in the Twilio SMS credentials in your LeadRelay .env file:\n\n"
        "  TWILIO_ACCOUNT_SID=\n"
        "  TWILIO_AUTH_TOKEN=\n"
        "  TWILIO_FROM_NUMBER=\n"
        "  SMS_ALERT_TO_NUMBER=\n\n"
        "Once set, urgent leads (score >= 4) will trigger SMS alerts to your phone.\n\n"
        "— LeadRelay"
    ),
)
print("Reminder sent.")
