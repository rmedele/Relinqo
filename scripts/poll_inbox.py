from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import SessionLocal
from app.inbox_poll import poll_inbox


if __name__ == "__main__":
    db = SessionLocal()
    try:
        print(poll_inbox(db))
    finally:
        db.close()
