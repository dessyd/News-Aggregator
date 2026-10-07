"""Fonctions utilitaires : fichiers JSON, nettoyage HTML, URL, prompts, dates."""
from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]

_TRACKING = re.compile(r"^(utm_|fbclid|gclid|xtor|cmp)", re.I)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def fr_date(d) -> str:
    """Date en français : « mardi 6 octobre 2026 »."""
    jour = "1er" if d.day == 1 else str(d.day)
    return f"{JOURS[d.weekday()]} {jour} {MOIS[d.month - 1]} {d.year}"


def strip_html(text: str | None) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text or "", flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def is_http_url(url: str | None) -> bool:
    return bool(url) and url.strip().lower().startswith(("http://", "https://"))


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not _TRACKING.match(k)]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def article_id(url: str) -> str:
    return "a" + hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:8]


def strip_comments(text: str) -> str:
    """Supprime les commentaires <!-- ... --> d'un fichier de prompt."""
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def render_prompt(text: str, **variables) -> str:
    """Remplace {{variable}} (sans toucher aux accolades JSON du reste du texte)."""
    for key, value in variables.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


def extract_json(text: str):
    """Extrait le premier objet/tableau JSON d'une réponse (tolère les ```json ... ```)."""
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S | re.I)
    if fence:
        t = fence.group(1).strip()
    decoder = json.JSONDecoder()
    for i, ch in enumerate(t):
        if ch in "{[":
            try:
                obj, _ = decoder.raw_decode(t[i:])
                return obj
            except json.JSONDecodeError:
                continue
    raise ValueError("Aucun JSON valide dans la réponse du modèle")


def clamp_int(value, low, high, default):
    try:
        return max(low, min(high, int(float(value))))
    except (TypeError, ValueError):
        return default
