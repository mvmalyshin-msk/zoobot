"""Одна философия во всех описаниях: «подбираем проверенных специалистов», четыре услуги в одном порядке, одинаковые слова."""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_brandcopy_test.db"

import brand  # noqa: E402
import db  # noqa: E402
import texts  # noqa: E402
import webserver  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ORDER = ("Выгул", "Зооняня", "Передержка", "Зоотакси")


class CopyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        self.client = TestClient(TestServer(webserver.make_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    def test_canon_has_four_services_in_order(self):
        self.assertEqual(tuple(t for _, t in brand.SERVICES), ORDER)
        low = brand.PITCH.lower()
        self.assertEqual(sorted(low.index(w) for w in ("выгул", "зоонян", "передержк", "зоотакси")),
                         [low.index(w) for w in ("выгул", "зоонян", "передержк", "зоотакси")])    # порядок как в меню
        self.assertIn("проверенных специалистов", brand.PITCH)

    def test_bot_and_manifest_use_the_same_words(self):
        self.assertEqual(texts.BOT_SHORT_DESCRIPTION, brand.SHORT)
        self.assertLessEqual(len(brand.SHORT), 120)
        self.assertIn("проверенн", texts.BOT_DESCRIPTION)
        man = json.loads((ROOT / "webapp" / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual(man["description"], brand.SHORT)
        adm = json.loads((ROOT / "webapp" / "manifest-admin.webmanifest").read_text(encoding="utf-8"))
        self.assertNotIn("собак", adm.get("description", ""))

    async def test_home_says_the_canon(self):
        t = await (await self.client.get("/")).text()
        self.assertIn('name="description" content="Подберём проверенного специалиста', t)   # мета-описание: та же суть
        for w in ORDER:
            self.assertIn(w, t)
        self.assertIn("Подберём проверенного специалиста", t)   # услуги и так на карточках (14.11)
        self.assertIn("выбирать будете вы", t)

    async def test_every_client_page_has_the_strap(self):
        for p in ("/request", "/contact", "/chat/login", "/chat/login/tg"):
            t = await (await self.client.get(p)).text()
            if p in ("/request", "/contact"):
                self.assertIn('class="site-header"', t, p)
                self.assertIn('class="site-footer"', t, p)
                self.assertNotIn("КЛИЕНТСКОЕ МЕНЮ", t, p)
                self.assertIn('href="/chat/login"', t, p)
                self.assertIn('href="/portal"', t, p)
            else:
                self.assertIn(brand.STRAP, t, p)
                self.assertEqual([t.index(f">{w}<") for w in ORDER], sorted(t.index(f">{w}<") for w in ORDER), p)


if __name__ == "__main__":
    unittest.main()
