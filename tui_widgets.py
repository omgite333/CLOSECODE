"""Everything the Textual frontend puts on screen: the conversation blocks,
the multiline input, the modal dialogs, and the worker-thread bridge that
drives the permission prompt.

One widget per message kind, so CloseCodeApp's handlers are a straight
mount() with no layout maths.
"""

import concurrent.futures
import time

from rich.text import Text

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Input,
    Label,
    ListItem,
    Markdown,
    Static,
    TextArea,
)

from tui_theme import _style_color


# ---------------------------------------------------------------------------
# conversation widgets
# ---------------------------------------------------------------------------

class NoticeBlock(Static):
    def __init__(self, text: str, style: str = "dim"):
        color = _style_color(style)
        super().__init__(Text(text, style=color))


class UserBlock(Static):
    def __init__(self, text: str):
        super().__init__()
        self._text = text

    def compose(self) -> ComposeResult:
        yield Label("you", classes="role-you")
        yield Label(self._text, classes="msg-body")


class AgentBlock(Static):
    """Assistant message; its Markdown body is updated as tokens stream."""

    def __init__(self, mode: str):
        super().__init__()
        self._mode = mode
        # Text set before compose() runs (mount defers child creation by a
        # tick, so rapid stream_start/update/stop bursts must not be lost).
        self._pending_text = ""

    def compose(self) -> ComposeResult:
        yield Label(self._mode, classes="role-agent")
        yield Markdown(self._pending_text, classes="msg-body")

    def update_text(self, text: str) -> None:
        self._pending_text = text
        try:
            self.query_one(Markdown).update(text)
        except Exception:
            pass  # not composed yet; compose() seeds from _pending_text


class ToolBlock(Static):
    """One tool call: header line, click to expand the result preview."""

    def __init__(self, name: str, args: dict):
        super().__init__()
        self._name = name
        self._t0 = time.monotonic()
        self._expanded = False
        args_str = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())
        if len(args_str) > 100:
            args_str = args_str[:100] + "…"
        self._args_str = f"({args_str})" if args_str else ""
        # finish() may run before compose() (mount defers child creation);
        # stash and apply on mount if needed.
        self._finished: tuple | None = None

    def compose(self) -> ComposeResult:
        yield Label("", id="thead", classes="tool-head")
        yield Static("", id="tbody", classes="tool-body")

    def on_mount(self) -> None:
        try:
            self.query_one("#thead", Label).update(
                Text.from_markup(f"[#56b6c2]→[/] {self._name}{self._args_str}")
            )
            self.query_one("#tbody", Static).display = False
        except Exception:
            pass
        if self._finished is not None:
            self._apply_finish(*self._finished)

    def finish(self, content: str) -> None:
        dur = time.monotonic() - self._t0
        self._finished = (content, dur)
        self._apply_finish(content, dur)

    def _apply_finish(self, content: str, dur: float) -> None:
        first = content.strip().split("\n")[0].lower() if content.strip() else ""
        ok = not first.startswith(("error", "traceback", "denied"))
        mark, color = ("✓", "#7fd88f") if ok else ("✗", "#e06c75")
        try:
            self.query_one("#thead", Label).update(
                Text.from_markup(f"[{color}]{mark}[/] {self._name} [#808080]· {dur:.1f}s[/]")
            )
            preview = "\n".join(content.strip().splitlines()[:8])
            if len(preview) > 600:
                preview = preview[:600] + "…"
            self.query_one("#tbody", Static).update(Text(preview, style="#808080"))
        except Exception:
            pass  # not composed yet; on_mount applies via _finished

    def on_click(self) -> None:
        self._expanded = not self._expanded
        try:
            self.query_one("#tbody", Static).display = self._expanded
        except Exception:
            pass


class TodoBlock(Static):
    def __init__(self, items: list):
        super().__init__()
        self._items = items

    def compose(self) -> ComposeResult:
        yield Label("todos", classes="block-title")
        icons = {
            "pending": ("○", "#808080"),
            "in_progress": ("◐", "#fab283"),
            "completed": ("✓", "#7fd88f"),
        }
        body = Text()
        for it in self._items:
            icon, color = icons.get(it.get("status"), ("?", "#808080"))
            body.append(f"{icon} ", style=color)
            content = it.get("content", "")
            if it.get("status") == "completed":
                body.append(content + "\n", style="strike #808080")
            else:
                body.append(content + "\n")
        yield Static(body, classes="inset")


