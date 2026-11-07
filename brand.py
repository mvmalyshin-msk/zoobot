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

LOGO = '<span class="brand"><span class="zoo">ЗОО</span>КОНСЬЕРЖ</span>'
