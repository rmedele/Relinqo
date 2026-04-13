from app.email_parser import parse_lead_fields


def test_parse_phone_and_location():
    result = parse_lead_fields({
        "body": "Hi, my name is Chris Stone. Need a quote at Edmonton. Call me at 780-555-1212.",
    })
    assert result["phone"] == "780-555-1212"
    assert result["location"] == "Edmonton"
    assert result["sender_name"] == "Chris Stone"
