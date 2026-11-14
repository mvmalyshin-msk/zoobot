"""Страницы услуг под поисковые запросы: «выгул собак», «зооняня», «передержка», «зоотакси».

Каждая страница отвечает на один запрос: что за услуга, как проходит, как заказать. Без цен и обещаний, которых
мы не можем выполнить: цена зависит от услуги, питомца и срочности, варианты предлагает администратор.
Лёгкие страницы без внешних скриптов; открываются на любой связи и попадают в карту сайта (seo.PUBLIC_PAGES)."""
import html as _html
import json

from aiohttp import web

import brand
import seo
import webform
from config import config

AREA = "Москве и Московской области"

PAGES = {
    "/vygul-sobak": {
        "title": "Выгул собак в Москве и Московской области — Зооконсьерж",
        "h1": "Выгул собак",
        "service": "Выгул собак",
        "lead": ("Подберём человека, который выгуляет вашу собаку рядом с домом: в рабочие часы, по расписанию или разово. "
                 "Мы работаем в Москве и Московской области."),
        "points": [
            "Прогулка нужной длительности: короткая для щенка или пожилой собаки, длинная для активной.",
            "Можно разово, на время отпуска или командировки, либо регулярно по будням.",
            "Расскажите про характер собаки, привычки и маршрут, чтобы исполнитель знал, чего ждать.",
            "Несколько вариантов по цене и уровню сервиса: вы выбираете подходящий.",
        ],
        "faq": [("Сколько длится прогулка?", "Длительность выбираете вы: от короткой прогулки до долгой. Укажите её в заявке."),
                ("Можно ли выгуливать несколько собак?", "Да, напишите в заявке, сколько у вас собак и какого они размера.")],
    },
    "/zoonyanya": {
        "title": "Зооняня на дому в Москве и Московской области — Зооконсьерж",
        "h1": "Зооняня на дому",
        "service": "Зооняня",
        "lead": ("Зооняня приходит к вам домой: покормит, поиграет, сменит воду и наполнитель, проверит питомца. "
                 "Подбираем её в Москве и Московской области."),
        "points": [
            "Визит на час-два, чтобы покормить и пообщаться с питомцем, или ночь с питомцем у вас дома.",
            "Подходит кошкам, собакам, грызунам и птицам: напишите, кто у вас живёт.",
            "Питомец остаётся в привычной обстановке и не переживает из-за переезда.",
            "Несколько вариантов по цене и уровню сервиса: вы выбираете подходящий.",
        ],
        "faq": [("Чем зооняня отличается от передержки?", "Зооняня приходит к питомцу домой, а при передержке питомец живёт у исполнителя или в зоотеле."),
                ("Можно ли на несколько дней?", "Да, укажите даты в заявке: можно ежедневные визиты или ночёвки.")],
    },
    "/peredergka": {
        "title": "Передержка кошек и собак в Москве и Московской области — Зооконсьерж",
        "h1": "Передержка кошек и собак",
        "service": "Передержка животных",
        "lead": ("Если вы уезжаете в отпуск или командировку, найдём передержку для питомца: у частного исполнителя или в зоотеле "
                 "в Москве и Московской области."),
        "points": [
            "Передержка на несколько дней или недель: даты и условия обсуждаем заранее.",
            "Расскажите про питание, лекарства и привычки питомца, чтобы за ним ухаживали, как дома.",
            "Подберём несколько вариантов: частный дом или зоотель, разные цены и уровень сервиса.",
            "Можно заказать зоотакси, чтобы питомца безопасно доставили на передержку.",
        ],
        "faq": [("Что нужно для передержки?", "Данные о питомце: порода, возраст, питание, лекарства и особенности характера. Об остальном договоримся."),
                ("Можно ли передержать несколько животных?", "Да, укажите их в заявке: подберём подходящий вариант.")],
    },
    "/zootaksi": {
        "title": "Зоотакси в Москве и Московской области — Зооконсьерж",
        "h1": "Зоотакси для питомца",
        "service": "Зоотакси",
        "lead": ("Безопасная перевозка питомца к ветеринару, на выставку, на передержку или в другой город. Заказываем зоотакси "
                 "в Москве и Московской области."),
        "points": [
            "Поездка к ветеринару, груммеру, на выставку, на вокзал или в аэропорт.",
            "Укажите адрес подачи, адрес назначения и время: подберём водителя.",
            "Для животных нужны переноска или пристёгнутая шлейка: расскажите, что есть у вас.",
            "Несколько вариантов по цене и уровню сервиса: вы выбираете подходящий.",
        ],
        "faq": [("Как заказать зоотакси?", "Оставьте заявку в Telegram, на сайте или через короткую форму: укажите адреса и время."),
                ("Можно ли ехать вместе с питомцем?", "Да, напишите об этом в заявке.")],
    },
}


