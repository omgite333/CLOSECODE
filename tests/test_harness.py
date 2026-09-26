"""Tests for harness.py — the sandbox, the file tools, and undo.

The sandbox tests are the most load-bearing here: resolve_path is the single
choke point that keeps a model-supplied path inside the working directory, and
its comment records a real bug (absolute paths silently replacing the sandbox
root). Each escape route gets its own test.
"""

import os
import time

import pytest

from harness import Harness, PermissionDenied


# ---------------------------------------------------------------------------
# resolve_path: the sandbox boundary
# ---------------------------------------------------------------------------

def test_relative_path_resolves_inside_workdir(harness):
    target = harness.resolve_path("src/main.py")
    assert target == harness.workdir / "src" / "main.py"


@pytest.mark.parametrize(
    "supplied",
    [
        "main.py",
        "./main.py",
        "/main.py",            # models think "/x" means "project root"
        "//main.py",
        "\\\\main.py",
        "C:/main.py",          # windows drive prefix
        "c:main.py",
    ],
)
def test_leading_slashes_and_drive_prefixes_stay_relative(harness, supplied):
    """These are the phrasings that previously escaped the sandbox."""
    target = harness.resolve_path(supplied)
    assert target.parent == harness.workdir
    assert target.name == "main.py"


def test_backslashes_are_normalized(harness):
    target = harness.resolve_path("src\\pkg\\mod.py")
    assert target == harness.workdir / "src" / "pkg" / "mod.py"


@pytest.mark.parametrize(
    "escape",
    [
        "../outside.txt",
        "../../etc/passwd",
        "a/../../escape.txt",
        "..",
        "nested/../../../escape.txt",
    ],
)
def test_parent_traversal_is_denied(harness, escape):
    with pytest.raises(PermissionDenied):
        harness.resolve_path(escape)


def test_sibling_directory_never_reaches_its_real_location(harness, workdir):
    """'/tmp/x/sandbox-evil' is not inside '/tmp/x/sandbox'. A naive
    startswith() check would wave it through; resolve_path strips the leading
    slash instead, so the path is re-rooted under the sandbox and the real
    sibling is never touched."""
    sibling = workdir.parent / (workdir.name + "-evil")
    sibling.mkdir()
    (sibling / "file.txt").write_text("not yours")

    target = harness.resolve_path(str(sibling / "file.txt"))
    assert harness.workdir in target.parents
    assert target != sibling / "file.txt"


def test_symlink_out_of_the_sandbox_is_denied(harness, workdir):
    """resolve() follows symlinks, so a link planted inside the sandbox that
    points outside it must still be caught."""
    outside = workdir.parent / "outside_dir"
    outside.mkdir()
    (workdir / "link").symlink_to(outside)
    with pytest.raises(PermissionDenied):
        harness.resolve_path("link/file.txt")


def test_workdir_itself_is_allowed(harness):
    assert harness.resolve_path(".") == harness.workdir


def test_absolute_workdir_path_is_rerooted_not_denied(harness):
    """A model that echoes back the real absolute sandbox path shouldn't be
    rejected — the same normalization that makes '/main.py' safe applies here,
    and the result still lands somewhere inside the sandbox."""
    target = harness.resolve_path(str(harness.workdir / "main.py"))
    assert harness.workdir in target.parents
    assert str(harness.workdir) not in str(target.relative_to(harness.workdir))


def test_sandbox_root_is_created_if_missing(tmp_path):
    target = tmp_path / "fresh"
    h = Harness(str(target), auto_approve=True)
    assert target.is_dir()


# ---------------------------------------------------------------------------
# read_file / write_file / list_dir
# ---------------------------------------------------------------------------

def test_write_then_read_roundtrip(harness, workdir):
    assert "Wrote" in harness.write_file("notes.txt", "hello sandbox")
    assert harness.read_file("notes.txt") == "hello sandbox"
    assert (workdir / "notes.txt").read_text() == "hello sandbox"


def test_write_file_creates_parent_directories(harness, workdir):
    harness.write_file("a/b/c/deep.txt", "nested")
    assert (workdir / "a/b/c/deep.txt").read_text() == "nested"


