import asyncio
import sqlite3
from pathlib import Path


class Database:
    def __init__(self, path: Path, logger):
        self.path = str(path)
        self.logger = logger
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _init(self):
        conn = sqlite3.connect(self.path)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS vps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                container_id TEXT,
                image TEXT,
                owner_id INTEGER,
                status TEXT,
                cpu REAL,
                mem_mb INTEGER,
                created_at TEXT
            )
            """
        )
        conn.commit()
        self._ensure_expiry_column(conn)
        conn.close()

    def _ensure_expiry_column(self, conn):
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(vps)")
        columns = [row[1] for row in cur.fetchall()]
        if "expires_at" not in columns:
            cur.execute("ALTER TABLE vps ADD COLUMN expires_at TEXT")
            conn.commit()

    async def run(self, fn, *args, **kwargs):
        return await asyncio.to_thread(self._run_sync, fn, *args, **kwargs)

    def _run_sync(self, fn, *args, **kwargs):
        conn = sqlite3.connect(self.path)
        try:
            res = fn(conn, *args, **kwargs)
            conn.commit()
            return res
        except Exception:
            self.logger.exception("DB operation failed")
            raise
        finally:
            conn.close()
