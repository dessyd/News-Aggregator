"""Appels au modèle (API Anthropic ou Mistral) + extraction robuste du JSON.

Le fournisseur se déduit du nom du modèle (voir `provider_for`) : on peut donc mélanger
les fournisseurs d'une étape à l'autre, par exemple l'étape 1 chez Mistral et les suivantes chez Claude.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request

from .util import extract_json

MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
# Préfixes des modèles servis par l'API Mistral ; tout autre nom part chez Anthropic.
MISTRAL_PREFIXES = ("mistral", "magistral", "ministral", "codestral", "devstral", "pixtral", "open-mistral")
RETRY_STATUSES = {429, 500, 502, 503, 504}


def provider_for(model: str) -> str:
    """« mistral » ou « anthropic », d'après le nom du modèle."""
    return "mistral" if model.lower().startswith(MISTRAL_PREFIXES) else "anthropic"


class LLM:
    """Client minimal.

    Anthropic : identifiants résolus par le SDK, ANTHROPIC_API_KEY (local) ou fédération d'identité (GitHub Actions).
    Mistral : clé lue dans MISTRAL_API_KEY (secret GitHub ou variable locale), appel REST sans dépendance supplémentaire.
    Chaque client n'est créé qu'au premier appel qui le concerne : un essai 100 % Mistral ne sollicite pas la fédération.
    """

    def __init__(self, temperature=None, max_retries: int = 4, mistral_reasoning_effort=None):
        self.temperature = temperature
        self.mistral_reasoning_effort = mistral_reasoning_effort
        self.max_retries = max_retries
        self.usage: dict[str, dict[str, int]] = {}
        self._lock = threading.Lock()
        self._anthropic = None

    def complete(self, model: str, system: str, user: str, max_tokens: int) -> str:
        if provider_for(model) == "mistral":
            text, tin, tout = self._complete_mistral(model, system, user, max_tokens)
        else:
            text, tin, tout = self._complete_anthropic(model, system, user, max_tokens)
        with self._lock:
            u = self.usage.setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
            u["calls"] += 1
            u["input_tokens"] += tin
            u["output_tokens"] += tout
        return text

    # ---------- Anthropic ----------
    def _anthropic_client(self):
        with self._lock:
            if self._anthropic is None:
                import anthropic  # import tardif : les tests et les essais Mistral n'en ont pas besoin

                self._anthropic = anthropic.Anthropic()
            return self._anthropic

    def _complete_anthropic(self, model, system, user, max_tokens):
        kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                      messages=[{"role": "user", "content": user}])
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        resp = self._anthropic_client().messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return text, resp.usage.input_tokens, resp.usage.output_tokens

    # ---------- Mistral ----------
    def _complete_mistral(self, model, system, user, max_tokens):
        key = os.environ.get("MISTRAL_API_KEY", "").strip()
        if not key:
            raise RuntimeError("MISTRAL_API_KEY absente : clé API Mistral requise pour le modèle " + model)
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            # Mode JSON : chaque étape attend un objet JSON (voir FORMATS dans pipeline.py).
            "response_format": {"type": "json_object"},
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.mistral_reasoning_effort:
            body["reasoning_effort"] = self.mistral_reasoning_effort
        data = self._post_mistral(body, key)
        text = _mistral_text(data["choices"][0]["message"].get("content"))
        usage = data.get("usage") or {}
        return text, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))

    def _post_mistral(self, body: dict, key: str) -> dict:
        """POST avec relances sur 429 et erreurs serveur. Les messages d'erreur ne contiennent jamais la clé."""
        payload = json.dumps(body).encode("utf-8")
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(MISTRAL_URL, data=payload, method="POST", headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            })
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in RETRY_STATUSES and attempt < self.max_retries:
                    time.sleep(_retry_delay(exc.headers.get("Retry-After") if exc.headers else None, attempt))
                    continue
                detail = _error_detail(exc)
                raise RuntimeError(f"API Mistral : HTTP {exc.code}{detail}") from None
            except urllib.error.URLError as exc:
                if attempt < self.max_retries:
                    time.sleep(_retry_delay(None, attempt))
                    continue
                raise RuntimeError(f"API Mistral injoignable ({exc.reason})") from None
        raise RuntimeError("API Mistral : relances épuisées")  # pragma: no cover


def _mistral_text(content) -> str:
    """Texte de la réponse. Avec le raisonnement activé, Mistral renvoie une liste de morceaux :
    {"type": "thinking", ...} (trace de raisonnement, ignorée) et {"type": "text", "text": ...} (réponse)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content
                       if isinstance(c, dict) and c.get("type") == "text")
    return ""


def _retry_delay(retry_after, attempt: int) -> float:
    try:
        return min(float(retry_after), 60.0)
    except (TypeError, ValueError):
        return min(2.0 * 2 ** attempt, 30.0)


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        msg = json.loads(exc.read().decode("utf-8")).get("message")
    except Exception:
        return ""
    return f" ({str(msg)[:200]})" if msg else ""


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
