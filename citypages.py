"""Страницы «услуга × город»: «выгул собак в Химках», «передержка в Зеленограде» и т. д., плюс общая страница /goroda.

Почему не «тысячи страниц»: Яндекс понижает сайты с одинаковыми страницами-дорвеями. Поэтому городов немного,
у каждой страницы свой текст (услуга + город + микрорайоны + свой вопрос в FAQ), каноническая ссылка на саму себя
и ссылка на общую страницу услуги. Цен и обещаний нет — как и на страницах услуг (servicepages.py)."""
import html as _html
import json

from aiohttp import web

import brand
import seo
import servicepages
from citydata import CITIES, HUB, SERVICES
from config import config


def _e(v) -> str:
    return _html.escape(str(v or ""), quote=True)


def _texts(service: str, c: dict) -> dict:
    """Текст страницы под конкретную услугу и город."""
    prep, gen, areas = c["prep"], c["gen"], c["areas"]
    air = f", в аэропорт {c['airport']}" if c["airport"] else ""
    if service == "/vygul-sobak":
        return {"h1": f"Выгул собак {prep}",
                "lead": f"Подберём человека, который выгуляет вашу собаку {prep}: разово, на время отпуска или регулярно по будням.",
                "points": [f"Работаем по всему городу: {areas}.",
                           "Длительность прогулки выбираете вы: короткая для щенка или пожилой собаки, длинная для активной.",
                           "Расскажите про характер собаки, привычки и любимый маршрут: исполнитель будет знать, чего ждать.",
                           "Предложим несколько вариантов по цене и уровню сервиса: эконом, комфорт и комфорт с деталями."],
                "faq": ("Можно ли заказать выгул на каждый будний день?",
                        "Да. Укажите в заявке дни и время, и мы подберём исполнителя под постоянный график.")}
    if service == "/zoonyanya":
        return {"h1": f"Зооняня {prep}",
                "lead": f"Зооняня приедет к вашему питомцу {prep}: покормит, поиграет, сменит воду и наполнитель, проверит, всё ли в порядке.",
                "points": [f"Работаем по всему городу: {areas}.",
                           "Визит на час-два или ночь с питомцем у вас дома: как удобнее вам.",
                           "Подходит кошкам, собакам, грызунам и птицам: питомец остаётся в привычной обстановке.",
                           "Предложим несколько вариантов по цене и уровню сервиса: эконом, комфорт и комфорт с деталями."],
                "faq": ("Сколько раз в день может приходить зооняня?",
                        "Столько, сколько нужно питомцу: один или несколько визитов. Напишите это в заявке.")}
    if service == "/peredergka":
        return {"h1": f"Передержка кошек и собак {prep}",
                "lead": f"Уезжаете в отпуск или командировку? Подберём передержку для питомца {prep} или рядом: у частного исполнителя или в зоотеле.",
                "points": [f"Ищем варианты рядом с вами: {areas}.",
                           "Передержка на несколько дней или недель: даты и условия обсуждаем заранее.",
                           "Расскажите про питание, лекарства и привычки питомца, чтобы за ним ухаживали, как дома.",
                           "Довезём питомца на передержку зоотакси, если вам некогда."],
                "faq": ("Можно ли посмотреть условия передержки заранее?",
                        "Да. Перед выбором варианта расскажем об условиях и ответим на вопросы.")}
    return {"h1": f"Зоотакси {prep}",
            "lead": f"Безопасно отвезём питомца {gen} и обратно: к ветеринару, груммеру, на передержку или выставку.",
            "points": [f"Подача по всему городу: {areas}.",
                       f"Поездки {gen} в Москву и по области: к ветеринару, на вокзал{air}.",
                       "Для животных нужны переноска или пристёгнутая шлейка: расскажите, что у вас есть.",
                       "Предложим несколько вариантов по цене и уровню сервиса: эконом, комфорт и комфорт с деталями."],
            "faq": ("Можно ли поехать вместе с питомцем?", "Да, напишите об этом в заявке.")}


def _ld(*items) -> str:
    return "".join('<script type="application/ld+json">' + json.dumps(d, ensure_ascii=False).replace("</", "<\\/") + "</script>"
                   for d in items)


