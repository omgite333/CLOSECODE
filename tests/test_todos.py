"""Tests for todos.py — the TodoStore invariants and the tool wrappers.

TodoStore.set() is a sanitizer, not just a setter: it drops junk, coerces bad
statuses, caps field lengths, and enforces the "exactly one in_progress" rule
the system prompt tells the model to follow. Models violate that rule often
enough that the store has to enforce it, so each invariant gets its own test.
"""

import pytest

from todos import TodoStore, format_todos


@pytest.fixture
def store():
    return TodoStore()


def item(content="do a thing", status="pending", active_form=None):
    return {
        "content": content,
        "status": status,
        "activeForm": active_form if active_form is not None else f"doing {content}",
    }


# ---------------------------------------------------------------------------
# set(): sanitization
# ---------------------------------------------------------------------------

def test_set_stores_items_verbatim(store):
    store.set([item("read the file", "pending")])
    assert store.get() == [
        {"content": "read the file", "status": "pending", "activeForm": "doing read the file"}
    ]


def test_set_replaces_the_whole_list(store):
    """The tool is documented as "call with the FULL list every time" — it is
    a replace, never a merge."""
    store.set([item("one"), item("two")])
    store.set([item("only")])
    assert [i["content"] for i in store.get()] == ["only"]


def test_content_is_trimmed(store):
    store.set([item("  padded  ")])
    assert store.get()[0]["content"] == "padded"


def test_whitespace_only_content_is_dropped(store):
    store.set([item("   "), item(""), item("real")])
    assert [i["content"] for i in store.get()] == ["real"]


def test_non_dict_entries_are_dropped(store):
    store.set(["a string", 42, None, item("keeper")])
    assert [i["content"] for i in store.get()] == ["keeper"]


def test_unknown_status_is_coerced_to_pending(store):
    store.set([item("thing", status="in-flight")])
    assert store.get()[0]["status"] == "pending"


def test_missing_status_defaults_to_pending(store):
    store.set([{"content": "no status given"}])
    assert store.get()[0]["status"] == "pending"


def test_every_status_is_preserved_when_valid(store):
    store.set(
        [
            item("a", "pending"),
            item("b", "in_progress"),
            item("c", "completed"),
        ]
    )
    assert [i["status"] for i in store.get()] == ["pending", "in_progress", "completed"]


def test_content_is_capped_at_200_chars(store):
    store.set([item("x" * 500)])
    assert len(store.get()[0]["content"]) == 200


def test_active_form_is_capped_at_200_chars(store):
    store.set([item("thing", active_form="y" * 500)])
    assert len(store.get()[0]["activeForm"]) == 200


def test_missing_active_form_becomes_empty_string(store):
    store.set([{"content": "no activeForm", "status": "pending"}])
    assert store.get()[0]["activeForm"] == ""


def test_non_string_content_is_coerced(store):
    store.set([{"content": 12345, "status": "pending"}])
    assert store.get()[0]["content"] == "12345"


def test_extra_keys_are_discarded(store):
    store.set([{**item("thing"), "secret": "drop me", "id": 7}])
    assert set(store.get()[0]) == {"content", "status", "activeForm"}


def test_empty_list_clears_the_store(store):
    store.set([item("something")])
    store.set([])
    assert store.get() == []


# ---------------------------------------------------------------------------
# set(): the single-in_progress invariant
# ---------------------------------------------------------------------------

def test_first_in_progress_wins(store):
    store.set(
        [
            item("a", "in_progress"),
            item("b", "in_progress"),
            item("c", "in_progress"),
        ]
    )
    statuses = [(i["content"], i["status"]) for i in store.get()]
    assert statuses == [("a", "in_progress"), ("b", "pending"), ("c", "pending")]


def test_demotion_preserves_list_order(store):
    """Demoted items must not be reordered — the list is the plan, and the
    order the model wrote is the order the user reads."""
    store.set(
        [item("first", "in_progress"), item("second", "in_progress"), item("third", "pending")]
    )
    assert [i["content"] for i in store.get()] == ["first", "second", "third"]


def test_in_progress_may_be_absent(store):
    store.set([item("a", "pending"), item("b", "completed")])
    assert not any(i["status"] == "in_progress" for i in store.get())


