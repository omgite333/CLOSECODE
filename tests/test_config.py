"""Tests for config.py — the per-user API key / model store.

The file holds a secret, so two properties matter as much as correctness:
it is created 0600, and a corrupt or hostile file can't crash the launcher.
Both are tested here against a CLOSECODE_CONFIG_DIR temp dir.
"""

import json
import os
import stat

import pytest

import config
from config import (
    CONFIG_DIR_ENV,
    CONFIG_FILENAME,
    KEY_API,
    KEY_MODEL,
    config_dir,
    config_path,
    load_config,
    save_config_value,
)


# ---------------------------------------------------------------------------
# location
# ---------------------------------------------------------------------------

def test_config_dir_honours_the_env_override(isolated_home):
    assert config_dir() == isolated_home.config_dir


def test_config_path_is_config_json_in_that_dir(isolated_home):
    assert config_path() == isolated_home.config_dir / CONFIG_FILENAME
    assert config_path().name == "config.json"


def test_config_dir_is_created_on_demand(tmp_path, monkeypatch):
    target = tmp_path / "brand" / "new"
    monkeypatch.setenv(CONFIG_DIR_ENV, str(target))
    assert config_dir() == target
    assert target.is_dir()


def test_blank_env_override_falls_back_to_default(monkeypatch, tmp_path):
    """An empty/whitespace override must not produce a path of '' — that would
    write the API key into the current working directory. HOME is redirected so
    the fallback creates a temp dir, not a real ~/.closecode."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(CONFIG_DIR_ENV, "   ")
    assert config_dir() == tmp_path / ".closecode"
    assert config_dir().is_dir()


def test_env_override_expands_a_tilde(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(CONFIG_DIR_ENV, "~/somewhere")
    assert config_dir() == tmp_path / "somewhere"


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

def test_load_missing_file_returns_empty_dict(isolated_home):
    assert load_config() == {}


def test_load_reads_back_saved_values(isolated_home):
    save_config_value(KEY_API, "sk-or-v1-abc")
    save_config_value(KEY_MODEL, "some/model:free")
    assert load_config() == {KEY_API: "sk-or-v1-abc", KEY_MODEL: "some/model:free"}


@pytest.mark.parametrize(
    "content",
    [
        "{not valid json",
        "",
        "null",
        "[]",
        '"a string"',
        "123",
        "\x00\x01binary",
    ],
)
def test_load_corrupt_or_non_object_file_returns_empty_dict(isolated_home, content):
    """A mangled config must degrade to 'no config' rather than raising on
    every launch — the user would be locked out of the app entirely."""
    config_path().write_text(content, encoding="utf-8")
    assert load_config() == {}


def test_load_survives_a_directory_in_place_of_the_file(isolated_home):
    config_path().mkdir()
    assert load_config() == {}


# ---------------------------------------------------------------------------
# save_config_value
# ---------------------------------------------------------------------------

def test_save_merges_rather_than_replaces(isolated_home):
    save_config_value(KEY_API, "key-one")
    save_config_value(KEY_MODEL, "model-one")
    data = json.loads(config_path().read_text())
    assert data == {KEY_API: "key-one", KEY_MODEL: "model-one"}


def test_save_overwrites_a_single_key(isolated_home):
    save_config_value(KEY_API, "old")
    save_config_value(KEY_API, "new")
    assert load_config()[KEY_API] == "new"
    assert len(load_config()) == 1


def test_save_returns_the_path_written(isolated_home):
    assert save_config_value(KEY_API, "k") == config_path()


def test_save_recovers_from_a_corrupt_file(isolated_home):
    """A user who hand-edited the file badly should still be able to save."""
    config_path().write_text("{{{", encoding="utf-8")
    save_config_value(KEY_API, "k")
    assert load_config() == {KEY_API: "k"}


def test_save_written_file_is_valid_json_with_trailing_newline(isolated_home):
    save_config_value(KEY_API, "k")
    raw = config_path().read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert json.loads(raw) == {KEY_API: "k"}


def test_save_preserves_unicode_values(isolated_home):
    save_config_value(KEY_MODEL, "modèle/百万:free")
    assert load_config()[KEY_MODEL] == "modèle/百万:free"


# ---------------------------------------------------------------------------
# permissions — the file holds a secret
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_config_file_is_owner_only(isolated_home):
    save_config_value(KEY_API, "sk-secret")
    mode = stat.S_IMODE(config_path().stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_config_file_is_not_world_or_group_readable(isolated_home):
    save_config_value(KEY_API, "sk-secret")
    mode = stat.S_IMODE(config_path().stat().st_mode)
    assert not mode & stat.S_IRGRP
    assert not mode & stat.S_IROTH


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_resaving_keeps_the_tight_permissions(isolated_home):
    save_config_value(KEY_API, "one")
    save_config_value(KEY_API, "two")
    assert stat.S_IMODE(config_path().stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_permissions_are_tightened_on_an_existing_loose_file(isolated_home):
    """A file left 0644 by an earlier version or a manual edit gets fixed on
    the next save, not just on creation."""
    config_path().write_text("{}", encoding="utf-8")
    os.chmod(config_path(), 0o644)
    save_config_value(KEY_API, "k")
    assert stat.S_IMODE(config_path().stat().st_mode) == 0o600
