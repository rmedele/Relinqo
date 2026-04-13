from app.database import SessionLocal
from app.routes.leads import ingest_lead
from app.schemas import LeadIngestRequest

samples = [
    LeadIngestRequest(source="demo", sender_name="Jamie", sender_email="jamie@example.com", subject="Need emergency plumber", body="Burst pipe in basement. Please call me ASAP at 780-555-1212 in Edmonton."),
    LeadIngestRequest(source="demo", sender_name="Taylor", sender_email="taylor@example.com", subject="Quote request", body="Looking for an estimate for roof repair in Sherwood Park."),
]

if __name__ == "__main__":
    db = SessionLocal()
    try:
        for sample in samples:
            ingest_lead(sample, db)
        print("Seeded demo leads")
    finally:
        db.close()
