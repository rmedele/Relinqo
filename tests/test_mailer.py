from app.mailer import send_email
from app.models import OrgSettings


class FakeSmtp:
    instances = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in = None
        self.sent_messages = []
        FakeSmtp.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.logged_in = (username, password)

    def send_message(self, message):
        self.sent_messages.append(message)


class FakeSmtpSsl(FakeSmtp):
    instances = []

    def __init__(self, host, port, timeout):
        super().__init__(host, port, timeout)
        FakeSmtpSsl.instances.append(self)


def _org_settings(port: int) -> OrgSettings:
    return OrgSettings(
        org_id=1,
        smtp_host="mail.relinqo.com",
        smtp_port=port,
        smtp_username="support@relinqo.com",
        smtp_password="secret",
        smtp_use_tls=True,
        smtp_from_email="support@relinqo.com",
    )


def test_send_email_uses_implicit_ssl_for_port_465(monkeypatch):
    FakeSmtp.instances = []
    FakeSmtpSsl.instances = []
    monkeypatch.setattr("smtplib.SMTP", FakeSmtp)
    monkeypatch.setattr("smtplib.SMTP_SSL", FakeSmtpSsl)

    sent, message = send_email(
        to_email="customer@example.com",
        subject="Test",
        body="Body",
        org_settings=_org_settings(465),
    )

    assert sent is True
    assert message == "sent"
    assert len(FakeSmtpSsl.instances) == 1
    assert FakeSmtpSsl.instances[0].started_tls is False


def test_send_email_uses_starttls_for_port_587(monkeypatch):
    FakeSmtp.instances = []
    FakeSmtpSsl.instances = []
    monkeypatch.setattr("smtplib.SMTP", FakeSmtp)
    monkeypatch.setattr("smtplib.SMTP_SSL", FakeSmtpSsl)

    sent, message = send_email(
        to_email="customer@example.com",
        subject="Test",
        body="Body",
        org_settings=_org_settings(587),
    )

    assert sent is True
    assert message == "sent"
    assert len(FakeSmtpSsl.instances) == 0
    assert FakeSmtp.instances[0].started_tls is True
