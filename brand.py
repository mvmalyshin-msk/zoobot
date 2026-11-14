"""Общая «шапка» страниц: название и иконка для экрана «Домой», фирменный CSS. Одинаково на всех страницах,
чтобы клиент сразу видел, что это один продукт."""
NAME = "Зооконсьерж"

HEAD = (f'<meta name="theme-color" content="#FC6751">'
        f'<meta name="apple-mobile-web-app-capable" content="yes">'
        f'<meta name="mobile-web-app-capable" content="yes">'
        f'<meta name="apple-mobile-web-app-status-bar-style" content="default">'
        f'<meta name="apple-mobile-web-app-title" content="{NAME}">'
        f'<meta name="application-name" content="{NAME}">'
        f'<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">'
        f'<link rel="icon" type="image/png" sizes="32x32" href="/icons/favicon-32.png">'
        f'<link rel="manifest" href="/manifest.webmanifest">'
        f'<link rel="stylesheet" href="/brand.css">')
TRACK = '<script defer src="/a.js"></script>'  # собственный счётчик посещений (analytics.py); в портале админа его нет
HEAD = HEAD + TRACK

# Админ на экране «Домой» — отдельное приложение: открывает портал (не клиентский чат) и называется иначе.
ADMIN_TITLE = "ЗК Админ"
HEAD_ADMIN = (HEAD.replace(TRACK, "").replace('href="/manifest.webmanifest"', 'href="/manifest-admin.webmanifest"')
              .replace(f'apple-mobile-web-app-title" content="{NAME}"', f'apple-mobile-web-app-title" content="{ADMIN_TITLE}"')
              .replace(f'application-name" content="{NAME}"', f'application-name" content="{ADMIN_TITLE}"')
              .replace('theme-color" content="#FC6751"', 'theme-color" content="#d62b2b"'))

_BACK_JS = ("if(history.length>1&&document.referrer&&document.referrer.indexOf(location.origin)===0)"
            "{history.back();return false}")


def back(href: str = "/", compact: bool = False) -> str:
    """Стрелка «Назад»: возвращает туда, откуда пришли; если пришли не с нашего сайта — ведёт на href (страница выше)."""
    cls = "back compact" if compact else "back"
    return (f'<a class="{cls}" href="{href}" aria-label="Назад" onclick="{_BACK_JS}">'
            f'<span aria-hidden="true">←</span><b>Назад</b></a>')


def backrow(href: str = "/") -> str:
    """Ряд с кнопкой «Назад» (на каждой странице глубже стартовой). На главную ведёт надпись ЗООКОНСЬЕРЖ (LOGO)."""
    return f'<div class="backrow">{back(href)}</div>'


# Единый словарь: одни и те же слова и порядок услуг на всех страницах и в боте.
SERVICES = (("walk", "Выгул"), ("nanny", "Зооняня"), ("boarding", "Передержка"), ("taxi", "Зоотакси"))
PITCH = ("Зооконсьерж подбирает проверенных специалистов для вашего питомца: выгул, зооняню, передержку и зоотакси — "
         "несколько вариантов по цене и уровню сервиса, чтобы вам было легко выбрать.")
SHORT = "Подбор проверенных специалистов для питомцев: выгул, зооняня, передержка, зоотакси"
CHIPS = "".join(f'<span class="chip {k}">{t}</span>' for k, t in SERVICES)
# Подпись под логотипом на каждой странице: суть сервиса и четыре услуги теми же цветами.
STRAP = f'<div class="strap"><p>Подбираем проверенных специалистов для питомцев</p><div class="chips">{CHIPS}</div></div>'

LOGO = '<a class="brand" href="/" aria-label="На главную"><span class="zoo">ЗОО</span>КОНСЬЕРЖ</a>'


def public_header() -> str:
    return (f'<header class="site-header"><div class="site-header-inner">{LOGO}'
            '<nav class="site-nav" aria-label="Основная навигация">'
            '<a href="/#services">Услуги</a><a href="/#how-it-works">Как работаем</a><a href="/stati">Статьи</a>'
            '<a class="site-nav-access" href="/chat/login">Кабинет клиента</a>'
            '<a class="site-nav-access" href="/portal">Вход администратора</a>'
            '<a class="site-nav-cta" href="/request">Оставить заявку</a></nav></div></header>')


def public_footer() -> str:
    return (f'<footer class="site-footer"><div class="site-footer-inner"><div><a class="brand footer-brand" href="/">'
            '<span class="zoo">ЗОО</span>КОНСЬЕРЖ</a><p>Помогаем подобрать заботу о питомце в Москве и Московской области.</p></div>'
            '<nav aria-label="Ссылки в подвале"><a href="/vygul-sobak">Выгул</a><a href="/zoonyanya">Зооняня</a>'
            '<a href="/peredergka">Передержка</a><a href="/zootaksi">Зоотакси</a></nav>'
            '<nav aria-label="Информация"><a href="/contact">Связаться</a><a href="/stati">Статьи</a>'
            '<a href="/chat/login">Личный кабинет клиента</a><a href="/portal">Кабинет администратора</a>'
            '<a href="/policy">Политика обработки данных</a></nav>'
            '<p class="site-footer-area">Работаем в Москве и Московской области</p></div></footer>')
