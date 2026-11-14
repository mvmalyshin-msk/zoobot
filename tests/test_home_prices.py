"""Главная, переделанная по просьбе владельца 14.11.2026: меньше слов, услуги на первом экране,
каждая услуга — сразу в заявку (услуга уже выбрана) и с ценой «от … ₽» из тарифов бота (меняются в портале)."""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_home_prices_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import db  # noqa: E402
import pricing  # noqa: E402
import webserver  # noqa: E402
from utils import rub  # noqa: E402

CATS = ("walk", "sitter", "boarding", "taxi")


class HomePrices(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        self.client = TestClient(TestServer(webserver.make_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def home(self):
        return await (await self.client.get("/")).text()

    async def lowest(self):
        items = await pricing.with_client_price(await db.fetch_all("SELECT * FROM services WHERE active=1"))
        return {c: min(s["from_price"] for s in items if s["category"] == c) for c in CATS}

    async def test_every_service_card_goes_to_request_and_shows_price_from(self):
        t = await self.home()
        low = await self.lowest()
        for c in CATS:
            m = re.search(rf'<a class="service-card[^"]*" href="/request\?service={c}"[^>]*>(.*?)</a>', t, re.S)
            self.assertIsNotNone(m, c)
            self.assertIn("от " + rub(low[c]), m.group(1).replace("\u00a0", " "), c)

    async def test_price_follows_tariffs_from_portal(self):
        await db.execute("UPDATE services SET base_price=base_price+1000 WHERE code='walk30'")
        try:
            low = await self.lowest()
            self.assertIn("от " + rub(low["walk"]), (await self.home()).replace("\u00a0", " "))
        finally:
            await db.execute("UPDATE services SET base_price=base_price-1000 WHERE code='walk30'")

    async def test_services_are_on_the_first_screen(self):
        t = await self.home()
        hero = t[t.index('<section class="hero"'):]
        hero = hero[:hero.index("</section>")]
        self.assertIn('class="services"', hero)                         # услуги — в первом блоке, а не ниже
        self.assertLess(t.index('class="services"'), t.index('id="how-it-works"'))

    async def test_extra_words_are_gone(self):
        t = await self.home()
        for extra in ("ЧЕМ МОЖЕМ ПОМОЧЬ", "ПОНЯТНЫЙ ПРОЦЕСС", "ОТВЕТЫ НА ВОПРОСЫ", "Начнём с вашей задачи",
                      "Хотите сначала обсудить?", "Услуги для разных ситуаций", "class=\"eyebrow\""):
            self.assertNotIn(extra, t)

    async def test_request_form_preselects_the_clicked_service(self):
        items = await pricing.with_client_price(await db.fetch_all("SELECT * FROM services WHERE active=1"))
        for c in CATS:
            cheapest = min((s for s in items if s["category"] == c), key=lambda s: s["from_price"])
            t = await (await self.client.get(f"/request?service={c}")).text()
            self.assertRegex(t, rf'<option value="{cheapest["id"]}" selected>', c)
        t = await (await self.client.get("/request?service=nonsense")).text()
        self.assertNotIn(" selected>", t)

    async def test_cards_are_compact_colour_tiles(self):
        """Вариант 5, выбран владельцем 14.11: цветные плитки без белого фона, маленькая иконка, стрелка, на iPhone 2×2."""
        t = await self.home()
        card = re.search(r"\.service-card\{([^}]*)\}", t).group(1)
        self.assertIn("background:var(--t)", card)                    # фон — цвет услуги, а не белый
        self.assertNotIn("min-height", card)                           # без пустого места
        pad = [int(x) for x in re.search(r"padding:([\d px]+)", card).group(1).replace("px", "").split()]
        self.assertTrue(max(pad) <= 14, pad)
        icon = re.search(r"\.service-icon\{([^}]*)\}", t).group(1)
        self.assertTrue(int(re.search(r"width:(\d+)px", icon).group(1)) <= 30)
        self.assertEqual(t.count('class="chev"'), 4)                    # стрелка «перейти» на каждой плитке
        mobile = t[t.index("@media(max-width:640px)"):]
        self.assertIn(".services{grid-template-columns:repeat(2,minmax(0,1fr))", mobile.replace(" ", ""))


if __name__ == "__main__":
    unittest.main()
