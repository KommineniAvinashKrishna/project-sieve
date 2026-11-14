from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from sieve.models import EvidencePack, ReasonCode


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def triage(self, pack: EvidencePack, reason_codes: List[ReasonCode]) -> str:
        """Return the raw provider response, expected to be a JSON TriageDecision."""
