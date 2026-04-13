import json
from pathlib import Path

from app.database import SessionLocal
from app.routes.leads import ingest_lead
from app.schemas import LeadIngestRequest

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "demo_plumbing_payloads.json"


if __name__ == "__main__":
    payloads = json.loads(DATA_PATH.read_text())
    db = SessionLocal()
    try:
        for item in payloads:
            ingest_lead(LeadIngestRequest(**item["payload"]), db)
        print(f"Seeded {len(payloads)} plumbing demo leads")
    finally:
        db.close()
