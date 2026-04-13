import json
from pathlib import Path

from app.database import SessionLocal
from app.routes.leads import ingest_lead
from app.schemas import LeadIngestRequest

PAYLOADS = [
    {"source": "website_form", "sender_name": "Megan Foster", "sender_email": "megan.foster82@gmail.com", "subject": "Basement sump issue", "body": "Our sump pump quit and water is collecting near the furnace in Sherwood Park. Call me at 780-555-0142."},
    {"source": "google_ads", "sender_name": "Daniel Ruiz", "sender_email": "daniel.ruiz.home@gmail.com", "subject": "Quote for hot water tank replacement", "body": "Need quote to replace a 50 gallon hot water tank in Edmonton. My number is 587-555-0188."},
    {"source": "email", "sender_name": "Alyssa Reed", "sender_email": "alyssa@reedpm.ca", "subject": "Furnace blowing cold air", "body": "Tenant says furnace is blowing cold air in our St. Albert duplex. Need morning service."},
    {"source": "facebook", "sender_name": "Noah Patel", "sender_email": "npatel1989@gmail.com", "subject": "AC stopped cooling", "body": "AC fan runs but house stays warm in Edmonton. Can someone come this week?"},
    {"source": "web_chat", "sender_name": "Jamie Lee", "sender_email": "jamielee.homeowner@gmail.com", "subject": "Breaker keeps tripping", "body": "Half the kitchen outlets lost power in Leduc and the breaker keeps tripping."},
    {"source": "website_form", "sender_name": "Kara Mitchell", "sender_email": "kara.mitchell23@yahoo.com", "subject": "Exterior lights estimate", "body": "Need estimate in Spruce Grove for two exterior fixtures and a bathroom timer replacement."},
    {"source": "thumbtack", "sender_name": "Brent Holloway", "sender_email": "brentholloway@outlook.com", "subject": "Missing shingles after wind", "body": "Missing shingles on detached home in Fort Saskatchewan. No active leak yet."},
    {"source": "email", "sender_name": "Priya Nair", "sender_email": "priya@mapledaycare.ca", "subject": "Roof leak over classroom", "body": "Small active leak around a roof vent over one classroom in Edmonton. Need assessment before snowfall."},
    {"source": "google_ads", "sender_name": "Chris Moreno", "sender_email": "cmoreno.family@gmail.com", "subject": "Garage door only opens a foot", "body": "Garage door opens about a foot then stops in Stony Plain. Need repair before Monday."},
    {"source": "website_form", "sender_name": "Lena Brooks", "sender_email": "lenabrooks77@gmail.com", "subject": "Insulated garage door quote", "body": "Interested in replacing our old single garage door with an insulated double in Edmonton."},
    {"source": "facebook", "sender_name": "Tyler Benson", "sender_email": "tyler.benson.home@gmail.com", "subject": "Water damage in kitchen", "body": "Dishwasher line leaked overnight in Beaumont and floor is wet. Cabinets may be affected."},
    {"source": "email", "sender_name": "Erin Walsh", "sender_email": "erin@walshholdings.ca", "subject": "Moisture check request", "body": "Minor sprinkler discharge in a storage room in Nisku last week. Looking for moisture inspection and cleanup quote."},
    {"source": "web_chat", "sender_name": "Sara Kim", "sender_email": "sarakim.home@gmail.com", "subject": "Slow kitchen drain", "body": "Kitchen sink drains slowly and gurgles when dishwasher runs in Edmonton. Not urgent."},
    {"source": "website_form", "sender_name": "Mark D'Souza", "sender_email": "mark.dsouza81@gmail.com", "subject": "Seasonal furnace tune-up", "body": "Need seasonal furnace tune-up for a townhouse in Edmonton we just moved into."},
    {"source": "google_ads", "sender_name": "Olivia Grant", "sender_email": "ogrant.homeowner@gmail.com", "subject": "Lights flicker when microwave runs", "body": "Dining room lights flicker when microwave is on in Sherwood Park. Should an electrician check this?"},
    {"source": "thumbtack", "sender_name": "Ben Carver", "sender_email": "ben.carver.roof@gmail.com", "subject": "Garage roof estimate", "body": "Need estimate for replacing asphalt shingles on a detached garage in Morinville this spring."},
    {"source": "website_form", "sender_name": "Julia Park", "sender_email": "jpark.shortnote@gmail.com", "subject": "Need help with something", "body": "Need help with something at the house in Edmonton. Not sure who handles it. Can someone call me?"},
    {"source": "email", "sender_name": "Kevin Morris", "sender_email": "kevin@rankrocketmedia.co", "subject": "Guaranteed SEO traffic", "body": "We can put your business at the top of Google in 7 days with exclusive leads and backlinks."},
]


if __name__ == "__main__":
    db = SessionLocal()
    try:
        ids = []
        for item in PAYLOADS:
            lead = ingest_lead(LeadIngestRequest(**item), db)
            ids.append(lead.id)
        print(json.dumps({"seeded": len(ids), "lead_ids": ids}))
    finally:
        db.close()