class ModelsBlock(Static):
    def __init__(self, models: list, current: str, source: str, query: str | None):
        super().__init__()
        self._models = models
        self._current = current
        self._source = source
        self._query = query

    def compose(self) -> ComposeResult:
        yield Label("models", classes="block-title")
        body = Text()
        for i, (mid, note) in enumerate(self._models[:40], 1):
            mark = "●" if mid == self._current else " "
            style = "#fab283" if mid == self._current else "#eeeeee"
            body.append(f"{i:>3} {mark} ", style="#808080")
            body.append(mid, style=style)
            body.append(f"  {note}\n", style="#808080")
        total = len(self._models)
        footer = f"showing {min(40, total)} of {total}"
        if self._source == "cache":
            footer += " · from cache (24h)"
        elif self._source == "fallback":
            footer += " · offline shortlist"
        if self._query:
            footer += f" · matching '{self._query}'"
        body.append(footer + "\n", style="#808080")
        body.append("/model <number> to switch", style="#808080")
        yield Static(body, classes="inset")


class SessionsBlock(Static):
    def __init__(self, sessions: list):
        super().__init__()
        self._sessions = sessions

    def compose(self) -> ComposeResult:
        from datetime import datetime

        yield Label("sessions", classes="block-title")
        body = Text()
        if not self._sessions:
            body.append("No saved sessions yet.\n", style="#808080")
        for s in self._sessions:
            when = datetime.fromtimestamp(s.updated_at).strftime("%b %d %H:%M")
            body.append(f"#{s.id} ", style="#808080")
            body.append(f"{s.name or '(unnamed)'} ", style="#eeeeee")
            body.append(f"{s.mode or 'build'} · {s.message_count} msgs · {when}\n", style="#808080")
        body.append("/resume <id> to switch · /delete <id> to remove", style="#808080")
        yield Static(body, classes="inset")


COMMANDS = [
    ("/plan", "switch to read-only plan mode"),
    ("/build", "switch to build mode (all tools)"),
    ("/models", "list OpenRouter models"),
    ("/model", "list models / switch by number or id"),
    ("/key", "paste a new OpenRouter API key"),
    ("/sessions", "list saved sessions"),
    ("/resume", "resume a saved session"),
    ("/delete", "delete a saved session"),
    ("/usage", "show token usage this session"),
    ("/undo", "revert last file change(s): /undo [n]"),
    ("/clear", "clear conversation history"),
    ("/compact", "summarize history into fresh context"),
    ("/help", "show this help"),
]


class HelpBlock(Static):
    def compose(self) -> ComposeResult:
        yield Label("commands", classes="block-title")
        body = Text()
        for cmd, desc in COMMANDS:
            body.append(f"{cmd:<12}", style="#fab283")
            body.append(f"{desc}\n", style="#808080")
        body.append("esc — interrupt the agent mid-turn\n", style="#808080")
        body.append("ctrl+q — quit", style="#808080")
        yield Static(body, classes="inset")


class WelcomeBlock(Static):
    def __init__(self, model: str, mode: str, tool_names: list):
        super().__init__()
        self._model = model
        self._mode = mode
        self._tool_names = tool_names

    def compose(self) -> ComposeResult:
        yield Static("", classes="welcome-gap")
        info = Text()
        info.append(self._mode, style="#5c9cf5")
        info.append(" · ", style="#3c3c3c")
        info.append(self._model, style="#fab283")
        info.append(f" · {len(self._tool_names)} tools", style="#808080")
        yield Label(info, classes="welcome-line")
        hints = Text()
        hints.append("Type a task and hit Enter. ", style="#808080")
        hints.append("/help", style="#fab283")
        hints.append(" for commands · ", style="#808080")
        hints.append("esc", style="#fab283")
        hints.append(" interrupts · ", style="#808080")
        hints.append("ctrl+q", style="#fab283")
        hints.append(" quits", style="#808080")
        yield Label(hints, classes="welcome-line")
        tip = Text()
        tip.append("● Tip  ", style="#fab283")
        tip.append("Run ", style="#808080")
        tip.append("/model", style="#eeeeee")
        tip.append(" to list and switch models", style="#808080")
        yield Label(tip, classes="welcome-line")


