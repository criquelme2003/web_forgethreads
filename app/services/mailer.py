"""Envío por SMTP de las invitaciones a expertos y de los enlaces de activación de admins (RF-12, RF-48)."""

import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import Settings


class MailError(Exception):
    pass


def _message(settings: Settings, to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from or settings.smtp_username or ""
    msg["To"] = to
    msg.set_content(body)
    return msg


def build_invitation_message(
    settings: Settings,
    to: str,
    project_name: str,
    link: str,
    expires: str,
    nombre: str | None = None,
    edit_days: int = 0,
) -> EmailMessage:
    edit = (
        f"Después de enviarlas, con el mismo enlace puedes modificarlas durante {edit_days} días.\n"
        if edit_days
        else ""
    )
    return _message(
        settings,
        to,
        f"Invitación a responder: {project_name}",
        f"Hola{' ' + nombre if nombre else ''}:\n\n"
        f"Te invitamos a ingresar tu evaluación para el proyecto «{project_name}».\n\n"
        "Abre el siguiente enlace para subir las matrices CC, CE y EE:\n\n"
        f"{link}\n\n"
        f"El enlace es personal y vence el {expires} si no respondes.\n"
        f"{edit}",
    )


def build_activation_message(
    settings: Settings, to: str, nombre: str, link: str, expires: str
) -> EmailMessage:
    """Enlace para que un admin nuevo elija su contraseña, o para restablecerla (RF-48)."""
    return _message(
        settings,
        to,
        "Acceso de administrador a forgethreads",
        f"Hola{' ' + nombre if nombre else ''}:\n\n"
        "Tienes una cuenta de administrador en forgethreads. Para elegir tu contraseña, abre:\n\n"
        f"{link}\n\n"
        f"El enlace es personal, sirve una sola vez y vence el {expires}.\n",
    )


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
