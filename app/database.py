from __future__ import annotations

import sqlite3
from contextlib import contextmanager
import os
from pathlib import Path
from typing import Iterator

from .time_utils import SQLITE_SHANGHAI_NOW


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.getenv("TEAM_CARD_DB_PATH", DATA_DIR / "cards.db"))


def dict_factory(cursor: sqlite3.Cursor, row: sqlite3.Row) -> dict:
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        shanghai_now = SQLITE_SHANGHAI_NOW
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                upload_batch TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'available'
                    CHECK (status IN ('available', 'reserved', 'redeemed')),
                card_id INTEGER,
                created_at TEXT NOT NULL DEFAULT ({shanghai_now}),
                reserved_at TEXT,
                redeemed_at TEXT,
                downloaded_at TEXT,
                FOREIGN KEY (card_id) REFERENCES cards(id)
            );

            CREATE INDEX IF NOT EXISTS idx_accounts_status
                ON accounts(status, id);
            CREATE INDEX IF NOT EXISTS idx_accounts_name
                ON accounts(name);
            CREATE INDEX IF NOT EXISTS idx_accounts_card
                ON accounts(card_id);

            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                account_count INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'unused'
                    CHECK (status IN ('unused', 'redeemed')),
                created_at TEXT NOT NULL DEFAULT ({shanghai_now}),
                claimed_at TEXT,
                redeemed_at TEXT,
                downloaded_at TEXT,
                download_count INTEGER NOT NULL DEFAULT 0,
                batch_code TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_cards_status
                ON cards(status, id);

            CREATE TABLE IF NOT EXISTS redemptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                format TEXT NOT NULL DEFAULT 'sub2api',
                account_count INTEGER NOT NULL,
                downloaded_at TEXT NOT NULL DEFAULT ({shanghai_now}),
                client_ip TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (card_id) REFERENCES cards(id)
            );
            """
        )
        ensure_column(conn, "cards", "claimed_at", "TEXT")
        ensure_column(conn, "cards", "batch_code", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "redemptions", "format", "TEXT NOT NULL DEFAULT 'sub2api'")


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
