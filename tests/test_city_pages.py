"""Страницы «услуга × город» и общая страница /goroda: открываются, индексируются, не дублируют друг друга."""
import json
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_city_pages_test.db"

import citydata  # noqa: E402
import seo  # noqa: E402
import webserver  # noqa: E402

CITY_PAGES = [p for p in citydata.PATHS if p != citydata.HUB]


class CityPages(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        self.client = TestClient(TestServer(webserver.make_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_every_city_page_opens_and_is_indexable(self):
        titles, descs = set(), set()
        for path in CITY_PAGES:
            r = await self.client.get(path)
            t = await r.text()
            self.assertEqual(r.status, 200, path)
            self.assertNotIn("noindex", r.headers.get("X-Robots-Tag", ""), path)
            title = re.search(r"<title>([^<]+)</title>", t).group(1)
            desc = re.search(r'<meta name="description" content="([^"]+)"', t).group(1)
            self.assertGreaterEqual(len(desc), 60, path)
            titles.add(title)
            descs.add(desc)
            self.assertEqual(len(re.findall(r"<h1[ >]", t)), 1, path)
            self.assertIn(f'rel="canonical" href="', t, path)
            self.assertRegex(t, r'rel="canonical" href="[^"]*' + re.escape(path) + '"', path)
            self.assertIn('href="/request"', t, path)
            self.assertIn("https://t.me/", t, path)
            for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', t, re.S):
                json.loads(block)  # структурированные данные валидны
        self.assertEqual(len(titles), len(CITY_PAGES), "заголовки страниц должны быть разными")
        self.assertEqual(len(descs), len(CITY_PAGES), "описания страниц должны быть разными")

    async def test_city_name_is_on_its_page(self):
        for slug, c in citydata.CITIES.items():
            t = await (await self.client.get(f"/vygul-sobak/{slug}")).text()
            self.assertIn(c["prep"], t)

    async def test_hub_and_service_pages_link_to_cities(self):
        hub = await (await self.client.get(citydata.HUB)).text()
        for path in CITY_PAGES:
            self.assertIn(f'href="{path}"', hub)
        for service in citydata.SERVICES:
            t = await (await self.client.get(service)).text()
            for slug in citydata.CITIES:
                self.assertIn(f'href="{service}/{slug}"', t)

    async def test_in_sitemap(self):
        x = await (await self.client.get("/sitemap.xml")).text()
        for path in citydata.PATHS:
            self.assertIn(path, seo.PUBLIC_PAGES)
            self.assertIn(path + "</loc>", x)

    async def test_no_false_promises(self):
        for path in CITY_PAGES:
            t = (await (await self.client.get(path)).text()).lower()
            for bad in ("круглосуточно", "гарантируем", "лицензи", "дешевле всех", "₽"):
                self.assertNotIn(bad, t, path)

    async def test_unknown_city_is_404(self):
        self.assertEqual((await self.client.get("/vygul-sobak/tver")).status, 404)


if __name__ == "__main__":
    unittest.main()
