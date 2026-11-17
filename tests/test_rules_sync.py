"""Правила времени одинаковы везде: сайт, бот, статьи, страницы услуг, «О нас»; старые формулировки не возвращаются."""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("DB_PATH", "/tmp/zoobot_rules_sync_test.db")

import texts  # noqa: E402
import timerules  # noqa: E402
import webform  # noqa: E402

PUBLIC = ["articles.py", "servicepages.py", "about.py", "texts.py", "webform.py", "citypages.py", "handlers/client.py"]
OLD = ["за 40 минут", "через 40 минут", "40 минут от сейчас", "не раньше чем за сутки", "Не позднее чем за сутки",
       "не позднее чем за сутки", "Заявки принимаем ежедневно с 6:00 до 22:00", "обычно за 1–2 часа"]


class RulesSync(unittest.TestCase):
    def test_constants_match_the_agreed_rules(self):
        self.assertEqual((timerules.SERVICE_FROM, timerules.SERVICE_TO), (6, 22))
        self.assertEqual((timerules.ADMIN_FROM, timerules.ADMIN_TO), (9, 20))
        self.assertEqual((timerules.PHONE_FROM, timerules.PHONE_TO), (9, 22))
        self.assertEqual(timerules.BUFFER.total_seconds(), 2 * 3600)

    def test_no_old_wording_in_public_sources(self):
        for f in PUBLIC:
            src = (ROOT / f).read_text(encoding="utf-8")
            for bad in OLD:
                self.assertNotIn(bad, src, f"{f}: «{bad}»")

    def test_bot_texts_carry_the_same_rules(self):
        for t in (texts.ASK_WHEN, texts.ASK_WHEN_START, texts.ASK_WHEN_TAXI):
            for part in ("06:00 до 22:00", "2 часа", "09:00 до 20:00"):
                self.assertIn(part, t)

    def test_public_pages_mention_all_rules(self):
        for f in ("about.py",):
            src = (ROOT / f).read_text(encoding="utf-8")
            for part in ("06:00 до 22:00", "2 часа", "09:00 до 20:00", "Экспресс"):
                self.assertIn(part, src, f)

    def test_every_form_step_has_a_help_hint(self):
        html = webform.render_service_form("walk", [{"id": 1, "name": "Выгул 30 минут", "from_price": 300, "base_price": 300}], {}, {}, "n")
        for key in ("variant", "time", "where", "contacts"):
            self.assertIn(f'data-tip="{key}"', html)
            self.assertIn(f'"{key}"', webform.TIME_JS)


if __name__ == "__main__":
    unittest.main()
