"""
token_tracker.py

Accumulates token usage across a session. Different providers report usage
differently (LangChain's standardized `usage_metadata` on newer chunks vs.
older `response_metadata['token_usage']` dicts vs. nothing at all for some
free-tier endpoints) — this tries each in order and degrades gracefully
rather than crashing when a provider reports none of them.
"""

from dataclasses import dataclass


@dataclass
class TokenTracker:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0
    calls_without_usage_data: int = 0

    def add_from_message(self, message) -> None:
        usage = getattr(message, "usage_metadata", None)
        if usage:
            self.prompt_tokens += usage.get("input_tokens", 0) or 0
            self.completion_tokens += usage.get("output_tokens", 0) or 0
            self.total_tokens += usage.get("total_tokens", 0) or 0
            self.calls += 1
            return

        meta = getattr(message, "response_metadata", None) or {}
        tu = meta.get("token_usage") or meta.get("usage")
        if tu:
            p = tu.get("prompt_tokens", 0) or 0
            c = tu.get("completion_tokens", 0) or 0
            self.prompt_tokens += p
            self.completion_tokens += c
            self.total_tokens += tu.get("total_tokens", p + c) or (p + c)
            self.calls += 1
            return

        self.calls += 1
        self.calls_without_usage_data += 1

    def summary(self) -> str:
        if self.calls == 0:
            return "no model calls yet this session"
        line = f"{self.calls} model calls \u00b7 {self.prompt_tokens} in / {self.completion_tokens} out / {self.total_tokens} total tokens"
        if self.calls_without_usage_data:
            line += f"  ({self.calls_without_usage_data} calls reported no usage data \u2014 provider limitation, not a bug)"
        return line