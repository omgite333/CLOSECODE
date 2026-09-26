"""Everything in the Rich CLI that waits for the human: the permission dialog,
the API-key paste, the input prompt, and the Esc-to-interrupt listener.

Split out from the rendering half so a terminal that isn't a TTY (piped input,
CI) can fail here in one place instead of scattered across print helpers.
"""

import select
import sys
import threading

from getpass import getpass

from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text

from ui_theme import (
    BORDER_SUBTLE,
    ERROR,
    SUCCESS,
    TEXT,
    TEXT_MUTED,
    WARNING,
    console,
)

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
    body.append("△ ", style=f"bold {WARNING}")
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
