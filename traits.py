"""Повадки питомца (анкета для выгульщика): что нужно знать до выхода на улицу.

В базе хранится строка кодов через запятую (pets.traits), порядок всегда как в TRAITS.
"""
TRAITS: list[tuple[str, str]] = [
    ("pull", "тянет поводок"),
    ("pick", "подбирает с земли"),
    ("aggr_dog", "агрессия к собакам"),
    ("aggr_people", "агрессия к людям"),
    ("muzzle", "нужен намордник"),
    ("nocontact", "нельзя знакомиться с другими собаками"),
    ("fear", "есть страхи (громкие звуки и т. п.)"),
    ("chase", "бежит за кошками и птицами"),
    ("paws", "протирать лапы после прогулки"),
]
LABELS = dict(TRAITS)
DANGER = {"aggr_dog", "aggr_people"}  # на карточке исполнителя выделяем предупреждением


def toggle(codes: list[str], code: str) -> list[str]:
    """Включить/выключить повадку. Неизвестный код игнорируется."""
    if code not in LABELS:
        return list(codes)
    return [c for c in codes if c != code] if code in codes else [*codes, code]


def _ordered(codes) -> list[str]:
    return [c for c, _ in TRAITS if c in set(codes)]


def pack(codes: list[str]) -> str:
    return ",".join(_ordered(codes))


def unpack(raw: str | None) -> list[str]:
    return _ordered((raw or "").split(","))


def describe(codes: list[str]) -> str:
    return "; ".join(LABELS[c] for c in _ordered(codes))


def has_danger(codes: list[str]) -> bool:
    return bool(DANGER & set(codes))
