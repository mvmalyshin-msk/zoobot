"""Письма с кодом входа в портал. Почтовый ящик и пароль приложения задаются только в Railway → Variables:
SMTP_HOST, SMTP_PORT (по умолчанию 465), SMTP_USER, SMTP_PASSWORD, SMTP_FROM (необязательно).
Подходит бесплатная почта: Яндекс (smtp.yandex.ru), Mail.ru (smtp.mail.ru), Gmail (smtp.gmail.com) с паролем приложения."""
import asyncio
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger(__name__)


REQUIRED = ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD")
PASSWORD_PAGES = (("gmail", "Gmail", "https://myaccount.google.com/apppasswords"),
                  ("yandex", "Яндекс Почта", "https://id.yandex.ru/security/app-passwords"),
                  ("mail.ru", "Mail.ru", "https://account.mail.ru/user/2-step-auth/passwords"))


def configured() -> bool:
    return all(os.getenv(k) for k in REQUIRED)


def missing() -> list[str]:
    """Каких переменных не хватает (только имена: значения никуда не выводим)."""
    return [k for k in REQUIRED if not os.getenv(k)]


def password_help() -> tuple[str, str] | None:
    """(название почты, адрес, где создать пароль приложения) по SMTP_HOST; None — почта незнакомая."""
    host = (os.getenv("SMTP_HOST") or "").lower()
    for key, name, url in PASSWORD_PAGES:
        if key in host:
            return name, url
    return None


def explain(exc: Exception) -> str:
    """Причина отказа простыми словами (без адресов и паролей)."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "почтовый сервис не принял пароль: нужен пароль приложения (не обычный пароль от почты), а SMTP_USER — полный адрес ящика"
    if isinstance(exc, (TimeoutError, OSError, smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected)):
        return "не удалось соединиться с почтовым сервером: проверьте SMTP_HOST и SMTP_PORT (465 или 587)"
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "почтовый сервис не принял адрес получателя"
    return f"неизвестная ошибка ({type(exc).__name__})"


def _send_sync(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = os.getenv("SMTP_FROM") or os.environ["SMTP_USER"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    port = int(os.getenv("SMTP_PORT") or 465)
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(os.environ["SMTP_HOST"], port, context=ctx, timeout=20) as s:
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(os.environ["SMTP_HOST"], port, timeout=20) as s:
            s.starttls(context=ctx)
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            s.send_message(msg)


async def send_checked(to: str, subject: str, body: str) -> tuple[bool, str]:
    """(получилось, причина простыми словами). Пароль и текст письма в журнал не попадают."""
    if not configured():
        return False, "не хватает: " + ", ".join(missing())
    try:
        await asyncio.to_thread(_send_sync, to, subject, body)
        return True, ""
    except Exception as e:
        log.warning("Письмо не отправлено: %s", type(e).__name__)
        return False, explain(e)


async def send(to: str, subject: str, body: str) -> bool:
    return (await send_checked(to, subject, body))[0]
