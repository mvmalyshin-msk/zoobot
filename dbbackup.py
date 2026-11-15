"""Ежедневная копия базы рядом с ней (папка backups на томе): последние KEEP штук."""
import asyncio
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from config import config

log = logging.getLogger(__name__)
KEEP = 7


def _copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    s = sqlite3.connect(src)
    try:
        o = sqlite3.connect(dest)
        try:
            s.backup(o)
        finally:
            o.close()
    finally:
        s.close()


async def run_once(src: Path | None = None, dest_dir: Path | None = None, stamp: str | None = None,
                   keep: int = KEEP) -> Path | None:
    src = Path(src or config.db_path)
    if not src.exists():
        log.error("Копия базы: файла %s нет", src)
        return None
    dest_dir = Path(dest_dir or Path(os.path.dirname(os.path.abspath(src))) / "backups")
    out = dest_dir / f"auto-{stamp or datetime.now().strftime('%Y-%m-%d')}.db"
    try:
        await asyncio.to_thread(_copy, src, out)
        for old in sorted(dest_dir.glob("auto-*.db"))[:-keep]:
            old.unlink(missing_ok=True)
    except Exception:
        log.exception("Копия базы не создана")
        return None
    log.info("Копия базы: %s", out.name)
    return out
