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

SLUGS = ("/stati/sobaka-prygaet-na-gostej", "/stati/sobaka-voruet-so-stola", "/stati/kak-podstrich-kogti-sobake", "/stati/koshka-boitsya-veterinara", "/stati/pervyj-vizit-shchenka-k-veterinaru", "/stati/zootaksi-v-aeroport", "/stati/kak-peredat-klyuchi-sitteru", "/stati/koshka-ne-hodit-v-lotok", "/stati/pereezd-s-sobakoj", "/stati/sobaka-i-rebenok", "/stati/sobaka-podbiraet-s-zemli", "/stati/s-kem-ostavit-homyaka-ili-popugaya", "/stati/kamera-dlya-nablyudeniya-za-pitomcem", "/stati/kak-chasto-kupat-koshku", "/stati/sobaka-laet-doma", "/stati/kot-deret-mebel", "/stati/kak-priuchit-shchenka-k-pelenke", "/stati/shchenok-skulit-nochyu", "/stati/kak-chasto-kupat-sobaku", "/stati/skolko-stoit-soderzhanie-sobaki", "/stati/sobaka-ne-est-na-perederzhke", "/stati/odezhda-dlya-sobaki-zimoj", "/stati/uhod-za-pozhiloj-sobakoj", "/stati/kak-podruzhit-koshku-i-sobaku", "/stati/uhod-za-pozhiloj-koshkoj", "/stati/shchenok-v-novom-dome", "/stati/kotenok-v-novom-dome", "/stati/kak-otuchit-sobaku-tyanut-povodok", "/stati/kak-vybrat-zoonyanyu-dlya-koshki", "/stati/pereezd-s-koshkoj-v-novuyu-kvartiru", "/stati/kogda-mozhno-gulyat-so-shchenkom", "/stati/kak-perevozit-sobaku-v-mashine", "/stati/kak-vybrat-dogsittera", "/stati/kak-vybrat-perenosku-dlya-koshki", "/stati/kot-oret-po-nocham", "/stati/kak-perevezti-sobaku-v-poezde-i-samolete", "/stati/kak-perevezti-koshku", "/stati/kak-podgotovit-sobaku-k-perederzhke", "/stati/sobaka-boitsya-salyuta", "/stati/sobaka-zimoj-reagenty-lapy", "/stati/skolko-stoit-zoonyanya", "/stati/skolko-stoit-vygul-sobaki", "/stati/mozhno-li-ostavit-koshku-odnu-doma", "/stati/sobaka-ne-ostaetsya-doma-odna", "/stati/zoogostinica-ili-perederzhka-doma", "/stati/zootaksi-dlya-sobak-i-koshek", "/stati/skolko-stoit-perederzhka-sobaki-i-koshki", "/stati/s-kem-ostavit-pitomca-na-novogodnie-prazdniki", "/stati/kak-vybrat-peredergku", "/stati/skolko-gulyat-s-sobakoi", "/stati/kak-podgotovit-koshku-k-otezdu")


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
            self.assertIn('"dateModified": "', t, path)
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

    async def test_price_article_fills_live_prices_and_has_rich_blocks(self):
        import db
        await db.init_db()
        path = "/stati/skolko-stoit-perederzhka-sobaki-i-koshki"
        r = await self.client.get(path)
        t = await r.text()
        self.assertEqual(r.status, 200)
        self.assertNotIn("{{", t)
        prices = await articles.current_prices()
        self.assertIn(articles.rub(prices["boarding_home"]).replace(" ", "\u00a0"), t)
        self.assertIn(articles.rub(prices["boarding_home"] * 11).replace(" ", "\u00a0"), t)
        self.assertIn("<table>", t)
        self.assertGreaterEqual(t.count("<h3>"), 6)
        self.assertGreaterEqual(t.count('<img src="/images/stati/'), 2)
        self.assertNotIn('alt=""', t)
        self.assertGreaterEqual(t.count('href="/request"'), 2)  # заявка в середине и в конце
        self.assertIn("Источники", t)
        self.assertGreaterEqual(t.count("<summary>"), 5)
        title = articles.ARTICLES[path]["title"]
        self.assertLessEqual(len(title), 60)
        self.assertLessEqual(len(articles.ARTICLES[path]["description"]), 155)
        for src in articles.ARTICLES[path]["images"]:
            img = await self.client.get(src)
            self.assertEqual(img.status, 200, src)

    def test_fill_prices_without_data_leaves_no_braces(self):
        self.assertEqual(articles.fill_prices("от {{boarding_home}}", {}), "от по запросу")
        self.assertEqual(articles.fill_prices("{{x_x2}}", {"x": 1000}), "2\u00a0000\u00a0₽")

    async def test_zootaksi_article_shows_live_taxi_price(self):
        import db
        await db.init_db()
        t = await (await self.client.get("/stati/zootaksi-dlya-sobak-i-koshek")).text()
        prices = await articles.current_prices()
        self.assertIn(articles.rub(prices["taxi"]).replace(" ", "\u00a0"), t)
        self.assertNotIn("{{", t)
        self.assertIn('href="/zootaksi"', t)

    async def test_about_page_is_public_linked_and_honest(self):
        r = await self.client.get("/o-nas")
        t = await r.text()
        self.assertEqual(r.status, 200)
        self.assertIn('"@type": "LocalBusiness"', t)
        self.assertIn('href="/policy"', t)
        self.assertIn("/o-nas", seo.PUBLIC_PAGES)
        self.assertIn('href="/o-nas"', await (await self.client.get("/stati")).text())  # ссылка в подвале
        for bad in ("гарантируем", "лицензи", "застрахован", "отзыв"):
            self.assertNotIn(bad, t.lower())

    async def test_cat_and_dog_boarding_pages(self):
        import db
        await db.init_db()
        prices = await articles.current_prices()
        for path in ("/peredergka-sobak", "/peredergka-koshek"):
            r = await self.client.get(path)
            t = await r.text()
            self.assertEqual(r.status, 200, path)
            self.assertNotIn("{{", t, path)
            self.assertIn(articles.rub(prices["boarding_hotel"]).replace(" ", "\u00a0"), t, path)
            self.assertIn('"@type": "FAQPage"', t, path)
            self.assertIn("/stati/skolko-stoit-perederzhka-sobaki-i-koshki", t, path)  # статья о ценах
            self.assertNotIn(f'href="{path}/', t, path)  # нет ссылок на несуществующие городские подстраницы
            self.assertIn(path, seo.PUBLIC_PAGES)
            self.assertEqual(len(re.findall(r"<h1[ >]", t)), 1, path)
        parent = await (await self.client.get("/peredergka")).text()
        self.assertIn('href="/peredergka-sobak"', parent)
        self.assertIn('href="/peredergka-koshek"', parent)

    def test_site_texts_follow_positioning(self):
        """positioning.md: фотоотчёт не обещаем, «проверенные» без конкретики не пишем."""
        import about
        import servicepages
        text = (about.render("https://x") + str(servicepages.PAGES)
                + " ".join(str(a.get("help", "")) + str(a.get("sections", "")) for a in articles.ARTICLES.values()))
        for bad in ("фотоотчёты", "присылает фото.", "проверенных специалист", "короткий отчёт с фото"):
            self.assertNotIn(bad, text)

    async def test_home_links_to_top_articles(self):
        t = await (await self.client.get("/")).text()
        self.assertIn("Полезные статьи", t)
        for p in articles.HOME_PICKS:
            self.assertIn(p, articles.ARTICLES)
            self.assertIn(f'href="{p}"', t)


if __name__ == "__main__":
    unittest.main()
