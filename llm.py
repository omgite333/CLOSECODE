import os
from langchain_openrouter import ChatOpenRouter

# Exposed so main.py's banner can show the real model name instead of
# "unknown" — previously main.py only checked HF_MODEL_ID/OPENROUTER_MODEL
# env vars, but this file hardcodes the model directly, so neither existed.
DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"


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