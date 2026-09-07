import os

from dotenv import load_dotenv
from langchain_huggingface import ChatHuggingFace, HuggingFaceEndpoint


DEFAULT_MODEL = "Qwen/Qwen3-32B"


def get_llm(model_override: str | None = None) -> ChatHuggingFace:
    load_dotenv()

    model_id = (
        model_override
        or os.getenv("HF_MODEL_ID")
        or DEFAULT_MODEL
    )

    model = HuggingFaceEndpoint(
        repo_id=model_id,
        task="text-generation",
        max_new_tokens=1500,
    )

    llm = ChatHuggingFace(llm=model)

    return llm