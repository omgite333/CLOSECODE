
import asyncio
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from langgraph.errors import GraphRecursionError  # noqa: E402

import session  # noqa: E402
import ui  # noqa: E402
from agent import SYSTEM_PROMPT, build_graph  # noqa: E402
from harness import Harness  # noqa: E402
from llm import DEFAULT_MODEL  # noqa: E402
from mcp_tools import get_git_repo_path, get_git_tools  # noqa: E402
from modes import filter_tools_for_mode, mode_system_note  # noqa: E402
from token_tracker import TokenTracker  # noqa: E402
from tools import LOCAL_TOOLS, bind_harness  # noqa: E402


def system_message_for(mode: str) -> SystemMessage:
    return SystemMessage(content=SYSTEM_PROMPT + mode_system_note(mode))


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
    live-updating panel, and tracks token usage along the way."""
    ui.print_thinking()

    live = None
    text_buffer = ""
    final_messages = messages
    turn_start = time.monotonic()

    try:
        async for event in graph.astream_events({"messages": messages}, version="v2"):
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
        if live is not None:
            ui.stream_stop(live)

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

    graph = build_graph(filter_tools_for_mode(all_tools, mode), model_override)

    resume = "--continue" in sys.argv
    if resume:
        session_path = session.latest_session_path()
        loaded = session.load(session_path) if session_path else None
        if loaded:
            messages = loaded
            ui.print_notice(f"Resumed session {session_path.name} ({len(messages)} messages)", style="cyan")
        else:
            session_path = session.new_session_path()
            messages = [system_message_for(mode)]
    else:
        session_path = session.new_session_path()
        messages = [system_message_for(mode)]

    model_name = model_override or os.environ.get("HF_MODEL_ID") or os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
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
                messages[0] = system_message_for(mode)
                ui.set_context(mode, model_name)
                ui.print_notice("Switched to PLAN mode \u2014 read-only tools only.", style="magenta")
            elif cmd == "build":
                mode = "build"
                graph = build_graph(filter_tools_for_mode(all_tools, mode), model_override)
                messages[0] = system_message_for(mode)
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

        messages.append(HumanMessage(content=raw))
        ui.print_user_message(raw)
        messages = await run_turn(graph, messages, token_tracker)
        session.save(session_path, messages)


if __name__ == "__main__":
    asyncio.run(main())