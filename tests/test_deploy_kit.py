"""Пакет развёртывания на российский сервер: скрипты без синтаксических ошибок, секретов в них нет."""
import re
import subprocess
import unittest
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
SCRIPTS = ("install.sh", "update.sh", "backup.sh", "check.sh")


class DeployKit(unittest.TestCase):
    def test_scripts_exist_and_parse(self):
        for name in SCRIPTS:
            p = DEPLOY / name
            self.assertTrue(p.exists(), name)
            r = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{name}: {r.stderr}")

    def test_scripts_are_executable(self):
        for name in SCRIPTS:
            self.assertTrue((DEPLOY / name).stat().st_mode & 0o111, name)

    def test_no_secrets_inside_scripts(self):
        for name in SCRIPTS:
            t = (DEPLOY / name).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\d{8,}:[A-Za-z0-9_-]{30,}", t), f"{name}: похоже на токен бота")
            self.assertNotIn("password=", t.lower())

    def test_install_asks_token_hidden_and_locks_env(self):
        t = (DEPLOY / "install.sh").read_text(encoding="utf-8")
        self.assertIn("read -r -s", t)          # токен вводится скрыто
        self.assertIn("chmod 600", t)           # файл с настройками виден только владельцу
        self.assertIn("TELEGRAM_PROXY", t)


    def test_branch_is_chosen_and_remembered_for_updates(self):
        i = (DEPLOY / "install.sh").read_text(encoding="utf-8")
        u = (DEPLOY / "update.sh").read_text(encoding="utf-8")
        self.assertIn('BRANCH="${BRANCH:-main}"', i)
        self.assertIn('--branch "$BRANCH"', i)
        self.assertIn(".branch", i)
        self.assertIn(".branch", u)
        self.assertNotIn("origin/main", u)


if __name__ == "__main__":
    unittest.main()
