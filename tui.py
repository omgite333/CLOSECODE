"""Textual TUI frontend for CloseCode — an OpenCode-style full-screen UI.

Layout: header / scrollable conversation / slash-command suggestions /
status bar / multiline input.

The agent itself is untouched: user input funnels through
main.submit_text(), and agent output arrives via TuiRenderer, which posts
Textual messages that the app turns into widgets. Permission prompts from
the Harness arrive on worker threads and are shown as modal dialogs via
ConfirmBridge (the worker thread blocks on a Future until the user picks).

This module is the app itself — layout, message handlers, input handling,
and the suggestion popup. The pieces it builds on live next door:
tui_theme (banner, palette, CSS), tui_messages (the renderer -> app
protocol), and tui_widgets (the on-screen blocks, input box, and modals).
They are re-exported here so `from tui import ...` keeps working unchanged.
"""

import concurrent.futures
import re
import time

from rich.text import Text

from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, VerticalScroll
from textual.widgets import Label, ListItem, ListView, Static, TextArea

from tui_messages import (
    ContextMsg,
    HelpMsg,
    ModelsMsg,
    NoticeMsg,
    SessionsMsg,
    StreamStartMsg,
    StreamStopMsg,
    StreamUpdateMsg,
    TodosMsg,
    ToolEndMsg,
    ToolStartMsg,
    TurnCompleteMsg,
    TuiRenderer,
    UserMsg,
)
from tui_theme import (
    APP_CSS,
    BIG_TITLE,
    _STYLE_COLORS,
    _big_title,
    _style_color,
)
from tui_widgets import (
    COMMANDS,
    AgentBlock,
    CommandInput,
    ConfirmBridge,
    HelpBlock,
    KeyModal,
    ModelsBlock,
    NoticeBlock,
    PermissionModal,
    SessionsBlock,
    TodoBlock,
    ToolBlock,
    UserBlock,
    WelcomeBlock,
    YesNoModal,
)


# ---------------------------------------------------------------------------
# app
# ---------------------------------------------------------------------------

class CloseCodeApp(App):
    TITLE = "CloseCode"
    BINDINGS = [("ctrl+q", "quit", "Quit")]

    CSS = APP_CSS

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
        with Vertical(id="header"):
            yield Static(BIG_TITLE, id="header-title")
        yield Static("", id="current-file")
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
            self.ctx.mode,
            [t.name for t in self.ctx.all_tools],
        ))
        try:
            self.query_one("#current-file", Static).update(
                Text.from_markup(
                    f"[#808080]current file · [/#808080][#eeeeee]{self.ctx.harness.workdir}[/#eeeeee]"
                )
            )
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
