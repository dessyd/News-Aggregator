"""Appels au modèle (API Anthropic) + extraction robuste du JSON."""
from __future__ import annotations

import threading

from .util import extract_json


class LLM:
    """Client minimal. Identifiants résolus par le SDK : ANTHROPIC_API_KEY (local) ou fédération d'identité (GitHub Actions)."""

    def __init__(self, temperature=None):
        import anthropic  # import tardif : les tests n'en ont pas besoin

        self.client = anthropic.Anthropic()
        self.temperature = temperature
        self.usage: dict[str, dict[str, int]] = {}
        self._lock = threading.Lock()

    def complete(self, model: str, system: str, user: str, max_tokens: int) -> str:
        kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                      messages=[{"role": "user", "content": user}])
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        resp = self.client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        with self._lock:
            u = self.usage.setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
            u["calls"] += 1
            u["input_tokens"] += resp.usage.input_tokens
            u["output_tokens"] += resp.usage.output_tokens
        return text


def call_json(llm, model: str, system: str, user: str, max_tokens: int, retries: int = 1):
    """Appelle le modèle et renvoie un objet JSON ; relance une fois si la réponse est invalide."""
    last_error = None
    for attempt in range(retries + 1):
        prompt = user if attempt == 0 else (
            user + "\n\nRappel : réponds uniquement avec un JSON valide, complet, sans texte autour.")
        text = llm.complete(model, system, prompt, max_tokens)
        try:
            return extract_json(text)
        except ValueError as exc:
            last_error = exc
    raise ValueError(str(last_error))
