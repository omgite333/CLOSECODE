"""Tests for the plan/build tool gate.

The security claim in tools.py is that plan mode is enforced "by never binding
those tools to the model at all, which is a stronger guarantee than trusting
the model to just not call them". These tests hold both shipped
`filter_tools_for_mode` implementations to that claim.

There are two of them — tools.py's and modes.py's — and main.py imports the
modes.py one. They are NOT equivalent (see the divergence tests at the bottom,
which document four tools that leak into plan mode in one implementation or the
other), so both are covered here.
"""

import pytest

import modes
import tools as tools_mod
from tools import LOCAL_TOOLS

# Read-only: no filesystem, shell, or git mutation.
READ_ONLY = ["read_file", "list_dir", "tavily_search", "glob", "grep",
             "list_background", "tail_logs"]

# Must never be bound in plan mode.
MUTATING = ["bash", "write_file", "edit_file", "run_tests",
            "start_background", "kill_background", "undo_last_change"]

# Tools each implementation agrees are blocked in both.
BLOCKED_IN_BOTH = ["bash", "write_file", "edit_file"]

# Stand-ins for MCP-provided tools (e.g. the git server).
MCP_TOOLS = ["git_status", "git_diff", "git_log", "git_commit", "git_push",
             "git_create_branch"]


class FakeTool:
    """Minimal tool stand-in: the filters only ever read .name."""

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"FakeTool({self.name!r})"


def names(tool_list):
    return [t.name for t in tool_list]


@pytest.fixture(params=[modes, tools_mod], ids=["modes.py", "tools.py"])
def filter_fn(request):
    """Both shipped implementations, so neither can drift unnoticed."""
    return request.param.filter_tools_for_mode


# ---------------------------------------------------------------------------
# build mode
# ---------------------------------------------------------------------------

def test_build_mode_keeps_everything(filter_fn):
    for name in READ_ONLY + MUTATING + MCP_TOOLS:
        assert names(filter_fn([FakeTool(name)], "build")) == [name]


def test_build_mode_returns_the_same_list_object_in_tools_py():
    """tools.py returns the input unchanged in build mode — assert identity,
    since a caller may rely on getting its own list back."""
    tool_list = [FakeTool("bash")]
    assert tools_mod.filter_tools_for_mode(tool_list, "build") is tool_list


@pytest.mark.parametrize("mode", ["PLANN", "plann", "", "unknown", "read"])
def test_unknown_mode_fails_closed(filter_fn, mode):
    """A typo'd mode must not hand back the full toolset."""
    assert names(filter_fn([FakeTool("bash"), FakeTool("write_file")], mode)) == []


def test_empty_tool_list(filter_fn):
    assert filter_fn([], "plan") == []
    assert filter_fn([], "build") == []


# ---------------------------------------------------------------------------
# plan mode
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", BLOCKED_IN_BOTH)
def test_core_mutating_tools_are_unbound_in_plan_mode(filter_fn, name):
    """The core guarantee: write, edit, and shell are never passed to the
    model, so it cannot call them even if it tries."""
    assert filter_fn([FakeTool(name)], "plan") == []


def test_bash_is_blocked_even_for_read_only_commands(filter_fn):
    """`ls` is harmless, but plan mode blocks bash outright rather than
    parsing each command — assert the blanket block is real."""
    assert filter_fn([FakeTool("bash")], "plan") == []


@pytest.mark.parametrize("name", READ_ONLY)
def test_read_only_tools_survive_plan_mode(filter_fn, name):
    assert names(filter_fn([FakeTool(name)], "plan")) == [name]


@pytest.mark.parametrize("name", ["git_status", "git_diff", "git_log"])
def test_non_mutating_mcp_tools_survive_plan_mode(filter_fn, name):
    assert names(filter_fn([FakeTool(name)], "plan")) == [name]


@pytest.mark.parametrize("name", ["git_commit", "git_push", "git_create_branch"])
def test_mutating_mcp_tools_are_dropped_in_plan_mode(filter_fn, name):
    """The MCP heuristic is keyword-based, so these three are caught."""
    assert filter_fn([FakeTool(name)], "plan") == []


def test_filtering_does_not_mutate_the_input_list(filter_fn):
    original = [FakeTool("bash"), FakeTool("read_file")]
    snapshot = names(original)
    filter_fn(original, "plan")
    assert names(original) == snapshot


def test_plan_mode_never_returns_an_empty_safe_subset(filter_fn):
    """Sanity: the agent can still read things in plan mode."""
    assert names(filter_fn([FakeTool("read_file")], "plan")) == ["read_file"]


# ---------------------------------------------------------------------------
# leaks: tools that stay bound in plan mode
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    reason="BUG: modes.py's PLAN_MODE_BLOCKED_EXACT omits 'run_tests' and none "
           "of PLAN_MODE_BLOCKED_KEYWORDS match it. tools.py blocks it "
           "explicitly. run_tests executes an arbitrary shell command in the "
           "sandbox, so plan mode's 'you literally cannot run anything that "
           "writes to disk or mutates state' claim is broken for the "
           "implementation main.py actually imports.",
)
def test_known_bug_run_tests_leaks_into_plan_mode_in_modes_py():
    assert modes.filter_tools_for_mode([FakeTool("run_tests")], "plan") == []


