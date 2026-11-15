"""Заявка с сайта: мат в любом текстовом поле не принимается, ошибка стоит под своим полем, заявка не создаётся."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_web_prof_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import webform  # noqa: E402


def form(**kw):
    base = {"service_id": "1", "name": "Анна", "phone": "8 900 123-45-67", "address": "Химки, улица Победы, 3",
            "pet": "Рекс", "comment": "добрый пёс", "consent": "on", "service": "walk", "when_date": "2099-01-01", "when_time": "10:00"}
    base.update(kw)
    return base


class WebProfanity(unittest.TestCase):
    def test_clean_form_has_no_errors(self):
        _, errors = webform.validate(form())
        self.assertEqual(errors, {})

    def test_each_text_field_rejects_profanity(self):
        for field, value in (("name", "Хуй"), ("pet", "пиздец а не пёс"), ("comment", "ебаный двор"),
                             ("address", "Химки, нахуй, 3"), ("comment", "бл*дь")):
            clean, errors = webform.validate(form(**{field: value}))
            self.assertIn(field, errors, (field, value))
            self.assertIn("нецензурн", errors[field])

    def test_error_rendered_under_field(self):
        _, errors = webform.validate(form(comment="ебаный двор"))
        html = webform.render_service_form("walk", [{"id": 1, "name": "Выгул", "category": "walk", "from_price": 400,
                                                    "base_price": 400}], form(comment="ебаный двор"), errors, "n")
        i = html.find('id="comment"')
        self.assertIn("нецензурн", html[i:i + 700])

    def test_page_contains_browser_rules(self):
        html = webform.render_service_form("walk", [{"id": 1, "name": "Выгул", "category": "walk", "from_price": 400,
                                                    "base_price": 400}], form(), {}, "n")
        self.assertIn("zcProf", html)


if __name__ == "__main__":
    unittest.main()
