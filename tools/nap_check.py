"""Проверка NAP: совпадают ли название, ссылки и регион на странице с «золотым стандартом» docs/growth/nap.json.

Запуск:  python3 tools/nap_check.py URL [URL ...]
         python3 tools/nap_check.py --local   (поднимет сайт у себя и проверит все публичные страницы)
Код выхода 0 — всё совпадает, 1 — есть расхождения. Без внешних библиотек, кроме aiohttp (он уже в requirements)."""
import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAP = json.loads((ROOT / "docs/growth/nap.json").read_text(encoding="utf-8"))


def check(url: str, html: str) -> list[str]:
    problems = []
    for need in NAP["must_contain"]:
        if need not in html:
            problems.append(f"нет «{need}»")
    for bad in NAP["must_not_contain"]:
        if bad in html:
            problems.append(f"устаревшая ссылка «{bad}»")
    if not re.search(r"Москв", html):
        problems.append("не указан регион (Москва и Московская область)")
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            problems.append("сломанный JSON-LD")
            continue
        if data.get("@type") == "LocalBusiness" and data.get("name") != NAP["name"]:
            problems.append(f"в разметке другое название: {data.get('name')}")
    return problems


async def _fetch_all(urls, session):
    out = []
    for u in urls:
        async with session.get(u) as r:
            out.append((u, r.status, await r.text()))
    return out


async def main(argv) -> int:
    from aiohttp import ClientSession
    if argv[:1] == ["--local"]:
        sys.path.insert(0, str(ROOT))
        os.environ.setdefault("BOT_TOKEN", "1:x")
        os.environ.setdefault("ADMIN_IDS", "111")
        os.environ.setdefault("DB_PATH", "/tmp/zoobot_nap_check.db")
        from aiohttp.test_utils import TestClient, TestServer
        import db
        import seo
        import webserver
        await db.init_db()
        client = TestClient(TestServer(webserver.make_app()))
        await client.start_server()
        try:
            pages = list(seo.PUBLIC_PAGES)
            results = [(p, (r := await client.get(p)).status, await r.text()) for p in pages]
        finally:
            await client.close()
    else:
        async with ClientSession() as s:
            results = await _fetch_all(argv, s)
    bad = 0
    for url, status, html in results:
        problems = ([f"HTTP {status}"] if status != 200 else []) + check(url, html)
        bad += bool(problems)
        print(("✅ " if not problems else "❌ ") + url + ("" if not problems else " — " + "; ".join(problems)))
    print(f"\nПроверено: {len(results)}, с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
