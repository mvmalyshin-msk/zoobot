"""Единые правила времени заказа: сайт, Telegram-бот и админка считают по ним одинаково.

• Услуга оказывается с 06:00 до 22:00 (выгул, зооняня, зоотакси).
• Администраторы обрабатывают заявки с 09:00 до 20:00.
• На подбор исполнителей нужно минимум 2 часа: заказ принимается не раньше чем через 2 часа.
• «Экспресс» (галочка, доступна любому клиенту) снимает только эти 2 часа: заказ можно подать за считанные минуты (EXPRESS_AHEAD),
  но только пока администраторы работают. Ночью экспресса нет, график работы он не отменяет.
Тексты пояснений подстраиваются под ситуацию клиента (см. check)."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta

SERVICE_FROM, SERVICE_TO = 6, 22          # часы оказания услуги
ADMIN_FROM, ADMIN_TO = 9, 20              # часы работы администраторов
BUFFER = timedelta(hours=2)               # время на подбор трёх исполнителей
EXPRESS_AHEAD = timedelta(minutes=15)     # минимум до начала при «Экспресс»
MAX_AHEAD = timedelta(days=366)
STEP = 15                                 # минуты: время заказа кратно 15

SERVICE_NOUN = {"walk": "Выгул собак доступен", "sitter": "Визиты зооняни доступны", "taxi": "Зоотакси доступно"}


def _minutes(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def admin_open(dt: datetime) -> bool:
    return ADMIN_FROM * 60 <= _minutes(dt) < ADMIN_TO * 60


def express_available(now: datetime) -> bool:
    """Экспресс работает, пока администраторы на связи."""
    return admin_open(now)


def in_service(dt: datetime) -> bool:
    m = _minutes(dt)
    return SERVICE_FROM * 60 <= m <= SERVICE_TO * 60


def _ceil_quarter(dt: datetime) -> datetime:
    dt = dt.replace(second=0, microsecond=0) + (timedelta(minutes=STEP) if (dt.second or dt.microsecond) else timedelta())
    extra = (-_minutes(dt)) % STEP
    return dt + timedelta(minutes=extra)


def next_admin_open(now: datetime) -> datetime:
    if admin_open(now):
        return now
    morning = now.replace(hour=ADMIN_FROM, minute=0, second=0, microsecond=0)
    return morning if now < morning else morning + timedelta(days=1)


def earliest(now: datetime, express: bool = False) -> datetime:
    """Самое раннее время начала. Вне графика админов: ближайшее утро 09:00 + 2 часа (экспресс ночью не работает)."""
    if admin_open(now):
        return _ceil_quarter(now + (EXPRESS_AHEAD if express else BUFFER))
    return _ceil_quarter(next_admin_open(now) + BUFFER)


def fmt_earliest(dt: datetime, now: datetime) -> str:
    t = dt.strftime("%H:%M")
    if dt.date() == now.date():
        return t
    if dt.date() == now.date() + timedelta(days=1):
        return f"завтра в {t}"
    return f"{dt:%d.%m} в {t}"


@dataclass(frozen=True)
class Verdict:
    ok: bool
    code: str                    # ok | past | far | out_of_service | admin_closed | too_soon
    message: str                 # пояснение клиенту (пусто, если всё в порядке)
    earliest: datetime
    express_blocked: bool = False  # галочку «Экспресс» поставили, когда администраторы не работают


def check(dt: datetime, now: datetime, cat: str, express: bool = False) -> Verdict:
    """Можно ли заказать на время dt (время с поясом) в момент now. Сообщения написаны под конкретную ситуацию."""
    eff = express and express_available(now)
    blocked = express and not eff
    e = earliest(now, eff)
    when = fmt_earliest(e, now)
    if dt < now.replace(second=0, microsecond=0):
        return Verdict(False, "past", f"Это время уже прошло. Выберите время не раньше {when}.", e, blocked)
    if dt > now + MAX_AHEAD:
        return Verdict(False, "far", "Это слишком далеко: принимаем заказы не позже чем на год вперёд.", e, blocked)
    if not in_service(dt):
        noun = SERVICE_NOUN.get(cat, "Заказы принимаются на время")
        return Verdict(False, "out_of_service",
                       f"{noun} с {SERVICE_FROM:02d}:00 до {SERVICE_TO:02d}:00. Пожалуйста, выберите время в рамках этого окна.", e, blocked)
    if dt < e:
        if not admin_open(now):
            return Verdict(False, "admin_closed",
                           f"Нам нужно 2 часа для подбора лучших предложений. Администраторы начинают обработку с {ADMIN_FROM:02d}:00 утра. "
                           f"Ближайшее доступное время для вашего заказа — {when}.", e, blocked)
        if eff:
            return Verdict(False, "too_soon",
                           f"Даже с «Экспресс» нужно хотя бы {int(EXPRESS_AHEAD.total_seconds() // 60)} минут на подбор. "
                           f"Выберите время не раньше {when}.", e, blocked)
        return Verdict(False, "too_soon",
                       f"На подбор 3 лучших исполнителей нам требуется минимум 2 часа. Пожалуйста, выберите время не раньше {when} "
                       f"или поставьте галочку «Экспресс».", e, blocked)
    return Verdict(True, "ok", "", e, blocked)


def slots(day: date, now: datetime, cat: str, express: bool = False, step: int = STEP) -> list[dict]:
    """Время выбранного дня для календаря: [{t: '06:00', ok: True/False}], недоступное — ok False (затемняется)."""
    out = []
    for m in range(SERVICE_FROM * 60, SERVICE_TO * 60 + 1, step):
        dt = datetime(day.year, day.month, day.day, m // 60, m % 60, tzinfo=now.tzinfo)
        out.append({"t": f"{m // 60:02d}:{m % 60:02d}", "ok": check(dt, now, cat, express).ok})
    return out
