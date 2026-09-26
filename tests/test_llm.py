"""Tests for llm.py — model resolution, pricing notes, and the model list.

The network path is stubbed: the live OpenRouter call and the disk cache are
both replaced, so the suite never touches the network and never reads or
writes the real ~/.cache/closecode file.
"""

import pytest

import llm
from llm import (
    DEFAULT_MODEL,
    KNOWN_MODELS,
    _note_for,
    fetch_openrouter_models,
    get_llm,
    resolve_model_arg,
)

CHOICES = [("id/one", "note one"), ("id/two", "note two"), ("id/three", "note three")]


# ---------------------------------------------------------------------------
# resolve_model_arg
# ---------------------------------------------------------------------------

def test_a_number_picks_from_the_choice_list():
    assert resolve_model_arg("1", CHOICES) == "id/one"
    assert resolve_model_arg("2", CHOICES) == "id/two"
    assert resolve_model_arg("3", CHOICES) == "id/three"


def test_surrounding_whitespace_is_ignored():
    assert resolve_model_arg("  2  ", CHOICES) == "id/two"


def test_a_raw_model_id_passes_through_unchanged():
    assert resolve_model_arg("anthropic/claude-3.5-sonnet", CHOICES) == "anthropic/claude-3.5-sonnet"


def test_an_out_of_range_number_falls_through_to_a_raw_id():
    """A user typing '9' against a 3-item list means the model id '9', not a
    crash and not a silent pick from the list."""
    assert resolve_model_arg("9", CHOICES) == "9"


def test_number_zero_falls_through():
    assert resolve_model_arg("0", CHOICES) == "0"


def test_an_empty_argument_returns_empty():
    assert resolve_model_arg("   ", CHOICES) == ""


def test_a_free_tier_suffix_is_not_treated_as_a_number():
    """'qwen/qwen-2.5-coder-32b-instruct:free' contains digits but is not
    all-digits, so it must reach the model as an id."""
    arg = "qwen/qwen-2.5-coder-32b-instruct:free"
    assert resolve_model_arg(arg, CHOICES) == arg


def test_a_negative_number_is_not_a_list_index():
    assert resolve_model_arg("-1", CHOICES) == "-1"


# ---------------------------------------------------------------------------
# _note_for
# ---------------------------------------------------------------------------

def test_a_free_model_is_labelled_free():
    assert _note_for({"name": "Model X", "id": "x", "pricing": {"prompt": "0"}}) == "Model X \u00b7 free"


def test_a_zero_price_is_free():
    assert _note_for({"name": "Model X", "pricing": {"prompt": 0}}) == "Model X \u00b7 free"


def test_a_paid_model_shows_its_input_price_per_million():
    note = _note_for({"name": "Big", "pricing": {"prompt": "0.000003"}})
    assert note == "Big \u00b7 $3.00/M"


def test_a_cheap_model_rounds_to_cents():
    assert _note_for({"name": "Cheap", "pricing": {"prompt": 1e-9}}).endswith("$0.00/M")


def test_a_missing_pricing_block_counts_as_free():
    assert _note_for({"name": "Unpriced"}) == "Unpriced \u00b7 free"


def test_a_null_pricing_value_counts_as_free():
    assert _note_for({"name": "Null", "pricing": {"prompt": None}}) == "Null \u00b7 free"


def test_a_non_numeric_price_does_not_crash():
    note = _note_for({"name": "Weird", "pricing": {"prompt": "free"}})
    assert note.startswith("Weird \u00b7 ")


def test_a_missing_name_falls_back_to_the_id():
    assert _note_for({"id": "some/id", "pricing": {"prompt": 0}}) == "some/id \u00b7 free"


def test_a_completely_empty_entry_does_not_crash():
    assert _note_for({}).endswith("\u00b7 free")


# ---------------------------------------------------------------------------
# fetch_openrouter_models
# ---------------------------------------------------------------------------

