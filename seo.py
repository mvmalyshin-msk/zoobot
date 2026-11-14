"""Поисковая оптимизация: robots.txt, карта сайта, мета-теги главной, закрытые разделы, уведомление IndexNow.

Всё бесплатно и без аккаунтов, кроме подтверждения прав в Яндекс Вебмастере, Google Search Console и Bing Webmaster:
их коды берутся из переменных окружения (YANDEX_VERIFICATION, GOOGLE_VERIFICATION, BING_VERIFICATION) — это не секреты,
они и так видны в коде страницы."""
import html
import json
import logging
import os
from datetime import date
from urllib.parse import urlparse

from aiohttp import ClientSession, ClientTimeout, web

import brand
import db
from citydata import PATHS as CITY_PATHS
from config import config

log = logging.getLogger(__name__)

# Ключ IndexNow — не секрет: он лежит на сайте открытым файлом, так протокол доказывает, что сайт ваш.
INDEXNOW_KEY = "7c3f1e9a5b2d4086a1f7e3c9b5d20a64"
INDEXNOW_ENDPOINTS = ("https://yandex.com/indexnow", "https://www.bing.com/indexnow")
PUBLIC_PAGES = ("/", "/request", "/contact", "/policy", "/vygul-sobak", "/zoonyanya", "/peredergka", "/zootaksi",
                "/stati", "/stati/kak-vybrat-peredergku", "/stati/skolko-gulyat-s-sobakoi", "/stati/kak-podgotovit-koshku-k-otezdu") + CITY_PATHS
CLOSED_PREFIXES = ("/portal", "/chat", "/map", "/cabinet", "/api/", "/export/", "/verify/", "/contact/call")
DISALLOW = ("/portal", "/cabinet", "/api/", "/export/", "/verify/", "/chat/", "/contact/call")

TITLE = "Зооконсьерж — выгул, зооняня, передержка и зоотакси в Москве и Московской области"
DESCRIPTION = ("Подберём проверенного специалиста для вашего питомца: выгул собак, зооняня на дому, передержка, зоотакси "
               "в Москве и Московской области. Несколько вариантов по цене и уровню сервиса, заявка за минуту.")

FAQ = [
    ("Какие услуги для питомцев вы подбираете?",
     "Выгул собак, зооняню (визит покормить и поиграть или ночь с питомцем), передержку в зоотеле или у частного "
     "исполнителя и зоотакси для безопасной перевозки питомца."),
    ("В каких городах вы работаете?", "Работаем в Москве и Московской области. Если вы рядом, напишите нам, и мы подскажем, возможно ли приехать."),
    ("Как оставить заявку?",
     "Через Telegram-бот, через чат на сайте или через короткую форму. Укажите услугу, питомца и время, и мы подберём "
     "несколько вариантов по цене и уровню сервиса."),
    ("Сколько стоит услуга?",
     "Цена зависит от услуги, размера питомца и срочности. Мы предложим несколько вариантов, и вы выберете подходящий по цене."),
]


def base_url(request: web.Request) -> str:
    if config.webapp_url:
        return config.webapp_url.rstrip("/")
    return f"{request.scheme}://{request.host}"


def robots_txt(base: str) -> str:
    lines = ["User-agent: *"] + [f"Disallow: {p}" for p in DISALLOW] + ["Allow: /", "", f"Sitemap: {base}/sitemap.xml", ""]
    return "\n".join(lines)


def sitemap_xml(base: str) -> str:
    today = date.today().isoformat()
    rows = "".join(f"<url><loc>{html.escape(base + p)}</loc><lastmod>{today}</lastmod>"
                   f"<changefreq>{'weekly' if p == '/' else 'monthly'}</changefreq>"
                   f"<priority>{'1.0' if p == '/' else '0.6'}</priority></url>" for p in PUBLIC_PAGES)
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</urlset>'


# Публичные коды подтверждения прав (не секреты): используются, если переменная окружения не задана.
DEFAULT_VERIFICATION = {"YANDEX_VERIFICATION": "915082d5fc8d5cda"}


def verification_tags() -> str:
    out = ""
    for env, name in (("YANDEX_VERIFICATION", "yandex-verification"), ("GOOGLE_VERIFICATION", "google-site-verification"),
                      ("BING_VERIFICATION", "msvalidate.01")):
        v = (os.environ.get(env) or DEFAULT_VERIFICATION.get(env, "")).strip()
        if v:
            out += f'<meta name="{name}" content="{html.escape(v, quote=True)}">'
    return out


def _ld(data: dict) -> str:
    return '<script type="application/ld+json">' + json.dumps(data, ensure_ascii=False).replace("</", "<\\/") + "</script>"


