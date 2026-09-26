import os
from langchain_openrouter import ChatOpenRouter

import json
import time

import requests

# Exposed so main.py's banner can show the real model name instead of
# "unknown" — previously main.py only checked HF_MODEL_ID/OPENROUTER_MODEL
# env vars, but this file hardcodes the model directly, so neither existed.
DEFAULT_MODEL = "nvidia/nemotron-3.5-lightning:free"

# Curated starter list shown by the /models command. Each entry is
# (openrouter model id, short note). This is just shortcuts — the user can
# always switch to any other OpenRouter model id with `/model <id>`.
# Free-tier availability and pricing change over time; when in doubt check
# https://openrouter.ai/models before picking a paid one.
KNOWN_MODELS = [
    ("nvidia/nemotron-3.5-lightning:free", "default · free · fast, decent tool calling"),
    ("qwen/qwen-2.5-coder-32b-instruct:free", "free · code-specialized, solid tool use"),
    ("meta-llama/llama-3.1-8b-instruct:free", "free · fastest, weakest tool calling"),
    ("qwen/qwen-2.5-72b-instruct", "paid · strong, reliable tool calling"),
    ("meta-llama/llama-3.1-70b-instruct", "paid · strong, reliable tool calling"),
]


def resolve_model_arg(arg: str, choices: list) -> str:
    """Turn a /model argument into a model id. A number picks from `choices`
    (1-based, as shown by the last /models listing); anything else is
    treated as a raw OpenRouter model id and passed through unchanged."""
    arg = arg.strip()
    if arg.isdigit():
        idx = int(arg) - 1
        if 0 <= idx < len(choices):
            return choices[idx][0]
    return arg


# ---------------------------------------------------------------------------
# Live model list from OpenRouter
# ---------------------------------------------------------------------------

_MODELS_ENDPOINT = "https://openrouter.ai/api/v1/models"
_MODELS_CACHE_PATH = os.path.join(
    os.path.expanduser("~"), ".cache", "closecode", "openrouter_models.json"
)
_MODELS_CACHE_TTL = 24 * 3600  # seconds


def _load_cached_models():
    """Return the cached [(id, note)] list if it's still fresh, else None."""
    try:
        with open(_MODELS_CACHE_PATH) as f:
            data = json.load(f)
        if time.time() - data.get("fetched_at", 0) < _MODELS_CACHE_TTL:
            models = data.get("models")
            if models:
                return [tuple(m) for m in models]
    except Exception:
        pass
    return None


def _save_cached_models(models: list) -> None:
    try:
        os.makedirs(os.path.dirname(_MODELS_CACHE_PATH), exist_ok=True)
        with open(_MODELS_CACHE_PATH, "w") as f:
            json.dump({"fetched_at": time.time(), "models": models}, f)
    except Exception:
        pass


def _note_for(entry: dict) -> str:
    """Short human note: display name + free or $/M input pricing."""
    name = entry.get("name") or entry.get("id", "")
    pricing = entry.get("pricing") or {}
    try:
        prompt_per_token = float(pricing.get("prompt") or 0)
    except (TypeError, ValueError):
        prompt_per_token = 0
    if prompt_per_token == 0:
        return f"{name} · free"
    return f"{name} · ${prompt_per_token * 1e6:.2f}/M"


def fetch_openrouter_models(force_refresh: bool = False):
    """Return (models, source) where models is [(id, note)] and source is
    one of "live", "cache", "fallback".

    Tries the 24h disk cache first, then the OpenRouter API (no auth
    needed for the public models endpoint). Anything failing — no network,
    bad response — falls back to the hardcoded KNOWN_MODELS shortlist so
    /models always shows something useful.
    """
    if not force_refresh:
        cached = _load_cached_models()
        if cached:
            return cached, "cache"
    try:
        resp = requests.get(_MODELS_ENDPOINT, timeout=15)
        resp.raise_for_status()
        items = resp.json().get("data") or []
        models = []
        for entry in items:
            mid = entry.get("id") or ""
            if not mid:
                continue
            try:
                free = float((entry.get("pricing") or {}).get("prompt") or 0) == 0
            except (TypeError, ValueError):
                free = False
            models.append((mid, _note_for(entry), free))
        # Free models first (cheapest to try), then alphabetical — stable,
        # predictable ordering so list numbers don't shuffle randomly.
        models.sort(key=lambda t: (not t[2], t[0].lower()))
        models = [(mid, note) for mid, note, _ in models]
        if models:
            _save_cached_models(models)
            return models, "live"
    except Exception:
        pass
    return list(KNOWN_MODELS), "fallback"


def get_llm(model_override: str = None):

    token = os.environ.get("OPENROUTER_API_KEY")

    if not token:
        raise EnvironmentError(
            "OPENROUTER_API_KEY is not set. "
            "Get a key at https://openrouter.ai/settings/keys"
        )

    model = model_override or os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)

    llm = ChatOpenRouter(
        model=model,
        temperature=0.2,
        max_tokens=4096,
        api_key=token,
    )

    return llm