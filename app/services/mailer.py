"""Envío por SMTP de las invitaciones a expertos (RF-12, modo `smtp`)."""

import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import Settings


class MailError(Exception):
    pass


def build_invitation_message(
    settings: Settings, to: str, project_name: str, link: str, expires: str
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"Invitación a responder: {project_name}"
    msg["From"] = settings.smtp_from or settings.smtp_username or ""
    msg["To"] = to
    msg.set_content(
        "Hola:\n\n"
        f"Te invitamos a ingresar tu evaluación para el proyecto «{project_name}».\n\n"
        "Abre el siguiente enlace para subir las matrices CC, CE y EE:\n\n"
        f"{link}\n\n"
        f"El enlace es personal, sirve para un solo envío y vence el {expires}.\n"
    )
    return msg


def _send_sync(settings: Settings, msg: EmailMessage) -> None:
    context = ssl.create_default_context()
    if settings.smtp_use_ssl:
        # Puerto 465: TLS desde el inicio de la conexión.
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout, context=context
        )
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout)
    with server:
        if not settings.smtp_use_ssl:
            server.starttls(context=context)
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password or "")
        server.send_message(msg)


async def send_mail(settings: Settings, msg: EmailMessage) -> None:
    """Envía el mensaje sin bloquear el event loop. Lanza MailError ante cualquier falla SMTP o de red."""
    if not settings.smtp_host:
        raise MailError("SMTP no configurado (falta SMTP_HOST)")
    try:
        await asyncio.to_thread(_send_sync, settings, msg)
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"No se pudo enviar el correo: {e}") from e
