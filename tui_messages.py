"""The renderer -> app protocol: Textual messages and TuiRenderer.

The renderer half of the frontend never touches widgets. It posts the message
classes below onto the app, and CloseCodeApp's on_<name>_msg handlers turn
those into widgets. Keeping the boundary explicit is what lets the agent run
on a worker thread while the UI stays responsive.
"""

from textual.message import Message

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
