from app.classifier import classify_lead


def test_classify_urgent_request():
    result = classify_lead({
        "sender_name": "Jamie",
        "sender_email": "jamie@example.com",
        "subject": "Emergency help",
        "body": "We have an active leak and no heat. Please call ASAP.",
    })
    assert result.category == "urgent_request"
    assert result.urgency_score == 5


def test_classify_spam():
    result = classify_lead({
        "sender_name": "Spammer",
        "sender_email": "spam@example.com",
        "subject": "SEO boost",
        "body": "We can sell backlinks and casino traffic.",
    })
    assert result.category == "spam"
