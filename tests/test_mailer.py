import asyncio
from typing import ClassVar

import pytest

from app.core.config import Settings
from app.services import mailer
from app.services.mailer import MailError, build_invitation_message, send_mail


def _settings(**overrides) -> Settings:
    return Settings(
        auth_username="u", auth_password="p", session_secret_key="s" * 20,
        invitation_delivery="smtp", smtp_host="smtp.example.com", smtp_username="ft@example.com",
        smtp_password="clave", **overrides,
    )


class FakeSMTP:
    instances: ClassVar[list["FakeSMTP"]] = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.calls = host, port, []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        self.calls.append(("send", msg["To"]))


def test_send_over_ssl_port_465(monkeypatch) -> None:
    FakeSMTP.instances = []
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)
    settings = _settings()
    msg = build_invitation_message(settings, "e@x.cl", "Retail", "http://h/front/invite/t", "2026-10-27")

    asyncio.run(send_mail(settings, msg))

    server = FakeSMTP.instances[0]
    assert (server.host, server.port) == ("smtp.example.com", 465)
    assert server.calls == [("login", "ft@example.com", "clave"), ("send", "e@x.cl")]
    assert msg["From"] == "ft@example.com"


def test_starttls_when_ssl_disabled(monkeypatch) -> None:
    FakeSMTP.instances = []
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    settings = _settings(smtp_use_ssl=False, smtp_port=587)
    msg = build_invitation_message(settings, "e@x.cl", "Retail", "http://h", "mañana")

    asyncio.run(send_mail(settings, msg))

    assert FakeSMTP.instances[0].calls[0] == "starttls"


def test_network_errors_become_mail_error(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", boom)
    settings = _settings()
    with pytest.raises(MailError, match="refused"):
        asyncio.run(send_mail(settings, build_invitation_message(settings, "e@x.cl", "R", "l", "x")))
