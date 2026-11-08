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

# Админ на экране «Домой» — отдельное приложение: открывает портал (не клиентский чат) и называется иначе.
ADMIN_TITLE = "ЗК Админ"
HEAD_ADMIN = (HEAD.replace('href="/manifest.webmanifest"', 'href="/manifest-admin.webmanifest"')
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


LOGO = '<a class="brand" href="/" aria-label="На главную"><span class="zoo">ЗОО</span>КОНСЬЕРЖ</a>'
