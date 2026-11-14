"""Скорость сайта (аудит 14.11.2026, замеры с живого сайта на Amvera):
- главная отвечала ~850 мс, хотя код собирает её за 5 мс: каждый запрос к базе открывал новое соединение SQLite
  (поток + открытие файла на сетевом диске хостинга);
- ответы не сжимались (HTML 17,9 КБ как есть);
- шрифты и картинки без срока кэша — браузер перепроверял их при каждом заходе (1,4–2 с на мобильной сети);
- персонаж 169 КБ PNG при показе ~180 px.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_speed_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import aiosqlite  # noqa: E402

import db  # noqa: E402
import webserver  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        app = webserver.make_app()
        app["bot"] = None
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def get(self, path, **h):
        return await self.client.get(path, headers={"Accept-Encoding": "gzip", **h}, auto_decompress=False)


class Compression(Base):
    async def test_pages_and_css_are_gzipped(self):
        for path in ("/", "/request", "/brand.css", "/chat/login"):
            r = await self.get(path)
            self.assertEqual(r.headers.get("Content-Encoding"), "gzip", path)

    async def test_already_compressed_files_are_not_gzipped_again(self):
        for path in ("/fonts/ptsans-bold.woff2", "/images/hero-mascot.webp"):
            r = await self.get(path)
            self.assertEqual(r.status, 200, path)
            self.assertIsNone(r.headers.get("Content-Encoding"), path)

    async def test_no_gzip_when_browser_does_not_ask(self):
        r = await self.client.get("/", headers={"Accept-Encoding": "identity"}, auto_decompress=False)
        self.assertIsNone(r.headers.get("Content-Encoding"))


class Caching(Base):
    async def test_fonts_and_images_are_cached_for_a_month(self):
        for path in ("/fonts/ptsans-regular.woff2", "/images/hero-mascot.png", "/images/hero-mascot.webp",
                     "/icons/favicon-32.png"):
            cc = (await self.get(path)).headers.get("Cache-Control", "")
            self.assertIn("max-age=2592000", cc, path)

    async def test_css_is_used_from_cache_and_refreshed_in_background(self):
        cc = (await self.get("/brand.css")).headers.get("Cache-Control", "")
        self.assertIn("stale-while-revalidate", cc)
        self.assertNotIn("no-cache", cc)

    async def test_pages_stay_fresh(self):
        for path in ("/", "/request"):
            self.assertIn("no-cache", (await self.get(path)).headers.get("Cache-Control", ""), path)

    async def test_server_timing_shows_server_time(self):
        st = (await self.get("/")).headers.get("Server-Timing", "")
        self.assertRegex(st, r"^app;dur=\d+(\.\d+)?$")


class Mascot(Base):
    async def test_home_uses_small_webp_with_png_fallback(self):
        t = await (await self.client.get("/")).text()
        self.assertIn('<source type="image/webp" srcset="/images/hero-mascot.webp">', t)
        self.assertIn('src="/images/hero-mascot.png"', t)                  # старые браузеры
        size = (ROOT / "webapp" / "images" / "hero-mascot.webp").stat().st_size
        self.assertLess(size, 40_000)


class DbConnection(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await db.init_db()

    async def test_reads_and_writes_reuse_one_connection(self):
        real = aiosqlite.connect
        opened = []

        def counting(*a, **kw):
            opened.append(1)
            return real(*a, **kw)
        await db.close_shared()
        with mock.patch.object(aiosqlite, "connect", side_effect=counting):
            for _ in range(20):
                await db.fetch_one("SELECT 1 AS x")
                await db.fetch_all("SELECT 1 AS x")
            await db.execute("CREATE TABLE IF NOT EXISTS speed_probe (v INTEGER)")
            await db.execute("INSERT INTO speed_probe (v) VALUES (1)")
        self.assertEqual(len(opened), 1)

    async def test_transaction_still_atomic_next_to_shared_connection(self):
        await db.execute("CREATE TABLE IF NOT EXISTS speed_tx (v INTEGER)")
        await db.execute("DELETE FROM speed_tx")
        with self.assertRaises(RuntimeError):
            async with db.transaction() as conn:
                await conn.execute("INSERT INTO speed_tx (v) VALUES (1)")
                raise RuntimeError("стоп")
        self.assertEqual((await db.fetch_one("SELECT COUNT(*) AS n FROM speed_tx"))["n"], 0)
        async with db.transaction() as conn:
            await conn.execute("INSERT INTO speed_tx (v) VALUES (2)")
        self.assertEqual((await db.fetch_one("SELECT COUNT(*) AS n FROM speed_tx"))["n"], 1)   # общее соединение видит

    async def test_new_database_file_is_picked_up(self):
        await db.fetch_one("SELECT 1")
        for s in ("", "-wal", "-shm"):
            try:
                os.remove(os.environ["DB_PATH"] + s)
            except FileNotFoundError:
                pass
        await db.init_db()                                                   # файл базы создан заново
        self.assertIsNotNone(await db.fetch_one("SELECT COUNT(*) AS n FROM services"))


if __name__ == "__main__":
    unittest.main()