def head_tags(base: str) -> str:
    """Для главной: описание, канонический адрес, превью для соцсетей и мессенджеров, структурированные данные."""
    url = f"{base}/"
    d = html.escape(DESCRIPTION, quote=True)
    t = html.escape(TITLE, quote=True)
    img = f"{base}/icons/icon-512.png"
    business = {
        "@context": "https://schema.org", "@type": "LocalBusiness", "name": brand.NAME, "url": url, "image": img,
        "description": DESCRIPTION, "areaServed": [{"@type": "AdministrativeArea", "name": "Московская область"}, {"@type": "City", "name": "Москва"}],
        "knowsLanguage": "ru", "openingHours": "Mo-Su 06:00-22:00",
        "sameAs": [f"https://t.me/{config.bot_username}"],
        "makesOffer": [{"@type": "Offer", "itemOffered": {"@type": "Service", "name": n}}
                       for n in ("Выгул собак", "Зооняня", "Передержка животных", "Зоотакси")],
    }
    faq = {"@context": "https://schema.org", "@type": "FAQPage",
           "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in FAQ]}
    return (f'<link rel="canonical" href="{html.escape(url, quote=True)}">'
            f'<meta property="og:type" content="website"><meta property="og:locale" content="ru_RU">'
            f'<meta property="og:site_name" content="{brand.NAME}"><meta property="og:title" content="{t}">'
            f'<meta property="og:description" content="{d}"><meta property="og:url" content="{html.escape(url, quote=True)}">'
            f'<meta property="og:image" content="{html.escape(img, quote=True)}">'
            f'<meta name="twitter:card" content="summary"><meta name="twitter:title" content="{t}">'
            f'<meta name="twitter:description" content="{d}">'
            f'{verification_tags()}{_ld(business)}{_ld(faq)}')


def faq_html() -> str:
    items = "".join(f"<details><summary>{html.escape(q)}</summary><p>{html.escape(a)}</p></details>" for q, a in FAQ)
    return f'<h2>Частые вопросы</h2><div class="faq">{items}</div>'


async def _robots(request: web.Request) -> web.Response:
    return web.Response(text=robots_txt(base_url(request)), content_type="text/plain", charset="utf-8")


async def _sitemap(request: web.Request) -> web.Response:
    return web.Response(text=sitemap_xml(base_url(request)), content_type="application/xml", charset="utf-8")


async def _indexnow_key(request: web.Request) -> web.Response:
    return web.Response(text=INDEXNOW_KEY, content_type="text/plain")


@web.middleware
async def _noindex(request: web.Request, handler):
    resp = await handler(request)
    p = request.path
    if any(p == c.rstrip("/") or p.startswith(c) for c in CLOSED_PREFIXES) and "X-Robots-Tag" not in resp.headers:
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


def setup(app: web.Application) -> None:
    app.middlewares.append(_noindex)
    app.router.add_get("/robots.txt", _robots)
    app.router.add_get("/sitemap.xml", _sitemap)
    app.router.add_get(f"/{INDEXNOW_KEY}.txt", _indexnow_key)


async def _post(url: str, payload: dict) -> int:
    async with ClientSession(timeout=ClientTimeout(total=15)) as s:
        async with s.post(url, json=payload, headers={"Content-Type": "application/json; charset=utf-8"}) as r:
            return r.status


async def indexnow_ping(base: str, version, post=_post) -> None:
    """Один раз на каждую версию сообщает Яндексу и Bing (а через них и DuckDuckGo, Yahoo, Ecosia), что сайт обновился.
    Без публичного адреса ничего не делает. Не получилось — не запоминает и пробует при следующем запуске. Не падает."""
    host = urlparse(base or "").hostname or ""
    if not host or host in ("localhost", "127.0.0.1") or not base.startswith("https://"):
        return
    key = f"indexnow:{version}"
    try:
        if await db.fetch_one("SELECT 1 AS x FROM kv WHERE key=?", (key,)):
            return
        payload = {"host": host, "key": INDEXNOW_KEY, "keyLocation": f"{base}/{INDEXNOW_KEY}.txt",
                   "urlList": [f"{base}{p}" for p in PUBLIC_PAGES]}
        ok = 0
        for ep in INDEXNOW_ENDPOINTS:
            try:
                status = await post(ep, payload)
                log.info("IndexNow %s -> %s", ep, status)
                ok += 200 <= status < 300
            except Exception as e:  # сеть/сервис недоступны — не страшно
                log.warning("IndexNow %s не ответил: %s", ep, e)
        if ok:
            await db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (key, "1"))
    except Exception:
        log.exception("IndexNow: ошибка")
