"""Мы работаем в Москве и Московской области, а не только в Химках: публичные тексты это так и говорят."""
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_region_test.db"

import seo  # noqa: E402

PUBLIC = ("seo.py", "webapp/home.html", "webform.py", "contact.py")


class RegionTexts(unittest.TestCase):
    def test_public_pages_do_not_claim_service_only_in_khimki(self):
        for name in PUBLIC:
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotRegex(text, r"в Химках", name)
            self.assertNotRegex(text, r'"name": "Химки"', name)

    def test_seo_texts_name_moscow_and_the_region(self):
        for t in (seo.TITLE, seo.DESCRIPTION):
            self.assertIn("Московской области", t)
        self.assertTrue(re.search(r"Москв", seo.TITLE + seo.DESCRIPTION))


if __name__ == "__main__":
    unittest.main()
