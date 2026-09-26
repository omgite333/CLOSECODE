
import difflib
import inspect
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from guardrails import check_bash_command, check_write_content


class PermissionDenied(Exception):
    pass


# ---------------------------------------------------------------------------
# checkpoints (undo)
# ---------------------------------------------------------------------------

# Max undo entries kept per working directory. Oldest snapshots are pruned
# (and their files deleted) past this cap.
_CHECKPOINT_CAP = 50


def _fmt_ts(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


# ---------------------------------------------------------------------------
# diffs for permission prompts
# ---------------------------------------------------------------------------

_DIFF_CONTEXT_LINES = 3
_DIFF_LINE_CAP = 80  # max diff lines shipped to the frontend; the UI notes the rest


def _unified_diff_lines(old: str, new: str, path: str) -> tuple:
    """Unified diff of two file contents.

    Returns (lines, total, stat): `lines` capped at _DIFF_LINE_CAP,
    `total` the uncapped line count (so the UI can say "… N more lines"),
    `stat` like "+12 −4".
    """
    raw = list(
        difflib.unified_diff(
            old.splitlines(), new.splitlines(),
            fromfile=f"a/{path}", tofile=f"b/{path}",
            lineterm="", n=_DIFF_CONTEXT_LINES,
        )
    )
    added = sum(1 for l in raw if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in raw if l.startswith("-") and not l.startswith("---"))
    return raw[:_DIFF_LINE_CAP], len(raw), f"+{added} −{removed}"


def _diff_details(path: str, old: str, new: str, new_file: bool) -> dict:
    """Payload handed to the confirm frontend so it can show the diff
    before the user approves. `kind` lets frontends switch rendering."""
    lines, total, stat = _unified_diff_lines(old, new, path)
    return {
        "kind": "diff",
        "path": path,
        "lines": lines,
        "total_lines": total,
        "stat": stat,
        "new_file": new_file,
    }


class Harness:
    def __init__(
        self,
        workdir: str,
        auto_approve: bool = False,
        confirm_fn: Optional[Callable[..., str]] = None,
    ):
        self.workdir = Path(workdir).resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.auto_approve = auto_approve
        # Defaults to plain input() if no styled confirm function is given —
        # keeps Harness usable standalone without depending on ui.py.
        # Signature: confirm_fn(question, details) -> "allow" | "always" | "deny".
        # `details` is an optional dict the frontend uses for richer prompts
        # (e.g. {"kind": "diff", ...} for file writes); None = plain prompt.
        self._confirm_fn = confirm_fn or (
            lambda action, details=None: "allow" if input(f"\n[permission] Allow agent to {action}? [y/N] ").strip().lower() in ("y", "yes") else "deny"
        )
        # Some frontends' confirm only takes (question) — e.g. the stock
        # classic ui.confirm. Detect once so we never pass an arg it can't
        # handle; details-aware frontends get the richer prompt.
        try:
            self._confirm_takes_details = len(inspect.signature(self._confirm_fn).parameters) >= 2
        except (TypeError, ValueError):
            self._confirm_takes_details = False
        # Undo checkpoints: snapshots of file content before every mutation,
        # persisted under .closecode/checkpoints so /undo survives restarts.
        self._ckdir = self.workdir / ".closecode" / "checkpoints"
        self._undo_stack: list = self._load_checkpoints()
        # Background processes: id -> {label, command, proc, log, log_path,
        # started, reaped}. Processes outlive the session; kill explicitly.
        self._bgdir = self.workdir / ".closecode" / "bg"
        self._bg: dict = {}
        self._bg_counter = 0

    def confirm(self, action_description: str, details: dict | None = None) -> bool:
        if self.auto_approve:
            return True
        if self._confirm_takes_details:
            result = self._confirm_fn(action_description, details)
        else:
            # Frontend only understands the plain prompt (e.g. stock
            # classic ui.confirm) — diff details are silently skipped.
            result = self._confirm_fn(action_description)
        if result == "always":
            self.auto_approve = True
            return True
        return result == "allow"

    def resolve_path(self, relative_path: str) -> Path:
        # Normalize before joining. Models very commonly pass absolute-looking
        # paths (e.g. "/my_project/main.py" — they're thinking "project root",
        # not "host filesystem root"). That's a real problem with pathlib:
        # Path("/sandbox") / "/my_project/main.py" DISCARDS the left side
        # entirely (joining an absolute path onto another replaces it), so
        # the join silently points outside the sandbox instead of into it.
        # This is exactly what caused write_file to appear to create files
        # while bash (which correctly uses the same self.workdir) saw an
        # empty directory — write_file's target had quietly become
        # `/my_project/main.py` on the host, not `<workdir>/my_project/main.py`.
        #
        # Fix: strip any leading slash/backslash and any Windows drive
        # prefix before joining, so every path is treated as relative to
        # the sandbox no matter how the model phrased it. bash and every
        # file tool then agree on the exact same root in every case.
        cleaned = relative_path.replace("\\", "/").lstrip("/")
        if len(cleaned) > 1 and cleaned[1] == ":":  # e.g. "C:/foo" or "C:foo"
            cleaned = cleaned[2:].lstrip("/")

        target = (self.workdir / cleaned).resolve()
        if self.workdir != target and self.workdir not in target.parents:
            raise PermissionDenied(f"Path '{relative_path}' escapes the sandboxed working directory.")
        return target

    @staticmethod
    def _read_for_diff(target: Path) -> tuple:
        """(old_content, is_new_file) for the pre-approval diff. Never
        raises — an unreadable path is treated as a new file."""
        try:
            if target.is_file():
                return target.read_text(errors="replace"), False
        except OSError:
            pass
        return "", True

    @staticmethod
    def _read_for_diff(target: Path) -> tuple:
        """(old_content, is_new_file) for the pre-approval diff. Never
        raises — an unreadable path is treated as a new file."""
        try:
            if target.is_file():
                return target.read_text(errors="replace"), False
        except OSError:
            pass
        return "", True

    # -- checkpoints ------------------------------------------------------
    def _load_checkpoints(self) -> list:
        manifest = self._ckdir / "manifest.json"
        try:
            entries = json.loads(manifest.read_text())
        except (OSError, ValueError):
            return []
        # Drop entries whose snapshot files vanished (manual cleanup etc.).
        live = [e for e in entries
                if not e.get("existed") or (self._ckdir / str(e.get("snapshot") or "")).is_file()]
        return live[-_CHECKPOINT_CAP:]

    def _save_checkpoints(self) -> None:
        try:
            self._ckdir.mkdir(parents=True, exist_ok=True)
            (self._ckdir / "manifest.json").write_text(json.dumps(self._undo_stack[-_CHECKPOINT_CAP:]))
        except OSError:
            pass

    def _push_undo(self, rel_path: str, old_bytes: bytes | None, op: str) -> None:
        """Snapshot a file's prior content before mutating it.
        old_bytes=None means the file didn't exist (undo will delete it)."""
        seq = (self._undo_stack[-1]["seq"] + 1) if self._undo_stack else 1
        entry = {"seq": seq, "path": rel_path, "op": op, "ts": time.time(),
                 "existed": old_bytes is not None, "snapshot": None}
        if old_bytes is not None:
            try:
                self._ckdir.mkdir(parents=True, exist_ok=True)
                name = f"{seq:06d}.old"
                (self._ckdir / name).write_bytes(old_bytes)
                entry["snapshot"] = name
            except OSError:
                pass
        self._undo_stack.append(entry)
        while len(self._undo_stack) > _CHECKPOINT_CAP:
            dropped = self._undo_stack.pop(0)
            if dropped.get("snapshot"):
                try:
                    (self._ckdir / dropped["snapshot"]).unlink()
                except OSError:
                    pass
        self._save_checkpoints()

    def _apply_write(self, rel_path: str, new_content: str, op: str) -> None:
        """Snapshot prior content, then write. No permission prompt and no
        content guardrail here — the caller already approved and vetted.
        The sandbox path check still applies."""
        target = self.resolve_path(rel_path)
        try:
            old_bytes = target.read_bytes() if target.is_file() else None
        except OSError:
            old_bytes = None
        self._push_undo(rel_path, old_bytes, op)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new_content)

    def undo_last(self, n: int = 1) -> str:
        """Revert the last n file mutation(s) made through this harness
        (write_file/edit_file only — shell side effects can't be tracked).
        Undo itself is not recorded, so there is no redo."""
        try:
            n = max(1, int(n))
        except (TypeError, ValueError):
            return "Usage: /undo [n]  (n = how many changes to revert)"
        if not self._undo_stack:
            return "Nothing to undo — no file changes have been recorded."
        total = len(self._undo_stack)
        undone = []
        failed = None
        for _ in range(min(n, total)):
            entry = self._undo_stack.pop()
            try:
                target = self.resolve_path(entry["path"])
            except PermissionDenied as e:
                failed = f"{entry['path']}: {e}"
                self._undo_stack.append(entry)
                break
            try:
                if entry["existed"]:
                    snap = self._ckdir / str(entry["snapshot"])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(snap.read_bytes())
                    try:
                        snap.unlink()
                    except OSError:
                        pass
                    undone.append(f"\u21a9 {entry['path']} — restored content from {_fmt_ts(entry['ts'])} ({entry['op']})")
                else:
                    if target.is_file() or target.is_symlink():
                        target.unlink()
                    undone.append(f"\u21a9 {entry['path']} — deleted (it was created by the agent)")
            except OSError as e:
                failed = f"{entry['path']}: {e}"
                self._undo_stack.append(entry)
                break
        self._save_checkpoints()
        lines = [f"Undid {len(undone)} change(s):"] + [f"  {u}" for u in undone]
        if len(undone) < n and not failed:
            lines.append("  (that was everything recorded)")
        if failed:
            lines.append(f"Undo stopped: {failed}")
        return "\n".join(lines)

    # -- background processes ----------------------------------------------
    def start_background(self, command: str, label: str = "") -> str:
        """Start a shell command as a background process. Output streams to
        .closecode/bg/<id>.log. Returns the process id for tail/kill."""
        reason = check_bash_command(command)
        if reason:
            return (f"Guardrail blocked this command and it was not started:\n{reason}\n\n"
                    f"Ask the user to approve or reformulate it.")
        if not self.confirm(f"run in background: `{command}`"):
            return "Permission denied by user."
        self._bg_counter += 1
        bid = f"bg{self._bg_counter}"
        try:
            self._bgdir.mkdir(parents=True, exist_ok=True)
            log_path = self._bgdir / f"{bid}.log"
            logf = open(log_path, "ab", buffering=0)
        except OSError as e:
            return f"Could not start background process: {e}"
        try:
            proc = subprocess.Popen(
                command, shell=True, cwd=str(self.workdir),
                stdout=logf, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as e:
            try:
                logf.close()
            except OSError:
                pass
            return f"Could not start background process: {e}"
        self._bg[bid] = {"label": label or command[:40], "command": command,
                         "proc": proc, "log": logf, "log_path": log_path,
                         "started": time.time(), "reaped": False}
        return (f"Started background process {bid} (pid {proc.pid}): `{command}`\n"
                f"Logs: .closecode/bg/{bid}.log — use tail_logs(\"{bid}\") to read output, "
                f"list_background() for status, kill_background(\"{bid}\") to stop it.")

    def _bg_status(self, bid: str, entry: dict) -> str:
        rc = entry["proc"].poll()
        if rc is None:
            return "running"
        if not entry.get("reaped"):
            try:
                entry["log"].close()
            except OSError:
                pass
            entry["reaped"] = True
        return f"exited ({rc})"

    def list_background(self) -> str:
        if not self._bg:
            return "No background processes."
        lines = []
        for bid, e in self._bg.items():
            age = int(time.time() - e["started"])
            lines.append(f"{bid} [{e['label']}] · {self._bg_status(bid, e)} · "
                         f"pid {e['proc'].pid} · {age}s · `{e['command']}`")
        return "\n".join(lines)

    def tail_logs(self, proc_id: str, lines: int = 30) -> str:
        entry = self._bg.get(proc_id)
        if entry is None:
            p = self._bgdir / f"{proc_id}.log"
            if not p.is_file():
                return f"No background process '{proc_id}'. Use list_background() to see active ones."
            log_path, status = p, "unknown (from a previous session)"
        else:
            log_path, status = entry["log_path"], self._bg_status(proc_id, entry)
        n = max(1, min(int(lines), 200))
        try:
            data = log_path.read_bytes().decode("utf-8", errors="replace").splitlines()
        except OSError:
            data = []
        tail = data[-n:]
        body = "\n".join(tail) if tail else "(no output yet)"
        return f"[{proc_id} · {status}] last {len(tail)} of {len(data)} line(s):\n{body}"

    def kill_background(self, proc_id: str) -> str:
        entry = self._bg.get(proc_id)
        if entry is None:
            return f"No background process '{proc_id}'."
        proc = entry["proc"]
        if proc.poll() is not None:
            try:
                entry["log"].close()
            except OSError:
                pass
            del self._bg[proc_id]
            return f"{proc_id} already exited ({proc.returncode}). Log kept at .closecode/bg/{proc_id}.log."
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            rc = proc.wait(timeout=3)
            outcome = f"terminated (exit {rc})"
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            rc = proc.wait(timeout=5)
            outcome = f"did not stop on SIGTERM; killed (exit {rc})"
        try:
            entry["log"].close()
        except OSError:
            pass
        del self._bg[proc_id]
        return (f"{proc_id} {outcome}. Log kept at .closecode/bg/{proc_id}.log — "
                f"tail_logs(\"{proc_id}\") still works.")

    def run_bash(self, command: str, timeout: int = 30) -> str:
        reason = check_bash_command(command)
        if reason:
            return (
                f"Guardrail blocked this command: {reason}. It looks destructive or "
                "malicious, so it cannot run \u2014 even if approval were granted. "
                "Rephrase the command to do the same thing safely, or use a "
                "different approach."
            )
        if not self.confirm(f"run: `{command}`"):
            return "Permission denied by user."
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=self.workdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            output, _ = proc.communicate(timeout=timeout)
            output = (output or "").strip() or "(no output)"
            return output[-4000:]
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()
            return (
                f"Command timed out after {timeout}s and was terminated (including any "
                f"child processes it started). If this was meant to be a long-running "
                f"process (a server, a watcher), start it with the start_background "
                f"tool instead — then use tail_logs to watch its output."
            )

    def read_file(self, path: str) -> str:
        try:
            target = self.resolve_path(path)
        except PermissionDenied as e:
            return str(e)
        if not target.exists():
            return f"File not found: {path}"
        if not target.is_file():
            return f"Not a file: {path}"
        return target.read_text(errors="replace")[:8000]

    def write_file(self, path: str, content: str) -> str:
        reason = check_write_content(content)
        if reason:
            return (
                f"Guardrail refused to write this content: {reason} (malicious-code "
                "indicator). Policy forbids creating malware or exploit code "
                "\u2014 even inside the sandbox."
            )
        try:
            target = self.resolve_path(path)
        except PermissionDenied as e:
            return str(e)
        # Show the user the real diff before they approve — no more
        # blind "write N chars" prompts. Skipped when auto-approve is on
        # (no prompt to enrich, so no diff to compute).
        details = None
        if not self.auto_approve:
            old, is_new = self._read_for_diff(target)
            details = _diff_details(path, old, content, is_new)
        if not self.confirm(f"write {len(content)} chars to `{path}`", details):
            return "Permission denied by user."
        self._apply_write(path, content, "write")
        return f"Wrote {len(content)} chars to {path}"

    def list_dir(self, path: str = ".") -> str:
        try:
            target = self.resolve_path(path)
        except PermissionDenied as e:
            return str(e)
        if not target.exists():
            return f"Path not found: {path}"
        if not target.is_dir():
            return f"Not a directory: {path}"
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
        lines = []
        for entry in entries:
            marker = "/" if entry.is_dir() else ""
            lines.append(f"{entry.name}{marker}")
        return "\n".join(lines) if lines else "(empty directory)"

    def edit_file(self, path: str, old_text: str, new_text: str) -> str:
        """Targeted find-and-replace — far cheaper in tokens than rewriting
        a whole file via write_file, and safer since it fails loudly if the
        anchor text isn't found or isn't unique, instead of silently
        clobbering unrelated content."""
        reason = check_write_content(new_text)
        if reason:
            return (
                f"Guardrail refused this edit: {reason} (malicious-code "
                "indicator). Policy forbids creating malware or exploit code."
            )
        try:
            target = self.resolve_path(path)
        except PermissionDenied as e:
            return str(e)
        if not target.exists():
            return f"File not found: {path}"
        content = target.read_text(errors="replace")
        count = content.count(old_text)
        if count == 0:
            return f"'old_text' not found in {path}. No changes made — check for exact whitespace/formatting differences."
        if count > 1:
            return f"'old_text' appears {count} times in {path}. Make it more specific so the edit is unambiguous. No changes made."
        updated = content.replace(old_text, new_text, 1)
        # Diff the edit before asking — the prompt shows exactly what
        # will change, with surrounding context.
        details = None
        if not self.auto_approve:
            details = _diff_details(path, content, updated, False)
        if not self.confirm(f"replace one occurrence of text in `{path}`", details):
            return "Permission denied by user."
        self._apply_write(path, updated, "edit")
        return f"Replaced 1 occurrence in {path}."

    def run_tests(self, command: str = "pytest", timeout: int = 60) -> str:
        """Runs a test command (default: pytest) and returns its output.
        Separate from run_bash mainly so the model has a clearly-named,
        single-purpose action for 'verify my work' rather than free-form
        shell access every time."""
        reason = check_bash_command(command)
        if reason:
            return f"Guardrail blocked this test command: {reason}. It looks destructive or malicious and cannot run."
        if not self.confirm(f"run tests with: `{command}`"):
            return "Permission denied by user."
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = (result.stdout or "") + (result.stderr or "")
            output = output.strip() or "(no output)"
            status = "PASSED" if result.returncode == 0 else f"FAILED (exit code {result.returncode})"
            return f"{status}\n\n{output[-4000:]}"
        except subprocess.TimeoutExpired:
            return f"Test command timed out after {timeout}s."