def test_write_file_overwrites_existing_content(harness, workdir):
    harness.write_file("f.txt", "first")
    harness.write_file("f.txt", "second")
    assert (workdir / "f.txt").read_text() == "second"


def test_read_missing_file_reports_not_found(harness):
    assert harness.read_file("nope.txt") == "File not found: nope.txt"


def test_read_directory_reports_not_a_file(harness, workdir):
    (workdir / "adir").mkdir()
    assert harness.read_file("adir") == "Not a file: adir"


def test_read_outside_sandbox_is_denied(harness):
    assert "escapes the sandboxed working directory" in harness.read_file("../secrets.env")


def test_read_is_truncated_to_8000_chars(harness):
    harness.write_file("big.txt", "x" * 20_000)
    assert len(harness.read_file("big.txt")) == 8000


def test_list_dir_marks_subdirectories(harness, workdir):
    (workdir / "sub").mkdir()
    (workdir / "file.txt").write_text("x")
    listing = harness.list_dir()
    assert "file.txt" in listing
    assert "sub/" in listing


def test_list_dir_sorts_directories_first_then_alphabetically(harness, workdir):
    """Sort key is (is_file, name), so directories lead and each group is
    alphabetical — a stable, scannable listing for the model."""
    (workdir / "zzz_dir").mkdir()
    (workdir / "bbb_file.txt").write_text("x")
    (workdir / "aaa_file.txt").write_text("x")
    assert harness.list_dir().splitlines() == ["zzz_dir/", "aaa_file.txt", "bbb_file.txt"]


def test_list_empty_directory(harness, workdir):
    assert harness.list_dir() == "(empty directory)"


def test_list_missing_path(harness):
    assert harness.list_dir("nope") == "Path not found: nope"


def test_list_a_file(harness, workdir):
    (workdir / "f.txt").write_text("x")
    assert harness.list_dir("f.txt") == "Not a directory: f.txt"


def test_list_dir_outside_sandbox_denied(harness):
    assert "escapes the sandboxed working directory" in harness.list_dir("..")


# ---------------------------------------------------------------------------
# permission gating
# ---------------------------------------------------------------------------

def test_write_is_denied_when_user_declines(harness_factory, deny_all, workdir):
    h = harness_factory(auto_approve=False, confirm_fn=deny_all)
    assert h.write_file("denied.txt", "nope") == "Permission denied by user."
    assert not (workdir / "denied.txt").exists()


def test_always_grants_auto_approve_for_the_rest_of_the_session(harness_factory, workdir):
    calls = []

    def confirm(action, details=None):
        calls.append(action)
        return "always" if len(calls) == 1 else "deny"

    h = harness_factory(auto_approve=False, confirm_fn=confirm)
    h.write_file("one.txt", "a")
    h.write_file("two.txt", "b")
    assert len(calls) == 1
    assert h.auto_approve is True
    assert (workdir / "two.txt").exists()


def test_auto_approve_skips_the_prompt_entirely(harness_factory, allow_all, workdir):
    calls = []
    h = harness_factory(
        auto_approve=True, confirm_fn=lambda a, d=None: calls.append(a) or "deny"
    )
    h.write_file("auto.txt", "written")
    assert calls == []
    assert (workdir / "auto.txt").exists()


def test_single_arg_confirm_fallback(harness_factory, workdir):
    """Frontends whose confirm() only takes a question must still work —
    harness detects the arity and calls it with one arg."""
    seen = []

    def confirm(action):
        seen.append(action)
        return "allow"

    h = harness_factory(auto_approve=False, confirm_fn=confirm)
    h.write_file("classic.txt", "x")
    assert len(seen) == 1
    assert (workdir / "classic.txt").exists()


