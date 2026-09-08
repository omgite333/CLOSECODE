
import os
import signal
import subprocess
from pathlib import Path
from typing import Callable, Optional


class PermissionDenied(Exception):
    pass


class Harness:
    def __init__(
        self,
        workdir: str,
        auto_approve: bool = False,
        confirm_fn: Optional[Callable[[str], str]] = None,
    ):
        self.workdir = Path(workdir).resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.auto_approve = auto_approve
        # Defaults to plain input() if no styled confirm function is given —
        # keeps Harness usable standalone without depending on ui.py.
        self._confirm_fn = confirm_fn or (
            lambda action: "allow" if input(f"\n[permission] Allow agent to {action}? [y/N] ").strip().lower() in ("y", "yes") else "deny"
        )

    def confirm(self, action_description: str) -> bool:
        if self.auto_approve:
            return True
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

    def run_bash(self, command: str, timeout: int = 30) -> str:
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
                f"process (a server, a watcher), run it in the background instead — see "
                f"the system prompt for how."
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
        try:
            target = self.resolve_path(path)
        except PermissionDenied as e:
            return str(e)
        if not self.confirm(f"write {len(content)} chars to `{path}`"):
            return "Permission denied by user."
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
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
        if not self.confirm(f"replace one occurrence of text in `{path}`"):
            return "Permission denied by user."
        updated = content.replace(old_text, new_text, 1)
        target.write_text(updated)
        return f"Replaced 1 occurrence in {path}."

    def run_tests(self, command: str = "pytest", timeout: int = 60) -> str:
        """Runs a test command (default: pytest) and returns its output.
        Separate from run_bash mainly so the model has a clearly-named,
        single-purpose action for 'verify my work' rather than free-form
        shell access every time."""
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