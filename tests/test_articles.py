"""Статьи для сайта: полезные тексты под поисковые запросы владельцев питомцев."""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_articles_test.db"

import articles  # noqa: E402
import seo  # noqa: E402
import webserver  # noqa: E402

SLUGS = ("/stati/kak-vybrat-peredergku", "/stati/skolko-gulyat-s-sobakoi", "/stati/kak-podgotovit-koshku-k-otezdu")


class Articles(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        self.client = TestClient(TestServer(webserver.make_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_every_article_opens_and_is_indexable(self):
        for path in SLUGS:
            r = await self.client.get(path)
            t = await r.text()
            self.assertEqual(r.status, 200, path)
            self.assertNotIn("noindex", r.headers.get("X-Robots-Tag", ""), path)
            self.assertRegex(t, r"<title>[^<]{20,}</title>", path)
            self.assertIn('rel="canonical"', t, path)
            self.assertRegex(t, r'<meta name="description" content="[^"]{60,}"', path)
            self.assertEqual(len(re.findall(r"<h1[ >]", t)), 1, path)
            self.assertGreaterEqual(len(re.findall(r"<h2[ >]", t)), 3, path)
            self.assertIn('"@type": "Article"', t, path)
            self.assertIn('href="/request"', t, path)
            self.assertIn('class="site-header"', t, path)
            self.assertIn('class="site-footer"', t, path)

    async def test_articles_link_to_a_service_page_and_name_the_region(self):
        for path in SLUGS:
            t = (await (await self.client.get(path)).text())
            self.assertRegex(t, r'href="/(vygul-sobak|zoonyanya|peredergka|zootaksi)"', path)
            self.assertIn("Московской области", t, path)
            self.assertNotIn("в химках", t.lower(), path)

    async def test_index_lists_all_and_everything_is_in_sitemap(self):
        r = await self.client.get("/stati")
        t = await r.text()
        self.assertEqual(r.status, 200)
        for path in SLUGS:
            self.assertIn(f'href="{path}"', t)
        x = await (await self.client.get("/sitemap.xml")).text()
        for path in ("/stati",) + SLUGS:
            self.assertIn(path, seo.PUBLIC_PAGES)
            self.assertIn(path + "</loc>", x)

    async def test_index_teasers_have_single_sentence_punctuation(self):
        t = await (await self.client.get("/stati")).text()
        self.assertNotIn("..</p>", t)
        for article in articles.ARTICLES.values():
            teaser = article["lead"].split(". ", 1)[0].strip()
            if not teaser.endswith("."):
                teaser += "."
            self.assertIn(f"<p>{articles._e(teaser)}</p>", t)

    async def test_home_links_to_articles(self):
        self.assertIn('href="/stati"', await (await self.client.get("/")).text())

    async def test_unknown_article_404_and_no_false_promises_or_medical_claims(self):
        self.assertEqual((await self.client.get("/stati/net-takoi")).status, 404)
        for a in articles.ARTICLES.values():
            body = (a["title"] + a["lead"] + " ".join(s["body"] for s in a["sections"])).lower()
            for bad in ("круглосуточно", "гарантируем", "лицензи", "дешевле всех", "вылечит", "лечение"):
                self.assertNotIn(bad, body, a["title"])

    async def test_audit_blocks_faq_dates_help_and_crosslinks(self):
        for path in SLUGS:
            t = await (await self.client.get(path)).text()
            self.assertIn('"@type": "FAQPage"', t, path)
            self.assertIn("Частые вопросы", t, path)
            self.assertIn("Как мы можем помочь", t, path)
            self.assertIn("Обновлено", t, path)
            self.assertIn(f'"dateModified": "{articles.UPDATED}"', t, path)
            for other in SLUGS:
                if other != path:
                    self.assertIn(f'href="{other}"', t, path)

    async def test_service_pages_link_back_to_their_articles(self):
        for path, a in articles.ARTICLES.items():
            t = await (await self.client.get(a["service"])).text()
            self.assertIn("Полезно почитать", t)
            self.assertIn(f'href="{path}"', t)

    async def test_titles_are_unique(self):
        titles = [a["title"] for a in articles.ARTICLES.values()]
        self.assertEqual(len(titles), len(set(titles)))


if __name__ == "__main__":
    unittest.main()
