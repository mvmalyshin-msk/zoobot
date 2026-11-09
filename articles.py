"""Статьи для сайта: полезные тексты, которые находят владельцы питомцев по запросам вроде «как выбрать передержку».

Без цен, медицинских советов и обещаний: только общие ориентиры и вопросы, которые стоит задать исполнителю.
Каждая статья ведёт на страницу услуги и на заявку. Попадают в карту сайта (seo.PUBLIC_PAGES)."""
import html as _html
import json

from aiohttp import web

import brand
import ribbons
import seo
import servicepages
from config import config

PUBLISHED = "2026-11-09"
BASE = "/stati"

ARTICLES = {
    "/stati/kak-vybrat-peredergku": {
        "title": "Как выбрать передержку для кошки или собаки — Зооконсьерж",
        "h1": "Как выбрать передержку для кошки или собаки",
        "lead": ("Уезжаете в отпуск или командировку и ищете, у кого оставить питомца в Москве и Московской области? "
                 "Разбираем, чем частная передержка отличается от зоотеля и о чём спросить до того, как оставить животное."),
        "service": "/peredergka",
        "service_name": "Подобрать передержку",
        "sections": [
            {"h": "Частная передержка или зоотель",
             "body": ("У частного исполнителя питомец живёт в обычной квартире, внимание делится на одного-двух животных, но всё зависит "
                      "от человека и условий.\n\nВ зоотеле есть штат и распорядок, обычно отдельные места для каждого животного, но "
                      "обстановка менее домашняя. Для спокойных питомцев подходит и то и другое, пугливым и пожилым часто проще дома.")},
            {"h": "Что спросить до передержки",
             "body": ("Сколько животных будет рядом с вашим и как их содержат вместе или отдельно.\n\nКто будет присматривать, в какое время и "
                      "как часто вы получите фото и сообщения.\n\nЧто делать, если питомец заболеет или потеряет аппетит, к какому "
                      "ветеринару обращаются и кто за это отвечает.\n\nКакие требования по прививкам и обработкам, чтобы заранее всё подготовить.")},
            {"h": "Что передать вместе с питомцем",
             "body": ("Привычный корм на весь срок и небольшой запас сверху, лекарства с понятной схемой приёма, любимую игрушку или лежанку, "
                      "переноску или шлейку и контакты на случай, если вас не будет на связи.\n\nНапишите о привычках: чего боится, как "
                      "ест, когда гуляет, как реагирует на других животных.")},
            {"h": "Как проверить, что всё хорошо",
             "body": ("Попросите прислать фото или видео в первый день и договоритесь, как часто будете получать новости.\n\nЕсли что-то "
                      "вызывает вопросы, обсудите это сразу, а не по возвращении. Хорошая передержка не боится вопросов.")},
        ],
    },
    "/stati/skolko-gulyat-s-sobakoi": {
        "title": "Сколько гулять с собакой в день: ориентиры — Зооконсьерж",
        "h1": "Сколько гулять с собакой в день",
        "lead": ("Универсальной цифры нет: многое зависит от возраста, породы и характера собаки. Собрали общие ориентиры и подсказки, "
                 "что делать, если вы не успеваете гулять сами. Выгул подберём в Москве и Московской области."),
        "service": "/vygul-sobak",
        "service_name": "Подобрать выгул собаки",
        "sections": [
            {"h": "От чего зависит время прогулки",
             "body": ("От возраста: щенкам и пожилым собакам обычно нужны более частые и короткие выходы.\n\nОт породы и энергии: "
                      "рабочие и спортивные породы требуют больше движения, чем декоративные.\n\nОт здоровья: если есть вопросы "
                      "с суставами или сердцем, нагрузку лучше обсудить с ветеринаром.")},
            {"h": "Общие ориентиры",
             "body": ("Взрослой здоровой собаке обычно нужно выходить на улицу несколько раз в день: утром, днём и вечером, а одна из "
                      "прогулок должна быть длиннее и активнее.\n\nЭто ориентир, а не правило. Лучше смотреть на поведение: спокойная "
                      "собака после прогулки показывает, что нагрузки хватает.")},
            {"h": "Признаки, что прогулок мало",
             "body": ("Собака портит вещи, громко лает или воет дома, не может успокоиться вечером, пытается выбежать при каждой "
                      "возможности. Это повод пересмотреть режим, добавить игры и более длинные прогулки.")},
            {"h": "Если нет времени гулять самому",
             "body": ("В рабочие часы, в командировку или после болезни собаку может выгулять другой человек. Расскажите ему про "
                      "характер, команды, маршрут и что собака подбирает на улице. Начните с короткой пробной прогулки.")},
        ],
    },
    "/stati/kak-podgotovit-koshku-k-otezdu": {
        "title": "Как подготовить кошку к вашему отъезду — Зооконсьерж",
        "h1": "Как подготовить кошку к вашему отъезду",
        "lead": ("Кошки не любят перемен. Расскажем, как подготовить питомца к вашему отъезду, что оставить зооняне и когда лучше "
                 "выбрать передержку. Подбираем специалистов в Москве и Московской области."),
        "service": "/zoonyanya",
        "service_name": "Подобрать зооняню",
        "sections": [
            {"h": "Оставить дома или на передержку",
             "body": ("Многим кошкам лучше дома: знакомый запах, привычная лежанка, привычный распорядок. Тогда достаточно зооняни, "
                      "которая приходит кормить, менять воду и наполнитель и проводит время с питомцем.\n\nПередержка подойдёт, "
                      "если вас не будет долго, кошке нужен постоянный присмотр или дома что-то будет меняться, например ремонт.")},
            {"h": "Что подготовить дома",
             "body": ("Чистые лотки и запас наполнителя, корм и вода на несколько дней сверху, понятное место для миски.\n\nУберите "
                      "то, что кошка может сломать или проглотить, закройте окна и балконы, оставьте контакты ветеринара.")},
            {"h": "Что рассказать зооняне",
             "body": ("Сколько и чем кормить, в какое время, какие лекарства и как их давать. Где что лежит, какие места любит кошка, "
                      "чего боится. Что считать нормой, а когда звонить вам или ветеринару.")},
            {"h": "Как снизить стресс",
             "body": ("Заранее познакомьте кошку с няней: пусть придёт один раз, пока вы дома. Не меняйте корм и наполнитель перед "
                      "отъездом. Оставьте вещь с вашим запахом. Договоритесь, как часто будете получать фото или сообщения.")},
        ],
    },
}


