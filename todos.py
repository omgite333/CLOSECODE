"""Todo tracking for the agent (Claude Code-style TodoWrite/TodoRead).

Todos live in memory, scoped to the current session — main.py clears the
store whenever the session changes (/clear, /resume, new session). The
pattern mirrors tools.py: a module-global store bound once at startup via
bind_todo_store().
"""

from typing import Optional

from langchain_core.tools import tool

_store: Optional["TodoStore"] = None

VALID_STATUSES = ("pending", "in_progress", "completed")


class TodoStore:
    def __init__(self):
        self.items: list[dict] = []

    def set(self, items: list[dict]) -> list[dict]:
        """Replace the whole list. Sanitizes entries: drops empties,
        coerces bad statuses to pending, and enforces exactly one
        in_progress at a time (extras are demoted to pending)."""
        cleaned = []
        for it in items:
            if not isinstance(it, dict):
                continue
            content = str(it.get("content", "")).strip()
            if not content:
                continue
            status = it.get("status", "pending")
            if status not in VALID_STATUSES:
                status = "pending"
            cleaned.append(
                {
                    "content": content[:200],
                    "status": status,
                    "activeForm": str(it.get("activeForm", ""))[:200],
                }
            )
        seen_active = False
        for it in cleaned:
            if it["status"] == "in_progress":
                if seen_active:
                    it["status"] = "pending"
                seen_active = True
        self.items = cleaned
        return self.items

    def get(self) -> list[dict]:
        return list(self.items)

    def clear(self) -> None:
        self.items = []


def bind_todo_store(store: TodoStore) -> None:
    """Call once at startup before the graph runs any tool calls."""
    global _store
    _store = store


def _require_store() -> TodoStore:
    if _store is None:
        raise RuntimeError("TodoStore not bound. Call bind_todo_store() before running the agent.")
    return _store


def format_todos(items: list[dict]) -> str:
    """One-line-per-todo text form, also used for tool results."""
    if not items:
        return "(no todos)"
    icons = {"pending": "○", "in_progress": "◐", "completed": "✓"}
    return "\n".join(
        f"{icons.get(it['status'], '?')} {it['content']}" for it in items
    )


@tool
def todo_write(todos: list[dict]) -> str:
    """Create or update the agent's task list. Call with the FULL list every
    time (it replaces the previous list). Each item: {"content": "do X",
    "status": "pending"|"in_progress"|"completed", "activeForm": "Doing X"}.
    Use for any task with 3+ steps: write the plan first, keep exactly one
    item in_progress, flip each to completed as you finish it, and re-write
    the list when the plan changes. This list is how the user sees progress."""
    items = _require_store().set(todos if isinstance(todos, list) else [])
    return "Todo list updated:\n" + format_todos(items)


@tool
def todo_read() -> str:
    """Read the current task list with each item's status."""
    return format_todos(_require_store().get())


TODO_TOOLS = [todo_write, todo_read]
