"""The Rich CLI renderer: everything the app *prints*.

The interactive half (permission prompts, the input box, Esc-to-interrupt) is
in ui_prompts.py and the palette in ui_theme.py; this module is the read-only
output side plus the re-exports that keep `import ui` working unchanged for
render.py, main.py, and anything else that talks to the old flat namespace.
"""

from datetime import datetime

from rich.console import Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from ui_theme import (
    ACCENT,
    BORDER_SUBTLE,
    INFO,
    PRIMARY,
    SECONDARY,
    TEXT,
    TEXT_MUTED,
    WARNING,
    _banner_art,
    _NAMED_STYLES,
    _READ_KINDS,
    console,
)

# Re-exported so callers that reach for the interactive helpers through `ui`
# (main.py builds an EscListener and asks for API keys) keep working against
# the same objects that ui_prompts defines.
from ui_prompts import (  # noqa: F401
    EscListener,
    confirm,
    confirm_save_key,
    prompt_api_key,
    user_prompt,
)

# Current agent mode and model, remembered for labels and turn markers. Lives
# here rather than in ui_theme because the renderers below are what read it.
_context_mode = "build"
_context_model = ""


def set_context(mode: str, model_name: str) -> None:
    """Remember the current agent/mode and model for labels and markers."""
    global _context_mode, _context_model
    _context_mode = mode
    _context_model = model_name


def print_banner(model_name: str, sandbox_path: str, tool_names: list[str]) -> None:
    console.print()
    console.print(_banner_art("OGBOT"), justify="center")
    console.print()

    info = Text()
    info.append(f"model   {model_name}\n", style=TEXT_MUTED)
    info.append(f"workdir {sandbox_path}\n", style=TEXT_MUTED)
    info.append(f"tools   {', '.join(tool_names)}", style=TEXT_MUTED)
    console.print(info, justify="center")

    console.print()
    console.print(Text("type a task, or 'exit' to quit.", style=TEXT_MUTED), justify="center")
    console.print()
    console.print(Rule(style=BORDER_SUBTLE))
    console.print()


def print_user_message(content: str) -> None:
    console.print()
    console.print(Text("you", style=f"bold {SECONDARY}"))
    console.print(Text(content))


def _agent_header() -> Text:
    head = Text()
    head.append(_context_mode, style=f"bold {PRIMARY}")
    return head


def _agent_renderable(text_so_far: str):
    if text_so_far:
        body = Markdown(text_so_far, style=TEXT)
    else:
        body = Text("")
    return Group(_agent_header(), body)


def stream_start() -> Live:
    """Call when the first real content token of a response arrives.
    Returns a Live object — pass it to stream_update() for each subsequent
    chunk, and stream_stop() when the message is complete."""
    live = Live(_agent_renderable(""), console=console, refresh_per_second=12)
    live.start()
    return live


def stream_update(live: Live, text_so_far: str) -> None:
    live.update(_agent_renderable(text_so_far))


def stream_stop(live: Live) -> None:
    live.stop()
    console.print()


def print_thinking() -> None:
    line = Text()
    line.append("thinking… ", style=TEXT_MUTED)
    line.append("(esc to interrupt)", style=TEXT_MUTED)
    console.print(line)


def _direction(name: str) -> str:
    return "read" if any(k in name for k in _READ_KINDS) else "write"


def print_tool_call(name: str, args: dict) -> None:
    line = Text()
    if _direction(name) == "read":
        line.append("→ ", style=f"bold {INFO}")
    else:
        line.append("← ", style=f"bold {PRIMARY}")
    line.append(name, style=f"bold {TEXT}")
    args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    if len(args_str) > 140:
        args_str = args_str[:140] + "…"
    if args_str:
        line.append(f"({args_str})", style=TEXT_MUTED)
    console.print(line)


def print_tool_result(content: str) -> None:
    preview = content.strip().splitlines()[0] if content.strip() else ""
    if len(preview) > 120:
        preview = preview[:120] + "…"
    console.print(Text(preview, style=TEXT_MUTED))


def print_turn_complete(duration: float) -> None:
    console.print()
    marker = Text()
    marker.append("▣ ", style=TEXT_MUTED)
    marker.append(_context_mode, style=f"bold {PRIMARY}")
    marker.append(f" · {duration:.1f}s", style=TEXT_MUTED)
    console.print(marker)


def print_token_usage(summary: str) -> None:
    console.print(Text(summary, style=TEXT_MUTED))


