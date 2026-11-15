"""«С Ленина 5 до аэропорта»: оба адреса в одном сообщении разбираются, обычный адрес не трогаем."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")

import geo  # noqa: E402


class SplitRouteTest(unittest.TestCase):
    def test_splits_two_points(self):
        self.assertEqual(geo.split_route("С Ленина 5 до аэропорта Шереметьево"), ("Ленина 5", "аэропорта Шереметьево"))
        self.assertEqual(geo.split_route("от Мира 10 до Тверская 7"), ("Мира 10", "Тверская 7"))
        self.assertEqual(geo.split_route("из Сходни Ленина 5 в Химки Мира 3"), ("Сходни Ленина 5", "Химки Мира 3"))

    def test_plain_address_untouched(self):
        for t in ("Ленина 5", "Железнодорожная улица, 33 к1", "Москва, улица Ленина, 5", "до дома", "сюда"):
            self.assertIsNone(geo.split_route(t), t)

    def test_half_must_look_like_address(self):
        self.assertIsNone(geo.split_route("с утра до вечера"))
        self.assertIsNone(geo.split_route("от 5 до 7"))

    def test_garbage_ignored(self):
        self.assertIsNone(geo.split_route(""))
        self.assertIsNone(geo.split_route("с до"))


if __name__ == "__main__":
    unittest.main()


class FakeState:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.current = None

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kw):
        self.data.update(kw)

    async def set_state(self, s):
        self.current = s


class FlowTest(unittest.IsolatedAsyncioTestCase):
    async def test_point_a_message_with_route_saves_destination(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from handlers import client
        m = MagicMock()
        m.text = "с Ленина 5 до Мира 7"
        m.from_user = MagicMock()
        st = FakeState()
        with patch.object(client, "_address_input", new=AsyncMock()) as ai:
            await client.msg_point_a(m, st)
        self.assertEqual(st.data["pending_b"], "Мира 7")
        self.assertEqual(ai.await_args.args[2], "Ленина 5")
        self.assertEqual(ai.await_args.args[3], "a")

    async def test_plain_address_clears_old_pending(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from handlers import client
        m = MagicMock()
        m.text = "Ленина 5"
        st = FakeState({"pending_b": "старое"})
        with patch.object(client, "_address_input", new=AsyncMock()) as ai:
            await client.msg_point_a(m, st)
        self.assertIsNone(st.data["pending_b"])
        self.assertEqual(ai.await_args.args[2], "Ленина 5")

    async def test_after_a_confirmed_destination_is_searched_not_asked(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from handlers import client
        msg = MagicMock()
        msg.answer = AsyncMock()
        st = FakeState({"pending_b": "Мира 7"})
        with patch.object(client, "_address_input", new=AsyncMock()) as ai:
            await client._address_done(msg, st, "a", "Ленина, 5, Москва", "Район", False, True, MagicMock(),
                                       lat=55.7, lon=37.6)
        self.assertIsNone(st.data["pending_b"])
        self.assertEqual(ai.await_args.args[2:4], ("Мира 7", "b"))
        msg.answer.assert_not_awaited()  # вопрос «Куда поедем?» не задаём второй раз

    async def test_without_pending_asks_destination(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from handlers import client
        msg = MagicMock()
        msg.answer = AsyncMock()
        st = FakeState()
        with patch.object(client, "_address_input", new=AsyncMock()) as ai:
            await client._address_done(msg, st, "a", "Ленина, 5, Москва", "Район", False, True, MagicMock(),
                                       lat=55.7, lon=37.6)
        ai.assert_not_awaited()
        msg.answer.assert_awaited()