def test_write_prompt_carries_a_diff(harness_factory, allow_all, workdir):
    captured = {}

    def confirm(action, details=None):
        captured["action"] = action
        captured["details"] = details
        return "allow"

    h = harness_factory(auto_approve=False, confirm_fn=confirm)
    (workdir / "f.py").write_text("print(1)\n")
    h.write_file("f.py", "print(2)\n")

    details = captured["details"]
    assert details["kind"] == "diff"
    assert details["path"] == "f.py"
    assert details["new_file"] is False
    assert details["stat"] == "+1 \u22121"
    assert any(l.startswith("+print(2)") for l in details["lines"])


def test_new_file_diff_is_flagged(harness_factory, allow_all):
    captured = {}
    h = harness_factory(
        auto_approve=False, confirm_fn=lambda a, d=None: captured.update(d or {}) or "allow"
    )
    h.write_file("brand_new.py", "x = 1\n")
    assert captured["new_file"] is True


def test_no_diff_computed_when_auto_approving(harness):
    """auto_approve short-circuits before the prompt, so the read + diff that
    enriches it is skipped entirely — nothing is wasted on a prompt nobody
    will see."""
    calls = []

    h = Harness(str(harness.workdir), auto_approve=True,
                confirm_fn=lambda a, d=None: calls.append((a, d)) or "deny")
    h.write_file("f.txt", "x")
    h.edit_file("f.txt", "x", "y")
    assert calls == []


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------

def test_edit_replaces_single_occurrence(harness, workdir):
    (workdir / "f.py").write_text("value = 1\nother = 2\n")
    assert "Replaced 1 occurrence" in harness.edit_file("f.py", "value = 1", "value = 42")
    assert (workdir / "f.py").read_text() == "value = 42\nother = 2\n"


def test_edit_missing_anchor_changes_nothing(harness, workdir):
    (workdir / "f.py").write_text("original\n")
    out = harness.edit_file("f.py", "nonexistent", "replacement")
    assert "not found" in out
    assert (workdir / "f.py").read_text() == "original\n"


def test_edit_ambiguous_anchor_is_refused(harness, workdir):
    """The whole point of edit_file is failing safely instead of clobbering
    the wrong occurrence — assert it refuses when the anchor isn't unique."""
    (workdir / "dup.py").write_text("x = 1\nx = 1\n")
    out = harness.edit_file("dup.py", "x = 1", "x = 2")
    assert "appears 2 times" in out
    assert "No changes made" in out
    assert (workdir / "dup.py").read_text() == "x = 1\nx = 1\n"


def test_edit_missing_file(harness):
    assert harness.edit_file("ghost.py", "a", "b") == "File not found: ghost.py"


def test_edit_outside_sandbox_denied(harness):
    assert "escapes the sandboxed working directory" in harness.edit_file("../x", "a", "b")


# ---------------------------------------------------------------------------
# undo / checkpoints
# ---------------------------------------------------------------------------

def test_undo_restores_previous_content(harness, workdir):
    (workdir / "f.py").write_text("v1\n")
    harness.write_file("f.py", "v2\n")
    out = harness.undo_last()
    assert "Undid 1 change(s)" in out
    assert (workdir / "f.py").read_text() == "v1\n"


def test_undo_deletes_files_the_agent_created(harness, workdir):
    harness.write_file("new.txt", "created by agent")
    assert (workdir / "new.txt").exists()
    harness.undo_last()
    assert not (workdir / "new.txt").exists()


def test_undo_is_lifo_and_supports_n(harness, workdir):
    (workdir / "f.txt").write_text("original\n")
    harness.write_file("f.txt", "second\n")
    harness.write_file("other.txt", "temp\n")
    out = harness.undo_last(2)
    assert "Undid 2 change(s)" in out
    assert not (workdir / "other.txt").exists()
    assert (workdir / "f.txt").read_text() == "original\n"


def test_undo_clamps_to_what_was_recorded(harness, workdir):
    harness.write_file("only.txt", "x")
    out = harness.undo_last(10)
    assert "Undid 1 change(s)" in out
    assert "that was everything recorded" in out


def test_undo_with_empty_stack(harness):
    assert harness.undo_last() == "Nothing to undo \u2014 no file changes have been recorded."


def test_undo_rejects_nonsense_n(harness):
    harness.write_file("f.txt", "x")
    assert harness.undo_last("many").startswith("Usage: /undo")


