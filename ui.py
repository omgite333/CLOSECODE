"""
ui.py

Terminal presentation layer, separated from agent logic so main.py stays
readable. Aims for the same *feel* as OpenCode/Claude Code's CLI — a boot
banner, a live trace of each tool call as it happens (marker + one-line
summary), and the final answer rendered as Markdown in a bordered panel —
without trying to pixel-clone their actual UI (which is a Go/Bubble Tea
renderer; this is a much simpler line-based approximation in Python).

Only the boot banner (ASCII logo + info block) is centered. Everything else
— tool trace, permission prompts, the response panel, your typed input — is
left-aligned. An earlier version centered everything, which looked broken
in practice: multi-line command output (e.g. `ls -la`) centers each line
independently and falls apart visually, and typed input can't be centered
at all (terminals echo keystrokes at a fixed cursor position), so it stood
out jarringly against centered labels above it. Real tools like OpenCode/
Claude Code only center their boot banner for exactly this reason.
"""

import pyfiglet
from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

console = Console()

TOOL_ICON = "[bold cyan]\u2192[/bold cyan]"
RESULT_ICON = "[dim]\u23bf[/dim]"
AGENT_ICON = "[bold green]\u25cf[/bold green]"

PANEL_WIDTH = 80

# Dark gray -> white, left to right, matching the blocky pixel-logo aesthetic
# (built for this project's own name, not a reproduction of any specific
# tool's actual wordmark/logo).
_GRADIENT = ["#4a4a4a", "#666666", "#888888", "#aaaaaa", "#cccccc", "#e8e8e8", "#ffffff"]


def _gradient_ascii_art(text: str, font: str = "blocky") -> Text:
    art = pyfiglet.figlet_format(text, font=font)
    lines = art.rstrip("\n").split("\n")
    width = max(len(line) for line in lines) or 1

    result = Text()
    for line in lines:
        for col, char in enumerate(line):
            color = _GRADIENT[min(int(col / width * len(_GRADIENT)), len(_GRADIENT) - 1)]
            result.append(char, style=color)
        result.append("\n")
    return result


def print_banner(model_name: str, sandbox_path: str, tool_names: list[str]) -> None:
    console.print()
    console.print(_gradient_ascii_art("OGBOT"), justify="center")

    info = Text()
    info.append(f"model:   {model_name}\n", style="dim")
    info.append(f"sandbox: {sandbox_path}\n", style="dim")
    info.append(f"tools:   {', '.join(tool_names)}", style="dim")
    console.print(info, justify="center")

    console.print()
    console.print("[dim]Type a task, or 'exit' to quit.[/dim]", justify="center")
    console.print()


def print_tool_call(name: str, args: dict) -> None:
    args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    if len(args_str) > 100:
        args_str = args_str[:100] + "\u2026"
    console.print(f"{TOOL_ICON} [cyan]{name}[/cyan]([dim]{args_str}[/dim])")


def print_tool_result(content: str) -> None:
    preview = content.strip().replace("\n", " ")
    if len(preview) > 100:
        preview = preview[:100] + "\u2026"
    console.print(f"  {RESULT_ICON} [dim]{preview}[/dim]")


def _response_panel(text: str) -> Panel:
    return Panel(
        Markdown(text) if text else "",
        title=f"{AGENT_ICON} [green]agent[/green]",
        title_align="left",
        border_style="green",
        padding=(0, 1),
        width=min(PANEL_WIDTH, console.width),
    )


def stream_start() -> Live:
    """Call when the first real content token of a response arrives.
    Returns a Live object — pass it to stream_update() for each subsequent
    chunk, and stream_stop() when the message is complete."""
    live = Live(_response_panel(""), console=console, refresh_per_second=12)
    live.start()
    return live


def stream_update(live: Live, text_so_far: str) -> None:
    live.update(_response_panel(text_so_far))


def stream_stop(live: Live) -> None:
    live.stop()


def start_spinner():
    return console.status("[cyan]thinking\u2026[/cyan]", spinner="dots")


def print_token_usage(summary: str) -> None:
    console.print(f"[dim]{summary}[/dim]")


def print_notice(text: str, style: str = "dim") -> None:
    console.print(f"[{style}]{text}[/{style}]")


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
    console.print(Panel(text, title="commands", border_style="dim",
                         width=min(PANEL_WIDTH, console.width)))


def confirm(question: str) -> bool:
    """Drop-in replacement for Harness's plain input()-based confirm."""
    console.print()
    console.print(f"[bold yellow]permission[/bold yellow] Allow agent to {question}? [y/N] ", end="")
    answer = console.input().strip().lower()
    return answer in ("y", "yes")


def user_prompt(mode: str) -> str:
    console.print()
    mode_label = "[bold magenta]PLAN[/bold magenta]" if mode == "plan" else "[bold blue]BUILD[/bold blue]"
    console.print(f"{mode_label} [bold blue]you[/bold blue] [dim]\u203a[/dim] ", end="")
    return console.input().strip()