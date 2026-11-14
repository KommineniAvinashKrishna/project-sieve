from __future__ import annotations

import json
import os
import urllib.request
from typing import List

from sieve.models import EvidencePack, ReasonCode
from sieve.prompt import SYSTEM_PROMPT, build_prompt
from sieve.providers.base import LLMProvider


class OpenAICompatibleProvider(LLMProvider):
    """Minimal optional provider for any OpenAI-compatible /chat/completions endpoint."""

    name = "openai-compatible"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "OpenAICompatibleProvider":
        base_url = os.environ.get("SIEVE_LLM_BASE_URL")
        api_key = os.environ.get("SIEVE_LLM_API_KEY")
        model = os.environ.get("SIEVE_LLM_MODEL")
        if not (base_url and api_key and model):
            raise RuntimeError(
                "SIEVE_LLM_BASE_URL, SIEVE_LLM_API_KEY and SIEVE_LLM_MODEL must be set"
            )
        return cls(base_url=base_url, api_key=api_key, model=model)

    def triage(self, pack: EvidencePack, reason_codes: List[ReasonCode]) -> str:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(pack, reason_codes)},
            ],
        }
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer %s" % self.api_key,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        return body["choices"][0]["message"]["content"]
