"""Политика обработки персональных данных: текст, страница /policy, кнопка в согласии."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")

import keyboards as kb  # noqa: E402
import policy  # noqa: E402
import webserver  # noqa: E402

OP = {"name": "ИП Иванов Иван Иванович", "inn": "500100732259", "contact": "+7 900 000-00-00"}


class Text(unittest.TestCase):
    def test_has_required_sections(self):
        t = policy.render_text(OP)
        for part in ("Общие положения", "Цели обработки", "Правовые основания", "Категории",
                     "Порядок и условия", "Сроки хранения", "Права субъекта", "Меры защиты",
                     "Рассылки", "Изменение политики", "152-ФЗ", "Роскомнадзор", "10 рабочих дней"):
            self.assertIn(part, t, part)

    def test_operator_details_are_inserted(self):
        t = policy.render_text(OP)
        self.assertIn("ИП Иванов Иван Иванович", t)
        self.assertIn("ИНН 500100732259", t)
        self.assertIn("+7 900 000-00-00", t)

    def test_works_without_operator_details(self):
        t = policy.render_text({})
        self.assertIn("Зооконсьерж", t)
        self.assertNotIn("ИНН", t)
        self.assertNotIn("None", t)
        self.assertNotIn("{", t)

    def test_no_forbidden_data_requested(self):
        t = policy.render_text(OP)
        self.assertIn("не запрашиваем", t)  # паспорт, карты, коды из СМС

    def test_discloses_map_services_and_hosting(self):
        t = policy.render_text(OP)
        self.assertIn("OpenStreetMap", t)
        self.assertIn("Amvera", t)

    def test_chunks_fit_telegram_limit(self):
        parts = policy.chunks(policy.render_text(OP))
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertLessEqual(len(p), 4000)
        self.assertEqual("".join(parts).replace("\n", ""), policy.render_text(OP).replace("\n", ""))


class Html(unittest.TestCase):
    def test_html_escapes_operator_values(self):
        h = policy.render_html({"name": "<script>alert(1)</script>", "contact": "a&b"})
        self.assertNotIn("<script>alert", h)
        self.assertIn("&lt;script&gt;", h)
        self.assertIn("a&amp;b", h)

    def test_html_is_a_page(self):
        h = policy.render_html(OP)
        self.assertTrue(h.startswith("<!doctype html>"))
        self.assertIn("viewport", h)
        self.assertIn("Политика обработки персональных данных", h)
        self.assertIn('class="site-header"', h)
        self.assertIn('class="site-footer"', h)


class Link(unittest.TestCase):
    def test_explicit_policy_url_wins(self):
        self.assertEqual(policy.link("https://x.ru/p", "https://bot.up.railway.app"), "https://x.ru/p")

    def test_falls_back_to_own_page(self):
        self.assertEqual(policy.link("", "https://bot.up.railway.app"), "https://bot.up.railway.app/policy")

    def test_example_placeholder_is_not_a_link(self):
        self.assertEqual(policy.link("https://example.com/privacy", ""), "")
        self.assertEqual(policy.link("https://example.com/privacy", "https://b.app"), "https://b.app/policy")

    def test_no_link_at_all(self):
        self.assertEqual(policy.link("", ""), "")


class Keyboard(unittest.TestCase):
    def test_consent_with_link(self):
        rows = kb.consent("https://b.app/policy").inline_keyboard
        self.assertEqual(rows[0][0].url, "https://b.app/policy")
        self.assertEqual(rows[1][0].callback_data, "pdn:agree")

    def test_consent_without_link_shows_policy_in_chat(self):
        rows = kb.consent("").inline_keyboard
        self.assertEqual(rows[0][0].callback_data, "pol:show")
        self.assertIsNone(rows[0][0].url)
        self.assertEqual(rows[1][0].callback_data, "pdn:agree")


class Server(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        self.client = TestClient(TestServer(webserver.make_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_policy_page(self):
        r = await self.client.get("/policy")
        self.assertEqual(r.status, 200)
        self.assertIn("text/html", r.headers["Content-Type"])
        self.assertIn("Политика обработки персональных данных", await r.text())


if __name__ == "__main__":
    unittest.main()