@pytest.mark.xfail(
    reason="BUG: tools.py's plan filter is a safe-list plus a keyword scan, but "
           "the keyword list (commit, push, reset, ..., write, create) matches "
           "none of these three names, so they fall through the 'likely an MCP "
           "tool, allow it' branch and stay bound. modes.py blocks all three by "
           "name, which is the behaviour the plan-mode docs promise.",
)
@pytest.mark.parametrize("name", ["start_background", "kill_background", "undo_last_change"])
def test_known_bug_background_and_undo_tools_leak_into_plan_mode_in_tools_py(name):
    assert tools_mod.filter_tools_for_mode([FakeTool(name)], "plan") == []


def test_plan_mode_leak_matrix_is_documented():
    """Pin the exact per-implementation verdict for every local tool. When a
    filter is fixed, this table is the thing that has to change with it, and
    the diff makes it obvious which entries flipped."""
    expected = {
        "bash": (False, False),
        "read_file": (True, True),
        "write_file": (False, False),
        "list_dir": (True, True),
        "edit_file": (False, False),
        "run_tests": (True, False),              # leak in modes.py
        "start_background": (False, True),       # leak in tools.py
        "list_background": (True, True),
        "tail_logs": (True, True),
        "kill_background": (False, True),        # leak in tools.py
        "undo_last_change": (False, True),       # leak in tools.py
        "tavily_search": (True, True),
    }
    for name, (bound_in_modes, bound_in_tools) in expected.items():
        in_modes = name in names(modes.filter_tools_for_mode([FakeTool(name)], "plan"))
        in_tools = name in names(tools_mod.filter_tools_for_mode([FakeTool(name)], "plan"))
        assert (in_modes, in_tools) == (bound_in_modes, bound_in_tools), (
            f"{name}: plan-mode availability changed "
            f"(modes.py={'bound' if in_modes else 'blocked'}, "
            f"tools.py={'bound' if in_tools else 'blocked'}) — update this table"
        )


def test_the_two_filters_still_disagree(filter_fn=None):
    """The root cause of the leaks above: two copies of the same policy, and
    they are not equivalent. Whichever is used, the guarantee differs."""
    every = [FakeTool(n) for n in READ_ONLY + MUTATING + MCP_TOOLS]
    by_modes = set(names(modes.filter_tools_for_mode(every, "plan")))
    by_tools = set(names(tools_mod.filter_tools_for_mode(every, "plan")))
    assert by_modes != by_tools
    assert by_modes - by_tools == {"run_tests"}
    assert by_tools - by_modes == {"start_background", "kill_background", "undo_last_change"}


# ---------------------------------------------------------------------------
# fail-closed behaviour for unrecognised tool objects
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    reason="BUG: neither filter fails closed for a tool whose name is missing. "
           "modes.py does t.name.lower() and raises AttributeError; tools.py "
           "uses getattr(t, 'name', '') which yields '' — matching no keyword, "
           "so an unrecognisable tool is treated as a safe MCP read tool and "
           "bound to the model.",
)
def test_known_bug_nameless_tool_does_not_fail_closed():
    class Nameless:
        pass

    for filter in (modes.filter_tools_for_mode, tools_mod.filter_tools_for_mode):
        assert filter([Nameless()], "plan") == []


# ---------------------------------------------------------------------------
# consistency with the shipped LOCAL_TOOLS
# ---------------------------------------------------------------------------

def test_local_tools_match_the_names_used_in_the_tests():
    """This file hardcodes tool names, and so do both filters' blocklists. If
    LOCAL_TOOLS or SEARCH_TOOLS ever gains, loses, or renames one, this fails —
    which is the point, since a rename silently changes plan-mode
    availability. glob/grep live in SEARCH_TOOLS, not LOCAL_TOOLS."""
    from search import SEARCH_TOOLS

    assert {t.name for t in LOCAL_TOOLS} == (
        set(READ_ONLY) | set(MUTATING)
    ) - {"glob", "grep"}
    assert {t.name for t in SEARCH_TOOLS} == {"glob", "grep"}


def test_modes_exact_blocklist_contains_everything_it_claims():
    assert modes.PLAN_MODE_BLOCKED_EXACT == set(BLOCKED_IN_BOTH) | {
        "start_background", "kill_background", "undo_last_change",
    }


def test_tools_safe_list_is_exactly_the_read_only_core():
    assert tools_mod._PLAN_SAFE_LOCAL_NAMES == {"read_file", "list_dir", "tavily_search"}


# ---------------------------------------------------------------------------
# mode_system_note
# ---------------------------------------------------------------------------

def test_plan_note_explains_the_restriction_and_honesty_rule():
    note = modes.mode_system_note("plan")
    assert "PLAN MODE" in note
    assert "not available" in note
    assert "Do not claim to have made changes you did not actually make" in note


def test_build_note_describes_full_access():
    note = modes.mode_system_note("build")
    assert "BUILD MODE" in note
    assert "full access" in note


def test_notes_are_suffixed_not_replacements():
    """Both notes start with newlines so they append to a system prompt rather
    than replacing it."""
    for mode in ("plan", "build"):
        assert modes.mode_system_note(mode).startswith("\n\n")
