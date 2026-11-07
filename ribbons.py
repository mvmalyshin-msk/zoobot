"""Лента роли: синяя — клиентский интерфейс, красная — администратор.
Чтобы с первого взгляда было видно, где чьё меню, и админ не путал клиентские экраны со своими."""
import html as _h

CLIENT = "client"
ADMIN = "admin"

_STYLE = {
    CLIENT: ("#1d6fd6", "🔵", "КЛИЕНТСКОЕ МЕНЮ · Зооконсьерж"),
    ADMIN: ("#d62b2b", "🔴", "АДМИНИСТРАТОР · меню управления"),
}


def html(role: str) -> str:
    """Полоса сверху страницы. Для клиента — синяя, для администратора — красная."""
    color, dot, label = _STYLE[role]
    return ('<div class="role-ribbon" role="banner" style="background:%s;color:#fff;font:700 13px/1.3 -apple-system,'
            'system-ui,sans-serif;text-align:center;padding:7px 10px;letter-spacing:.3px">%s %s</div>'
            % (color, dot, _h.escape(label)))


def tg_prefix(role: str) -> str:
    """Telegram не умеет полосы: метка роли — первая строка сообщения."""
    _, dot, label = _STYLE[role]
    return f"{dot} {label}\n\n"
