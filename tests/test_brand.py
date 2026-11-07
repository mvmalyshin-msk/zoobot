import asyncio
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_brand_test.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import webserver  # noqa: E402

LOOP = asyncio.new_event_loop()
NAME = "Зооконсьерж"
CREAM, CORAL = "#fdf6ec", "#fc6751"


def run(fn):
    async def go():
        await db.init_db()
        app = webserver.make_app()
        app["bot"] = object()
        async with TestClient(TestServer(app)) as c:
            return await fn(c)
    return LOOP.run_until_complete(go())


class BrandTest(unittest.TestCase):
    def test_manifest_names_the_app_like_the_bot(self):
        async def t(c):
            r = await c.get("/manifest.webmanifest")
            self.assertEqual(r.status, 200)
            m = json.loads(await r.text())
            self.assertEqual(m["name"], NAME)
            self.assertEqual(m["short_name"], NAME)
            self.assertEqual(m["display"], "standalone")
            self.assertEqual(m["background_color"].lower(), CREAM)
            self.assertEqual(m["theme_color"].lower(), CORAL)
            for ic in m["icons"]:
                self.assertEqual((await c.get(ic["src"])).status, 200, ic["src"])
            self.assertTrue(any(i["sizes"] == "512x512" for i in m["icons"]))
        run(t)

    def test_pages_carry_home_screen_icon_name_and_brand_css(self):
        async def t(c):
            for path in ("/chat", "/request"):
                html = await (await c.get(path)).text()
                self.assertIn('rel="apple-touch-icon"', html, path)
                self.assertIn(f'name="apple-mobile-web-app-title" content="{NAME}"', html, path)
                self.assertIn('rel="manifest"', html, path)
                self.assertIn('/brand.css', html, path)
                self.assertIn(f"<title>{NAME}", html, path)
        run(t)

    def test_brand_css_and_fonts(self):
        async def t(c):
            css = await (await c.get("/brand.css")).text()
            for token in ("#fdf6ec", "#fc6751", "#2eb2a5", "#3970d7", "#e6b13d", "PT Sans"):
                self.assertIn(token, css.lower() if token.startswith("#") else css)
            self.assertEqual((await c.get("/fonts/ptsans-bold.woff2")).status, 200)
            self.assertEqual((await c.get("/fonts/ptsans-regular.woff2")).status, 200)
            self.assertEqual((await c.get("/icons/apple-touch-icon.png")).status, 200)
        run(t)

    def test_no_external_resources(self):
        async def t(c):
            for path in ("/chat", "/request", "/brand.css"):
                body = await (await c.get(path)).text()
                self.assertNotIn("fonts.googleapis", body, path)
                self.assertNotIn("http://", body.replace("http://www.w3.org", ""), path)
        run(t)


if __name__ == "__main__":
    unittest.main()
