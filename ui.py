
import select
import sys
import threading

from datetime import datetime
from getpass import getpass

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

import pyfiglet

try:
    import termios
    import tty
    _HAS_TERMIOS = True
except ImportError:  # pragma: no cover - Windows fallback
    _HAS_TERMIOS = False

try:
    import msvcrt
    _HAS_MSVCRT = True
except ImportError:
    _HAS_MSVCRT = False

console = Console()

# Simple, solid block wordmark for "OGBOT" — no fancy/decorative figlet
# fonts, just clean filled rectangles. Falls back to a plain bold pyfiglet
# render for any other banner text.
_BLOCK_GLYPHS = {
    "O": ["█████", "█   █", "█   █", "█   █", "█████"],
    "G": ["█████", "█    ", "█  ██", "█   █", "█████"],
    "B": ["████ ", "█   █", "████ ", "█   █", "████ "],
    "T": ["█████", "  █  ", "  █  ", "  █  ", "  █  "],
}

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
    """Simple, solid banner. If every character in `text` has a hand-drawn
    block glyph (currently just what's needed for "OGBOT"), render clean
    filled rectangles. Otherwise fall back to a plain pyfiglet font for
    arbitrary text."""
    if text and all(ch in _BLOCK_GLYPHS for ch in text.upper()):
        rows = ["" for _ in range(5)]
        for ch in text.upper():
            glyph = _BLOCK_GLYPHS[ch]
            for i in range(5):
                rows[i] += glyph[i] + " "
        lines = [row.rstrip() for row in rows]
    else:
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
    line = Text()
    line.append("thinking\u2026 ", style=TEXT_MUTED)
    line.append("(esc to interrupt)", style=TEXT_MUTED)
    console.print(line)


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
    console.print(Text("/resume <id> to switch \u00b7 /delete <id> to remove",
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


def prompt_api_key() -> str:
    """Ask the user to paste their OpenRouter API key without echoing it
    to the terminal. Returns the stripped key, or "" if nothing entered."""
    console.print()
    console.print(Text("Paste your OpenRouter API key (input is hidden).", style=f"bold {WARNING}"))
    console.print(Text("Get one at https://openrouter.ai/settings/keys", style=TEXT_MUTED))
    try:
        key = getpass("key: ")
    except Exception:
        # getpass can fail when stdin isn't a real TTY — fall back to a
        # visible prompt rather than crashing.
        key = console.input("key: ")
    return (key or "").strip()


def confirm_save_key() -> bool:
    """Ask whether the just-pasted key should persist into .env."""
    answer = console.input("Save this key to .env for next time? [y/N] ").strip().lower()
    return answer in ("y", "yes")


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


class EscListener:
    """Watches stdin for an Esc keypress on a background thread while a turn
    is streaming, without blocking the asyncio event loop.

    Terminal input is a blocking, thread-only affair (raw/cbreak mode via
    termios), so this runs on its own thread and hands control back to the
    event loop by calling `event.set()` through `loop.call_soon_threadsafe`.
    Safe to call `start()`/`stop()` even when stdin isn't a real TTY (e.g.
    piped input, some CI environments) — it just no-ops in that case.
    """

    def __init__(self, loop, event) -> None:
        self._loop = loop
        self._event = event
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not sys.stdin.isatty():
            return
        if not _HAS_TERMIOS and not _HAS_MSVCRT:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def _signal_esc(self) -> None:
        self._loop.call_soon_threadsafe(self._event.set)

    def _watch(self) -> None:
        if _HAS_TERMIOS:
            self._watch_termios()
        elif _HAS_MSVCRT:
            self._watch_msvcrt()

    def _watch_termios(self) -> None:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while not self._stop.is_set():
                ready, _, _ = select.select([sys.stdin], [], [], 0.1)
                if ready:
                    ch = sys.stdin.read(1)
                    if ch == "\x1b":
                        self._signal_esc()
                        return
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def _watch_msvcrt(self) -> None:  # pragma: no cover - Windows only
        while not self._stop.is_set():
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                if ch == b"\x1b":
                    self._signal_esc()
                    return
            else:
                self._stop.wait(0.1)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.3)
            self._thread = None


def confirm(question: str, details: dict | None = None) -> str:
    """Permission prompt styled like an opencode permission dialog.

    When `details` carries a diff (kind == "diff"), the colorized unified
    diff is printed first so the user approves the actual change, not a
    blind "write N chars" summary. Returns "allow", "always", or "deny".
    """
    console.print()
    if details and details.get("kind") == "diff":
        lines = details.get("lines", [])
        total = details.get("total_lines", len(lines))
        shown = "\n".join(lines)
        if total > len(lines):
            shown += f"\n… {total - len(lines)} more lines"
        title = f"diff · {details.get('path', '')} · {details.get('stat', '')}"
        if details.get("new_file"):
            title += " · new file"
        console.print(
            Panel(
                Syntax(shown or "(no changes)", "diff", theme="ansi_dark"),
                title=title, title_align="left",
                border_style=BORDER_SUBTLE, padding=(0, 1),
            )
        )
    body = Text()
    body.append("\u25b3 ", style=f"bold {WARNING}")
    body.append(f"Allow agent to {question}?", style=TEXT)
    console.print(Panel(body, title="permission", title_align="left",
                        border_style=BORDER_SUBTLE, padding=(0, 1)))
    opts = Text()
    opts.append("1", style=f"bold {SUCCESS}"); opts.append(") Allow    ")
    opts.append("2", style=f"bold {WARNING}"); opts.append(") Always Allow    ")
    opts.append("3", style=f"bold {ERROR}");   opts.append(") Don't Allow")
    console.print(opts)
    answer = console.input("[1/2/3] ").strip()
    if answer == "1":
        return "allow"
    if answer == "2":
        return "always"
    return "deny"


def user_prompt(mode: str) -> str:
    console.print()
    console.print(Rule(style=BORDER_SUBTLE))
    prompt = Text()
    prompt.append("> ", style=f"bold {TEXT}")
    prompt.append(mode, style=TEXT_MUTED)
    prompt.append("  ")
    return console.input(prompt).strip()