"""Tests for session.py — the SQLite session store.

session.py does its schema setup and legacy-JSON migration at *import* time,
so these tests import it lazily through a helper that first points
CLOSECODE_SESSIONS_DIR at a temp dir and then rebinds the module's DB_PATH.
Importing it for real would create ~/.closecode/sessions/sessions.db.
"""

import json
import sqlite3

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


@pytest.fixture
def sess(isolated_home, monkeypatch):
    """A session module bound to a throwaway SQLite database.

    The module caches SESSIONS_DIR/DB_PATH at import, so both the env var (for
    the real import) and the module attributes (for the cached ones) are
    redirected.
    """
    import session

    monkeypatch.setattr(session, "SESSIONS_DIR", isolated_home.sessions_dir)
    monkeypatch.setattr(session, "DB_PATH", isolated_home.db_path)
    session._init()
    return session


def msgs(*pairs):
    return [HumanMessage(content=a) for a, _ in pairs] + [AIMessage(content=b) for _, b in pairs]


# ---------------------------------------------------------------------------
# location
# ---------------------------------------------------------------------------

def test_db_lives_in_the_sessions_dir(sess, isolated_home):
    assert sess.DB_PATH.parent == isolated_home.sessions_dir
    assert sess.DB_PATH.name == "sessions.db"


def test_init_creates_the_database_file(sess, isolated_home):
    assert isolated_home.db_path.is_file()


def test_init_is_idempotent(sess):
    sess._init()
    sess._init()  # must not raise "table already exists"


def test_sessions_dir_env_var_name(sess):
    assert sess.SESSIONS_DIR_ENV == "CLOSECODE_SESSIONS_DIR"


# ---------------------------------------------------------------------------
# new_session
# ---------------------------------------------------------------------------

def test_new_session_returns_an_incrementing_id(sess):
    assert sess.new_session() == 1
    assert sess.new_session() == 2


def test_new_session_defaults(sess):
    sid = sess.new_session()
    info = sess.get_session(sid)
    assert info.model == ""
    assert info.mode == "build"
    assert info.message_count == 0
    assert info.name == ""


def test_new_session_stores_model_and_mode(sess):
    sid = sess.new_session(model="some/model:free", mode="plan")
    info = sess.get_session(sid)
    assert info.model == "some/model:free"
    assert info.mode == "plan"


def test_new_session_sets_timestamps(sess):
    info = sess.get_session(sess.new_session())
    assert info.created_at > 0
    assert info.updated_at >= info.created_at


# ---------------------------------------------------------------------------
# save / load
# ---------------------------------------------------------------------------

def test_save_then_load_roundtrip(sess):
    sid = sess.new_session()
    original = [HumanMessage(content="hi"), AIMessage(content="hello")]
    sess.save(sid, original, model="m", mode="build")
    loaded = sess.load(sid)
    assert [m.content for m in loaded] == ["hi", "hello"]


def test_load_preserves_message_types(sess):
    sid = sess.new_session()
    original = [SystemMessage(content="s"), HumanMessage(content="h"), AIMessage(content="a")]
    sess.save(sid, original)
    assert [m.type for m in sess.load(sid)] == ["system", "human", "ai"]


def test_save_preserves_message_order(sess):
    sid = sess.new_session()
    original = [HumanMessage(content=str(i)) for i in range(20)]
    sess.save(sid, original)
    assert [m.content for m in sess.load(sid)] == [str(i) for i in range(20)]


def test_save_replaces_previous_messages(sess):
    """save() is a full replace — the agent re-saves the whole history every
    turn, so anything left behind would be duplicated in the UI."""
    sid = sess.new_session()
    sess.save(sid, [HumanMessage(content="one"), AIMessage(content="two")])
    sess.save(sid, [HumanMessage(content="three")])
    loaded = sess.load(sid)
    assert [m.content for m in loaded] == ["three"]
    assert sess.get_session(sid).message_count == 1


def test_save_updates_message_count(sess):
    sid = sess.new_session()
    sess.save(sid, [HumanMessage(content=str(i)) for i in range(7)])
    assert sess.get_session(sid).message_count == 7


def test_save_updates_model_and_mode(sess):
    sid = sess.new_session(model="old", mode="build")
    sess.save(sid, [HumanMessage(content="x")], model="new", mode="plan")
    info = sess.get_session(sid)
    assert info.model == "new"
    assert info.mode == "plan"


def test_save_advances_updated_at(sess):
    sid = sess.new_session()
    first = sess.get_session(sid).updated_at
    sess.save(sid, [HumanMessage(content="x")])
    assert sess.get_session(sid).updated_at >= first


