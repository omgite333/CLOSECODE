"""Visual constants for the Textual frontend: the header banner font, the
loose colour names that agent notices use, and the app stylesheet.

Kept apart from the widget classes so the palette can be read (and tweaked)
without importing any Textual widget machinery. APP_CSS is attached to
CloseCodeApp.CSS in tui.py.
"""

# ---------------------------------------------------------------------------
# Big centered "CLOSECODE" banner for the header (5x5 block-letter font).
# ---------------------------------------------------------------------------

_BIG_FONT = {
    "C": [" ████", "█    ", "█    ", "█    ", " ████"],
    "D": ["████ ", "█   █", "█   █", "█   █", "████ "],
    "E": ["█████", "█    ", "████ ", "█    ", "█████"],
    "L": ["█    ", "█    ", "█    ", "█    ", "█████"],
    "O": [" ███ ", "█   █", "█   █", "█   █", " ███ "],
    "S": [" ████", "█    ", " ███ ", "    █", "████ "],
}


def _big_title(text: str) -> str:
    return "\n".join(
        " ".join(_BIG_FONT[ch][r] for ch in text) for r in range(5)
    )


BIG_TITLE = _big_title("CLOSECODE")


# Loose colour names the agent uses for notices ("dim", "red", ...) mapped
# onto the opencode palette.
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


APP_CSS = """
Screen {
    background: #0a0a0a;
    color: #eeeeee;
}
#header {
    height: 7;
    background: #0a0a0a;
    border-bottom: solid #3c3c3c;
    padding-top: 1;
}
#header-title {
    color: #fab283;
    text-style: bold;
    text-align: center;
}
#current-file {
    height: 1;
    text-align: center;
    margin-top: 1;
}
.welcome-gap {
    height: 2;
}
.welcome-line {
    width: 1fr;
    text-align: center;
    margin-bottom: 1;
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