def _e(v) -> str:
    return _html.escape(str(v or ""), quote=True)


CSS = servicepages.CSS + """
.art p{line-height:1.6;margin:0 0 12px}.art h2{margin:24px 0 8px}.list a{display:block;padding:12px 0;border-top:1px solid #eadfd6;
text-decoration:none;font-weight:700}.list p{margin:2px 0 0;font-weight:400;color:#5b5047}
"""


def _head(title: str, desc: str, url: str, ld: str) -> str:
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)}</title>
<meta name="description" content="{_e(desc)}"><link rel="canonical" href="{_e(url)}">
<meta property="og:type" content="article"><meta property="og:locale" content="ru_RU"><meta property="og:site_name" content="{brand.NAME}">
<meta property="og:title" content="{_e(title)}"><meta property="og:description" content="{_e(desc)}"><meta property="og:url" content="{_e(url)}">
{seo.verification_tags()}{ld}{brand.HEAD}<style>{CSS}</style></head>"""


def render_article(path: str, base: str) -> str:
    a = ARTICLES[path]
    url = f"{base}{path}"
    data = {"@context": "https://schema.org", "@type": "Article", "headline": a["h1"], "description": a["lead"],
            "inLanguage": "ru", "datePublished": PUBLISHED, "dateModified": PUBLISHED, "mainEntityOfPage": url,
            "author": {"@type": "Organization", "name": brand.NAME},
            "publisher": {"@type": "Organization", "name": brand.NAME, "url": f"{base}/"}}
    ld = '<script type="application/ld+json">' + json.dumps(data, ensure_ascii=False).replace("</", "<\\/") + "</script>"
    body = "".join(f"<h2>{_e(s['h'])}</h2>" + "".join(f"<p>{_e(p)}</p>" for p in s["body"].split("\n\n")) for s in a["sections"])
    return _head(a["title"], a["lead"], url, ld) + f"""<body>{ribbons.html(ribbons.CLIENT)}<main class="art">
{brand.backrow(BASE)}
<div class="top">{brand.LOGO}</div>
<h1>{_e(a["h1"])}</h1>
<p class="lead">{_e(a["lead"])}</p>
{body}
<div class="ways"><a class="way tg" href="https://t.me/{_e(config.bot_username)}">Оставить заявку в Telegram</a>
<a class="way site" href="/request">Оставить заявку на сайте</a></div>
<p class="hint"><a href="{_e(a["service"])}">{_e(a["service_name"])}</a> · <a href="{BASE}">Все статьи</a> ·
<a href="/contact">Связаться с администратором</a></p>
</main></body></html>"""


def render_index(base: str) -> str:
    title = "Статьи о питомцах: передержка, выгул, отъезд — Зооконсьерж"
    desc = ("Полезные статьи для владельцев кошек и собак: как выбрать передержку, сколько гулять с собакой, "
            "как подготовить питомца к отъезду. Москва и Московская область.")
    items = "".join(f'<a href="{_e(k)}">{_e(v["h1"])}<p>{_e(v["lead"].split(". ")[0])}.</p></a>' for k, v in ARTICLES.items())
    return _head(title, desc, f"{base}{BASE}", "") + f"""<body>{ribbons.html(ribbons.CLIENT)}<main>
{brand.backrow("/")}
<div class="top">{brand.LOGO}</div>
<h1>Статьи о питомцах</h1>
<p class="lead">Короткие и понятные тексты для владельцев кошек и собак. Работаем в Москве и Московской области.</p>
<div class="list">{items}</div>
<div class="ways"><a class="way site" href="/request">Оставить заявку на сайте</a></div>
</main></body></html>"""


def _article_handler(path: str):
    async def handler(request: web.Request) -> web.Response:
        return web.Response(text=render_article(path, seo.base_url(request)), content_type="text/html",
                            headers={"Cache-Control": "no-cache"})
    return handler


async def _index(request: web.Request) -> web.Response:
    return web.Response(text=render_index(seo.base_url(request)), content_type="text/html",
                        headers={"Cache-Control": "no-cache"})


def setup(app: web.Application) -> None:
    app.router.add_get(BASE, _index)
    for path in ARTICLES:
        app.router.add_get(path, _article_handler(path))