@pytest.fixture
def cache_file(monkeypatch, tmp_path):
    """Redirect the 24h model cache into a temp file. The real
    _load_cached_models is left intact, so cache behaviour is exercised for
    real instead of stubbed out."""
    path = tmp_path / "models.json"
    monkeypatch.setattr(llm, "_MODELS_CACHE_PATH", str(path))
    return path


@pytest.fixture
def no_cache(cache_file, monkeypatch):
    """An empty cache: the real loader runs but finds nothing."""
    monkeypatch.setattr(llm, "_load_cached_models", lambda: None)
    return cache_file


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_live_fetch_succeeds(no_cache, monkeypatch):
    monkeypatch.setattr(llm.requests, "get", lambda url, timeout: FakeResponse({
        "data": [
            {"id": "paid/one", "name": "Paid One", "pricing": {"prompt": "0.000002"}},
            {"id": "free/one", "name": "Free One", "pricing": {"prompt": "0"}},
        ],
    }))
    models, source = fetch_openrouter_models()
    assert source == "live"
    assert [m[0] for m in models] == ["free/one", "paid/one"]  # free models first


def test_free_models_are_sorted_first_then_alphabetically(no_cache, monkeypatch):
    monkeypatch.setattr(llm.requests, "get", lambda url, timeout: FakeResponse({
        "data": [
            {"id": "zebra/free", "pricing": {"prompt": "0"}},
            {"id": "alpha/paid", "pricing": {"prompt": "0.000001"}},
            {"id": "middle/free", "pricing": {"prompt": "0"}},
        ],
    }))
    models, _ = fetch_openrouter_models()
    assert [m[0] for m in models] == ["middle/free", "zebra/free", "alpha/paid"]


def test_ordering_is_stable_across_calls(no_cache, monkeypatch):
    """The /models list must not reshuffle between renders, or the numbers the
    user is about to type stop meaning anything."""
    payload = {"data": [
        {"id": "b/free", "pricing": {"prompt": "0"}},
        {"id": "a/paid", "pricing": {"prompt": "0.000001"}},
    ]}
    monkeypatch.setattr(llm.requests, "get", lambda url, timeout: FakeResponse(payload))
    first, _ = fetch_openrouter_models()
    second, _ = fetch_openrouter_models()
    assert first == second


def test_entries_without_an_id_are_dropped(no_cache, monkeypatch):
    monkeypatch.setattr(llm.requests, "get", lambda url, timeout: FakeResponse({
        "data": [{"name": "No Id"}, {"id": "good/one", "pricing": {"prompt": "0"}}],
    }))
    models, _ = fetch_openrouter_models()
    assert [m[0] for m in models] == ["good/one"]


def test_an_empty_live_list_falls_back(no_cache, monkeypatch):
    monkeypatch.setattr(llm.requests, "get", lambda url, timeout: FakeResponse({"data": []}))
    models, source = fetch_openrouter_models()
    assert source == "fallback"
    assert models == list(KNOWN_MODELS)


def test_a_network_error_falls_back(no_cache, monkeypatch):
    def boom(url, timeout):
        raise llm.requests.ConnectionError("no network")
    monkeypatch.setattr(llm.requests, "get", boom)
    models, source = fetch_openrouter_models()
    assert source == "fallback"
    assert models == list(KNOWN_MODELS)


def test_a_malformed_response_falls_back(no_cache, monkeypatch):
    def boom(url, timeout):
        raise ValueError("not json at all")
    monkeypatch.setattr(llm.requests, "get", boom)
    models, source = fetch_openrouter_models()
    assert source == "fallback"


def test_a_fresh_cache_is_used_without_a_network_call(cache_file, monkeypatch):
    import json

    cache = {"fetched_at": llm.time.time(), "models": [["cached/one", "note"]]}
    cache_file.write_text(json.dumps(cache))

    def boom(*a, **k):
        raise AssertionError("the network must not be touched when the cache is fresh")

    monkeypatch.setattr(llm.requests, "get", boom)
    models, source = fetch_openrouter_models()
    assert source == "cache"
    assert models == [("cached/one", "note")]


