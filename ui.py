"""
ui.py

OpenCode-style chat terminal UI.

Restyles the line-based streaming client to look like the opencode TUI:
a conversation thread where user and assistant messages appear inline
with labels, tool steps are marked with -> (read) / <- (write) arrows,
each finished turn closes with a completion marker, and input sits in a
divider-framed bar at the bottom. Colors are taken from the opencode
default theme (dark variant).

Like opencode, everything is left-aligned — only the boot header is
centered. There is no full-screen rendering here: output flows
line-by-line so the tool stream and the typewriter response stay
compatible with the astream_events loop in main.py.
"""

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

import pyfiglet

console = Console()

# ---- opencode default theme (dark variant) --------------------------------
BG = "#0a0a0a"
BG_PANEL = "#141414"
PRIMARY = "#fab283"  # peach  -> agent label / write arrows
SECONDARY = "#5c9cf5"  # blue   -> "you" label
ACCENT = "#9d7cd8"  # purple -> mode accents
ERROR = "#e06c75"
WARNING = "#f5a742"
SUCCESS = "#7fd88f"
INFO = "#56b6c2"  # cyan   -> read arrows
TEXT = "#eeeeee"
TEXT_MUTED = "#808080"
BORDER = "#484848"
BORDER_SUBTLE = "#3c3c3c"

# Tool names containing these treat as read-only (->), everything else is
# a write (<-). Mirrors opencode's arrow direction for tool calls.
_READ_KINDS = ("read", "list", "status", "diff", "log", "show", "grep", "search")

# Map the loose rich color names used by callers onto the opencode palette.
_NAMED_STYLES = {
    "dim": TEXT_MUTED,
    "yellow": WARNING,
    "red": ERROR,
    "green": SUCCESS,
    "cyan": INFO,
    "magenta": ACCENT,
    "blue": SECONDARY,
}

_context_mode = "build"
_context_model = ""


def set_context(mode: str, model_name: str) -> None:
    """Remember the current agent/mode and model for labels and markers."""
    global _context_mode, _context_model
    _context_mode = mode
    _context_model = model_name


def _banner_art(text: str, font: str = "standard") -> Text:
    art = pyfiglet.figlet_format(text, font=font)
    lines = art.rstrip("\n").split("\n")
    result = Text()
    for line in lines:
        result.append(line, style=f"bold {PRIMARY}")
        result.append("\n")
    return result


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
    console.print(Text("thinking\u2026", style=TEXT_MUTED))


def _direction(name: str) -> str:
    return "read" if any(k in name for k in _READ_KINDS) else "write"


def print_tool_call(name: str, args: dict) -> None:
    line = Text()
    if _direction(name) == "read":
        line.append("\u2192 ", style=f"bold {INFO}")
    else:
        line.append("\u2190 ", style=f"bold {PRIMARY}")
    line.append(name, style=f"bold {TEXT}")
    args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    if len(args_str) > 140:
        args_str = args_str[:140] + "\u2026"
    if args_str:
        line.append(f"({args_str})", style=TEXT_MUTED)
    console.print(line)


def print_tool_result(content: str) -> None:
    preview = content.strip().splitlines()[0] if content.strip() else ""
    if len(preview) > 120:
        preview = preview[:120] + "\u2026"
    console.print(Text(preview, style=TEXT_MUTED))


def print_turn_complete(duration: float) -> None:
    console.print()
    marker = Text()
    marker.append("\u25a3 ", style=TEXT_MUTED)
    marker.append(_context_mode, style=f"bold {PRIMARY}")
    marker.append(f" \u00b7 {duration:.1f}s", style=TEXT_MUTED)
    console.print(marker)


def print_token_usage(summary: str) -> None:
    console.print(Text(summary, style=TEXT_MUTED))


def print_notice(text: str, style: str = "dim") -> None:
    color = _NAMED_STYLES.get(style, style)
    console.print(f"[{color}]{text}[/{color}]", overflow="ellipsis")


def print_help() -> None:
    text = (
        "/plan          switch to read-only plan mode (explore only, no writes/commits)\n"
        "/build         switch to build mode (all tools enabled)\n"
        "/model <id>    switch the active model for this session\n"
        "/usage         show cumulative token usage this session\n"
        "/clear         clear conversation history (tools/session file untouched)\n"
        "/help          show this message\n"
        "exit / quit    quit"
    )
    console.print(
        Panel(Text(text), title="commands", title_align="left",
              border_style=BORDER_SUBTLE)
    )


def confirm(question: str) -> bool:
    """Permission prompt styled like an opencode permission dialog."""
    console.print()
    body = Text()
    body.append("\u25b3 ", style=f"bold {WARNING}")
    body.append(f"Allow agent to {question}?", style=TEXT)
    console.print(Panel(body, title="permission", title_align="left",
                        border_style=BORDER_SUBTLE, padding=(0, 1)))
    answer = console.input("[y/N] ").strip().lower()
    return answer in ("y", "yes")


def user_prompt(mode: str) -> str:
    console.print()
    console.print(Rule(style=BORDER_SUBTLE))
    prompt = Text()
    prompt.append("> ", style=f"bold {TEXT}")
    prompt.append(mode, style=TEXT_MUTED)
    prompt.append("  ")
    return console.input(prompt).strip()