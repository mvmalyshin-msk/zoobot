"""Карточки услуг на главной: четыре услуги на первом экране, цена «от … ₽» из тарифов бота (портал → «Тарифы»),
нажатие ведёт сразу в заявку с выбранной услугой (/request?service=walk). Цены берутся при каждом открытии
страницы, поэтому изменение тарифа сразу видно на сайте."""
import html

import db
import pricing
from utils import rub

ORDER = ("walk", "sitter", "boarding", "taxi")
TITLES = {"walk": "Выгул", "sitter": "Зооняня", "boarding": "Передержка", "taxi": "Зоотакси"}
UNIT = {"выгул": "за прогулку", "час": "в час", "сутки": "в сутки", "поездка": "за поездку"}

ICONS = {
    "walk": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="6" cy="8" r="1.6"/><circle cx="10" cy="5" r="1.6"/><circle cx="14" cy="5" r="1.6"/><circle cx="18" cy="8" r="1.6"/><path d="M12 11c-3.1 0-5.5 2.4-5.5 5.2 0 2 1.4 3.2 3.3 2.5.8-.3 1.4-.7 2.2-.7s1.4.4 2.2.7c1.9.7 3.3-.5 3.3-2.5C17.5 13.4 15.1 11 12 11z"/></svg>',
    "sitter": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M3.5 11 12 4l8.5 7M5.5 9.5V20h13V9.5"/><path d="M12 17.2c-2.6-1.7-3.6-2.8-3.6-4.1a2 2 0 0 1 3.6-1.2 2 2 0 0 1 3.6 1.2c0 1.3-1 2.4-3.6 4.1z"/></svg>',
    "boarding": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M3 18.5V6M3 15h18v3.5M21 15v-2.5a3 3 0 0 0-3-3h-6.5V15"/><circle cx="7.2" cy="11.8" r="1.6"/></svg>',
    "taxi": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M4 17v-4.2l1.7-4.6A2 2 0 0 1 7.6 7h8.8a2 2 0 0 1 1.9 1.2L20 12.8V17M4 17h16M4 17v2.5M20 17v2.5M9.5 4h5"/><circle cx="7.6" cy="13.8" r=".9" fill="currentColor"/><circle cx="16.4" cy="13.8" r=".9" fill="currentColor"/></svg>',
}


async def cheapest_by_category() -> dict[str, dict]:
    """Самый дешёвый вариант каждой услуги с ценой для клиента (база + наценка сервиса; зоотакси — минимальная поездка)."""
    items = await pricing.with_client_price(await db.fetch_all("SELECT * FROM services WHERE active=1 ORDER BY id"))
    out: dict[str, dict] = {}
    for s in items:
        c = s["category"]
        if c not in out or s["from_price"] < out[c]["from_price"]:
            out[c] = s
    return out


def _price(n: int) -> str:
    return "от " + rub(n).replace(" ", " ")


async def cards_html() -> str:
    low = await cheapest_by_category()
    cards = []
    for c in ORDER:
        s = low.get(c)
        price = (f'<span class="price">{_price(s["from_price"])}</span>'
                 f'<span class="unit">{html.escape(UNIT.get(s["unit"], ""))}</span>') if s else '<span class="unit">по запросу</span>'
        label = f'{TITLES[c]}: {_price(s["from_price"]).replace(chr(160), " ")} {UNIT.get(s["unit"], "")}' if s else TITLES[c]
        cards.append(
            f'<a class="service-card {c}" href="/request?service={c}" aria-label="{html.escape(label)}. Оставить заявку">'
            f'<span class="service-icon">{ICONS[c]}</span><h3>{TITLES[c]}</h3>{price}'
            '<span class="go" aria-hidden="true">Заказать →</span></a>')
    return '<div class="services" id="services">' + "".join(cards) + "</div>"
