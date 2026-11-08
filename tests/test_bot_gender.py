"""Бот говорит о себе в мужском роде («Запомнил», а не «Запомнила» — замечание владельца)."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["texts.py", "keyboards.py", "orders.py", "talk.py", "webchat.py", "cabinet.py", "portal.py",
         "phoneverify.py", "webform.py"] + [str(p.relative_to(ROOT)) for p in (ROOT / "handlers").glob("*.py")]
# формы, которыми бот говорит о себе; «оплата прошла», «кнопка устарела» и слова клиента («перевела») сюда не входят
FEMININE = ["запомнила", "сохранила", "добавила", "записала", "передала", "узнала", "перенесла", "поняла", "убрала",
            "нашла", "написала", "отправила", "приняла", "проверила", "нашла", "рада", "готова", "должна",
            "уверена", "смогла", "увидела", "подобрала", "посчитала", "обновила", "удалила", "сама"]
WORD = re.compile(r"(?<![А-Яа-яЁё])(" + "|".join(FEMININE) + r")(?![А-Яа-яЁё])", re.I)


class BotSpeaksMasculineTest(unittest.TestCase):
    def test_no_feminine_first_person_forms_in_bot_texts(self):
        found = []
        for f in FILES:
            for i, line in enumerate((ROOT / f).read_text(encoding="utf-8").splitlines(), 1):
                if line.strip().startswith(("#", '"""')):  # комментарии и docstring — не речь бота
                    continue
                for m in WORD.finditer(line):
                    found.append(f"{f}:{i}: {m.group(1)}")
        self.assertEqual(found, [])

    def test_address_check_has_yo(self):
        import os, sys
        sys.path.insert(0, str(ROOT))
        os.environ.setdefault("BOT_TOKEN", "1:x")
        os.environ.setdefault("ADMIN_IDS", "111")
        import texts
        self.assertIn("всё верно?", texts.ADDR_CHECK)  # «ё» там, где она нужна


if __name__ == "__main__":
    unittest.main()
