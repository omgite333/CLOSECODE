"""Textual TUI frontend for CloseCode — an OpenCode-style full-screen UI.

Layout: header / scrollable conversation / slash-command suggestions /
status bar / multiline input.

The agent itself is untouched: user input funnels through
main.submit_text(), and agent output arrives via TuiRenderer, which posts
Textual messages that the app turns into widgets. Permission prompts from
the Harness arrive on worker threads and are shown as modal dialogs via
ConfirmBridge (the worker thread blocks on a Future until the user picks).
"""

import concurrent.futures
import re
import time

from rich.text import Text

from textual import events
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Input,
    Label,
    ListItem,
    ListView,
    Markdown,
    Static,
    TextArea,
)

from render import Renderer


# ---------------------------------------------------------------------------
# messages: renderer -> app
# ---------------------------------------------------------------------------

class StreamStartMsg(Message):
    def __init__(self, sid: int):
        super().__init__()
        self.sid = sid


class StreamUpdateMsg(Message):
    def __init__(self, sid: int, text: str):
        super().__init__()
        self.sid = sid
        self.text = text


class StreamStopMsg(Message):
    def __init__(self, sid: int):
        super().__init__()
        self.sid = sid


class ToolStartMsg(Message):
    def __init__(self, tid: int, name: str, args: dict):
        super().__init__()
        self.tid = tid
        self.name = name
        self.args = args


class ToolEndMsg(Message):
    def __init__(self, tid: int, content: str):
        super().__init__()
        self.tid = tid
        self.content = content


class TodosMsg(Message):
    def __init__(self, items: list):
        super().__init__()
        self.items = items


class NoticeMsg(Message):
    def __init__(self, text: str, style: str = "dim"):
        super().__init__()
        self.text = text
        self.style = style


class TurnCompleteMsg(Message):
    def __init__(self, duration: float):
        super().__init__()
        self.duration = duration


class ContextMsg(Message):
    def __init__(self, mode: str, model: str):
        super().__init__()
        self.mode = mode
        self.model = model


class UserMsg(Message):
    def __init__(self, text: str):
        super().__init__()
        self.text = text


class ModelsMsg(Message):
    def __init__(self, models: list, current: str, source: str, query: str | None):
        super().__init__()
        self.models = models
        self.current = current
        self.source = source
        self.query = query


class SessionsMsg(Message):
    def __init__(self, sessions: list):
        super().__init__()
        self.sessions = sessions


class HelpMsg(Message):
    pass


# ---------------------------------------------------------------------------
# renderer: posts messages, never touches widgets
# ---------------------------------------------------------------------------

class TuiRenderer(Renderer):
    def __init__(self, app: "CloseCodeApp"):
        self._app = app
        self._seq = 0
        self._stop_event = None

    def _post(self, msg: Message) -> None:
        self._app.post_message(msg)

    def set_context(self, mode, model_name):
        self._post(ContextMsg(mode, model_name))

    def thinking(self):
        self._post(NoticeMsg("thinking…  (esc to interrupt)", "dim"))

    def stream_start(self):
        self._seq += 1
        self._post(StreamStartMsg(self._seq))
        return self._seq

    def stream_update(self, handle, text):
        self._post(StreamUpdateMsg(handle, text))

    def stream_stop(self, handle):
        self._post(StreamStopMsg(handle))

    def tool_call(self, name, args):
        self._seq += 1
        self._post(ToolStartMsg(self._seq, name, args or {}))
        return self._seq

    def tool_result(self, handle, content):
        self._post(ToolEndMsg(handle, content))

    def todos(self, items):
        self._post(TodosMsg(items))

    def notice(self, text, style="dim"):
        self._post(NoticeMsg(text, style))

    def turn_complete(self, duration):
        self._post(TurnCompleteMsg(duration))

    def turn_started(self, stop_event):
        self._stop_event = stop_event

    def interrupt(self):
        if self._stop_event is not None:
            self._stop_event.set()

    def user_message(self, text):
        self._post(UserMsg(text))

    def models(self, models, current, source="live", query=None):
        self._post(ModelsMsg(models, current, source, query))

    def sessions(self, sessions):
        self._post(SessionsMsg(sessions))

    def help(self):
        self._post(HelpMsg())


# ---------------------------------------------------------------------------
# conversation widgets
# ---------------------------------------------------------------------------

_STYLE_COLORS = {
    "dim": "#808080",
    "yellow": "#f5a742",
    "red": "#e06c75",
    "green": "#7fd88f",
    "cyan": "#56b6c2",
    "magenta": "#9d7cd8",
    "blue": "#5c9cf5",
}


