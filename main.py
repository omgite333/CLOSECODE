

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
from guardrails import check_user_input, redact_message  # noqa: E402
from harness import Harness  # noqa: E402
from llm import DEFAULT_MODEL  # noqa: E402
from mcp_tools import get_git_repo_path, get_git_tools  # noqa: E402
from modes import filter_tools_for_mode, mode_system_note  # noqa: E402
from token_tracker import TokenTracker  # noqa: E402
from tools import LOCAL_TOOLS, bind_harness  # noqa: E402


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


async def run_turn(graph, messages: list, token_tracker: TokenTracker) -> list:
    """Streams one user turn via astream_events. Prints tool calls/results
    as they happen, streams the final text response token-by-token into a
    live-updating panel, and tracks token usage along the way.

    Also races the event stream against an Esc keypress (watched on a
    background thread by ui.EscListener) so the user can bail out of a turn
    that's stuck, taking too long, or headed somewhere they don't want it
    to go, without killing the whole process.
    """
    ui.print_thinking()

    live = None
    text_buffer = ""
    final_messages = messages
    turn_start = time.monotonic()
    interrupted = False

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    esc = ui.EscListener(loop, stop_event)
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
                        live = ui.stream_start()
                    ui.stream_update(live, text_buffer)

            elif kind == "on_chat_model_end":
                output = event["data"].get("output")
                if output is not None:
                    token_tracker.add_from_message(output)
                    _blocked, reason = redact_message(output)
                    if reason:
                        ui.print_notice(
                            f"Guardrail \u2014 blocked {reason}. The flagged content was "
                            "removed from conversation history.",
                            style="bold red",
                        )
                if live is not None:
                    ui.stream_stop(live)
                    live = None
                text_buffer = ""

            elif kind == "on_tool_start":
                ui.print_tool_call(event["name"], event["data"].get("input") or {})

            elif kind == "on_tool_end":
                output = event["data"].get("output")
                content = getattr(output, "content", None)
                if content is None:
                    content = str(output)
                ui.print_tool_result(str(content))

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
        ui.print_notice(
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
        ui.print_notice(
            f"Error during this turn [{type(e).__name__}]: {detail}", style="bold red"
        )
        return messages
    finally:
        esc.stop()
        if live is not None:
            ui.stream_stop(live)

    if interrupted:
        ui.print_notice(
            "Interrupted (Esc) \u2014 turn stopped early; any tool call already in "
            "flight may still finish on its own. History kept up to the last "
            "completed step.",
            style="yellow",
        )
        return final_messages

    ui.print_turn_complete(time.monotonic() - turn_start)
    return final_messages


async def main():
    workdir = os.environ.get("AGENT_WORKDIR", "./sandbox")
    auto_approve = os.environ.get("AGENT_AUTO_APPROVE", "false").lower() == "true"
    use_git = os.environ.get("AGENT_ENABLE_GIT", "false").lower() == "true"

    harness = Harness(workdir=workdir, auto_approve=auto_approve, confirm_fn=ui.confirm)
    bind_harness(harness)

    all_tools = list(LOCAL_TOOLS)
    if use_git:
        repo_path = get_git_repo_path()
        try:
            git_tools = await get_git_tools(repo_path)
            all_tools.extend(git_tools)
        except Exception as e:
            ui.print_notice(f"Could not load git MCP tools, continuing without them: {e}", style="yellow")

    mode = "build"
    model_override = None
    token_tracker = TokenTracker()

    model_name = model_override or os.environ.get("HF_MODEL_ID") or os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)

    # Start fresh, or --continue resume the most-recently-used non-empty session
    # from the SQLite store (metadata restores its mode/model too).
    resume = "--continue" in sys.argv
    current_id = session.latest_session_id() if resume else None
    if current_id is not None:
        loaded = session.load(current_id)
        if loaded:
            info = session.get_session(current_id)
            if info is not None:
                mode = info.mode or mode
                if info.model and not model_override:
                    model_override = info.model
                    model_name = info.model
            ui.print_notice(
                f"Resumed session #{current_id} ({info.name if info else '(unnamed)'}, {len(loaded)} msgs)",
                style="cyan",
            )
        else:
            current_id = None

    if current_id is None:
        current_id = session.new_session(model=model_name, mode=mode)
        loaded = None

    messages = list(loaded) if loaded is not None else [system_message_for(mode)]

    graph = build_graph(filter_tools_for_mode(all_tools, mode), model_override)
    ui.set_context(mode, model_name)
    ui.print_banner(model_name, str(harness.workdir), [t.name for t in all_tools])

    while True:
        try:
            raw = ui.user_prompt(mode)
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not raw:
            continue
        if raw.lower() in {"exit", "quit"}:
            break

        if raw.startswith("/"):
            parts = raw[1:].strip().split(maxsplit=1)
            cmd = parts[0].lower() if parts else ""
            arg = parts[1] if len(parts) > 1 else ""

            if cmd == "plan":
                mode = "plan"
                graph = build_graph(filter_tools_for_mode(all_tools, mode), model_override)
                set_system_message(messages, mode)
                ui.set_context(mode, model_name)
                ui.print_notice("Switched to PLAN mode \u2014 read-only tools only.", style="magenta")
            elif cmd == "build":
                mode = "build"
                graph = build_graph(filter_tools_for_mode(all_tools, mode), model_override)
                set_system_message(messages, mode)
                ui.set_context(mode, model_name)
                ui.print_notice("Switched to BUILD mode \u2014 all tools enabled.", style="blue")
            elif cmd == "model":
                if arg:
                    model_override = arg
                    model_name = arg
                    graph = build_graph(filter_tools_for_mode(all_tools, mode), model_override)
                    ui.set_context(mode, model_name)
                    ui.print_notice(f"Switched model to {arg}", style="cyan")
                else:
                    ui.print_notice("Usage: /model <model-id>", style="yellow")
            elif cmd == "sessions":
                ui.print_sessions(session.list_sessions())
            elif cmd == "resume":
                if not arg:
                    ui.print_notice("Usage: /resume <session-id>  (see /sessions)", style="yellow")
                    continue
                try:
                    sid = int(arg)
                except ValueError:
                    ui.print_notice("Usage: /resume <session-id>  (see /sessions)", style="yellow")
                    continue
                info = session.get_session(sid)
                loaded = session.load(sid) if info else None
                if info is None or not loaded:
                    ui.print_notice(f"No resumable session #{sid}. See /sessions.", style="yellow")
                    continue
                if info.mode:
                    mode = info.mode
                if info.model and not model_override:
                    model_override = info.model
                    model_name = info.model
                graph = build_graph(filter_tools_for_mode(all_tools, mode), model_override)
                current_id = sid
                messages = list(loaded)
                set_system_message(messages, mode)
                ui.set_context(mode, model_name)
                ui.print_notice(
                    f"Resumed session #{sid} ({info.name or '(unnamed)'}, {len(messages)} msgs)",
                    style="cyan",
                )
            elif cmd == "delete":
                if not arg:
                    ui.print_notice("Usage: /delete <session-id>  (see /sessions)", style="yellow")
                    continue
                try:
                    sid = int(arg)
                except ValueError:
                    ui.print_notice("Usage: /delete <session-id>  (see /sessions)", style="yellow")
                    continue
                if sid == current_id:
                    ui.print_notice("Can't delete the active session. Resume a different one first.", style="yellow")
                    continue
                if session.delete_session(sid):
                    ui.print_notice(f"Deleted session #{sid}.", style="yellow")
                else:
                    ui.print_notice(f"No session #{sid}. See /sessions.", style="yellow")
            elif cmd == "usage":
                ui.print_token_usage(token_tracker.summary())
            elif cmd == "clear":
                messages = [system_message_for(mode)]
                ui.print_notice("History cleared.")
            elif cmd == "help":
                ui.print_help()
            else:
                ui.print_notice(f"Unknown command: /{cmd}  (try /help)", style="yellow")
            continue

        guard = check_user_input(raw)
        if guard is not None:
            ui.print_notice(f"Guardrail \u2014 {guard}", style="bold red")
            continue

        messages.append(HumanMessage(content=raw))
        ui.print_user_message(raw)
        messages = await run_turn(graph, messages, token_tracker)
        session.save(current_id, messages, model=model_name, mode=mode)


if __name__ == "__main__":
    asyncio.run(main())