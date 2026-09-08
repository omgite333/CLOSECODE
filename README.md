# terminal-agent

A minimal terminal coding agent built with LangGraph (agent loop), LangChain
(tool + model abstraction), LangSmith (tracing), and a Hugging Face model as
the LLM. Same shape as OpenCode/Terminus 2: read task -> decide -> run tool ->
observe result -> repeat.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:
- `HUGGINGFACEHUB_API_TOKEN` — from https://huggingface.co/settings/tokens
- `HF_MODEL_ID` — a model that supports tool/function calling (see note below)
- `LANGCHAIN_API_KEY` — optional, from https://smith.langchain.com, enables tracing

## Run

```bash
python main.py
```

The agent operates inside `./sandbox` (configurable via `AGENT_WORKDIR`) and
will ask for permission before running shell commands or writing files,
unless `AGENT_AUTO_APPROVE=true`.

## Persistent sessions (SQLite)

Conversations are stored in `./sessions/sessions.db` as per-message rows plus
session metadata (name, model, mode, created/updated timestamps, message
count). Existing `session_*.json` files are auto-migrated into the DB on the
first run.

- `--continue` — resume the most recently used session (its mode/model are
  restored from metadata).
- `/sessions` — list all saved sessions with metadata.
- `/resume <id>` — switch to a saved conversation.
- `/delete <id>` — delete a saved session.
- Each turn is saved automatically; a session's name is derived from its
  first user message.

## Guardrails

The agent is scoped to coding only, and `guardrails.py` enforces that with
four layers:

1. **Input scope** — off-topic chatter (greetings, opinions, trivia, creative
   writing, news takes) is redirected to a coding task, and clearly malicious
   requests (keyloggers, account hacking, phishing kits) are refused before
   they reach the model.
2. **Command blocking** — destructive shells commands (`rm -rf /`, `mkfs`,
   disk wipes, fork bombs, `curl | sh`, reverse shells) are blocked before
   execution, *even when auto-approve is on*.
3. **Write scanning** — file writes/edits containing malware indicators
   (ransomware, keyloggers, miners, persistence, injection) are refused.
4. **Output redaction** — the model's final answer is scanned and flagged
   content is scrubbed from conversation history.

These are conservative heuristics on top of the sandbox + per-action
permission prompts, not a hard guarantee. Set `AGENT_DISABLE_GUARDRAILS=true`
in `.env` to disable them entirely (only for trusted, isolated testing).

## A note on model choice

Tool-calling reliability varies significantly across open Hugging Face
models — this is the single biggest factor in whether this agent actually
works well. Frontier closed models (what Claude Code / OpenCode use by
default) are heavily trained specifically for reliable tool use; open models
are improving but inconsistent.

Models worth trying, roughly in order of tool-calling reliability:
- `Qwen/Qwen2.5-72B-Instruct`
- `meta-llama/Meta-Llama-3.1-70B-Instruct`
- `meta-llama/Meta-Llama-3.1-8B-Instruct` (fastest/cheapest, least reliable)

If a smaller model frequently fails to call tools correctly, or hallucinates
tool arguments, that's expected — it's a real, documented gap between open
and closed models on agentic tasks, not a bug in this code. Swapping
`HF_MODEL_ID` is the first thing to try before changing anything else.

## Where to go next

1. **Watch a trace in LangSmith** (smith.langchain.com) once you have a run —
   seeing the exact messages/tool calls at each step is the fastest way to
   debug why the agent did something unexpected.
2. **Add more tools** — `list_dir`, `edit_file` (targeted find/replace instead
   of full overwrite), `run_tests`.
3. **Split client/server** — move `build_graph()` behind a small FastAPI/
   WebSocket server, and make `main.py` a thin client that streams from it.
   This is the step that makes it architecturally closer to OpenCode.
4. **Swap the sandbox for Docker** — the current harness restricts file paths
   but shell commands still run on your actual machine. For anything beyond
   personal experimentation, run `bash` calls inside a container instead.