def test_an_expired_cache_is_ignored(cache_file, monkeypatch):
    import json

    stale = {"fetched_at": llm.time.time() - (llm._MODELS_CACHE_TTL + 60),
             "models": [["stale/one", "note"]]}
    cache_file.write_text(json.dumps(stale))
    monkeypatch.setattr(llm.requests, "get", lambda url, timeout: FakeResponse({
        "data": [{"id": "fresh/one", "name": "Fresh One", "pricing": {"prompt": "0"}}],
    }))
    models, source = fetch_openrouter_models()
    assert source == "live"
    assert models == [("fresh/one", "Fresh One \u00b7 free")]


def test_force_refresh_skips_the_cache(cache_file, monkeypatch):
    import json

    cache_file.write_text(json.dumps({
        "fetched_at": llm.time.time(), "models": [["cached/one", "note"]],
    }))
    monkeypatch.setattr(llm.requests, "get", lambda url, timeout: FakeResponse({
        "data": [{"id": "live/one", "name": "Live One", "pricing": {"prompt": "0"}}],
    }))
    models, source = fetch_openrouter_models(force_refresh=True)
    assert source == "live"
    assert [m[0] for m in models] == ["live/one"]


def test_a_corrupt_cache_file_is_ignored(cache_file, monkeypatch):
    cache_file.write_text("{{{ not json")
    monkeypatch.setattr(llm.requests, "get", lambda url, timeout: FakeResponse({
        "data": [{"id": "live/one", "pricing": {"prompt": "0"}}],
    }))
    _, source = fetch_openrouter_models()
    assert source == "live"


def test_a_successful_fetch_writes_the_cache(no_cache, monkeypatch):
    import json

    monkeypatch.setattr(llm.requests, "get", lambda url, timeout: FakeResponse({
        "data": [{"id": "live/one", "name": "One", "pricing": {"prompt": "0"}}],
    }))
    fetch_openrouter_models()
    cached = json.loads(no_cache.read_text())
    assert cached["models"] == [["live/one", "One \u00b7 free"]]
    assert cached["fetched_at"] > 0


def test_an_unwritable_cache_location_does_not_break_the_fetch(no_cache, monkeypatch, tmp_path):
    """A read-only home must not stop /models from working."""
    unwritable = tmp_path / "no" / "such" / "dir"
    unwritable.parent.mkdir(parents=True)
    unwritable.parent.chmod(0o500)
    monkeypatch.setattr(llm, "_MODELS_CACHE_PATH", str(unwritable / "sub" / "models.json"))
    monkeypatch.setattr(llm.requests, "get", lambda url, timeout: FakeResponse({
        "data": [{"id": "live/one", "pricing": {"prompt": "0"}}],
    }))
    models, source = fetch_openrouter_models()
    unwritable.parent.chmod(0o700)
    assert source == "live"
    assert [m[0] for m in models] == ["live/one"]


# ---------------------------------------------------------------------------
# get_llm
# ---------------------------------------------------------------------------

def test_get_llm_without_an_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(EnvironmentError, match="OPENROUTER_API_KEY is not set"):
        get_llm()


def test_get_llm_uses_the_default_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    llm_obj = get_llm()
    assert llm_obj.model_name == DEFAULT_MODEL


def test_get_llm_honours_the_env_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_MODEL", "env/model:free")
    assert get_llm().model_name == "env/model:free"


def test_an_explicit_override_beats_the_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_MODEL", "env/model:free")
    assert get_llm("override/model").model_name == "override/model"


def test_the_api_key_is_not_leaked_into_the_model_name(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-secret-value")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    assert "sk-secret" not in get_llm().model_name


def test_default_model_is_in_the_known_models_list():
    """The banner advertises DEFAULT_MODEL, so it should be one the /models
    list can also show."""
    assert DEFAULT_MODEL in [m[0] for m in KNOWN_MODELS]


def test_known_models_are_unique_id_note_pairs():
    ids = [m[0] for m in KNOWN_MODELS]
    assert len(ids) == len(set(ids))
    assert all(len(m) == 2 for m in KNOWN_MODELS)
