"""Per-user persistent config, stored at ~/.closecode/config.json.

Holds the OpenRouter API key and the default model so both survive across
directories, sessions, and reinstalls — unlike the old approach of saving
the key into the current directory's .env (which asked again every time
you launched from a different folder).

The location can be overridden with the CLOSECODE_CONFIG_DIR env var
(mainly so tests can point it at a temp dir). The file is created with
0600 permissions where the OS supports it, since it holds a secret.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_DIR_ENV = "CLOSECODE_CONFIG_DIR"
CONFIG_FILENAME = "config.json"

KEY_API = "openrouter_api_key"
KEY_MODEL = "model"


def config_dir() -> Path:
    """User-level config directory, created on first use."""
    override = os.environ.get(CONFIG_DIR_ENV, "").strip()
    base = Path(override).expanduser() if override else Path.home() / ".closecode"
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_path() -> Path:
    return config_dir() / CONFIG_FILENAME


def load_config() -> dict:
    """Read the config file; {} when missing, corrupt, or not an object."""
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config_value(key: str, value: str) -> Path:
    """Merge one key into the config file and return its path."""
    data = load_config()
    data[key] = value
    path = config_path()
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows / filesystems without POSIX perms — best effort
    return path