def _style_color(style: str) -> str:
    return _STYLE_COLORS.get(style.replace("bold ", ""), _STYLE_COLORS["dim"])


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
    ("/model", "switch model by number or id"),
    ("/key", "paste a new OpenRouter API key"),
    ("/sessions", "list saved sessions"),
    ("/resume", "resume a saved session"),
    ("/delete", "delete a saved session"),
    ("/usage", "show token usage this session"),
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
    def __init__(self, model: str, workdir: str, tool_names: list):
        super().__init__()
        self._model = model
        self._workdir = workdir
        self._tool_names = tool_names

    def compose(self) -> ComposeResult:
        yield Label("CLOSECODE", classes="welcome-title")
        body = Text()
        body.append(f"model   {self._model}\n", style="#808080")
        body.append(f"workdir {self._workdir}\n", style="#808080")
        body.append(f"tools   {', '.join(self._tool_names)}\n\n", style="#808080")
        body.append("Type a task and hit Enter. ", style="#808080")
        body.append("/help", style="#fab283")
        body.append(" for commands · ", style="#808080")
        body.append("esc", style="#fab283")
        body.append(" interrupts · ", style="#808080")
        body.append("ctrl+q", style="#fab283")
        body.append(" quits", style="#808080")
        yield Static(body)


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


# ---------------------------------------------------------------------------
# app
# ---------------------------------------------------------------------------

