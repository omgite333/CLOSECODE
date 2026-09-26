import argparse
import asyncio
import contextlib
import os
import sys
import time
import warnings

warnings.filterwarnings(
    "ignore",
    message="Core Pydantic V1 functionality isn't compatible with Python 3.14",
    category=UserWarning,
)

from dotenv import load_dotenv

load_dotenv()

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from langgraph.errors import GraphRecursionError  # noqa: E402

import session  # noqa: E402
import ui  # noqa: E402
from agent import SYSTEM_PROMPT, build_graph  # noqa: E402
from config import KEY_API, KEY_MODEL, config_path, load_config, save_config_value  # noqa: E402
from guardrails import check_user_input, redact_message  # noqa: E402
from harness import Harness  # noqa: E402
from llm import DEFAULT_MODEL, KNOWN_MODELS, fetch_openrouter_models, get_llm, resolve_model_arg  # noqa: E402
from mcp_tools import get_git_repo_path, get_git_tools  # noqa: E402
from modes import filter_tools_for_mode, mode_system_note  # noqa: E402
from render import ClassicRenderer, Renderer  # noqa: E402
from search import SEARCH_TOOLS, bind_search_root  # noqa: E402
from todos import TODO_TOOLS, TodoStore, bind_todo_store  # noqa: E402
from token_tracker import TokenTracker  # noqa: E402
from tools import LOCAL_TOOLS, bind_harness  # noqa: E402


# Module-global todo store, bound to the todo tools once at startup and
# cleared whenever the session changes (/clear, /resume, new session).
todo_store = TodoStore()


class AgentCtx:
    """Mutable per-session state shared by both frontends (classic console
    loop and the Textual TUI). Slash commands mutate this in place."""

    def __init__(self):
        self.harness = None
        self.all_tools: list = []
        self.graph = None
        self.mode = "build"
        self.model_override = None
        self.model_name = ""
        self.token_tracker = None
        self.messages: list = []
        self.current_id = None
        self.listed_models: list = []
        self.render: Renderer = None
        self.esc_factory = None  # ui.EscListener for classic, None for TUI
        self.key_prompter = None  # async () -> str (TUI shows a password modal)
        self.confirm_save_key = None  # async () -> bool


def system_message_for(mode: str) -> SystemMessage:
    return SystemMessage(content=SYSTEM_PROMPT + mode_system_note(mode))


def set_system_message(messages: list, mode: str) -> None:
    """Ensure a session's first message is the current system prompt, in the
    right mode note, without clobbering a non-system first message."""
    if messages and getattr(messages[0], "type", "") == "system":
        messages[0] = system_message_for(mode)
    else:
        messages.insert(0, system_message_for(mode))


def _looks_like_tool_json(text: str) -> bool:
    """True when a streamed buffer is a tool call the model is writing out as
    JSON (qwen2.5-coder via Ollama does this instead of native tool_calls).
    Such content is re-parsed into a real tool call in agent.py, so we keep
    it out of the chat panel entirely."""
    stripped = text.lstrip()
    return stripped.startswith("{") and '"name"' in stripped and '"arguments"' in stripped


