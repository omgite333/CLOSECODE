
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