"""
modes.py

Two modes, toggled at runtime via /plan and /build:

- build (default): full tool access — read, write, edit, run commands, git.
- plan: read/explore-only. Mutating tools are removed from what the model
  can even see (not just told not to use), so a plan-mode "mistake" can't
  actually touch the filesystem or git state. The model is told to respond
  with a concrete plan instead of pretending to have made changes.

Filtering by name/keyword is a heuristic, not a formal guarantee — good
enough for local tools (exact names) and MCP git tools (keyword match on
mutating verbs), but if you add a new tool later, check it's classified
correctly here rather than assuming.
"""

PLAN_MODE_BLOCKED_EXACT = {"bash", "write_file", "edit_file"}
PLAN_MODE_BLOCKED_KEYWORDS = [
    "commit", "push", "checkout", "reset", "merge", "rebase",
    "add", "rm", "delete", "remove", "stash", "write", "create_branch",
]


def filter_tools_for_mode(tools: list, mode: str) -> list:
    if mode == "build":
        return list(tools)

    kept = []
    for t in tools:
        name = t.name.lower()
        if name in PLAN_MODE_BLOCKED_EXACT:
            continue
        if any(keyword in name for keyword in PLAN_MODE_BLOCKED_KEYWORDS):
            continue
        kept.append(t)
    return kept


def mode_system_note(mode: str) -> str:
    if mode == "plan":
        return (
            "\n\nYou are currently in PLAN MODE. Tools that write files, edit files, "
            "run shell commands, or change git state are not available to you right now. "
            "Explore and read what you need, then respond with a clear, concrete, "
            "step-by-step plan of the changes you would make. Do not claim to have made "
            "changes you did not actually make — you can't, in this mode."
        )
    return (
        "\n\nYou are currently in BUILD MODE. You have full access to read, write, edit, "
        "run commands, and make git changes as needed to complete the task."
    )