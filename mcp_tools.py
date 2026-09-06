"""
mcp_tools.py

Connects to the official `mcp-server-git` MCP server and loads its tools
(git status, diff, log, commit, branch management, etc.) as LangChain tools,
so the agent can use them exactly like the local bash/read_file/write_file
tools — no special-casing needed in agent.py.

Requires: pip install mcp-server-git   (already in requirements.txt)

MCP tool loading is inherently async (it spawns a subprocess and speaks the
MCP protocol over stdio), which is why main.py is now an async script.

Note: mcp-server-git's own tools operate DIRECTLY on the filesystem path you
give it — they are NOT routed through harness.py's permission/sandbox
checks. That's a real trade-off: you get real git functionality, but git
commands (including things like `git reset --hard` if the model calls it)
run without the confirm-before-acting prompt your other tools have. Point
GIT_REPO_PATH at a repo you're comfortable letting the agent operate on
directly, and consider it a known gap if you take this further.
"""

import os

from langchain_mcp_adapters.client import MultiServerMCPClient


def build_mcp_client(repo_path: str) -> MultiServerMCPClient:
    return MultiServerMCPClient(
        {
            "git": {
                "command": "python",
                "args": ["-m", "mcp_server_git", "--repository", repo_path],
                "transport": "stdio",
            }
        }
    )


async def get_git_tools(repo_path: str):
    """Returns a list of LangChain-compatible tools backed by mcp-server-git.
    Call this once at startup (it's async — see main.py)."""
    client = build_mcp_client(repo_path)
    return await client.get_tools()


def get_git_repo_path() -> str:
    """Where the git MCP server should operate. Defaults to the same
    directory as the agent's sandbox, but can be pointed elsewhere."""
    return os.environ.get("GIT_REPO_PATH", os.environ.get("AGENT_WORKDIR", "./sandbox"))