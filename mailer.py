"""Письма с кодами входа (портал и клиенты сайта). Настройки — только в переменных проекта, значения никуда не выводим.

Способы отправки, по порядку (первый сработавший):
1. SMTP — своя бесплатная почта с паролем приложения: SMTP_HOST (smtp.gmail.com, smtp.yandex.ru, smtp.mail.ru),
   SMTP_PORT (465 по умолчанию или 587), SMTP_USER (полный адрес), SMTP_PASSWORD, SMTP_FROM (необязательно).
2. Brevo — по HTTPS, если хостинг закрыл SMTP: BREVO_API_KEY и BREVO_FROM (адрес отправителя, подтверждённый в Brevo).
3. Resend — по HTTPS: RESEND_API_KEY. Без своего домена (RESEND_FROM на resend.dev) Resend пишет только на почту
   владельца аккаунта, поэтому клиентам такой способ кодов не шлёт (client_mail_on)."""
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


class ResendError(Exception):
    """Resend отказал: код ответа (ключ и текст письма в сообщение не попадают)."""

    def __init__(self, status: int):
        super().__init__(f"Resend {status}")
        self.status = status


def _resend_on() -> bool:
    return bool(os.getenv("RESEND_API_KEY"))


def _smtp_on() -> bool:
    return all(os.getenv(k) for k in REQUIRED)


def _brevo_on() -> bool:
    return bool(os.getenv("BREVO_API_KEY") and os.getenv("BREVO_FROM"))


def configured() -> bool:
    """Письмо уйдёт хоть куда-то (хотя бы владельцу: подходит для кода входа администратора)."""
    return _smtp_on() or _brevo_on() or _resend_on()


def client_mail_on() -> bool:
    """Письмо уйдёт на любой адрес клиента. Resend с тестовым адресом resend.dev пишет только владельцу — не считается."""
    own_resend = _resend_on() and "@" in (os.getenv("RESEND_FROM") or "") and "resend.dev" not in os.getenv("RESEND_FROM", "")
    return _smtp_on() or _brevo_on() or own_resend


def missing() -> list[str]:
    """Каких переменных не хватает (только имена: значения никуда не выводим)."""
    if _resend_on() or _brevo_on():
        return []
    return [k for k in REQUIRED if not os.getenv(k)]


class BrevoError(Exception):
    """Brevo отказал: код ответа (ключ и текст письма в сообщение не попадают)."""

    def __init__(self, status: int):
        super().__init__(f"Brevo {status}")
        self.status = status


def brevo_payload(to: str, subject: str, body: str) -> dict:
    return {"sender": {"name": "Зооконсьерж", "email": os.getenv("BREVO_FROM", "")},
            "to": [{"email": to}], "subject": subject, "textContent": body}


async def _brevo(to: str, subject: str, body: str) -> None:
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        async with s.post("https://api.brevo.com/v3/smtp/email", json=brevo_payload(to, subject, body),
                          headers={"api-key": os.environ["BREVO_API_KEY"], "accept": "application/json"}) as r:
            if r.status >= 300:
                raise BrevoError(r.status)


def resend_payload(to: str, subject: str, body: str) -> dict:
    sender = os.getenv("RESEND_FROM") or "Зооконсьерж <onboarding@resend.dev>"
    return {"from": sender, "to": [to], "subject": subject, "text": body}


async def _resend(to: str, subject: str, body: str) -> None:
    """Письмо через HTTPS (Resend) — альтернативный способ, если SMTP недоступен."""
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        async with s.post("https://api.resend.com/emails", json=resend_payload(to, subject, body),
                          headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"}) as r:
            if r.status >= 300:
                raise ResendError(r.status)


def password_help() -> tuple[str, str] | None:
    """(название почты, адрес, где создать пароль приложения) по SMTP_HOST; None — почта незнакомая."""
    host = (os.getenv("SMTP_HOST") or "").lower()
    for key, name, url in PASSWORD_PAGES:
        if key in host:
            return name, url
    return None


def explain(exc: Exception) -> str:
    """Причина отказа простыми словами (без адресов и паролей)."""
    if isinstance(exc, BrevoError):
        return ("Brevo не принял ключ BREVO_API_KEY" if exc.status in (401, 403)
                else f"Brevo отказал в отправке (код {exc.status}): проверьте, что адрес BREVO_FROM подтверждён в Brevo")
    if isinstance(exc, ResendError):
        return ("Resend не принял ключ RESEND_API_KEY" if exc.status in (401, 403)
                else f"Resend отказал в отправке (код {exc.status}): письмо можно отправлять только на адрес, на который зарегистрирован Resend")
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
    ways = []
    if _smtp_on():
        ways.append(("SMTP", lambda: asyncio.to_thread(_send_sync, to, subject, body)))
    if _brevo_on():
        ways.append(("Brevo", lambda: _brevo(to, subject, body)))
    if _resend_on():
        ways.append(("Resend", lambda: _resend(to, subject, body)))
    reasons = []
    for name, go in ways:                       # первый сработавший; хостинг закрыл SMTP — пробуем по HTTPS
        try:
            await go()
            return True, ""
        except Exception as e:
            log.warning("Письмо не отправлено через %s: %s", name, type(e).__name__)
            reasons.append(explain(e))
    return False, "; ".join(reasons)


async def send(to: str, subject: str, body: str) -> bool:
    return (await send_checked(to, subject, body))[0]
