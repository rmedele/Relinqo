#!/usr/bin/env python3
"""Cron-friendly script to process due follow-ups and auto-close stale leads."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.followups import run_followups


def main():
    db = SessionLocal()
    try:
        result = run_followups(db)
        print(result)
    finally:
        db.close()


if __name__ == "__main__":
    main()
