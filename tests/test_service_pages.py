"""Страницы услуг под поисковые запросы: выгул, зооняня, передержка, зоотакси."""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_service_pages_test.db"

import seo  # noqa: E402
import servicepages  # noqa: E402
import webserver  # noqa: E402

SLUGS = ("/vygul-sobak", "/zoonyanya", "/peredergka", "/zootaksi")


class ServicePages(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        self.client = TestClient(TestServer(webserver.make_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_every_page_opens_and_is_indexable(self):
        for path in SLUGS:
            r = await self.client.get(path)
            t = await r.text()
            self.assertEqual(r.status, 200, path)
            self.assertNotIn("noindex", r.headers.get("X-Robots-Tag", ""), path)
            self.assertRegex(t, r"<title>[^<]{20,}</title>", path)
            self.assertIn('rel="canonical"', t, path)
            self.assertRegex(t, r'<meta name="description" content="[^"]{60,}"', path)
            self.assertEqual(len(re.findall(r"<h1[ >]", t)), 1, path)
            self.assertIn('href="/request"', t, path)
            self.assertIn("https://t.me/", t, path)
            self.assertIn("application/ld+json", t, path)

    async def test_pages_name_the_service_and_the_region_not_only_khimki(self):
        keys = {"/vygul-sobak": "выгул", "/zoonyanya": "зооняня", "/peredergka": "передержк", "/zootaksi": "зоотакси"}
        for path, key in keys.items():
            t = (await (await self.client.get(path)).text()).lower()
            self.assertIn(key, t, path)
            self.assertIn("московской области", t, path)
            self.assertNotIn("в химках", t, path)

    async def test_pages_are_in_sitemap_and_indexnow_list(self):
        for path in SLUGS:
            self.assertIn(path, seo.PUBLIC_PAGES)
        x = await (await self.client.get("/sitemap.xml")).text()
        for path in SLUGS:
            self.assertIn(path + "</loc>", x)

    async def test_home_links_to_every_service_page(self):
        t = await (await self.client.get("/")).text()
        for path in SLUGS:
            self.assertIn(f'href="{path}"', t)

    async def test_unknown_page_is_404_and_no_false_promises(self):
        self.assertEqual((await self.client.get("/vygul-koshek")).status, 404)
        for p in servicepages.PAGES.values():
            body = (p["title"] + p["lead"] + " ".join(p["points"])).lower()
            for bad in ("круглосуточно", "гарантируем", "лицензи", "дешевле всех"):
                self.assertNotIn(bad, body, p["title"])


if __name__ == "__main__":
    unittest.main()
