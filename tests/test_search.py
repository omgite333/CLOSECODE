"""Tests for search.py — the glob and grep tools.

Both are read-only, so they're the tools plan mode relies on most; a bug here
either leaks the filesystem or breaks exploration. The sandbox boundary and
the skip-list are the two things worth pinning hard.
"""

import pytest

from search import MAX_LINE_LEN, MAX_RESULTS, SKIP_DIRS, bind_search_root, glob, grep


@pytest.fixture
def tree(search_root):
    """A small source tree to search.

    search_root/
      main.py          'import os' / 'def main():'
      README.md        'def main(): documentation'
      src/
        util.py        'import os' / 'TARGET = 1'
        data.json      '{"key": "value"}'
      node_modules/
        dep.js         'def main():' (must be skipped)
      .venv/
        lib.py         'def main():' (must be skipped)
      binary.bin       NUL bytes (must be skipped by grep)
    """
    (search_root / "src").mkdir()
    (search_root / "node_modules").mkdir()
    (search_root / ".venv").mkdir()
    (search_root / "main.py").write_text("import os\n\ndef main():\n    pass\n")
    (search_root / "README.md").write_text("def main(): documentation\n")
    (search_root / "src" / "util.py").write_text("import os\n\nTARGET = 1\n")
    (search_root / "src" / "data.json").write_text('{"key": "value"}\n')
    (search_root / "node_modules" / "dep.js").write_text("def main():\n")
    (search_root / ".venv" / "lib.py").write_text("def main():\n")
    (search_root / "binary.bin").write_bytes(b"\x00\x01import os\x00\x02")
    return search_root


def lines(text):
    return [l for l in text.splitlines() if l]


# ---------------------------------------------------------------------------
# glob
# ---------------------------------------------------------------------------

def test_glob_finds_nested_files_by_extension(tree):
    out = glob.invoke({"pattern": "**/*.py", "path": "."})
    assert "src/util.py" in lines(out)


@pytest.mark.xfail(
    reason="BUG: matching is fnmatchcase(rel, pattern), and fnmatch treats '**' "
           "as ordinary '*' rather than 'zero or more directories'. So the "
           "pattern the tool's own docstring advertises ('**/*.py') silently "
           "omits top-level files — 'main.py' at the root is not returned. A "
           "model reaching for the documented example gets an incomplete file "
           "list and may conclude a root-level module does not exist.",
)
def test_known_bug_glob_star_star_pattern_misses_top_level_files(tree):
    found = lines(glob.invoke({"pattern": "**/*.py", "path": "."}))
    assert "main.py" in found


def test_glob_bare_extension_finds_both_levels(tree):
    """'*.py' works at every depth, because the bare filename is matched too."""
    found = lines(glob.invoke({"pattern": "*.py", "path": "."}))
    assert "main.py" in found
    assert "src/util.py" in found


def test_glob_matches_bare_filenames_too(tree):
    """fnmatch is applied to both the relative path and the bare name, so
    '*.py' finds nested files without a recursive-looking pattern."""
    found = lines(glob.invoke({"pattern": "*.py", "path": "."}))
    assert "src/util.py" in found


def test_glob_scopes_to_a_subdirectory(tree):
    found = lines(glob.invoke({"pattern": "*.py", "path": "src"}))
    assert found == ["util.py"]


def test_glob_skips_dependency_directories(tree):
    out = glob.invoke({"pattern": "**/*.py", "path": "."})
    assert not any("node_modules" in m for m in lines(out))
    assert not any(".venv" in m for m in lines(out))
    assert not any(m.startswith("venv/") for m in lines(out))


def test_glob_skips_dotfiles_and_dotdirs(tree):
    (tree / ".hidden.py").write_text("x = 1")
    out = glob.invoke({"pattern": "*.py", "path": "."})
    assert not any(m.startswith(".") for m in lines(out))
    assert ".hidden.py" not in lines(out)


def test_glob_results_are_sorted(tree):
    found = lines(glob.invoke({"pattern": "**/*", "path": "."}))
    assert found == sorted(found)


def test_glob_no_matches_message(tree):
    # NB the doubled dot: the message interpolates path="." and then a period.
    assert glob.invoke({"pattern": "*.rs", "path": "."}) == "No files match '*.rs' under .."


def test_glob_truncates_at_the_cap(tree):
    for i in range(MAX_RESULTS + 20):
        (tree / f"gen{i:03d}.py").write_text("x = 1")
    out = glob.invoke({"pattern": "*.py", "path": "."})
    assert f"truncated at {MAX_RESULTS}" in out
    assert len([l for l in out.splitlines() if l and "truncated" not in l]) == MAX_RESULTS


def test_glob_on_a_missing_directory(tree):
    assert glob.invoke({"pattern": "*", "path": "nope"}) == "Not a directory: nope"


def test_glob_rejects_parent_traversal(tree):
    out = glob.invoke({"pattern": "*", "path": ".."})
    assert "escapes the working directory" in out


def test_glob_rejects_an_escaping_path(tree, search_root):
    out = glob.invoke({"pattern": "*", "path": str(search_root.parent)})
    assert "escapes the working directory" in out


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------

