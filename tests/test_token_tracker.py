"""Tests for token_tracker.py.

The tracker reads usage off whatever shape the provider happened to return, so
most of the surface here is about not double-counting, not crashing on a
missing field, and honestly reporting calls that carried no usage data.
"""

import pytest

from token_tracker import TokenTracker


class Msg:
    """Minimal message stand-in; add_from_message only does getattr lookups."""

    def __init__(self, usage_metadata=None, response_metadata=None):
        if usage_metadata is not None:
            self.usage_metadata = usage_metadata
        if response_metadata is not None:
            self.response_metadata = response_metadata


# ---------------------------------------------------------------------------
# fresh tracker
# ---------------------------------------------------------------------------

def test_new_tracker_is_all_zeros():
    t = TokenTracker()
    assert (t.prompt_tokens, t.completion_tokens, t.total_tokens, t.calls) == (0, 0, 0, 0)
    assert t.calls_without_usage_data == 0


def test_summary_of_a_fresh_tracker():
    assert TokenTracker().summary() == "no model calls yet this session"


# ---------------------------------------------------------------------------
# usage_metadata — the modern shape
# ---------------------------------------------------------------------------

def test_usage_metadata_is_accumulated():
    t = TokenTracker()
    t.add_from_message(Msg(usage_metadata={
        "input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
    }))
    assert t.prompt_tokens == 100
    assert t.completion_tokens == 20
    assert t.total_tokens == 120
    assert t.calls == 1


def test_usage_metadata_accumulates_across_calls():
    t = TokenTracker()
    t.add_from_message(Msg(usage_metadata={"input_tokens": 10, "output_tokens": 1, "total_tokens": 11}))
    t.add_from_message(Msg(usage_metadata={"input_tokens": 20, "output_tokens": 2, "total_tokens": 22}))
    assert t.prompt_tokens == 30
    assert t.completion_tokens == 3
    assert t.total_tokens == 33
    assert t.calls == 2


def test_partial_usage_metadata_counts_as_present():
    """Only one field present: the missing ones default to 0 rather than
    blowing up on a KeyError."""
    t = TokenTracker()
    t.add_from_message(Msg(usage_metadata={"input_tokens": 50}))
    assert t.prompt_tokens == 50
    assert t.completion_tokens == 0
    assert t.calls == 1


def test_null_usage_values_are_treated_as_zero():
    t = TokenTracker()
    t.add_from_message(Msg(usage_metadata={
        "input_tokens": None, "output_tokens": None, "total_tokens": None,
    }))
    assert (t.prompt_tokens, t.completion_tokens, t.total_tokens) == (0, 0, 0)
    assert t.calls == 1