def test_undo_reverts_edits_too(harness, workdir):
    (workdir / "f.py").write_text("alpha\n")
    harness.edit_file("f.py", "alpha", "beta")
    harness.undo_last()
    assert (workdir / "f.py").read_text() == "alpha\n"


def test_checkpoints_survive_a_new_harness_instance(harness, workdir):
    """The manifest lives on disk precisely so /undo works across restarts."""
    (workdir / "f.txt").write_text("before\n")
    harness.write_file("f.txt", "after\n")

    fresh = Harness(str(workdir), auto_approve=True)
    fresh.undo_last()
    assert (workdir / "f.txt").read_text() == "before\n"


def test_corrupt_checkpoint_manifest_is_ignored(workdir):
    (workdir / ".closecode" / "checkpoints").mkdir(parents=True)
    (workdir / ".closecode" / "checkpoints" / "manifest.json").write_text("{not json")
    h = Harness(str(workdir), auto_approve=True)
    assert h.undo_last().startswith("Nothing to undo")


def test_checkpoint_snapshot_files_are_pruned(workdir):
    """Entries whose snapshot file vanished must not be replayed — undo would
    otherwise write a file from a missing source."""
    ckdir = workdir / ".closecode" / "checkpoints"
    ckdir.mkdir(parents=True)
    (ckdir / "manifest.json").write_text(
        '[{"seq": 1, "path": "gone.txt", "op": "write", "ts": 0, "existed": true, "snapshot": "000001.old"}]'
    )
    h = Harness(str(workdir), auto_approve=True)
    assert h.undo_last().startswith("Nothing to undo")


def test_checkpoint_cap_is_enforced(workdir):
    from harness import _CHECKPOINT_CAP

    h = Harness(str(workdir), auto_approve=True)
    for i in range(_CHECKPOINT_CAP + 5):
        h.write_file(f"f{i}.txt", "x")
    assert len(h._undo_stack) == _CHECKPOINT_CAP
    # Snapshots of pruned entries are deleted, so the cap also bounds disk use.
    snapshots = list((workdir / ".closecode" / "checkpoints").glob("*.old"))
    assert len(snapshots) <= _CHECKPOINT_CAP


# ---------------------------------------------------------------------------
# run_bash
# ---------------------------------------------------------------------------

def test_run_bash_returns_stdout(harness, workdir):
    (workdir / "marker.txt").write_text("hi")
    assert "hi" in harness.run_bash("cat marker.txt")


def test_run_bash_merges_stderr(harness):
    assert "boom" in harness.run_bash("echo boom 1>&2")


def test_run_bash_runs_in_the_sandbox(harness, workdir):
    (workdir / "only_here.txt").write_text("x")
    out = harness.run_bash("ls")
    assert "only_here.txt" in out
    assert ".closecode" in out or "only_here.txt" in out


def test_run_bash_no_output_placeholder(harness):
    assert harness.run_bash("true") == "(no output)"


def test_run_bash_output_is_capped_at_4000_chars(harness):
    out = harness.run_bash("python3 -c \"print('y' * 10000)\"")
    assert len(out) == 4000


def test_run_bash_denied_by_user(harness_factory, deny_all):
    assert harness_factory(auto_approve=False, confirm_fn=deny_all).run_bash("echo hi") == "Permission denied by user."


def test_run_bash_timeout_kills_the_process_group(harness, workdir):
    """A timed-out command must not keep running in the background — the
    timeout has to take the whole process group down."""
    out = harness.run_bash("sleep 30", timeout=1)
    assert "timed out after 1s" in out
    assert "start_background" in out


def test_run_bash_timeout_kills_children(harness, workdir):
    """The child-sleep case: `sleep 30` survives a naive kill of the shell."""
    marker = workdir / "still_running"
    h = harness
    out = h.run_bash(f"sleep 4; touch {marker}", timeout=1)
    assert "timed out" in out
    time.sleep(4)
    assert not marker.exists(), "child process outlived the timeout"


