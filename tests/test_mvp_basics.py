"""MVP, шаг 1 (проектное предложение 15.11.2026, решения владельца):
- админу в уведомлении о заявке — цена для клиента (база + наценка), а не базовая цена исполнителя;
- экран «Заявка принята» говорит, когда ответят: «в течение рабочего дня, обычно за 1–2 часа» (слова владельца);
- на главной один заголовок у вопросов-ответов, третий шаг «Как это работает» не обещает «Специалист приедет»
  (на передержку питомец едет сам)."""
import asyncio
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_mvp_basics_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import orders  # noqa: E402
import pricing  # noqa: E402
import webserver  # noqa: E402

FORM = {"name": "Анна", "phone": "8 900 777-88-99", "address": "Москва, ул. Ленина, 1",
        "when": "завтра в 10:00", "pet": "Дымок", "comment": "", "consent": "on", "site": ""}


class Base(unittest.TestCase):
    def run_app(self, coro):
        async def go():
            await db.init_db()
            sent = []

            async def fake(bot, text, reply_markup=None, **kw):
                sent.append(text)
            orig = orders.notify_admins
            orders.notify_admins = fake
            try:
                app = webserver.make_app()
                app["bot"] = object()
                async with TestClient(TestServer(app)) as c:
                    return await coro(c, sent)
            finally:
                orders.notify_admins = orig
        return asyncio.run(go())


class AdminSeesClientPrice(Base):
    def test_notification_has_price_with_markup(self):
        async def case(c, sent):
            await db.execute("DELETE FROM price_rules WHERE condition_type='always'")
            await db.execute("INSERT INTO price_rules (name, condition_type, effect_type, value, active) "
                             "VALUES ('Наценка сервиса', 'always', 'percent', 30, 1)")
            svc = await db.fetch_one("SELECT * FROM services WHERE category='walk' AND active=1 ORDER BY base_price LIMIT 1")
            client = (await pricing.with_client_price([svc]))[0]["from_price"]
            self.assertNotEqual(client, svc["base_price"])
            await c.post("/request", data={**FORM, "service_id": str(svc["id"])})
            text = sent[-1]
            self.assertIn(f"от {client:,}".replace(",", " "), text.replace(" ", " "))
            self.assertNotIn(f"(от {svc['base_price']:,}".replace(",", " "), text.replace(" ", " "))
        self.run_app(case)


class DonePageSaysWhen(Base):
    def test_response_time_promise(self):
        async def case(c, sent):
            svc = await db.fetch_one("SELECT id FROM services WHERE active=1 LIMIT 1")
            t = await (await c.post("/request", data={**FORM, "service_id": str(svc["id"])})).text()
            self.assertIn("в течение рабочего дня", t)
            self.assertIn("обычно за 1–2 часа", t)
        self.run_app(case)


class HomeCopy(Base):
    def test_one_faq_heading_and_honest_steps(self):
        async def case(c, sent):
            t = await (await c.get("/")).text()
            h2 = re.findall(r"<h2>([^<]+)</h2>", t)
            self.assertEqual(sum(1 for h in h2 if "опрос" in h), 1, h2)
            self.assertNotIn("Специалист приедет", t)
            self.assertIn("Подтвердим цену и исполнителя", t)
        self.run_app(case)


if __name__ == "__main__":
    unittest.main()