def test_saving_an_empty_history_clears_the_session(sess):
    sid = sess.new_session()
    sess.save(sid, [HumanMessage(content="something")])
    sess.save(sid, [])
    assert sess.load(sid) == []
    assert sess.get_session(sid).message_count == 0


def test_save_to_a_missing_session_is_a_no_op(sess):
    """No INSERT on the session row, so nothing is created — the agent would
    otherwise resurrect a deleted session on its next turn."""
    sess.save(9999, [HumanMessage(content="x")])
    assert sess.get_session(9999) is None


# ---------------------------------------------------------------------------
# session naming
# ---------------------------------------------------------------------------

def test_name_is_derived_from_the_first_user_message(sess):
    sid = sess.new_session()
    sess.save(sid, [SystemMessage(content="sys"), HumanMessage(content="fix the parser bug")])
    assert sess.get_session(sid).name == "fix the parser bug"


def test_derived_name_collapses_whitespace(sess):
    sid = sess.new_session()
    sess.save(sid, [HumanMessage(content="  fix   the\n\tparser  ")])
    assert sess.get_session(sid).name == "fix the parser"


def test_derived_name_is_capped_at_48_chars(sess):
    sid = sess.new_session()
    sess.save(sid, [HumanMessage(content="x" * 200)])
    assert len(sess.get_session(sid).name) == 48


def test_name_is_only_derived_once(sess):
    """The first user message names the session; later ones must not rename
    it, or the sidebar churns as the conversation goes on."""
    sid = sess.new_session()
    sess.save(sid, [HumanMessage(content="original topic")])
    sess.save(sid, [HumanMessage(content="original topic"),
                    HumanMessage(content="totally different topic"),
                    AIMessage(content="ok")])
    assert sess.get_session(sid).name == "original topic"


def test_name_is_empty_when_there_is_no_user_message(sess):
    sid = sess.new_session()
    sess.save(sid, [SystemMessage(content="s"), AIMessage(content="a")])
    assert sess.get_session(sid).name == ""


# ---------------------------------------------------------------------------
# get_session / list_sessions / latest_session_id
# ---------------------------------------------------------------------------

def test_get_session_returns_none_for_a_missing_id(sess):
    assert sess.get_session(4242) is None


def test_get_session_exposes_every_column(sess):
    info = sess.get_session(sess.new_session())
    for field in ("id", "name", "model", "mode", "summary",
                  "created_at", "updated_at", "message_count"):
        assert hasattr(info, field)


def test_list_sessions_returns_newest_first(sess):
    first = sess.new_session()
    sess.save(first, [HumanMessage(content="a")])
    second = sess.new_session()
    sess.save(second, [HumanMessage(content="b")])
    ids = [s.id for s in sess.list_sessions()]
    assert ids[0] == second
    assert first in ids


def test_list_sessions_on_an_empty_db(sess):
    assert sess.list_sessions() == []


def test_latest_session_id_picks_the_most_recent_with_messages(sess):
    empty = sess.new_session()
    filled = sess.new_session()
    sess.save(filled, [HumanMessage(content="real content")])
    assert sess.latest_session_id() == filled
    assert empty != sess.latest_session_id()


def test_latest_session_id_when_nothing_has_messages(sess):
    sess.new_session()
    assert sess.latest_session_id() is None


def test_latest_session_id_on_an_empty_db(sess):
    assert sess.latest_session_id() is None


# ---------------------------------------------------------------------------
# delete / rename
# ---------------------------------------------------------------------------

def test_delete_removes_the_session(sess):
    sid = sess.new_session()
    assert sess.delete_session(sid) is True
    assert sess.get_session(sid) is None


@pytest.mark.xfail(
    reason="BUG: the schema declares FOREIGN KEY (session_id) REFERENCES "
           "sessions(id) ON DELETE CASCADE, but SQLite ignores foreign keys "
           "unless PRAGMA foreign_keys=ON, and session._connect never sets it. "
           "delete_session() therefore leaves every message row of the deleted "
           "session behind as an orphan. ids come from AUTOINCREMENT so they are "
           "never reused, meaning the orphans are invisible but accumulate — a "
           "deleted session's history still sits on disk, which is not what a "
           "user deleting a conversation expects.",
)
def test_known_bug_delete_leaves_orphaned_message_rows(sess):
    import sqlite3

    sid = sess.new_session()
    sess.save(sid, [HumanMessage(content="a"), AIMessage(content="b")])
    assert sess.delete_session(sid) is True
    with sqlite3.connect(sess.DB_PATH) as conn:
        orphans = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", (sid,)
        ).fetchone()[0]
    assert orphans == 0