# ---------------------------------------------------------------------------
# run_tests
# ---------------------------------------------------------------------------

def test_run_tests_reports_pass(harness, workdir):
    (workdir / "ok.sh").write_text("#!/bin/sh\necho 1 passed\n")
    out = harness.run_tests("sh ok.sh")
    assert out.startswith("PASSED")
    assert "1 passed" in out


def test_run_tests_reports_fail_with_exit_code(harness, workdir):
    (workdir / "bad.sh").write_text("#!/bin/sh\necho 2 failed\nexit 1\n")
    out = harness.run_tests("sh bad.sh")
    assert out.startswith("FAILED (exit code 1)")
    assert "2 failed" in out


def test_run_tests_includes_stderr(harness, workdir):
    (workdir / "err.sh").write_text("#!/bin/sh\necho 'error detail' 1>&2\n")
    assert "error detail" in harness.run_tests("sh err.sh")


def test_run_tests_guardrail_blocks_destructive_command(harness):
    assert "Guardrail blocked" in harness.run_tests("rm -rf /")


def test_run_tests_denied_by_user(harness_factory, deny_all):
    assert harness_factory(auto_approve=False, confirm_fn=deny_all).run_tests("echo x") == "Permission denied by user."


# ---------------------------------------------------------------------------
# background processes
# ---------------------------------------------------------------------------

def _bg_entry(harness, proc_id):
    return harness._bg[proc_id]


def test_background_process_starts_and_reports_id(harness, workdir):
    out = harness.start_background("sleep 5", label="sleeper")
    assert "bg1" in out
    assert _bg_entry(harness, "bg1")["label"] == "sleeper"
    harness.kill_background("bg1")


def test_background_process_writes_to_a_log(harness):
    out = harness.start_background("echo hello-from-bg")
    proc_id = out.split()[3].rstrip(":")
    for _ in range(50):
        if "hello-from-bg" in harness.tail_logs(proc_id):
            break
        time.sleep(0.1)
    assert "hello-from-bg" in harness.tail_logs(proc_id)
    harness.kill_background(proc_id)


def test_list_background_reports_state(harness):
    harness.start_background("sleep 5", label="tagged")
    listing = harness.list_background()
    assert "bg1" in listing
    assert "tagged" in listing
    assert "running" in listing
    harness.kill_background("bg1")


def test_list_background_when_empty(harness):
    assert harness.list_background() == "No background processes."


def test_tail_logs_of_unknown_process(harness):
    assert "No background process 'nope'" in harness.tail_logs("nope")


def test_tail_logs_survives_restart(harness, workdir):
    """Logs are on disk, so a previous session's output is still readable."""
    harness.start_background("echo persisted")
    time.sleep(0.5)
    fresh = Harness(str(workdir), auto_approve=True)
    assert "persisted" in fresh.tail_logs("bg1")


def test_tail_logs_line_cap(harness):
    """tail_logs defaults to 30 lines and hard-caps at 200, and reports how
    many lines the log actually has so the model knows it's seeing a slice."""
    out = harness.start_background("for i in $(seq 1 50); do echo line$i; done")
    proc_id = out.split()[3].rstrip(":")

    for _ in range(50):
        body = harness.tail_logs(proc_id)
        if "of 50 line(s)" in body:
            break
        time.sleep(0.1)

    default_view = harness.tail_logs(proc_id)
    assert "last 30 of 50 line(s)" in default_view
    assert len(default_view.split("\n")[1:]) == 30

    assert "last 5 of 50 line(s)" in harness.tail_logs(proc_id, lines=5)
    # A value over the 200 cap is clamped, not honoured.
    assert "last 50 of 50 line(s)" in harness.tail_logs(proc_id, lines=5000)


def test_kill_background_stops_the_process(harness):
    out = harness.start_background("sleep 60")
    proc_id = out.split()[3].rstrip(":")
    entry = _bg_entry(harness, proc_id)
    killed = harness.kill_background(proc_id)
    assert "terminated" in killed
    assert entry["proc"].poll() is not None
    assert proc_id not in harness._bg


