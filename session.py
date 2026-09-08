
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from langchain_core.messages import messages_from_dict, messages_to_dict

SESSIONS_DIR = Path("./sessions")
DB_PATH = SESSIONS_DIR / "sessions.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'build',
    summary TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    payload TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    UNIQUE (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);
"""


@dataclass
class SessionInfo:
    id: int
    name: str
    model: str
    mode: str
    summary: str
    created_at: float
    updated_at: float
    message_count: int


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def _migrate_from_json() -> None:
    """Fold any legacy session_*.json files into the DB once, then leave them."""
    with _connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        if count:
            return
    files = sorted(SESSIONS_DIR.glob("session_*.json"))
    for path in files:
        try:
            msgs = messages_from_dict(json.loads(path.read_text()))
        except Exception:
            continue
        if not msgs:
            continue
        mtime = path.stat().st_mtime
        with _connect() as conn:
            cur = conn.execute(
                "INSERT INTO sessions (name, model, mode, summary, created_at, updated_at, message_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("", "", "build", "", mtime, mtime, len(msgs)),
            )
            sid = cur.lastrowid
            for seq, m in enumerate(msgs):
                conn.execute(
                    "INSERT INTO messages (session_id, seq, payload) VALUES (?, ?, ?)",
                    (sid, seq, json.dumps(messages_to_dict([m])[0])),
                )
            conn.commit()
        name = _derive_name(msgs)
        if name:
            conn.execute("UPDATE sessions SET name = ? WHERE id = ?", (name, sid))
            conn.commit()


def _derive_name(messages: list) -> str:
    """Short, human-readable name from the first user message."""
    for m in messages:
        if getattr(m, "type", "") == "human":
            text = str(getattr(m, "content", "") or "").strip()
            text = " ".join(text.split())
            return text[:48]
    return ""


def new_session(model: str = "", mode: str = "build") -> int:
    """Create a new session row and return its id."""
    now = time.time()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO sessions (model, mode, created_at, updated_at, message_count) VALUES (?, ?, ?, ?, 0)",
            (model, mode, now, now),
        )
        conn.commit()
        return int(cur.lastrowid)


def save(session_id: int, messages: list, model: str = "", mode: str = "build") -> None:
    """Replace a session's stored messages and refresh its metadata."""
    payloads = [json.dumps(d) for d in messages_to_dict(messages)]
    now = time.time()
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET model = ?, mode = ?, updated_at = ?, message_count = ? WHERE id = ?",
            (model, mode, now, len(payloads), session_id),
        )
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.executemany(
            "INSERT INTO messages (session_id, seq, payload) VALUES (?, ?, ?)",
            [(session_id, seq, p) for seq, p in enumerate(payloads)],
        )
        row = conn.execute("SELECT name FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row and not row["name"]:
            name = _derive_name(messages)
            if name:
                conn.execute("UPDATE sessions SET name = ? WHERE id = ?", (name, session_id))
        conn.commit()


def load(session_id: int) -> list:
    """Rehydrate a session's messages in order. Skips any corrupt rows."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT payload FROM messages WHERE session_id = ? ORDER BY seq", (session_id,)
        ).fetchall()
    payloads = []
    for row in rows:
        try:
            payloads.append(json.loads(row["payload"]))
        except Exception:
            continue
    return messages_from_dict(payloads)


def get_session(session_id: int) -> Optional[SessionInfo]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        return None
    return SessionInfo(**{k: row[k] for k in row.keys()})


def list_sessions() -> list[SessionInfo]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
    return [SessionInfo(**{k: row[k] for k in row.keys()}) for row in rows]


def latest_session_id() -> Optional[int]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM sessions WHERE message_count > 0 ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    return int(row["id"]) if row else None


def delete_session(session_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        return cur.rowcount > 0


def rename_session(session_id: int, name: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE sessions SET name = ? WHERE id = ?", (name.strip()[:80], session_id)
        )
        conn.commit()
        return cur.rowcount > 0


_init()
_migrate_from_json()