def test_usage_metadata_takes_priority_over_response_metadata():
    t = TokenTracker()
    t.add_from_message(Msg(
        usage_metadata={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        response_metadata={"token_usage": {"prompt_tokens": 999, "completion_tokens": 999}},
    ))
    assert t.prompt_tokens == 1
    assert t.total_tokens == 3


# ---------------------------------------------------------------------------
# response_metadata — the older / fallback shape
# ---------------------------------------------------------------------------

def test_token_usage_in_response_metadata():
    t = TokenTracker()
    t.add_from_message(Msg(response_metadata={
        "token_usage": {"prompt_tokens": 70, "completion_tokens": 30, "total_tokens": 100},
    }))
    assert t.prompt_tokens == 70
    assert t.completion_tokens == 30
    assert t.total_tokens == 100
    assert t.calls == 1


def test_usage_in_response_metadata():
    """Some providers key it 'usage' rather than 'token_usage'."""
    t = TokenTracker()
    t.add_from_message(Msg(response_metadata={
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
    }))
    assert t.total_tokens == 10
    assert t.calls == 1


def test_total_is_derived_when_absent_in_response_metadata():
    t = TokenTracker()
    t.add_from_message(Msg(response_metadata={
        "token_usage": {"prompt_tokens": 8, "completion_tokens": 4},
    }))
    assert t.total_tokens == 12


def test_zero_total_is_preserved_over_the_derived_sum():
    """A provider that explicitly reports total=0 should be believed, not
    overwritten with the prompt+completion sum — the code guards this with
    `or (p + c)`, which means 0 falls through to the sum."""
    t = TokenTracker()
    t.add_from_message(Msg(response_metadata={
        "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }))
    assert t.total_tokens == 0


# ---------------------------------------------------------------------------
# missing usage data
# ---------------------------------------------------------------------------

def test_a_message_with_no_metadata_is_counted_but_not_totalled():
    """The call still happened and still cost time, so calls increments — but
    it can't be attributed any tokens."""
    t = TokenTracker()
    t.add_from_message(Msg())
    assert t.calls == 1
    assert t.calls_without_usage_data == 1
    assert t.total_tokens == 0


def test_empty_usage_metadata_counts_as_missing():
    t = TokenTracker()
    t.add_from_message(Msg(usage_metadata={}))
    assert t.calls_without_usage_data == 1


def test_empty_token_usage_dict_counts_as_missing():
    t = TokenTracker()
    t.add_from_message(Msg(response_metadata={"token_usage": {}}))
    assert t.calls_without_usage_data == 1


def test_response_metadata_that_is_none_is_handled():
    t = TokenTracker()
    t.add_from_message(Msg(usage_metadata=None, response_metadata=None))
    assert t.calls == 1
    assert t.calls_without_usage_data == 1


def test_a_message_with_neither_attribute_is_handled():
    t = TokenTracker()
    t.add_from_message(object())
    assert t.calls == 1
    assert t.calls_without_usage_data == 1


def test_mixed_run_counts_only_the_data_less_calls():
    t = TokenTracker()
    t.add_from_message(Msg(usage_metadata={"input_tokens": 10, "output_tokens": 1, "total_tokens": 11}))
    t.add_from_message(Msg())
    t.add_from_message(Msg(usage_metadata={"input_tokens": 5, "output_tokens": 1, "total_tokens": 6}))
    assert t.calls == 3
    assert t.calls_without_usage_data == 1
    assert t.total_tokens == 17


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

def test_summary_reports_a_clean_run():
    t = TokenTracker()
    t.add_from_message(Msg(usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}))
    out = t.summary()
    assert "1 model calls" in out
    assert "100 in" in out
    assert "20 out" in out
    assert "120 total tokens" in out
    assert "reported no usage data" not in out


def test_summary_discloses_calls_without_usage_data():
    """Honest accounting: the user is told some calls couldn't be measured
    rather than being shown an undercount as if it were the whole story."""
    t = TokenTracker()
    t.add_from_message(Msg(usage_metadata={"input_tokens": 10, "output_tokens": 1, "total_tokens": 11}))
    t.add_from_message(Msg())
    t.add_from_message(Msg())
    out = t.summary()
    assert "3 model calls" in out
    assert "2 calls reported no usage data" in out
    assert "provider limitation" in out


def test_summary_of_only_data_less_calls():
    t = TokenTracker()
    t.add_from_message(Msg())
    out = t.summary()
    assert "1 model calls" in out
    assert "0 in / 0 out / 0 total tokens" in out
    assert "1 calls reported no usage data" in out


# ---------------------------------------------------------------------------
# integration with a real message type
# ---------------------------------------------------------------------------

def test_works_with_a_real_langchain_ai_message():
    """Not just the stand-ins: a real AIMessage carries usage_metadata and
    must be counted."""
    from langchain_core.messages import AIMessage

    t = TokenTracker()
    t.add_from_message(AIMessage(content="hi"))
    assert t.calls == 1

    t2 = TokenTracker()
    t2.add_from_message(AIMessage(
        content="hi",
        usage_metadata={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
    ))
    assert t2.total_tokens == 15
    assert t2.calls_without_usage_data == 0
