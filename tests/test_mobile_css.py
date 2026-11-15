"""Мобильная вёрстка: .section не обнуляет боковые поля .home-shell; поля даты/времени не вылезают на iPhone."""
import re
import unittest
from pathlib import Path

W = Path(__file__).resolve().parent.parent / "webapp"


class MobileCss(unittest.TestCase):
    def test_section_keeps_side_padding(self):
        css = (W / "home.html").read_text(encoding="utf-8")
        for m in re.finditer(r"\.section\{padding:([^}]*)\}", css):
            self.assertEqual(len(m.group(1).split()), 1, f"сокращённое padding у .section затирает поля: {m.group(0)}")

    def test_date_time_inputs_fixed_for_ios(self):
        css = (W / "brand.css").read_text(encoding="utf-8")
        m = re.search(r'input\[type="?date"?\][^{]*\{([^}]*)\}', css)
        self.assertIsNotNone(m, "нет правила для input[type=date]")
        body = m.group(1)
        for need in ("-webkit-appearance:none", "min-width:0", "max-width:100%", "min-height:48px"):
            self.assertIn(need, body)
        self.assertIn("::-webkit-date-and-time-value", css)


if __name__ == "__main__":
    unittest.main()