def print_todos(items: list) -> None:
    """Render the agent's todo list (called after todo_write)."""
    if not items:
        return
    console.print()
    console.print(Text("todos", style=f"bold {ACCENT}"))
    icons = {"pending": "○", "in_progress": "◐", "completed": "✓"}
    for it in items:
        line = Text()
        line.append("  ", style=TEXT)
        line.append(f"{icons.get(it.get('status'), '?')} ", style=TEXT_MUTED)
        content = it.get("content", "")
        if it.get("status") == "completed":
            line.append(content, style=f"strike {TEXT_MUTED}")
        else:
            line.append(content, style=TEXT)
        console.print(line)


def print_notice(text: str, style: str = "dim") -> None:
    color = _NAMED_STYLES.get(style, style)
    console.print(f"[{color}]{text}[/{color}]", overflow="ellipsis")


def print_sessions(sessions: list) -> None:
    """Render saved sessions with their metadata. `sessions` items are
    session.SessionInfo dataclasses."""
    if not sessions:
        console.print(Text("No saved sessions yet.", style=TEXT_MUTED))
        return
    table = Table(title="sessions", title_justify="left",
                  border_style=BORDER_SUBTLE, pad_edge=False)
    table.add_column("#", justify="right", style=TEXT_MUTED, width=3)
    table.add_column("name", no_wrap=True, style=TEXT, max_width=42, overflow="ellipsis")
    table.add_column("mode", justify="center", style=ACCENT)
    table.add_column("model", style=TEXT_MUTED, max_width=24, overflow="ellipsis")
    table.add_column("msgs", justify="right", style=INFO)
    table.add_column("updated", style=TEXT_MUTED)
    for s in sessions:
        when = datetime.fromtimestamp(s.updated_at).strftime("%b %d %H:%M")
        table.add_row(
            str(s.id),
            s.name or "(unnamed)",
            s.mode or "build",
            s.model or "-",
            str(s.message_count),
            when,
        )
    console.print(table)
    console.print(Text("/resume <id> to switch · /delete <id> to remove",
                       style=TEXT_MUTED))


def print_help() -> None:
    text = (
        "/plan          switch to read-only plan mode (explore only, no writes/commits)\n"
        "/build         switch to build mode (all tools enabled)\n"
        "/models [q]   list OpenRouter models (● = current); --refresh updates\n"
        "/model <n|id>  switch model by list number or any OpenRouter model id\n"
        "/key           paste a new OpenRouter API key (optionally saved to .env)\n"
        "/sessions      list saved sessions (SQLite) with metadata\n"
        "/resume <id>   resume a saved conversation\n"
        "/delete <id>   delete a saved session\n"
        "/usage         show cumulative token usage this session\n"
        "/undo [n]      revert the last n file changes the agent made\n"
        "/clear         clear conversation history (session file untouched)\n"
        "/help          show this message\n"
        "esc            interrupt the agent mid-turn\n"
        "exit / quit    quit"
    )
    console.print(
        Panel(Text(text), title="commands", title_align="left",
              border_style=BORDER_SUBTLE)
    )


_MODELS_DISPLAY_LIMIT = 80


def print_models(models: list, current: str, source: str = "live", query: str = None) -> None:
    """Render a model list (from /models), starring the active model.

    The live OpenRouter list has hundreds of entries, so only the first
    _MODELS_DISPLAY_LIMIT rows are shown — the footer says how to narrow
    it. Numbering matches list position so `/model <number>` picks the
    row the user sees.
    """
    total = len(models)
    shown = models[:_MODELS_DISPLAY_LIMIT]
    table = Table(title="models", title_justify="left",
                  border_style=BORDER_SUBTLE, pad_edge=False)
    table.add_column("#", justify="right", style=TEXT_MUTED, width=4)
    table.add_column("", width=2)
    table.add_column("model id", style=TEXT, no_wrap=True, max_width=44, overflow="ellipsis")
    table.add_column("notes", style=TEXT_MUTED, max_width=40, overflow="ellipsis")
    for i, (mid, note) in enumerate(shown, 1):
        marker = Text("●", style=PRIMARY) if mid == current else Text(" ")
        table.add_row(str(i), marker, mid, note)
    console.print(table)

    footer = Text()
    if source == "cache":
        footer.append("from cache (24h) · ", style=TEXT_MUTED)
    elif source == "fallback":
        footer.append("offline — showing curated shortlist · ", style=WARNING)
    if query:
        footer.append(f"{total} match '{query}'", style=TEXT_MUTED)
    else:
        footer.append(f"showing {len(shown)} of {total}", style=TEXT_MUTED)
    console.print(footer)
    hints = Text("/model <number> to switch · /models <query> to filter · /models --refresh to update",
                 style=TEXT_MUTED)
    console.print(hints)
