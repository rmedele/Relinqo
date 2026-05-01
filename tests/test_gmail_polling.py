from app.gmail import gmail_poll_inbox


class _MessagesResource:
    def __init__(self):
        self.query = None

    def list(self, *, userId, q, maxResults):
        self.query = q
        return self

    def execute(self):
        return {"messages": []}


class _UsersResource:
    def __init__(self, messages):
        self._messages = messages

    def messages(self):
        return self._messages


class _GmailService:
    def __init__(self):
        self.messages_resource = _MessagesResource()

    def users(self):
        return _UsersResource(self.messages_resource)


def test_gmail_poll_uses_primary_inbox_query(monkeypatch):
    service = _GmailService()
    monkeypatch.setattr("app.gmail.get_gmail_service", lambda org_settings, db: service)

    result = gmail_poll_inbox(db=None, org_id=1, org_settings=object(), limit=10)

    assert result == {"ok": True, "created": 0, "skipped": 0, "lead_ids": []}
    assert "is:unread" in service.messages_resource.query
    assert "in:inbox" in service.messages_resource.query
    assert "category:primary" in service.messages_resource.query
    assert "-category:social" in service.messages_resource.query
    assert "-category:promotions" in service.messages_resource.query
    assert "-category:updates" in service.messages_resource.query
    assert "-category:forums" in service.messages_resource.query
