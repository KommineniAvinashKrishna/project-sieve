from __future__ import annotations

import os

from sieve.providers.base import LLMProvider
from sieve.providers.fake import FakeProvider


def provider_from_env() -> LLMProvider:
    name = os.environ.get("SIEVE_PROVIDER", "fake").lower()
    if name in ("fake", "deterministic"):
        return FakeProvider(mode=os.environ.get("SIEVE_FAKE_MODE", "normal"))
    if name in ("real", "openai", "openai-compatible"):
        from sieve.providers.real import OpenAICompatibleProvider

        return OpenAICompatibleProvider.from_env()
    raise ValueError("unknown SIEVE_PROVIDER: %s" % name)