def test_kill_background_reports_already_exited(harness):
    out = harness.start_background("true")
    proc_id = out.split()[3].rstrip(":")
    time.sleep(0.5)
    assert "already exited" in harness.kill_background(proc_id)


def test_kill_unknown_process(harness):
    assert harness.kill_background("bg999") == "No background process 'bg999'."


def test_kill_background_escalates_to_sigkill(harness, workdir):
    """A process that ignores SIGTERM must still die: killpg(SIGTERM), wait 3s,
    then killpg(SIGKILL). Assert the escalation and that the process is really
    gone."""
    script = workdir / "stubborn.py"
    marker = workdir / "ready"
    script.write_text(
        "import signal, time, pathlib, sys\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "pathlib.Path(sys.argv[1]).write_text('1')\n"
        "time.sleep(60)\n"
    )
    out = harness.start_background(f"python3 stubborn.py {marker}")
    proc_id = out.split()[3].rstrip(":")
    entry = _bg_entry(harness, proc_id)

    for _ in range(100):  # wait for the handler to be installed
        if marker.exists():
            break
        time.sleep(0.05)

    killed = harness.kill_background(proc_id)
    assert "did not stop on SIGTERM; killed" in killed
    assert entry["proc"].poll() is not None
    assert proc_id not in harness._bg


def test_background_start_is_blocked_by_guardrails(harness):
    out = harness.start_background("rm -rf /")
    assert "Guardrail blocked" in out
    assert harness._bg == {}


def test_background_start_denied_by_user(harness_factory, deny_all):
    h = harness_factory(auto_approve=False, confirm_fn=deny_all)
    assert h.start_background("sleep 5") == "Permission denied by user."
    assert h._bg == {}


def test_background_label_defaults_to_command(harness):
    harness.start_background("sleep 5")
    assert _bg_entry(harness, "bg1")["label"] == "sleep 5"
    harness.kill_background("bg1")


def test_background_log_is_kept_after_kill(harness):
    out = harness.start_background("echo byebye")
    proc_id = out.split()[3].rstrip(":")
    time.sleep(0.5)
    harness.kill_background(proc_id)
    assert "byebye" in harness.tail_logs(proc_id)


# ---------------------------------------------------------------------------
# diff helpers
# ---------------------------------------------------------------------------

def test_unified_diff_stat_counts_additions_and_removals():
    from harness import _unified_diff_lines

    lines, total, stat = _unified_diff_lines("a\nb\nc\n", "a\nB\nc\n", "f.txt")
    assert stat == "+1 \u22121"
    assert total > 0
    assert any(l.startswith("-b") for l in lines)


def test_diff_caps_lines_but_reports_true_total():
    from harness import _DIFF_LINE_CAP, _unified_diff_lines

    old = "\n".join(str(i) for i in range(500))
    new = "\n".join(str(i * 2) for i in range(500))
    lines, total, _ = _unified_diff_lines(old, new, "big.txt")
    assert len(lines) == _DIFF_LINE_CAP
    assert total > _DIFF_LINE_CAP


def test_new_file_diff_has_no_removals():
    from harness import _unified_diff_lines

    _, _, stat = _unified_diff_lines("", "x = 1\n", "new.py")
    assert stat == "+1 \u22120"


# ---------------------------------------------------------------------------
# integration: escape attempt end-to-end
# ---------------------------------------------------------------------------

def test_write_file_cannot_escape_via_absolute_path(harness, workdir):
    """The original bug: '/etc/evil.txt' as a model-supplied path landed on
    the host, not in the sandbox."""
    outside = workdir.parent / "escaped.txt"
    out = harness.write_file("/escaped.txt", "should land in sandbox")
    assert "Wrote" in out
    assert not outside.exists()
    assert (workdir / "escaped.txt").read_text() == "should land in sandbox"


def test_shell_sees_files_written_by_write_file(harness, workdir):
    """The two tool families must agree on the root, or the agent writes a
    file and then can't find it."""
    harness.write_file("visible.txt", "found me")
    assert "visible.txt" in harness.run_bash("ls")
    assert (workdir / "visible.txt").exists()
