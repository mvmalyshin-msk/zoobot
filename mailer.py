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


def configured() -> bool:
    return all(os.getenv(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"))


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


async def send(to: str, subject: str, body: str) -> bool:
    if not configured():
        return False
    try:
        await asyncio.to_thread(_send_sync, to, subject, body)
        return True
    except Exception as e:  # пароль/сервер неверные — в журнал без подробностей письма
        log.warning("Письмо не отправлено: %s", type(e).__name__)
        return False
