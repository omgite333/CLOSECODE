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