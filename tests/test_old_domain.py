"""Старый адрес сайта заблокирован у Tele2 и больше не работает: его не должно быть в коде."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD = "zoobot-production.up.railway.app"


class OldDomainGone(unittest.TestCase):
    def test_no_old_domain_in_sources(self):
        bad = []
        for p in list(ROOT.glob("*.py")) + list((ROOT / "handlers").glob("*.py")) + list((ROOT / "webapp").glob("*.*")):
            if p.is_file() and OLD in p.read_text(encoding="utf-8", errors="ignore"):
                bad.append(p.name)
        self.assertEqual(bad, [], f"старый адрес остался в: {bad}")


if __name__ == "__main__":
    unittest.main()