def test_delete_reports_false_for_a_missing_id(sess):
    assert sess.delete_session(7777) is False


def test_rename_sets_a_new_name(sess):
    sid = sess.new_session()
    assert sess.rename_session(sid, "my session") is True
    assert sess.get_session(sid).name == "my session"


def test_rename_overrides_a_derived_name(sess):
    sid = sess.new_session()
    sess.save(sid, [HumanMessage(content="auto name")])
    sess.rename_session(sid, "chosen name")
    assert sess.get_session(sid).name == "chosen name"


def test_rename_strips_and_truncates(sess):
    sid = sess.new_session()
    sess.rename_session(sid, "  spaced  ")
    assert sess.get_session(sid).name == "spaced"
    sess.rename_session(sid, "y" * 300)
    assert len(sess.get_session(sid).name) == 80


def test_rename_reports_false_for_a_missing_id(sess):
    assert sess.rename_session(7777, "nope") is False


# ---------------------------------------------------------------------------
# resilience
# ---------------------------------------------------------------------------

def test_corrupt_message_rows_are_skipped_not_fatal(sess):
    """A single unparseable payload must not take the whole history with it."""
    sid = sess.new_session()
    sess.save(sid, [HumanMessage(content="good one")])
    with sqlite3.connect(sess.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO messages (session_id, seq, payload) VALUES (?, ?, ?)",
            (sid, 1, "{not json"),
        )
        conn.commit()
    loaded = sess.load(sid)
    assert [m.content for m in loaded] == ["good one"]


def test_messages_are_stored_as_langchain_dicts(sess):
    """Persist format is messages_to_dict, so a session saved by a future
    langchain version can still be read back by this one."""
    sid = sess.new_session()
    sess.save(sid, [HumanMessage(content="schema check")])
    with sqlite3.connect(sess.DB_PATH) as conn:
        payload = conn.execute(
            "SELECT payload FROM messages WHERE session_id = ?", (sid,)
        ).fetchone()[0]
    decoded = json.loads(payload)
    assert decoded["type"] == "human"
    assert decoded["data"]["content"] == "schema check"


# ---------------------------------------------------------------------------
# legacy JSON migration
# ---------------------------------------------------------------------------

def test_legacy_json_files_are_imported(sess, isolated_home, monkeypatch):
    from langchain_core.messages import messages_to_dict

    legacy = [
        messages_to_dict([HumanMessage(content="old question")])[0],
        messages_to_dict([AIMessage(content="old answer")])[0],
    ]
    (isolated_home.sessions_dir / "session_abc.json").write_text(json.dumps(legacy))

    sess._migrate_from_json()

    imported = sess.list_sessions()
    assert len(imported) == 1
    assert [m.content for m in sess.load(imported[0].id)] == ["old question", "old answer"]


def test_migration_derives_a_name_for_imported_sessions(sess, isolated_home):
    from langchain_core.messages import messages_to_dict

    legacy = [messages_to_dict([HumanMessage(content="migrated topic")])[0]]
    (isolated_home.sessions_dir / "session_xyz.json").write_text(json.dumps(legacy))
    sess._migrate_from_json()
    assert sess.list_sessions()[0].name == "migrated topic"


def test_corrupt_legacy_file_is_skipped(sess, isolated_home):
    (isolated_home.sessions_dir / "session_bad.json").write_text("{{{ not json")
    sess._migrate_from_json()  # must not raise
    assert sess.list_sessions() == []


def test_empty_legacy_file_is_skipped(sess, isolated_home):
    (isolated_home.sessions_dir / "session_empty.json").write_text("[]")
    sess._migrate_from_json()
    assert sess.list_sessions() == []


def test_migration_is_skipped_when_sessions_already_exist(sess, isolated_home):
    """Once there's real data in the DB, the legacy files are left alone —
    otherwise every launch would re-import them as duplicates."""
    from langchain_core.messages import messages_to_dict

    (isolated_home.sessions_dir / "session_dup.json").write_text(
        json.dumps([messages_to_dict([HumanMessage(content="legacy")])[0]])
    )
    existing = sess.new_session()
    sess.save(existing, [HumanMessage(content="current")])

    sess._migrate_from_json()
    assert len(sess.list_sessions()) == 1
    assert sess.list_sessions()[0].id == existing
