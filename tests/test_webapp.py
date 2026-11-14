"""Мини-приложение с картой: разбор ответа и веб-сервер (без сети)."""
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")

import db  # noqa: E402
import geo  # noqa: E402
import webserver  # noqa: E402


class ParsePick(unittest.TestCase):
    def ok(self, **kw):
        base = {"lat": 55.9, "lon": 37.3, "label": "Железнодорожная улица, 33, Сходня"}
        base.update(kw)
        return geo.parse_pick(json.dumps(base))

    def test_good(self):
        p = self.ok(flat="238")
        self.assertEqual(p["label"], "Железнодорожная улица, 33, Сходня, кв. 238")
        self.assertAlmostEqual(p["lat"], 55.9)

    def test_no_flat(self):
        self.assertEqual(self.ok()["label"], "Железнодорожная улица, 33, Сходня")

    def test_outside_region(self):
        self.assertIsNone(self.ok(lat=59.9, lon=30.3))
        self.assertIsNone(self.ok(lat=0, lon=0))

    def test_bad_label(self):
        for lb in ("", "ывфвф 5", "x", "говно 5", "а" * 400):
            self.assertIsNone(self.ok(label=lb), lb)

    def test_bad_types(self):
        for raw in ("", "not json", "[]", "null", json.dumps({"lat": "x", "lon": 1, "label": "ул Ленина 5"}),
                    json.dumps({"lat": 55.9, "label": "ул Ленина 5"}),
                    json.dumps({"lat": float("nan"), "lon": 37, "label": "ул Ленина 5"})):
            self.assertIsNone(geo.parse_pick(raw), raw)

    def test_flat_sanitised(self):
        self.assertEqual(self.ok(flat="<b>5</b>")["label"], "Железнодорожная улица, 33, Сходня")
        self.assertTrue(self.ok(flat="12А")["label"].endswith("кв. 12А"))
        self.assertTrue(self.ok(flat="1" * 30)["label"].endswith("Сходня"))

    def test_outside_by_coords(self):
        self.assertTrue(geo.outside_by_coords(55.97, 37.2))   # Зеленоград
        self.assertFalse(geo.outside_by_coords(55.7558, 37.6173))  # центр


class Server(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer

        self.db_dir = tempfile.TemporaryDirectory()
        self.db_config = mock.patch.object(
            db, "config", replace(db.config, db_path=str(Path(self.db_dir.name) / "health.db"))
        )
        self.db_config.start()
        self.client = TestClient(TestServer(webserver.make_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.db_config.stop()
        self.db_dir.cleanup()

    async def test_index(self):
        r = await self.client.get("/map")
        self.assertEqual(r.status, 200)
        self.assertIn("text/html", r.headers["Content-Type"])
        self.assertIn("telegram-web-app.js", await r.text())

    async def test_vendor_and_health(self):
        self.assertEqual((await self.client.get("/vendor/leaflet.js")).status, 200)
        health = await self.client.get("/healthz")
        self.assertEqual(health.status, 200)
        self.assertEqual(await health.text(), "ok")

    async def test_readiness_checks_database(self):
        response = await self.client.get("/readyz")
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.text(), "ok")

    async def test_readiness_failure_is_generic(self):
        bad_path = Path(self.db_dir.name) / "missing" / "health.db"
        with mock.patch.object(db, "config", replace(db.config, db_path=str(bad_path))):
            response = await self.client.get("/readyz")
        self.assertEqual(response.status, 503)
        self.assertEqual(await response.text(), "not ready")
        self.assertNotIn(str(bad_path), await response.text())

    async def test_liveness_is_independent_of_database(self):
        bad_path = Path(self.db_dir.name) / "missing" / "health.db"
        with mock.patch.object(db, "config", replace(db.config, db_path=str(bad_path))):
            health = await self.client.get("/healthz")
            readiness = await self.client.get("/readyz")
        self.assertEqual(health.status, 200)
        self.assertEqual(await health.text(), "ok")
        self.assertEqual(readiness.status, 503)

    async def test_no_path_traversal(self):
        r = await self.client.get("/vendor/../../config.py")
        self.assertIn(r.status, (403, 404))
        r = await self.client.get("/vendor/%2e%2e/%2e%2e/config.py")
        self.assertIn(r.status, (403, 404))

    async def test_no_cache_on_index(self):
        r = await self.client.get("/map")
        self.assertIn("no-cache", r.headers.get("Cache-Control", ""))


if __name__ == "__main__":
    unittest.main()
