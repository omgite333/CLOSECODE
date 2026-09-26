"""Shared pytest fixtures.

Two things every test in this suite needs:

  1. The project root on sys.path. The app uses a flat layout (every top-level
     .py is a module imported by name — `import harness`, not
     `from closecode.harness import ...`), so tests import the same way.

  2. Total isolation from the developer's real ~/.closecode. config.py and
     session.py both default to paths under the home directory and session.py
     creates its SQLite DB at *import* time. The `isolated_home` fixture
     redirects both at a temp dir via the env vars those modules read, so
     running the suite never touches real sessions or config, and
     CLOSECODE_SESSIONS_DIR must be set before session.py is imported.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def workdir(tmp_path) -> Path:
    """An empty sandbox directory, the equivalent of the agent's cwd."""
    sandbox = tmp_path / "workdir"
    sandbox.mkdir()
    return sandbox


@pytest.fixture
def harness(workdir):
    """A Harness rooted at `workdir` with every action auto-approved.

    Auto-approve keeps the permission prompt out of the way so tests exercise
    the real sandbox/guardrail/undo logic instead of blocking on input().
    """
    from harness import Harness

    return Harness(str(workdir), auto_approve=True)


@pytest.fixture
def harness_factory(workdir):
    """Build a Harness with custom approval behaviour inside the sandbox."""
    from harness import Harness

    def make(auto_approve=False, confirm_fn=None):
        return Harness(str(workdir), auto_approve=auto_approve, confirm_fn=confirm_fn)

    return make


@pytest.fixture
def allow_all():
    """confirm_fn that always allows, for auto_approve=False harnesses."""

    def confirm(action, details=None):
        return "allow"

    return confirm


@pytest.fixture
def deny_all():
    """confirm_fn that always denies, for auto_approve=False harnesses."""

    def confirm(action, details=None):
        return "deny"

    return confirm


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point config.py and session.py at throwaway directories.

    Returns a namespace with `.config_dir` and `.sessions_dir`. The env vars
    are set before anything imports session, so module-level DB creation
    lands in the temp dir rather than the real ~/.closecode.
    """
    import types

    home = tmp_path / "home"
    config_dir = home / "config"
    sessions_dir = home / "sessions"
    config_dir.mkdir(parents=True)
    sessions_dir.mkdir(parents=True)

    monkeypatch.setenv("CLOSECODE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("CLOSECODE_SESSIONS_DIR", str(sessions_dir))
    # config_dir() and _sessions_dir() mkdir on every call, but be explicit
    # in case a future version stops doing that.
    config_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir.mkdir(parents=True, exist_ok=True)

    return types.SimpleNamespace(
        home=home,
        config_dir=config_dir,
        sessions_dir=sessions_dir,
        db_path=sessions_dir / "sessions.db",
    )


@pytest.fixture
def guardrails_on(monkeypatch):
    """Force guardrails enabled for this test.

    guardrails snapshots AGENT_DISABLE_GUARDRAILS into a module-level _ENABLED
    at import time, so a developer with the kill switch on in their .env would
    otherwise silently get a suite that asserts nothing. This also proves the
    "disabled" branch below is only taken when explicitly requested.
    """
    import guardrails

    monkeypatch.setattr(guardrails, "_ENABLED", True)
    return guardrails


@pytest.fixture
def search_root(tmp_path):
    """A bound search root: a temp tree plus a cleanup that unbinds it."""
    import search

    root = tmp_path / "search_root"
    root.mkdir()
    search.bind_search_root(str(root))
    yield root
    search._root = None  # unbind so a later test can't inherit this root


@pytest.fixture
def todo_store():
    """A bound TodoStore, so the module-level tool functions have one."""
    import todos

    store = todos.TodoStore()
    todos.bind_todo_store(store)
    yield store
    todos._store = None


@pytest.fixture
def bound_harness(harness):
    """A Harness also bound into tools.py for the @tool wrappers."""
    import tools

    tools.bind_harness(harness)
    yield harness
    tools._harness = None