async def run_turn(graph, messages: list, token_tracker: TokenTracker,
                   render: Renderer = None, esc_factory=None) -> list:
    """Streams one user turn via astream_events. Renders tool calls/results
    as they happen, streams the final text response token-by-token, and
    tracks token usage.

    `render` is the frontend (classic console or TUI). `esc_factory` builds
    the interrupt watcher — ui.EscListener for the classic loop (watches
    stdin on a thread); the TUI passes None and drives interruption itself
    via render.turn_started/render.interrupt.
    """
    render = render or ClassicRenderer()
    render.thinking()

    live = None
    tool_handle = None
    text_buffer = ""
    final_messages = messages
    turn_start = time.monotonic()
    interrupted = False

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    render.turn_started(stop_event)
    esc = esc_factory(loop, stop_event) if esc_factory else None
    if esc:
        esc.start()

    agen = graph.astream_events({"messages": messages}, version="v2")

    try:
        while True:
            next_task = asyncio.ensure_future(agen.__anext__())
            stop_task = asyncio.ensure_future(stop_event.wait())
            try:
                done, _pending = await asyncio.wait(
                    {next_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
                )
            except asyncio.CancelledError:
                next_task.cancel()
                stop_task.cancel()
                raise

            if stop_task in done:
                interrupted = True
                next_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await next_task
                with contextlib.suppress(Exception):
                    await agen.aclose()
                break

            stop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stop_task

            try:
                event = next_task.result()
            except StopAsyncIteration:
                break

            kind = event["event"]

            if kind == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                if getattr(chunk, "content", None):
                    text_buffer += chunk.content
                    if _looks_like_tool_json(text_buffer):
                        continue
                    if live is None:
                        live = render.stream_start()
                    render.stream_update(live, text_buffer)

            elif kind == "on_chat_model_end":
                output = event["data"].get("output")
                if output is not None:
                    token_tracker.add_from_message(output)
                    _blocked, reason = redact_message(output)
                    if reason:
                        render.notice(
                            f"Guardrail \u2014 blocked {reason}. The flagged content was "
                            "removed from conversation history.",
                            style="bold red",
                        )
                if live is not None:
                    render.stream_stop(live)
                    live = None
                text_buffer = ""

            elif kind == "on_tool_start":
                tool_handle = render.tool_call(event["name"], event["data"].get("input") or {})

            elif kind == "on_tool_end":
                output = event["data"].get("output")
                content = getattr(output, "content", None)
                if content is None:
                    content = str(output)
                render.tool_result(tool_handle, str(content))
                tool_handle = None
                if event.get("name") == "todo_write":
                    render.todos(todo_store.get())

            elif kind == "on_chain_end" and event.get("name") == "LangGraph":
                output = event["data"].get("output")
                if output and "messages" in output:
                    final_messages = output["messages"]

    except GraphRecursionError:
        # This used to be the visible symptom of the sandbox/workdir bug:
        # write_file silently failing on an absolute-looking path, bash
        # correctly showing an empty dir, and the model retrying the same
        # broken step until the recursion cap kicked in. That root cause is
        # fixed in harness.py now. If this still fires, it means the agent
        # is genuinely stuck in a loop for some other reason (e.g. a
        # command that keeps failing for a real, external reason) — surface
        # that plainly instead of a raw traceback, rather than papering
        # over it by just raising the limit.
        render.notice(
            "Stopped: the agent hit the step limit for this turn without finishing "
            "(likely repeating a failing action). Check the tool calls/results above "
            "for what kept failing, then try again or rephrase the task.",
            style="bold red",
        )
        return messages
    except Exception as e:
        detail = str(e) or repr(e)
        cause = getattr(e, "__cause__", None)
        if cause and str(cause) not in detail:
            detail = f"{detail} (caused by: {cause})"
        render.notice(
            f"Error during this turn [{type(e).__name__}]: {detail}", style="bold red"
        )
        return messages
    finally:
        if esc:
            esc.stop()
        if live is not None:
            render.stream_stop(live)

    if interrupted:
        render.notice(
            "Interrupted (Esc) \u2014 turn stopped early; any tool call already in "
            "flight may still finish on its own. History kept up to the last "
            "completed step.",
            style="yellow",
        )
        return final_messages

    render.turn_complete(time.monotonic() - turn_start)
    return final_messages


def save_api_key(key: str) -> str:
    """Persist an API key to the per-user config (~/.closecode/config.json)
    so it's asked only once per machine, no matter which directory
    closecode is launched from."""
    return str(save_config_value(KEY_API, key))


def ensure_api_key() -> str:
    """Make sure an OpenRouter API key is available. Lookup order:
    OPENROUTER_API_KEY env (which also covers the project's .env via
    load_dotenv) -> ~/.closecode/config.json -> prompt the user (hidden
    input) and offer to save it to the user config for next time.
    Exits if no key is given, since the agent can't call a model without one.

    Runs before any frontend takes over the terminal, so the classic
    console prompt is fine even in TUI mode."""
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        key = str(load_config().get(KEY_API, "") or "").strip()
    if key:
        os.environ["OPENROUTER_API_KEY"] = key
        return key
    ui.print_notice(
        "No OPENROUTER_API_KEY found (env, .env, or ~/.closecode/config.json).",
        style="yellow",
    )
    key = ui.prompt_api_key()
    if not key:
        ui.print_notice(
            "No API key provided — the agent can't run without one. "
            "Set OPENROUTER_API_KEY and restart.",
            style="bold red",
        )
        raise SystemExit(1)
    os.environ["OPENROUTER_API_KEY"] = key
    if ui.confirm_save_key():
        path = save_api_key(key)
        ui.print_notice(f"Saved to {path} — won't ask again on this machine.", style="green")
    return key


COMPACT_PROMPT = """You are summarizing a coding-assistant session so work can continue
in a fresh context window. Write a dense, structured summary covering:

1. What the user was trying to accomplish (the overall goal)
2. What was actually done — files created/modified, commands run, key decisions
3. Current state — what's working, what's unfinished or broken
4. Anything the user explicitly asked to remember, plus useful context
   (paths, model choices, config values) needed to continue seamlessly

Be concrete: name files, functions, and decisions. Skip greetings and
small talk. Write it so another agent could pick up exactly where this
one left off."""


async def compact_messages(messages: list, model_override: str = None) -> str:
    """Summarize the conversation with the plain LLM (no tools) and return
    the summary text. Long tool outputs are truncated so the summarizer
    itself doesn't blow the context window."""
    llm = get_llm(model_override)
    lines = []
    for m in messages:
        role = getattr(m, "type", "?")
        content = m.content if isinstance(m.content, str) else str(m.content)
        if len(content) > 2000:
            content = content[:2000] + "…[truncated]"
        lines.append(f"[{role}] {content}")
    convo = "\n\n".join(lines)
    summary = await llm.ainvoke(
        [SystemMessage(content=COMPACT_PROMPT), HumanMessage(content=convo)]
    )
    return summary.content if isinstance(summary.content, str) else str(summary.content)


async def _classic_key_prompter() -> str:
    return await asyncio.to_thread(ui.prompt_api_key)


async def _classic_confirm_save_key() -> bool:
    return await asyncio.to_thread(ui.confirm_save_key)


async def build_context(render: Renderer, esc_factory, key_prompter,
                        confirm_save_key, confirm_fn, resume: bool = False) -> AgentCtx:
    """All startup: API key, harness, tools, session. Shared by both
    frontends — each one then runs its own input loop on the returned ctx."""
    ensure_api_key()

    # Work in the directory closecode was launched from (like Claude Code /
    # opencode). Set AGENT_WORKDIR=./sandbox (or any path) to jail the agent
    # to a subfolder instead.
    workdir = os.environ.get("AGENT_WORKDIR", ".")
    auto_approve = os.environ.get("AGENT_AUTO_APPROVE", "false").lower() == "true"
    use_git = os.environ.get("AGENT_ENABLE_GIT", "false").lower() == "true"

    harness = Harness(workdir=workdir, auto_approve=auto_approve, confirm_fn=confirm_fn)
    bind_harness(harness)
    bind_todo_store(todo_store)
    bind_search_root(str(harness.workdir))

    all_tools = list(LOCAL_TOOLS) + SEARCH_TOOLS + TODO_TOOLS
    if use_git:
        repo_path = get_git_repo_path()
        try:
            git_tools = await get_git_tools(repo_path)
            all_tools.extend(git_tools)
        except Exception as e:
            render.notice(f"Could not load git MCP tools, continuing without them: {e}", style="yellow")

    ctx = AgentCtx()
    ctx.harness = harness
    ctx.all_tools = all_tools
    ctx.token_tracker = TokenTracker()
    ctx.listed_models = list(KNOWN_MODELS)
    ctx.render = render
    ctx.esc_factory = esc_factory
    ctx.key_prompter = key_prompter
    ctx.confirm_save_key = confirm_save_key

    ctx.model_override = None
    # New sessions start on the saved default model (set via /model),
    # unless explicitly overridden by env. Resuming a session below
    # restores that session's own model instead.
    ctx.model_name = (
        os.environ.get("HF_MODEL_ID")
        or os.environ.get("OLLAMA_MODEL")
        or str(load_config().get(KEY_MODEL, "") or "").strip()
        or DEFAULT_MODEL
    )

    # Start fresh, or resume the most-recently-used non-empty session from
    # the SQLite store (metadata restores its mode/model too).
    current_id = session.latest_session_id() if resume else None
    if current_id is not None:
        loaded = session.load(current_id)
        if loaded:
            info = session.get_session(current_id)
            if info is not None:
                ctx.mode = info.mode or ctx.mode
                if info.model and not ctx.model_override:
                    ctx.model_override = info.model
                    ctx.model_name = info.model
            render.notice(
                f"Resumed session #{current_id} ({info.name if info else '(unnamed)'}, {len(loaded)} msgs)",
                style="cyan",
            )
        else:
            current_id = None

    if current_id is None:
        current_id = session.new_session(model=ctx.model_name, mode=ctx.mode)
        loaded = None

    ctx.current_id = current_id
    ctx.messages = list(loaded) if loaded is not None else [system_message_for(ctx.mode)]
    ctx.graph = build_graph(filter_tools_for_mode(all_tools, ctx.mode), ctx.model_override)
    render.set_context(ctx.mode, ctx.model_name)
    return ctx


async def _show_models(ctx: AgentCtx, r, arg: str) -> None:
    """Fetch the OpenRouter model list and render it (shared by /models
    and bare /model, which acts as a picker)."""
    parts = arg.split()
    force = "--refresh" in parts
    query = " ".join(p for p in parts if p != "--refresh").strip().lower()
    r.notice("Fetching model list from OpenRouter…", style="dim")
    models, source = await asyncio.to_thread(fetch_openrouter_models, force_refresh=force)
    if query:
        models = [m for m in models
                  if query in m[0].lower() or query in m[1].lower()]
        if not models:
            r.notice(f"No models match '{query}'.", style="yellow")
            return
    ctx.listed_models = models
    r.models(models, ctx.model_name, source=source, query=query or None)


async def handle_command(ctx: AgentCtx, cmd: str, arg: str) -> None:
    """Slash-command dispatch shared by both frontends. Mutates ctx."""
    r = ctx.render
    if cmd == "plan":
        ctx.mode = "plan"
        ctx.graph = build_graph(filter_tools_for_mode(ctx.all_tools, ctx.mode), ctx.model_override)
        set_system_message(ctx.messages, ctx.mode)
        r.set_context(ctx.mode, ctx.model_name)
        r.notice("Switched to PLAN mode \u2014 read-only tools only.", style="magenta")
    elif cmd == "build":
        ctx.mode = "build"
        ctx.graph = build_graph(filter_tools_for_mode(ctx.all_tools, ctx.mode), ctx.model_override)
        set_system_message(ctx.messages, ctx.mode)
        r.set_context(ctx.mode, ctx.model_name)
        r.notice("Switched to BUILD mode \u2014 all tools enabled.", style="blue")
    elif cmd == "model":
        if not arg:
            # No arg: show the model list so the user can pick a number.
            await _show_models(ctx, r, "")
        else:
            new_model = resolve_model_arg(arg, ctx.listed_models)
            if arg.strip().isdigit() and new_model == arg.strip():
                r.notice(
                    f"No model #{arg.strip()} in the current list — run /models "
                    "first (or pass a full OpenRouter model id).",
                    style="yellow",
                )
            else:
                ctx.model_override = new_model
                ctx.model_name = new_model
                ctx.graph = build_graph(filter_tools_for_mode(ctx.all_tools, ctx.mode), ctx.model_override)
                r.set_context(ctx.mode, ctx.model_name)
                save_config_value(KEY_MODEL, new_model)
                r.notice(f"Switched model to {new_model} — saved as default for new sessions.", style="cyan")
    elif cmd == "models":
        await _show_models(ctx, r, arg)
    elif cmd == "key":
        key = await ctx.key_prompter()
        if not key:
            r.notice("No key entered — keeping the current one.", style="yellow")
        else:
            os.environ["OPENROUTER_API_KEY"] = key
            if await ctx.confirm_save_key():
                path = save_api_key(key)
                r.notice(f"Saved to {path} — won't ask again on this machine.", style="green")
            ctx.graph = build_graph(filter_tools_for_mode(ctx.all_tools, ctx.mode), ctx.model_override)
            r.notice("API key updated.", style="green")
    elif cmd == "sessions":
        r.sessions(session.list_sessions())
    elif cmd == "resume":
        if not arg:
            r.notice("Usage: /resume <session-id>  (see /sessions)", style="yellow")
            return
        try:
            sid = int(arg)
        except ValueError:
            r.notice("Usage: /resume <session-id>  (see /sessions)", style="yellow")
            return
        info = session.get_session(sid)
        loaded = session.load(sid) if info else None
        if info is None or not loaded:
            r.notice(f"No resumable session #{sid}. See /sessions.", style="yellow")
            return
        if info.mode:
            ctx.mode = info.mode
        if info.model and not ctx.model_override:
            ctx.model_override = info.model
            ctx.model_name = info.model
        ctx.graph = build_graph(filter_tools_for_mode(ctx.all_tools, ctx.mode), ctx.model_override)
        ctx.current_id = sid
        ctx.messages = list(loaded)
        todo_store.clear()
        set_system_message(ctx.messages, ctx.mode)
        r.set_context(ctx.mode, ctx.model_name)
        r.notice(
            f"Resumed session #{sid} ({info.name or '(unnamed)'}, {len(ctx.messages)} msgs)",
            style="cyan",
        )
    elif cmd == "delete":
        if not arg:
            r.notice("Usage: /delete <session-id>  (see /sessions)", style="yellow")
            return
        try:
            sid = int(arg)
        except ValueError:
            r.notice("Usage: /delete <session-id>  (see /sessions)", style="yellow")
            return
        if sid == ctx.current_id:
            r.notice("Can't delete the active session. Resume a different one first.", style="yellow")
            return
        if session.delete_session(sid):
            r.notice(f"Deleted session #{sid}.", style="yellow")
        else:
            r.notice(f"No session #{sid}. See /sessions.", style="yellow")
    elif cmd == "usage":
        r.notice(ctx.token_tracker.summary())
    elif cmd == "clear":
        ctx.messages = [system_message_for(ctx.mode)]
        todo_store.clear()
        r.notice("History cleared.")
    elif cmd == "compact":
        if len(ctx.messages) <= 2:
            r.notice("Nothing to compact yet.", style="yellow")
        else:
            r.notice("Compacting conversation…", style="dim")
            old_count = len(ctx.messages)
            try:
                summary = await compact_messages(ctx.messages, ctx.model_override)
            except Exception as e:
                r.notice(f"Compaction failed: {e}", style="bold red")
                return
            ctx.messages = [
                system_message_for(ctx.mode),
                HumanMessage(
                    content="[Summary of earlier conversation]\n" + summary
                ),
            ]
            session.save(ctx.current_id, ctx.messages, model=ctx.model_name, mode=ctx.mode)
            r.notice(
                f"Compacted {old_count} messages into a summary.",
                style="green",
            )
    elif cmd == "help":
        r.help()
    else:
        r.notice(f"Unknown command: /{cmd}  (try /help)", style="yellow")


async def submit_text(ctx: AgentCtx, text: str) -> bool:
    """Handle one submitted line of input. Returns True if the frontend
    should quit."""
    text = text.strip()
    if not text:
        return False
    if text.lower() in {"exit", "quit"}:
        return True

    if text.startswith("/"):
        parts = text[1:].strip().split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1] if len(parts) > 1 else ""
        await handle_command(ctx, cmd, arg)
        return False

    guard = check_user_input(text)
    if guard is not None:
        ctx.render.notice(f"Guardrail \u2014 {guard}", style="bold red")
        return False

    ctx.messages.append(HumanMessage(content=text))
    ctx.render.user_message(text)
    ctx.messages = await run_turn(ctx.graph, ctx.messages, ctx.token_tracker,
                                  render=ctx.render, esc_factory=ctx.esc_factory)
    session.save(ctx.current_id, ctx.messages, model=ctx.model_name, mode=ctx.mode)
    return False