def _page(title: str, desc: str, url: str, body: str, ld: str) -> str:
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)}</title>
<meta name="description" content="{_e(desc)}"><link rel="canonical" href="{_e(url)}">
<meta property="og:type" content="website"><meta property="og:locale" content="ru_RU"><meta property="og:site_name" content="{brand.NAME}">
<meta property="og:title" content="{_e(title)}"><meta property="og:description" content="{_e(desc)}"><meta property="og:url" content="{_e(url)}">
{seo.verification_tags()}{ld}{brand.HEAD}<style>{servicepages.CSS}</style></head><body>{brand.public_header()}<main class="public-main service-page">
{body}
</main>{brand.public_footer()}</body></html>"""


def render(service: str, city: str, base: str) -> str:
    c = CITIES[city]
    t = _texts(service, c)
    path = f"{service}/{city}"
    url = f"{base}{path}"
    title = f"{t['h1']} — {brand.NAME}"
    sname = servicepages.PAGES[service]["service"]
    q, a = t["faq"]
    faq = [(f"Вы работаете {c['prep']}?", f"Да. {c['name']}: {c['areas']}. Также работаем по всей Москве и Московской области."), (q, a)]
    same_city = "".join(f'<a href="{_e(s)}/{_e(city)}">{_e(n)}</a>' for s, n in SERVICES.items() if s != service)
    other_cities = "".join(f'<a href="{_e(service)}/{_e(k)}">{_e(v["name"])}</a>' for k, v in CITIES.items() if k != city)
    body = f"""<p class="eyebrow">УСЛУГИ · {_e(c['name'].upper())}</p>
<h1>{_e(t['h1'])}</h1>
<p class="lead">{_e(t['lead'])}</p>
<ul class="pts">{''.join(f'<li>{_e(x)}</li>' for x in t['points'])}</ul>
<div class="ways"><a class="way tg" href="https://t.me/{_e(config.bot_username)}">Оставить заявку в Telegram</a>
<a class="way site" href="/request">Оставить заявку на сайте</a></div>
<h2>Частые вопросы</h2><div class="faq">{''.join(f'<details><summary>{_e(x)}</summary><p>{_e(y)}</p></details>' for x, y in faq)}</div>
<h2>Подробнее об услуге</h2><p class="also"><a href="{_e(service)}">{_e(servicepages.PAGES[service]['h1'])}</a></p>
<h2>Другие услуги {_e(c['prep'])}</h2><p class="also">{same_city}</p>
<h2>Эта услуга в других городах</h2><p class="also">{other_cities}<a href="{HUB}">Все города</a></p>
<p class="hint">Работаем в Москве и Московской области. <a href="/contact">Связаться с администратором</a></p>"""
    ld = _ld({"@context": "https://schema.org", "@type": "Service", "name": f"{sname} {c['prep']}", "serviceType": sname,
              "provider": {"@type": "LocalBusiness", "name": brand.NAME, "url": f"{base}/"},
              "areaServed": {"@type": "City", "name": c["name"]}, "url": url, "description": t["lead"]},
             {"@context": "https://schema.org", "@type": "FAQPage",
              "mainEntity": [{"@type": "Question", "name": x, "acceptedAnswer": {"@type": "Answer", "text": y}} for x, y in faq]},
             {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
                 {"@type": "ListItem", "position": 1, "name": brand.NAME, "item": f"{base}/"},
                 {"@type": "ListItem", "position": 2, "name": servicepages.PAGES[service]["h1"], "item": f"{base}{service}"},
                 {"@type": "ListItem", "position": 3, "name": c["name"], "item": url}]})
    return _page(title, t["lead"], url, body, ld)


def render_hub(base: str) -> str:
    rows = "".join(f'<h2>{_e(c["name"])}</h2><p class="also">'
                   + "".join(f'<a href="{_e(s)}/{_e(k)}">{_e(n)}</a>' for s, n in SERVICES.items()) + "</p>"
                   for k, c in CITIES.items())
    desc = ("Где работает Зооконсьерж: выгул собак, зооняня, передержка и зоотакси в Химках, Зеленограде, Красногорске, "
            "Долгопрудном, Лобне, Мытищах, а также по всей Москве и Московской области.")
    body = f"""<p class="eyebrow">ГДЕ МЫ РАБОТАЕМ</p><h1>Города и районы</h1><p class="lead">{_e(desc)}</p>{rows}
<h2>Москва и вся область</h2><p class="also">{''.join(f'<a href="{_e(s)}">{_e(n)}</a>' for s, n in SERVICES.items())}</p>
<p class="hint">Не нашли свой город? <a href="/request">Оставьте заявку</a> — подскажем, сможем ли приехать.</p>"""
    return _page(f"Города и районы — {brand.NAME}", desc, f"{base}{HUB}", body, "")


def city_links_html(service: str) -> str:
    """Блок «Где работаем» для общих страниц услуг."""
    links = "".join(f'<a href="{_e(service)}/{_e(k)}">{_e(c["name"])}</a>' for k, c in CITIES.items())
    return f'<h2>Где работаем</h2><p class="also">{links}<a href="{HUB}">Все города</a></p>'


def _handler(service: str, city: str):
    async def handler(request: web.Request) -> web.Response:
        return web.Response(text=render(service, city, seo.base_url(request)), content_type="text/html",
                            headers={"Cache-Control": "no-cache"})
    return handler


async def _hub(request: web.Request) -> web.Response:
    return web.Response(text=render_hub(seo.base_url(request)), content_type="text/html", headers={"Cache-Control": "no-cache"})


def setup(app: web.Application) -> None:
    app.router.add_get(HUB, _hub)
    for s in SERVICES:
        for c in CITIES:
            app.router.add_get(f"{s}/{c}", _handler(s, c))