class CloseCodeApp(App):
    TITLE = "CloseCode"
    BINDINGS = [("ctrl+q", "quit", "Quit")]

    CSS = """
    Screen {
        background: #0a0a0a;
        color: #eeeeee;
    }
    #header {
        height: 3;
        background: #0a0a0a;
        border-bottom: solid #3c3c3c;
        padding: 0 1;
    }
    #header-title {
        color: #fab283;
        text-style: bold;
        width: auto;
    }
    #header-right {
        color: #808080;
        width: 1fr;
        text-align: right;
    }
    #conversation {
        height: 1fr;
        padding: 0 1;
    }
    .role-you {
        color: #5c9cf5;
        text-style: bold;
        margin-top: 1;
    }
    .role-agent {
        color: #fab283;
        text-style: bold;
        margin-top: 1;
    }
    .msg-body {
        color: #eeeeee;
    }
    .block-title {
        color: #9d7cd8;
        text-style: bold;
        margin-top: 1;
    }
    .inset {
        margin-left: 2;
    }
    .tool-head {
        margin-top: 1;
    }
    .tool-body {
        margin-left: 2;
    }
    .welcome-title {
        color: #fab283;
        text-style: bold;
        margin-top: 1;
    }
    #suggest {
        display: none;
        height: auto;
        max-height: 12;
        background: #141414;
        border: solid #484848;
        margin: 0 1;
    }
    #suggest.open {
        display: block;
    }
    #suggest-list {
        background: #141414;
        height: auto;
        max-height: 10;
    }
    #statusbar {
        height: 1;
        background: #141414;
        color: #808080;
        padding: 0 1;
    }
    #input {
        height: 5;
        background: #0a0a0a;
        border: solid #484848;
        margin: 0 1 1 1;
    }
    #input:focus {
        border: solid #fab283;
    }
    PermissionModal, KeyModal, YesNoModal {
        align: center middle;
    }
    #perm-box, #key-box, #yn-box {
        width: 64;
        height: auto;
        background: #141414;
        border: solid #484848;
        padding: 1 2;
    }
    .modal-title {
        color: #fab283;
        text-style: bold;
        margin-bottom: 1;
    }
    .modal-q {
        color: #eeeeee;
        margin-bottom: 1;
    }
    .modal-sub {
        color: #808080;
        margin-bottom: 1;
    }
    #perm-btns, #key-btns, #yn-btns {
        height: auto;
        align: center middle;
    }
    #perm-btns Button, #key-btns Button, #yn-btns Button {
        margin: 0 1;
    }
    """

    def __init__(self, ctx, submit_fn=None):
        super().__init__()
        self.ctx = ctx
        if submit_fn is None:
            from main import submit_text as _submit_text
            submit_fn = _submit_text
        self._submit_fn = submit_fn
        self.renderer = TuiRenderer(self)
        ctx.render = self.renderer
        self._streams: dict[int, dict] = {}
        self._tools: dict[int, ToolBlock] = {}
        self._history: list[str] = []
        self._hist_pos: int = 0
        self._turn_running = False
        self._last_turn: float | None = None
        self._suggest_open = False
        self._suggest_items: list[tuple[str, str]] = []
        self._suggest_idx = 0

    # -- layout -----------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Horizontal(id="header"):
            yield Label("CLOSECODE", id="header-title")
            yield Label("", id="header-right")
        yield VerticalScroll(id="conversation")
        with Container(id="suggest"):
            yield ListView(id="suggest-list")
        yield Static("", id="statusbar")
        yield CommandInput(id="input")

    async def on_mount(self) -> None:
        self.renderer.set_context(self.ctx.mode, self.ctx.model_name)
        conv = self.query_one("#conversation", VerticalScroll)
        await conv.mount(WelcomeBlock(
            self.ctx.model_name,
            str(self.ctx.harness.workdir),
            [t.name for t in self.ctx.all_tools],
        ))
        try:
            self.query_one("#header-right", Label).update(str(self.ctx.harness.workdir))
        except Exception:
            pass
        self._refresh_statusbar()
        self.query_one("#input", CommandInput).focus()

    # -- helpers ----------------------------------------------------------
    def _conv(self) -> VerticalScroll:
        return self.query_one("#conversation", VerticalScroll)

    def _input(self) -> CommandInput:
        return self.query_one("#input", CommandInput)

    def _scroll(self) -> None:
        try:
            self._conv().scroll_end(animate=False)
        except Exception:
            pass

    def _refresh_statusbar(self) -> None:
        tokens = ""
        try:
            if self.ctx.token_tracker is not None:
                tokens = self.ctx.token_tracker.summary()
        except Exception:
            pass
        state = "working…" if self._turn_running else "ready"
        last = f" · last {self._last_turn:.1f}s" if self._last_turn else ""
        try:
            self.query_one("#statusbar", Static).update(
                f"{self.ctx.model_name} · {self.ctx.mode} · {tokens} · {state}{last}"
            )
        except Exception:
            pass

    # -- renderer message handlers ----------------------------------------
    def on_user_msg(self, msg: UserMsg) -> None:
        self._conv().mount(UserBlock(msg.text))
        self._scroll()

    def on_notice_msg(self, msg: NoticeMsg) -> None:
        self._conv().mount(NoticeBlock(msg.text, msg.style))
        self._scroll()

    def on_stream_start_msg(self, msg: StreamStartMsg) -> None:
        blk = AgentBlock(self.ctx.mode)
        self._conv().mount(blk)
        self._streams[msg.sid] = {"w": blk, "pending": "", "last": 0.0}
        self._scroll()

    def on_stream_update_msg(self, msg: StreamUpdateMsg) -> None:
        s = self._streams.get(msg.sid)
        if not s:
            return
        s["pending"] = msg.text
        now = time.monotonic()
        if now - s["last"] >= 0.08:  # throttle markdown re-renders
            s["last"] = now
            s["w"].update_text(msg.text)
            self._scroll()

    def on_stream_stop_msg(self, msg: StreamStopMsg) -> None:
        s = self._streams.pop(msg.sid, None)
        if s:
            s["w"].update_text(s["pending"])

    def on_tool_start_msg(self, msg: ToolStartMsg) -> None:
        blk = ToolBlock(msg.name, msg.args)
        self._conv().mount(blk)
        self._tools[msg.tid] = blk
        self._scroll()

    def on_tool_end_msg(self, msg: ToolEndMsg) -> None:
        blk = self._tools.pop(msg.tid, None)
        if blk is not None:
            blk.finish(msg.content)

    def on_todos_msg(self, msg: TodosMsg) -> None:
        self._conv().mount(TodoBlock(msg.items))
        self._scroll()

    def on_turn_complete_msg(self, msg: TurnCompleteMsg) -> None:
        self._last_turn = msg.duration
        self._refresh_statusbar()
        self._conv().mount(NoticeBlock(f"· {self.ctx.mode} · {msg.duration:.1f}s", "dim"))
        self._scroll()

    def on_context_msg(self, msg: ContextMsg) -> None:
        self._refresh_statusbar()

    def on_models_msg(self, msg: ModelsMsg) -> None:
        self._conv().mount(ModelsBlock(msg.models, msg.current, msg.source, msg.query))
        self._scroll()

    def on_sessions_msg(self, msg: SessionsMsg) -> None:
        self._conv().mount(SessionsBlock(msg.sessions))
        self._scroll()

    def on_help_msg(self, msg: HelpMsg) -> None:
        self._conv().mount(HelpBlock())
        self._scroll()

    # -- permission modal entry point (called from worker threads) ---------
    def request_permission(self, question: str, fut: concurrent.futures.Future) -> None:
        def _done(result):
            if not fut.done():
                fut.set_result(result or "deny")

        self.push_screen(PermissionModal(question), _done)

    # -- input handling ----------------------------------------------------
    async def input_enter(self) -> None:
        if self._suggest_open:
            self._accept_suggestion()
            return
        await self._submit_current()

    async def input_escape(self) -> None:
        if self._suggest_open:
            self._close_suggestions()
        elif self._turn_running:
            self.renderer.interrupt()

    async def input_tab(self) -> bool:
        if self._suggest_open:
            self._accept_suggestion()
            return True
        return False

    async def input_up(self) -> bool:
        if self._suggest_open:
            self._suggest_move(-1)
            return True
        ta = self._input()
        if "\n" not in ta.text and self._history and self._hist_pos > 0:
            self._hist_pos -= 1
            ta.text = self._history[self._hist_pos]
            return True
        return False

    async def input_down(self) -> bool:
        if self._suggest_open:
            self._suggest_move(1)
            return True
        ta = self._input()
        if "\n" not in ta.text and self._history:
            if self._hist_pos < len(self._history) - 1:
                self._hist_pos += 1
                ta.text = self._history[self._hist_pos]
            else:
                self._hist_pos = len(self._history)
                ta.clear()
            return True
        return False

    async def _submit_current(self) -> None:
        ta = self._input()
        text = ta.text
        if not text.strip() or self._turn_running:
            return
        self._history.append(text)
        self._hist_pos = len(self._history)
        ta.clear()
        self._close_suggestions()
        self._turn_running = True
        self._refresh_statusbar()
        self.run_worker(self._do_submit(text), exclusive=True, description="agent-turn")

    async def _do_submit(self, text: str) -> None:
        quit_ = False
        try:
            quit_ = await self._submit_fn(self.ctx, text)
        except Exception as e:  # never let a worker die silently
            self.post_message(NoticeMsg(f"Error: {e}", "red"))
        finally:
            self._turn_running = False
            self._refresh_statusbar()
        if quit_:
            self.exit()

    # -- slash-command suggestions -----------------------------------------
    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "input":
            self._update_suggestions(event.text_area.text)

    def _update_suggestions(self, text: str) -> None:
        m = re.fullmatch(r"/([a-z]*)", text)
        if not m:
            self._close_suggestions()
            return
        frag = m.group(1)
        items = [(c, d) for c, d in COMMANDS if c[1:].startswith(frag)]
        if not items:
            self._close_suggestions()
            return
        self._suggest_items = items
        self._suggest_idx = 0
        lv = self.query_one("#suggest-list", ListView)
        lv.clear()
        for cmd, desc in items:
            lv.append(ListItem(Label(f"{cmd:<12}  {desc}", classes="suggest-item")))
        lv.index = 0
        self.query_one("#suggest", Container).add_class("open")
        self._suggest_open = True

    def _close_suggestions(self) -> None:
        if not self._suggest_open:
            return
        self._suggest_open = False
        try:
            self.query_one("#suggest", Container).remove_class("open")
        except Exception:
            pass

    def _suggest_move(self, delta: int) -> None:
        if not self._suggest_items:
            return
        self._suggest_idx = (self._suggest_idx + delta) % len(self._suggest_items)
        try:
            self.query_one("#suggest-list", ListView).index = self._suggest_idx
        except Exception:
            pass

    def _accept_suggestion(self) -> None:
        if not self._suggest_items:
            self._close_suggestions()
            return
        cmd = self._suggest_items[self._suggest_idx][0]
        ta = self._input()
        ta.text = cmd + " "
        self._close_suggestions()
        ta.focus()


async def run_tui(ctx, bridge: ConfirmBridge) -> None:
    """Launch the Textual TUI on an already-built AgentCtx."""
    app = CloseCodeApp(ctx)
    bridge.attach(app)

    async def _key_prompter() -> str:
        result = await app.push_screen_wait(KeyModal())
        return (result or "").strip()

    async def _confirm_save() -> bool:
        return bool(await app.push_screen_wait(YesNoModal("Save this key to .env for next time?")))

    ctx.key_prompter = _key_prompter
    ctx.confirm_save_key = _confirm_save
    await app.run_async()
