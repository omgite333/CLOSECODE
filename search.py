"""Code search tools: glob (find files by pattern) and grep (search contents).

Read-only, so both are available in plan mode too — unlike bash, which plan
mode blocks entirely. Skips dependency/build directories so results stay
relevant and fast.
"""

import fnmatch
import os
import re
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

_root: Optional[Path] = None

# Directories never descended into during search.
SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".cache",
    "dist",
    "build",
    ".idea",
    ".vscode",
    "target",
}

MAX_RESULTS = 50
MAX_LINE_LEN = 160


def bind_search_root(path: str) -> None:
    """Call once at startup — searches are confined to this root."""
    global _root
    _root = Path(path).resolve()


def _require_root() -> Path:
    if _root is None:
        raise RuntimeError("Search root not bound. Call bind_search_root() before running the agent.")
    _root.mkdir(parents=True, exist_ok=True)
    return _root


def _walk_files(base: Path):
    """Yield files under base, pruning skipped directories."""
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            yield Path(dirpath) / name


@tool
def glob(pattern: str, path: str = ".") -> str:
    """Find files matching a glob pattern (e.g. "**/*.py", "src/*.ts").
    Searches under path (default: working directory root), skipping
    dependency and build directories. Prefer this over bash find/ls."""
    root = _require_root()
    base = (root / path).resolve()
    if root not in base.parents and base != root:
        return f"Path '{path}' escapes the working directory."
    if not base.is_dir():
        return f"Not a directory: {path}"
    matches = []
    for f in _walk_files(base):
        rel = f.relative_to(base).as_posix()
        if fnmatch.fnmatchcase(rel, pattern) or fnmatch.fnmatchcase(f.name, pattern):
            matches.append(rel)
            if len(matches) >= MAX_RESULTS:
                break
    matches.sort()
    if not matches:
        return f"No files match '{pattern}' under {path}."
    suffix = f"\n…truncated at {MAX_RESULTS}, narrow the pattern." if len(matches) >= MAX_RESULTS else ""
    return "\n".join(matches) + suffix


@tool
def grep(pattern: str, path: str = ".", include: str = "*", max_results: int = 50) -> str:
    """Search file contents with a regex pattern. path: where to search
    (default: working directory root). include: file glob to restrict to
    (e.g. "*.py"). Returns file:line: match, newest relevant first —
    prefer this over bash grep/rg, and it's available in plan mode."""
    root = _require_root()
    base = (root / path).resolve()
    if root not in base.parents and base != root:
        return f"Path '{path}' escapes the working directory."
    if not base.is_dir():
        return f"Not a directory: {path}"
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"Invalid regex: {e}"
    safe_max = max(1, min(int(max_results), 200))
    hits = []
    for f in _walk_files(base):
        if not (fnmatch.fnmatchcase(f.relative_to(base).as_posix(), include)
                or fnmatch.fnmatchcase(f.name, include)):
            continue
        try:
            text = f.read_text(errors="strict")
        except Exception:
            continue  # binary or unreadable — skip
        if "\x00" in text[:8192]:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                rel = f.relative_to(root).as_posix()
                line = line.strip()
                if len(line) > MAX_LINE_LEN:
                    line = line[:MAX_LINE_LEN] + "…"
                hits.append(f"{rel}:{i}: {line}")
                if len(hits) >= safe_max:
                    break
        if len(hits) >= safe_max:
            break
    if not hits:
        return f"No matches for '{pattern}' under {path}."
    suffix = f"\n…truncated at {safe_max}, narrow the pattern." if len(hits) >= safe_max else ""
    return "\n".join(hits) + suffix


SEARCH_TOOLS = [glob, grep]
