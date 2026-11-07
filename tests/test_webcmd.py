"""В веб-чате есть меню команд рядом со строкой ввода, как в Telegram: тот же список и те же команды."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HTML = (ROOT / "webapp" / "chat.html").read_text(encoding="utf-8")


def _telegram_commands() -> list[str]:
    """Команды для клиента: первый список set_my_commands (админские команды сюда не входят)."""
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    first = src.split("set_my_commands([", 1)[1].split("])", 1)[0]
    return re.findall(r'BotCommand\(command="([a-z_]+)"', first)


class WebCommandMenuTest(unittest.TestCase):
    def test_menu_button_sits_next_to_the_input(self):
        form = re.search(r'<form id="f".*?</form>', HTML, re.S).group(0)
        self.assertIn('id="cmd"', form)
        self.assertLess(form.index('id="cmd"'), form.index('id="t"'))

    def test_menu_lists_the_same_commands_as_telegram(self):
        commands = _telegram_commands()
        self.assertTrue(commands)
        for cmd in commands:
            self.assertIn(f'data-cmd="/{cmd}"', HTML, cmd)

    def test_menu_item_sends_the_command_as_a_message(self):
        self.assertIn("send({text:c})", HTML)


if __name__ == "__main__":
    unittest.main()
