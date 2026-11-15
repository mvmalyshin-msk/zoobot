"""Страница «О сервисе» (/o-nas): кто мы, как работаем, где данные, как связаться.

Нужна для доверия людей и поисковиков («Прозрачный сайт» Яндекса, E-E-A-T): только правда о сервисе, без выдуманных
отзывов, лицензий и обещаний. Реквизиты оператора — из тех же переменных, что в политике (OPERATOR_NAME, OPERATOR_INN);
не заданы — строка не выводится."""
import html as _html
import json
import os

from aiohttp import web

import brand
import seo
import servicepages
from config import config

PATH = "/o-nas"
TITLE = "О сервисе «Зооконсьерж» — подбор специалистов для питомцев"
DESC = ("«Зооконсьерж» подбирает выгул, зооняню, передержку и зоотакси в Москве и Московской области: три варианта "
        "на выбор, учёт характера питомца, понятная цена.")

CSS = servicepages.CSS + """
.about-page p{line-height:1.6;margin:0 0 12px}.about-page ol,.about-page ul{padding-left:22px}.about-page li{margin:8px 0;line-height:1.5}
.facts{display:grid;gap:10px;margin:14px 0}.fact{border:1.5px solid #e8d6c4;border-radius:14px;padding:10px 14px;background:#fff}
.fact b{display:block}
"""


def _e(v) -> str:
    return _html.escape(str(v or ""), quote=True)


def _operator() -> str:
    name = os.environ.get("OPERATOR_NAME", "").strip()
    inn = os.environ.get("OPERATOR_INN", "").strip()
    if not name:
        return ""
    return f"<p>Оператор сервиса: {_e(name)}" + (f", ИНН {_e(inn)}" if inn else "") + ".</p>"


def render(base: str) -> str:
    url = f"{base}{PATH}"
    org = {"@context": "https://schema.org", "@type": "LocalBusiness", "name": brand.NAME, "url": f"{base}/",
           "description": DESC,
           "areaServed": [{"@type": "City", "name": "Москва"}, {"@type": "AdministrativeArea", "name": "Московская область"}],
           "openingHours": "Mo-Su 06:00-22:00",
           "sameAs": [f"https://t.me/{config.bot_username}"]}
    page = {"@context": "https://schema.org", "@type": "AboutPage", "name": TITLE, "url": url, "inLanguage": "ru"}
    ld = "".join('<script type="application/ld+json">' + json.dumps(d, ensure_ascii=False).replace("</", "<\\/") + "</script>"
                 for d in (org, page))
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(TITLE)}</title>
<meta name="description" content="{_e(DESC)}"><link rel="canonical" href="{_e(url)}">
<meta property="og:type" content="website"><meta property="og:locale" content="ru_RU"><meta property="og:site_name" content="{brand.NAME}">
<meta property="og:title" content="{_e(TITLE)}"><meta property="og:description" content="{_e(DESC)}"><meta property="og:url" content="{_e(url)}">
{seo.verification_tags()}{ld}{brand.HEAD}<style>{CSS}</style></head><body>{brand.public_header()}<main class="public-main about-page">
<p class="eyebrow">О СЕРВИСЕ</p>
<h1>«Зооконсьерж»: подбираем заботу о питомце</h1>
<p class="lead">Мы подбираем специалистов для кошек и собак в Москве и Московской области: выгул, зооняню, передержку
и зоотакси. Берём на себя поиск и рутину, а выбор оставляем вам.</p>

<h2>Что мы делаем</h2>
<div class="facts">
<a class="fact" href="/vygul-sobak"><b>Выгул собак</b>разово или по графику</a>
<a class="fact" href="/zoonyanya"><b>Зооняня</b>приходящая или с проживанием</a>
<a class="fact" href="/peredergka"><b>Передержка</b>у частного исполнителя или в зоогостинице</a>
<a class="fact" href="/zootaksi"><b>Зоотакси</b>к ветеринару, на передержку, в аэропорт</a>
</div>

<h2>Как мы работаем</h2>
<ol>
<li>Вы оставляете заявку в Telegram-боте или на сайте: услуга, питомец, адрес и время.</li>
<li>Мы уточняем характер, привычки, страхи и здоровье питомца — по анкете, чтобы не спрашивать дважды.</li>
<li>Подбираем три варианта: эконом, комфорт и комфорт с деталями. Вы сравниваете условия и цену и выбираете сами.</li>
<li>Исполнитель выполняет заказ и присылает новости: после выгула — короткий отчёт с фото.</li>
</ol>

<h2>Почему нам можно доверить питомца</h2>
<ul>
<li>Учитываем породу, возраст, характер и страхи: эти данные видит исполнитель до начала заказа.</li>
<li>Поиск, проверка и подбор специалиста уже входят в итоговую стоимость — скрытых доплат за нашу работу нет.</li>
<li>Цену «от» показываем открыто на сайте, а ориентировочную вилку — до подтверждения заказа.</li>
<li>Данные клиентов храним на сервере в России и обрабатываем по <a href="/policy">политике обработки персональных данных</a>.
По просьбе клиента удаляем его данные.</li>
</ul>

<h2>Где и когда работаем</h2>
<p>Москва и Московская область, в том числе <a href="/goroda">Химки, Зеленоград, Красногорск, Долгопрудный, Лобня, Мытищи</a>.
Заявки принимаем ежедневно с 6:00 до 22:00.</p>
{_operator()}
<h2>Как связаться</h2>
<p>Telegram-бот <a href="https://t.me/{_e(config.bot_username)}">@{_e(config.bot_username)}</a>, форма заявки на сайте или
<a href="/contact">страница связи с администратором</a>.</p>
<div class="ways"><a class="way tg" href="https://t.me/{_e(config.bot_username)}">Оставить заявку в Telegram</a>
<a class="way site" href="/request">Оставить заявку на сайте</a></div>
<p class="hint"><a href="/stati">Полезные статьи для владельцев питомцев</a></p>
</main>{brand.public_footer()}</body></html>"""


async def _page(request: web.Request) -> web.Response:
    return web.Response(text=render(seo.base_url(request)), content_type="text/html", headers={"Cache-Control": "no-cache"})


def setup(app: web.Application) -> None:
    app.router.add_get(PATH, _page)