# ---------------------------------------------------------------------------
# input
# ---------------------------------------------------------------------------

class CommandInput(TextArea):
    """Multiline input. Enter submits; ctrl+j inserts a newline; up/down
    walks history (when single-line); esc interrupts or closes suggestions.

    Overrides TextArea._on_key (not on_key) because TextArea consumes keys
    in its own private handler before they would bubble to on_key.
    """

    async def _on_key(self, event: events.Key) -> None:
        app = self.app
        key = event.key
        if key == "enter":
            event.prevent_default()
            event.stop()
            await app.input_enter()
            return
        if key == "escape":
            event.prevent_default()
            event.stop()
            await app.input_escape()
            return
        if key == "tab":
            if await app.input_tab():
                event.prevent_default()
                event.stop()
            return
        if key == "up":
            if await app.input_up():
                event.prevent_default()
                event.stop()
                return
        if key == "down":
            if await app.input_down():
                event.prevent_default()
                event.stop()
                return
        if key == "ctrl+j":
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        await super()._on_key(event)


# ---------------------------------------------------------------------------
# modals
# ---------------------------------------------------------------------------

class PermissionModal(ModalScreen):
    """opencode-style permission dialog. Dismisses with allow/always/deny."""

    BINDINGS = [
        ("1", "pick('allow')", "Allow"),
        ("2", "pick('always')", "Always allow"),
        ("3", "pick('deny')", "Don't allow"),
        ("escape", "pick('deny')", "Deny"),
    ]

    def __init__(self, question: str):
        super().__init__()
        self._question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="perm-box"):
            yield Label("Permission", classes="modal-title")
            yield Label(f'Allow agent to "{self._question}"?', classes="modal-q")
            with Horizontal(id="perm-btns"):
                yield Button("Allow  [1]", id="allow", variant="success")
                yield Button("Always allow  [2]", id="always", variant="warning")
                yield Button("Don't allow  [3]", id="deny", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss({"allow": "allow", "always": "always", "deny": "deny"}[event.button.id])

    def action_pick(self, choice: str) -> None:
        self.dismiss(choice)


class KeyModal(ModalScreen):
    """Hidden-input prompt for the OpenRouter API key. Dismisses with the
    key string, or "" on cancel."""

    def compose(self) -> ComposeResult:
        with Vertical(id="key-box"):
            yield Label("OpenRouter API key", classes="modal-title")
            yield Label("Paste your key (input hidden). Get one at openrouter.ai/settings/keys",
                        classes="modal-sub")
            yield Input(password=True, id="key-input", placeholder="sk-or-…")
            with Horizontal(id="key-btns"):
                yield Button("Save", id="save", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#key-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.dismiss(self.query_one("#key-input", Input).value.strip())
        else:
            self.dismiss("")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())


class YesNoModal(ModalScreen):
    """Dismisses with True/False."""

    def __init__(self, question: str):
        super().__init__()
        self._question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="yn-box"):
            yield Label(self._question, classes="modal-q")
            with Horizontal(id="yn-btns"):
                yield Button("Yes", id="yes", variant="success")
                yield Button("No", id="no", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class ConfirmBridge:
    """Sync callable for Harness: shows PermissionModal and blocks the
    calling (worker) thread on a Future until the user decides. The app's
    event loop stays responsive because only the worker thread blocks."""

    def __init__(self):
        self._app: "CloseCodeApp | None" = None

    def attach(self, app: "CloseCodeApp") -> None:
        self._app = app

    def __call__(self, question: str) -> str:
        app = self._app
        if app is None:
            return "deny"
        try:
            running = app.is_running
        except Exception:
            running = False
        if not running:
            return "deny"
        fut: concurrent.futures.Future = concurrent.futures.Future()
        app.call_from_thread(app.request_permission, question, fut)
        try:
            return fut.result(timeout=600)
        except Exception:
            return "deny"
