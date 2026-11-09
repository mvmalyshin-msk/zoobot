"""Поисковая оптимизация: robots.txt, карта сайта, мета-теги главной, закрытые разделы, IndexNow."""
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_seo_test.db"

import db  # noqa: E402
import seo  # noqa: E402
import webserver  # noqa: E402


class SeoTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        self.client = TestClient(TestServer(webserver.make_app()))
        await self.client.start_server()
        self.base = f"http://{self.client.host}:{self.client.port}"

    async def asyncTearDown(self):
        await self.client.close()

    async def test_robots_closes_private_and_points_to_sitemap(self):
        r = await self.client.get("/robots.txt")
        self.assertEqual(r.status, 200)
        self.assertIn("text/plain", r.headers["Content-Type"])
        t = await r.text()
        for p in ("/portal", "/cabinet", "/api/", "/export/", "/chat/"):
            self.assertIn(f"Disallow: {p}", t)
        self.assertNotIn("Disallow: /chat\n", t)  # сама /chat и /map — с noindex, а не запретом: иначе робот не увидит noindex
        self.assertNotIn("Disallow: /\n", t)
        self.assertIn(f"Sitemap: {self.base}/sitemap.xml", t)

    async def test_sitemap_lists_public_pages_only(self):
        r = await self.client.get("/sitemap.xml")
        self.assertEqual(r.status, 200)
        self.assertIn("xml", r.headers["Content-Type"])
        t = await r.text()
        locs = re.findall(r"<loc>(.*?)</loc>", t)
        self.assertEqual(locs[0], f"{self.base}/")
        for must in ("/request", "/contact", "/policy"):
            self.assertIn(f"{self.base}{must}", locs)
        for bad in ("portal", "chat", "api", "export"):
            self.assertFalse(any(bad in u for u in locs), bad)

    async def test_home_has_snippet_canonical_social_and_structured_data(self):
        t = await (await self.client.get("/")).text()
        self.assertIn(f'<link rel="canonical" href="{self.base}/">', t)
        self.assertIn('property="og:title"', t)
        self.assertIn('property="og:image"', t)
        self.assertIn('name="twitter:card"', t)
        blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', t, flags=re.S)
        types = {json.loads(b)["@type"] for b in blocks}
        self.assertTrue({"LocalBusiness", "FAQPage"} <= types, types)
        self.assertNotIn("__SEO__", t)

    async def test_home_mentions_places_and_services_for_search_phrases(self):
        t = await (await self.client.get("/")).text()
        for must in ("Московск", "Москв", "выгул собак", "передержка", "зоотакси"):
            self.assertIn(must.lower(), t.lower())

    async def test_private_sections_are_noindex(self):
        for path in ("/portal", "/chat", "/map", "/contact/call"):
            r = await self.client.get(path, allow_redirects=False)
            self.assertIn("noindex", r.headers.get("X-Robots-Tag", ""), path)
        r = await self.client.get("/")
        self.assertNotIn("noindex", r.headers.get("X-Robots-Tag", ""))

    async def test_verification_codes_come_from_variables(self):
        env = {"YANDEX_VERIFICATION": "abc123yan", "GOOGLE_VERIFICATION": "goo456", "BING_VERIFICATION": "bing789"}
        with mock.patch.dict(os.environ, env):
            t = await (await self.client.get("/")).text()
        self.assertIn('<meta name="yandex-verification" content="abc123yan">', t)
        self.assertIn('<meta name="google-site-verification" content="goo456">', t)
        self.assertIn('<meta name="msvalidate.01" content="bing789">', t)
        t = await (await self.client.get("/")).text()
        self.assertNotIn("yandex-verification", t)

    async def test_verification_value_is_escaped(self):
        with mock.patch.dict(os.environ, {"YANDEX_VERIFICATION": '"><script>x</script>'}):
            t = await (await self.client.get("/")).text()
        self.assertNotIn("<script>x</script>", t)

    async def test_indexnow_key_file(self):
        r = await self.client.get(f"/{seo.INDEXNOW_KEY}.txt")
        self.assertEqual(r.status, 200)
        self.assertEqual((await r.text()).strip(), seo.INDEXNOW_KEY)
        self.assertEqual((await self.client.get("/0123456789abcdef0123456789abcdef.txt")).status, 404)

    async def test_other_pages_have_description(self):
        for path in ("/request", "/contact"):
            t = await (await self.client.get(path)).text()
            self.assertIn('name="description"', t, path)


class IndexNow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await db.init_db()
        await db.execute("DELETE FROM kv WHERE key LIKE 'indexnow:%'")

    async def test_ping_once_per_version(self):
        sent = []

        async def fake(url, payload):
            sent.append((url, payload))
            return 200
        base = "https://example.up.railway.app"
        await seo.indexnow_ping(base, "9.9", post=fake)
        self.assertTrue(sent)
        for url, p in sent:
            self.assertEqual(p["host"], "example.up.railway.app")
            self.assertEqual(p["key"], seo.INDEXNOW_KEY)
            self.assertEqual(p["keyLocation"], f"{base}/{seo.INDEXNOW_KEY}.txt")
            self.assertIn(f"{base}/", p["urlList"])
        n = len(sent)
        await seo.indexnow_ping(base, "9.9", post=fake)
        self.assertEqual(len(sent), n)  # во второй раз для той же версии ничего не отправляется
        await seo.indexnow_ping(base, "10.0", post=fake)
        self.assertGreater(len(sent), n)

    async def test_no_ping_without_public_address(self):
        sent = []

        async def fake(url, payload):
            sent.append(url)
            return 200
        await seo.indexnow_ping("", "1.0", post=fake)
        await seo.indexnow_ping("http://localhost:8080", "1.0", post=fake)
        self.assertEqual(sent, [])

    async def test_failed_ping_is_not_remembered_and_never_raises(self):
        async def boom(url, payload):
            raise OSError("нет сети")
        await seo.indexnow_ping("https://x.example.com", "2.0", post=boom)
        sent = []

        async def ok(url, payload):
            sent.append(url)
            return 202
        await seo.indexnow_ping("https://x.example.com", "2.0", post=ok)
        self.assertTrue(sent)


if __name__ == "__main__":
    unittest.main()