def test_grep_reports_file_line_and_match(tree):
    out = grep.invoke({"pattern": "TARGET", "path": "."})
    assert "src/util.py:3: TARGET = 1" in out


def test_grep_line_numbers_are_one_based(tree):
    out = grep.invoke({"pattern": "import os", "path": "."})
    assert "main.py:1: import os" in out
    assert "src/util.py:1: import os" in out


def test_grep_finds_matches_in_several_files(tree):
    out = grep.invoke({"pattern": "def main", "path": "."})
    assert "main.py:3:" in out
    assert "README.md:1:" in out


def test_grep_is_scoped_to_a_subdirectory(tree):
    out = grep.invoke({"pattern": "import os", "path": "src"})
    assert "util.py:1:" in out
    assert "main.py" not in out


def test_grep_include_filter_restricts_by_extension(tree):
    out = grep.invoke({"pattern": "def main", "path": ".", "include": "*.md"})
    assert out == "README.md:1: def main(): documentation"


def test_grep_skips_dependency_directories(tree):
    out = grep.invoke({"pattern": "def main", "path": "."})
    assert "node_modules" not in out
    assert ".venv" not in out


def test_grep_skips_binary_files(tree):
    """NUL bytes mark a binary; without the check the match would be shipped
    to the model as mojibake."""
    out = grep.invoke({"pattern": "import os", "path": "."})
    assert "binary.bin" not in out


def test_grep_skips_undecodable_files(tree):
    (tree / "latin.py").write_bytes(b"# caf\xe9\nimport os\n")
    out = grep.invoke({"pattern": "import os", "path": ".", "include": "latin.py"})
    assert "latin.py" not in out


def test_grep_truncates_long_lines(tree):
    (tree / "long.py").write_text("VALUE = '" + "z" * 500 + "'\n")
    out = grep.invoke({"pattern": "VALUE", "path": ".", "include": "long.py"})
    assert "\u2026" in out
    body = out.split(": ", 2)[-1]
    assert len(body) == MAX_LINE_LEN + 1


def test_grep_strips_leading_whitespace_from_the_match(tree):
    (tree / "indented.py").write_text("def f():\n        return 1\n")
    out = grep.invoke({"pattern": "return 1", "path": ".", "include": "indented.py"})
    assert out.endswith(": return 1")


def test_grep_no_matches_message(tree):
    assert grep.invoke({"pattern": "zzz_not_here", "path": "."}) == "No matches for 'zzz_not_here' under .."


def test_grep_invalid_regex_is_reported_not_raised(tree):
    out = grep.invoke({"pattern": "[unclosed", "path": "."})
    assert out.startswith("Invalid regex:")


def test_grep_respects_max_results(tree):
    for i in range(30):
        (tree / f"many{i:02d}.py").write_text("MATCH = 1\nMATCH = 2\n")
    out = grep.invoke({"pattern": "MATCH", "path": ".", "max_results": 10})
    assert "truncated at 10" in out
    assert len([l for l in out.splitlines() if l and "truncated" not in l]) == 10


def test_grep_max_results_is_clamped_to_200(tree):
    for i in range(5):
        (tree / f"f{i}.py").write_text("MATCH\n")
    out = grep.invoke({"pattern": "MATCH", "path": ".", "max_results": 9999})
    assert "truncated" not in out
    assert out.count("MATCH") == 5


def test_grep_max_results_of_zero_becomes_one(tree):
    (tree / "one.py").write_text("MATCH\n")
    out = grep.invoke({"pattern": "MATCH", "path": ".", "max_results": 0})
    assert "one.py:1" in out


def test_grep_rejects_parent_traversal(tree):
    out = grep.invoke({"pattern": "x", "path": ".."})
    assert "escapes the working directory" in out


def test_grep_on_a_missing_directory(tree):
    assert grep.invoke({"pattern": "x", "path": "nope"}) == "Not a directory: nope"


def test_grep_paths_are_relative_to_the_root(tree):
    """Nested hits must be reported relative to the search root, not to the
    subdirectory being searched."""
    out = grep.invoke({"pattern": "TARGET", "path": "src"})
    assert out.startswith("src/util.py:3:")


# ---------------------------------------------------------------------------
# binding
# ---------------------------------------------------------------------------

def test_tools_refuse_to_run_before_a_root_is_bound(monkeypatch):
    import search

    search._root = None
    for tool, args in ((glob, {"pattern": "*"}), (grep, {"pattern": "x"})):
        with pytest.raises(RuntimeError, match="Search root not bound"):
            tool.invoke(args)


def test_binding_a_root_does_not_create_it_until_a_tool_runs(tmp_path):
    """bind_search_root only records the path; the directory is created lazily
    by _require_root() on first use."""
    import search

    target = tmp_path / "made" / "on" / "demand"
    bind_search_root(str(target))
    assert not target.exists()

    glob.invoke({"pattern": "*"})
    assert target.is_dir()
    search._root = None


def test_skip_dirs_covers_the_usual_offenders():
    for name in (".git", "node_modules", "__pycache__", "venv", ".venv", "dist", "build"):
        assert name in SKIP_DIRS
