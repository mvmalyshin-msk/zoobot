"""Дежурные способы оплаты без ссылки: СБП по номеру и перевод на карту.
Реквизиты берутся из переменных Railway (PAY_SBP_PHONE, PAY_SBP_BANK, PAY_CARD, PAY_RECIPIENT), в коде и в чате их нет.
Если переменной нет, способ клиенту не показывается."""
import os


def _v(env, key: str) -> str:
    return (env.get(key) or "").strip()


def available(env, kind: str) -> bool:
    return bool(_v(env, "PAY_SBP_PHONE" if kind == "sbp" else "PAY_CARD"))


def sbp_text(env, order_id: int, amount: int) -> str:
    bank = f" ({_v(env, 'PAY_SBP_BANK')})" if _v(env, "PAY_SBP_BANK") else ""
    who = f"\nПолучатель: {_v(env, 'PAY_RECIPIENT')}" if _v(env, "PAY_RECIPIENT") else ""
    return (f"📱 Оплата по СБП, заказ №{order_id}\nПереведите {amount} ₽ по номеру {_v(env, 'PAY_SBP_PHONE')}{bank}.{who}\n"
            f"В комментарии укажите: заказ №{order_id}. Потом нажмите «Я оплатил».")


def card_text(env, order_id: int, amount: int) -> str:
    who = f"\nПолучатель: {_v(env, 'PAY_RECIPIENT')}" if _v(env, "PAY_RECIPIENT") else ""
    return (f"💳 Перевод на карту, заказ №{order_id}\nПереведите {amount} ₽ на карту {_v(env, 'PAY_CARD')}.{who}\n"
            f"В комментарии укажите: заказ №{order_id}. Потом нажмите «Я оплатил».")


def methods(order_id: int) -> list[str]:
    """Какие альтернативные способы настроены сейчас."""
    return [k for k in ("sbp", "card") if available(os.environ, k)]
