"""
Memnon SaaS — user database.

SQLite-backed store for user accounts and per-user pipeline config.
Database file: runtime/users.db (created automatically).
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "runtime" / "users.db"


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def cursor():
    conn = _conn()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    finally:
        conn.close()


def init():
    with cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT UNIQUE NOT NULL,
                name          TEXT,
                google_token  TEXT,          -- JSON credentials blob
                lane          TEXT DEFAULT 'professional',
                profession    TEXT,          -- for professional lane
                tradition     TEXT,          -- for reflect lane
                inbox_folder_id  TEXT,       -- Drive folder ID for audio input
                notes_folder_id  TEXT,       -- Drive folder ID for note output
                active        INTEGER DEFAULT 1,
                created_at    TEXT DEFAULT (datetime('now'))
            )
        """)


# ── CRUD ──────────────────────────────────────────────────────────────────────

def upsert_user(email: str, name: str, google_token: dict) -> int:
    with cursor() as cur:
        cur.execute("""
            INSERT INTO users (email, name, google_token)
            VALUES (?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                name         = excluded.name,
                google_token = excluded.google_token
        """, (email, name, json.dumps(google_token)))
        cur.execute("SELECT id FROM users WHERE email = ?", (email,))
        return cur.fetchone()["id"]


def get_user(email: str) -> dict | None:
    with cursor() as cur:
        cur.execute("SELECT * FROM users WHERE email = ?", (email,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def update_user(email: str, **fields):
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with cursor() as cur:
        cur.execute(
            f"UPDATE users SET {set_clause} WHERE email = ?",
            (*fields.values(), email),
        )


def all_active_users() -> list[dict]:
    with cursor() as cur:
        cur.execute("SELECT * FROM users WHERE active = 1")
        return [dict(r) for r in cur.fetchall()]


def user_token(email: str) -> dict | None:
    user = get_user(email)
    if user and user.get("google_token"):
        return json.loads(user["google_token"])
    return None


def save_token(email: str, token: dict):
    with cursor() as cur:
        cur.execute(
            "UPDATE users SET google_token = ? WHERE email = ?",
            (json.dumps(token), email),
        )
