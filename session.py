"""
session.py

Saves conversation history to disk after every turn, using LangChain's own
messages_to_dict / messages_from_dict so message types (System/Human/AI/Tool)
round-trip correctly — hand-rolling this with raw json.dumps would lose the
type information and break tool_calls on reload.

Layout: ./sessions/session_<unix-timestamp-when-started>.json
"""

import json
import time
from pathlib import Path
from typing import Optional

from langchain_core.messages import messages_from_dict, messages_to_dict

SESSIONS_DIR = Path("./sessions")


def new_session_path() -> Path:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return SESSIONS_DIR / f"session_{int(time.time())}.json"


def latest_session_path() -> Optional[Path]:
    if not SESSIONS_DIR.exists():
        return None
    files = sorted(SESSIONS_DIR.glob("session_*.json"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def list_sessions() -> list[Path]:
    if not SESSIONS_DIR.exists():
        return []
    return sorted(SESSIONS_DIR.glob("session_*.json"), key=lambda p: p.stat().st_mtime)


def save(path: Path, messages: list) -> None:
    path.write_text(json.dumps(messages_to_dict(messages), indent=2))


def load(path: Path) -> list:
    data = json.loads(path.read_text())
    return messages_from_dict(data)