def _e(v) -> str:
    return _html.escape(str(v or ""), quote=True)


CSS = webform.CSS + """
.lead{font-size:1.08rem;line-height:1.5;margin:6px 0 16px}ul.pts{padding-left:20px;margin:0 0 18px}ul.pts li{margin:8px 0;line-height:1.45}
.ways{display:grid;gap:10px;margin:14px 0}.way{display:flex;align-items:center;justify-content:center;padding:14px 16px;border-radius:999px;
text-decoration:none;font-weight:700;min-height:52px}.way.tg{background:var(--coral);color:#fff}.way.site{background:#fff;color:var(--coral);border:2px solid var(--coral)}
.faq details{border-top:1.5px solid #c9b49a;padding:10px 0}.faq summary{font-weight:700;cursor:pointer}.also a{display:inline-block;margin:4px 10px 4px 0}
h2{margin:22px 0 8px}
"""


def render(path: str, base: str) -> str:
    p = PAGES[path]
    url = f"{base}{path}"
    desc = p["lead"]
    others = "".join(f'<a href="{_e(k)}">{_e(v["h1"])}</a>' for k, v in PAGES.items() if k != path)
    faq = "".join(f"<details><summary>{_e(q)}</summary><p>{_e(a)}</p></details>" for q, a in p["faq"])
    points = "".join(f"<li>{_e(x)}</li>" for x in p["points"])
    import articles  # здесь, а не наверху: articles сам импортирует servicepages
    links = "".join(f'<a href="{_e(k)}">{_e(h)}</a>' for k, h in articles.for_service(path))
    reading = f'<h2>Полезно почитать</h2><p class="also">{links}</p>' if links else ""
    service = {"@context": "https://schema.org", "@type": "Service", "name": p["service"], "serviceType": p["service"],
               "provider": {"@type": "LocalBusiness", "name": brand.NAME, "url": f"{base}/"},
               "areaServed": [{"@type": "AdministrativeArea", "name": "Московская область"}, {"@type": "City", "name": "Москва"}],
               "url": url, "description": desc}
    faq_ld = {"@context": "https://schema.org", "@type": "FAQPage",
              "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in p["faq"]]}
    ld = "".join('<script type="application/ld+json">' + json.dumps(d, ensure_ascii=False).replace("</", "<\\/") + "</script>"
                 for d in (service, faq_ld))
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(p["title"])}</title>
<meta name="description" content="{_e(desc)}"><link rel="canonical" href="{_e(url)}">
<meta property="og:type" content="website"><meta property="og:locale" content="ru_RU"><meta property="og:site_name" content="{brand.NAME}">
<meta property="og:title" content="{_e(p["title"])}"><meta property="og:description" content="{_e(desc)}"><meta property="og:url" content="{_e(url)}">
{seo.verification_tags()}{ld}{brand.HEAD}<style>{CSS}</style></head><body>{brand.public_header()}<main class="public-main service-page">
<p class="eyebrow">УСЛУГИ · МОСКВА И МОСКОВСКАЯ ОБЛАСТЬ</p>
<h1>{_e(p["h1"])}</h1>
<p class="lead">{_e(p["lead"])}</p>
<ul class="pts">{points}</ul>
<div class="ways"><a class="way tg" href="https://t.me/{_e(config.bot_username)}">Оставить заявку в Telegram</a>
<a class="way site" href="/request">Оставить заявку на сайте</a></div>
<h2>Частые вопросы</h2><div class="faq">{faq}</div>
{reading}<h2>Другие услуги</h2><p class="also">{others}</p>
<p class="hint">Работаем в {AREA}. <a href="/contact">Связаться с администратором</a></p>
</main>{brand.public_footer()}</body></html>"""


def _handler(path: str):
    async def handler(request: web.Request) -> web.Response:
        return web.Response(text=render(path, seo.base_url(request)), content_type="text/html",
                            headers={"Cache-Control": "no-cache"})
    return handler


def setup(app: web.Application) -> None:
    for path in PAGES:
        app.router.add_get(path, _handler(path))
