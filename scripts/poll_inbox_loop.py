"""Continuously poll the IMAP inbox on an interval.

Designed to run as a long-lived Docker container sidecar, replacing
the systemd timer used in bare-metal deployments.
"""

import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.database import SessionLocal
from app.inbox_poll import poll_inbox

POLL_INTERVAL = int(os.environ.get("INBOX_POLL_INTERVAL", "60"))

if __name__ == "__main__":
    if not settings.inbox_poll_enabled:
        print("INBOX_POLL_ENABLED is false — poller exiting.")
        sys.exit(0)

    print(f"Inbox poller started (interval={POLL_INTERVAL}s)")
    while True:
        db = SessionLocal()
        try:
            result = poll_inbox(db)
            if result.get("ok"):
                created = result.get("created", 0)
                if created:
                    print(f"Polled: {created} new lead(s)")
            else:
                print(f"Poll error: {result.get('error')}")
        except Exception as exc:
            print(f"Poll exception: {exc}")
        finally:
            db.close()
        time.sleep(POLL_INTERVAL)