def test_completed_before_in_progress_is_allowed(store):
    """A plan can legitimately finish a step before starting the next."""
    store.set([item("done", "completed"), item("doing", "in_progress")])
    assert [i["status"] for i in store.get()] == ["completed", "in_progress"]


# ---------------------------------------------------------------------------
# get() / clear()
# ---------------------------------------------------------------------------

def test_get_returns_a_new_list_object(store):
    store.set([item("a")])
    snapshot = store.get()
    snapshot.append(item("appended"))
    assert len(store.get()) == 1


@pytest.mark.xfail(
    reason="KNOWN GAP: get() returns list(self.items), a shallow copy, so the "
           "item dicts are shared with the store. A caller that mutates an item "
           "it got from get() corrupts the live todo list, and the same aliasing "
           "means main.py's renderer can hand the model-facing state to code "
           "that assumes it owns it. Would need copy.deepcopy or frozen items.",
)
def test_known_gap_get_shares_item_dicts_with_the_store(store):
    store.set([item("original")])
    snapshot = store.get()
    snapshot[0]["content"] = "mutated"
    assert store.get()[0]["content"] == "original"


def test_clear_empties_the_store(store):
    store.set([item("a")])
    store.clear()
    assert store.get() == []


def test_new_store_is_empty(store):
    assert store.get() == []


# ---------------------------------------------------------------------------
# format_todos
# ---------------------------------------------------------------------------

def test_format_empty(store):
    assert format_todos([]) == "(no todos)"


def test_format_uses_a_status_icon_per_item():
    text = format_todos(
        [
            {"content": "a", "status": "pending", "activeForm": ""},
            {"content": "b", "status": "in_progress", "activeForm": ""},
            {"content": "c", "status": "completed", "activeForm": ""},
        ]
    )
    assert text == "\u25cb a\n\u25d0 b\n\u2713 c"


def test_format_one_line_per_todo():
    text = format_todos(
        [{"content": f"step {i}", "status": "pending", "activeForm": ""} for i in range(5)]
    )
    assert len(text.splitlines()) == 5


# ---------------------------------------------------------------------------
# the @tool wrappers
# ---------------------------------------------------------------------------

def test_todo_write_updates_the_bound_store(todo_store):
    from todos import todo_write

    out = todo_write.invoke({"todos": [item("ship it", "in_progress")]})
    assert "Todo list updated" in out
    assert todo_store.get()[0]["content"] == "ship it"
    assert "ship it" in out


def test_todo_read_reflects_writes(todo_store):
    from todos import todo_read, todo_write

    todo_write.invoke({"todos": [item("first"), item("second", "completed")]})
    out = todo_read.invoke({})
    assert out == "\u25cb first\n\u2713 second"


def test_todo_read_on_a_fresh_store(todo_store):
    from todos import todo_read

    assert todo_read.invoke({}) == "(no todos)"


def test_todo_write_clears_on_a_non_list_when_called_directly(todo_store):
    """The isinstance guard in todo_write's body handles a non-list by
    clearing rather than raising. Reachable by calling the underlying function
    (see the xfail below for the tool-call path)."""
    from todos import todo_write

    todo_write.func([item("a")])
    assert "Todo list updated" in todo_write.func("not a list")
    assert todo_store.get() == []


@pytest.mark.xfail(
    reason="KNOWN GAP: LangChain validates tool arguments against the "
           "list[dict] annotation before the body runs, so a model that sends a "
           "non-list gets a pydantic ValidationError instead of the "
           "'clear rather than crash' behaviour the body was written for. The "
           "isinstance guard is unreachable through the tool interface, and a "
           "ValidationError mid-turn aborts the tool call.",
)
def test_known_gap_todo_write_rejects_a_non_list_via_the_tool(todo_store):
    from todos import todo_write

    out = todo_write.invoke({"todos": "not a list"})
    assert "Todo list updated" in out
    assert todo_store.get() == []


def test_tools_raise_when_no_store_is_bound():
    """The fail-fast message tells the developer exactly what to fix."""
    import todos

    todos._store = None
    for tool, args in ((todos.todo_write, {"todos": []}), (todos.todo_read, {})):
        with pytest.raises(RuntimeError, match="TodoStore not bound"):
            tool.invoke(args)
