
import os
from typing import Optional

import requests
from langchain_core.tools import tool

from harness import Harness

_harness: Optional[Harness] = None


def bind_harness(harness: Harness) -> None:
    """Call this once at startup before the graph runs any tool calls."""
    global _harness
    _harness = harness


def _require_harness() -> Harness:
    if _harness is None:
        raise RuntimeError("Harness not bound. Call bind_harness() before running the agent.")
    return _harness


@tool
def bash(command: str, timeout: int = 30) -> str:
    """Run a shell command in the sandboxed working directory and return its combined stdout/stderr.
    timeout: max seconds to wait (default 30). Increase this for slow operations like
    package installs or builds (e.g. timeout=120 for a two-minute cap) — the units are
    SECONDS, not milliseconds. Capped at 300s (5 minutes) regardless of what's requested."""
    safe_timeout = max(1, min(int(timeout), 300))
    return _require_harness().run_bash(command, timeout=safe_timeout)


@tool
def read_file(path: str) -> str:
    """Read a text file's contents. Path is relative to the agent's working directory."""
    return _require_harness().read_file(path)


@tool
def write_file(path: str, content: str) -> str:
    """Write text content to a file, creating parent directories if needed. Path is relative to the working directory."""
    return _require_harness().write_file(path, content)


@tool
def list_dir(path: str = ".") -> str:
    """List files and subdirectories at a given path (default: the working directory root). Directories are marked with a trailing '/'."""
    return _require_harness().list_dir(path)


@tool
def edit_file(path: str, old_text: str, new_text: str) -> str:
    """Replace one exact occurrence of old_text with new_text in a file. Use this instead of write_file for small changes — it's cheaper and fails safely if old_text isn't found or isn't unique, rather than risking an overwrite of the wrong content."""
    return _require_harness().edit_file(path, old_text, new_text)


@tool
def run_tests(command: str = "pytest") -> str:
    """Run the project's test suite (default command: 'pytest') and report PASSED/FAILED with output. Use this to verify a change actually works, not just that it was written."""
    return _require_harness().run_tests(command)


_TAVILY_API_URL = "https://api.tavily.com/search"


@tool
def tavily_search(query: str, max_results: int = 5) -> str:
    """Search the web using Tavily and return the top results (title, url, content snippet, relevance score). Handy for checking current API docs, finding library versions, resolving error messages, or verifying facts that changed after your training cutoff. Requires a TAVILY_API_KEY in .env."""
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return "Error: TAVILY_API_KEY is not set. Add it to the .env file and restart."
    safe_max = max(1, min(int(max_results), 10))
    try:
        resp = requests.post(
            _TAVILY_API_URL,
            json={"api_key": api_key, "query": query, "max_results": safe_max, "search_depth": "basic"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Tavily search failed: {e}"

    results = data.get("results") or []
    if not results:
        return f"No results found for: {query}"
    lines = [f"Query: {query}\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        url = r.get("url", "")
        content = (r.get("content") or "").strip()
        lines.append(f"{i}. {title}\n   URL: {url}\n   {content}")
    return "\n".join(lines)


# Local (non-MCP) tools. main.py combines this with any MCP-provided tools
# (e.g. git) before binding to the model.
LOCAL_TOOLS = [bash, read_file, write_file, list_dir, edit_file, run_tests, tavily_search]

# Tools considered safe in "plan" mode: read-only, no filesystem/shell
# mutation. bash is excluded entirely even though some commands are
# harmless (e.g. `ls`) — there's no reliable way to tell a read-only shell
# command from a destructive one without actually parsing it, so plan mode
# blocks bash outright rather than trying to guess.
_PLAN_SAFE_LOCAL_NAMES = {"read_file", "list_dir", "tavily_search"}

# Heuristic for filtering MCP tools (e.g. git) in plan mode: block anything
# whose name suggests it mutates state. This is a name-based guess, not a
# guarantee — if you add other MCP servers, sanity-check their tool names
# fall into one of these buckets as expected.
_MUTATING_KEYWORDS = ("commit", "push", "reset", "checkout", "branch", "merge", "add", "rm", "stash", "revert", "rebase", "delete", "write", "create")


def filter_tools_for_mode(tools: list, mode: str) -> list:
    """mode == 'build': every tool is available.
    mode == 'plan': only read-only tools are available, so the agent can
    explore and reason about a task but literally cannot call anything
    that writes to disk, runs arbitrary shell, or mutates git state —
    enforced by never binding those tools to the model at all, which is a
    stronger guarantee than trusting the model to just not call them."""
    if mode == "build":
        return tools

    filtered = []
    for t in tools:
        name = getattr(t, "name", "")
        if name in _PLAN_SAFE_LOCAL_NAMES:
            filtered.append(t)
        elif name in {"bash", "write_file", "edit_file", "run_tests"}:
            continue
        elif not any(keyword in name.lower() for keyword in _MUTATING_KEYWORDS):
            # Likely an MCP tool (e.g. git status/diff/log) that doesn't
            # match a known-mutating keyword — allow it through.
            filtered.append(t)
    return filtered