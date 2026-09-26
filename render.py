"""Renderer abstraction: one interface for turn output, two frontends.

The agent loop (main.run_turn, main.handle_command) talks only to a
Renderer. The classic line-based UI implements it by calling ui.py
functions; the Textual TUI (tui.py) implements it by posting messages to
the app. This keeps a single agent code path for both frontends.
"""

import ui


class Renderer:
    """Interface. Every method is sync — TUI implementations post messages
    to the app rather than touching widgets directly."""

    # -- context ---------------------------------------------------------
    def set_context(self, mode: str, model_name: str) -> None: ...

    # -- turn lifecycle --------------------------------------------------
    def thinking(self) -> None: ...
    def stream_start(self):
        """Begin a streamed assistant message. Returns an opaque handle
        passed to stream_update/stream_stop."""
        ...
    def stream_update(self, handle, text: str) -> None: ...
    def stream_stop(self, handle) -> None: ...
    def tool_call(self, name: str, args: dict):
        """Announce a tool call. Returns an opaque handle passed to
        tool_result so the frontend can match them."""
        ...
    def tool_result(self, handle, content: str) -> None: ...
    def todos(self, items: list) -> None: ...
    def notice(self, text: str, style: str = "dim") -> None: ...
    def turn_complete(self, duration: float) -> None: ...
    def turn_started(self, stop_event) -> None:
        """Gives the frontend the asyncio.Event that interrupts the turn —
        the TUI stores it so Esc can cancel a running turn."""
    def interrupt(self) -> None:
        """Ask the running turn (if any) to stop early."""

    # -- command output --------------------------------------------------
    def user_message(self, text: str) -> None: ...
    def models(self, models: list, current: str, source: str = "live", query: str = None) -> None: ...
    def sessions(self, sessions: list) -> None: ...
    def help(self) -> None: ...


class ClassicRenderer(Renderer):
    """The original rich/console UI from ui.py."""

    def set_context(self, mode, model_name):
        ui.set_context(mode, model_name)

    def thinking(self):
        ui.print_thinking()

    def stream_start(self):
        return ui.stream_start()

    def stream_update(self, handle, text):
        ui.stream_update(handle, text)

    def stream_stop(self, handle):
        ui.stream_stop(handle)

    def tool_call(self, name, args):
        ui.print_tool_call(name, args)
        return None

    def tool_result(self, handle, content):
        ui.print_tool_result(content)

    def todos(self, items):
        ui.print_todos(items)

    def notice(self, text, style="dim"):
        ui.print_notice(text, style=style)

    def turn_complete(self, duration):
        ui.print_turn_complete(duration)

    def turn_started(self, stop_event):
        pass

    def interrupt(self):
        pass

    def user_message(self, text):
        ui.print_user_message(text)

    def models(self, models, current, source="live", query=None):
        ui.print_models(models, current, source=source, query=query)

    def sessions(self, sessions):
        ui.print_sessions(sessions)

    def help(self):
        ui.print_help()