async def classic_loop(ctx: AgentCtx):
    """The original line-based REPL."""
    while True:
        try:
            raw = ui.user_prompt(ctx.mode)
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if await submit_text(ctx, raw):
            break


async def main(no_tui: bool = False, resume: bool = False):
    use_tui = not no_tui and sys.stdout.isatty()
    bridge = None
    if use_tui:
        from tui import ConfirmBridge
        bridge = ConfirmBridge()

    ctx = await build_context(
        render=ClassicRenderer(),
        esc_factory=ui.EscListener,
        key_prompter=_classic_key_prompter,
        confirm_save_key=_classic_confirm_save_key,
        confirm_fn=bridge if bridge is not None else ui.confirm,
        resume=resume,
    )

    if use_tui:
        ctx.esc_factory = None
        from tui import run_tui
        await run_tui(ctx, bridge)
    else:
        ui.print_banner(ctx.model_name, str(ctx.harness.workdir),
                        [t.name for t in ctx.all_tools])
        await classic_loop(ctx)


def cli_entry():
    """Entry point for the `closecode` console script (see pyproject.toml
    [project.scripts]). Installed via pip/pipx, runs the async main()."""
    parser = argparse.ArgumentParser(
        prog="closecode",
        description="CloseCode — an agentic terminal coding assistant",
    )
    parser.add_argument("--no-tui", action="store_true",
                        help="use the classic line-based UI instead of the full-screen TUI")
    parser.add_argument("--continue", dest="resume", action="store_true",
                        help="resume the most recent session")
    args = parser.parse_args()
    asyncio.run(main(no_tui=args.no_tui, resume=args.resume))


if __name__ == "__main__":
    cli_entry()
