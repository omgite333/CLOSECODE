"""Look and feel for the Rich CLI: the shared Console, the colour palette, and
the block-letter banner.

Everything here is either a constant or a pure function of its arguments, so
the other ui_* modules can import these names directly and every one of them
renders through the same Console instance.
"""

from rich.console import Console
from rich.text import Text

import pyfiglet

# One Console for the whole app so Rich can detect the terminal width once and
# keep every frame consistent during a Live stream.
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
