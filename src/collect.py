"""Collecte des flux RSS, normalisation et dédoublonnage (sans LLM)."""
from __future__ import annotations

import difflib
import os
import re
import socket
import time
from datetime import datetime, timedelta, timezone

import feedparser
import yaml

from .util import article_id, is_http_url, strip_html

UA = "revue-de-presse/1.0 (projet pedagogique)"
RETRY_PAUSE = 3  # secondes avant la relance d'un flux


def _lire_flux(url: str, parse):
    """Lit un flux ; une seule relance si la 1re tentative échoue ou ne renvoie rien (pannes ponctuelles observées).

    Renvoie (résultat, exception) : l'exception levée par la lecture, ou None.
    """
    for tentative in (1, 2):
        try:
            d, erreur = parse(url, agent=UA), None
        except Exception as exc:  # réseau, certificat, etc.
            d, erreur = {}, exc
        if d.get("entries") or tentative == 2:
            return d, erreur
        time.sleep(RETRY_PAUSE)


def load_feeds(path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return [f for f in data.get("feeds", []) if f.get("enabled", True)]


def _entry_date(entry):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def _norm_title(title: str) -> str:
    return re.sub(r"[^\w ]+", "", title.lower()).strip()


def _resolve_url(feed: dict) -> tuple[str, str, str | None]:
    """Renvoie (adresse à lire, étiquette affichable, erreur de configuration).

    Une entrée `url_env: NOM` lit son adresse dans la variable d'environnement NOM (secret GitHub) : l'adresse d'un flux
    de sortie de lecteur contient un jeton et ne doit jamais figurer dans le dépôt public, ni dans le rapport de collecte.
    L'étiquette `env:NOM` la remplace partout où l'adresse serait affichée ou enregistrée.
    """
    env = feed.get("url_env")
    if env:
        label = f"env:{env}"
        url = os.environ.get(env, "").strip()
        if not url:
            return "", label, f"variable d'environnement {env} absente ou vide"
        if not is_http_url(url):
            return "", label, f"variable d'environnement {env} : valeur invalide (adresse http ou https attendue)"
        return url, label, None
    if feed.get("url"):
        return feed["url"], feed["url"], None
    return "", feed["name"], "ni `url` ni `url_env` dans l'entrée"


def fetch_feed(feed: dict, cfg: dict, now: datetime, parse=feedparser.parse):
    """Renvoie (articles, rapport) pour un flux."""
    url, label, config_error = _resolve_url(feed)
    report = {"name": feed["name"], "url": label, "ok": False, "entries": 0,
              "kept": 0, "with_summary": 0, "status": None, "error": None}
    if config_error:
        report["error"] = config_error
        return [], report
    socket.setdefaulttimeout(25)
    d, erreur = _lire_flux(url, parse)
    report["status"] = d.get("status")  # statut HTTP de la dernière tentative (None si aucune réponse)
    entries = d.get("entries", []) or []
    if not entries:
        exc = erreur or d.get("bozo_exception")
        if exc is None:
            report["error"] = "aucune entrée (flux vide ou URL incorrecte)"
        elif feed.get("url_env"):
            # Fail-closed : le texte d'une erreur peut citer l'adresse (en entier, en partie, ou une adresse de redirection)
            # et le rapport de collecte est public ; pour une adresse secrète, seul le type de l'erreur est conservé.
            report["error"] = f"{label} : erreur de lecture ({type(exc).__name__})"
        else:
            report["error"] = str(exc)
        return [], report

    report["ok"] = True
    report["entries"] = len(entries)
    cutoff = now - timedelta(hours=cfg["max_age_hours"])
    articles = []
    for e in entries:
        title = strip_html(e.get("title"))
        link = (e.get("link") or "").strip()
        if not title or not is_http_url(link):
            continue
        published = _entry_date(e)
        if published and published < cutoff:
            continue
        raw = e.get("summary") or ((e.get("content") or [{}])[0].get("value")) or ""
        extrait = strip_html(raw)[: cfg["extrait_max_chars"]]
        if extrait:
            report["with_summary"] += 1
        articles.append({
            "id": article_id(link),
            "source": feed["name"],
            "lang": feed.get("lang", "fr"),
            "titre": title,
            "extrait": extrait,
            "url": link,
            "publie": published.isoformat() if published else None,
        })
    articles.sort(key=lambda a: a["publie"] or "", reverse=True)
    articles = articles[: cfg["max_articles_per_feed"]]
    report["kept"] = len(articles)
    return articles, report


def dedupe(articles: list[dict], threshold: float) -> list[dict]:
    """Supprime les doublons d'URL et les titres quasi identiques.

    Le doublon n'est pas perdu : sa source est conservée dans « autres_sources »
    (elle sera citée comme source supplémentaire du sujet).
    """
    kept: list[dict] = []
    norms: list[str] = []
    seen_ids: set[str] = set()
    for a in sorted(articles, key=lambda x: -len(x["extrait"])):  # on garde la version la plus riche
        if a["id"] in seen_ids:
            continue
        seen_ids.add(a["id"])
        norm = _norm_title(a["titre"])
        dup_index = None
        for i, other in enumerate(norms):
            if difflib.SequenceMatcher(None, norm, other).ratio() >= threshold:
                dup_index = i
                break
        if dup_index is not None:
            kept[dup_index].setdefault("autres_sources", []).append(
                {"source": a["source"], "titre": a["titre"], "url": a["url"]})
            continue
        kept.append(dict(a))
        norms.append(norm)
    kept.sort(key=lambda a: a["publie"] or "", reverse=True)
    return kept


def sample(articles: list[dict], n: int) -> list[dict]:
    """Prend n articles en alternant les sources (utile pour les essais économiques)."""
    by_source: dict[str, list[dict]] = {}
    for a in articles:
        by_source.setdefault(a["source"], []).append(a)
    out: list[dict] = []
    while len(out) < n and any(by_source.values()):
        for lst in by_source.values():
            if lst and len(out) < n:
                out.append(lst.pop(0))
    return out


def collect(feeds: list[dict], cfg: dict, now: datetime | None = None, parse=feedparser.parse):
    now = now or datetime.now(timezone.utc)
    all_articles: list[dict] = []
    reports = []
    for feed in feeds:
        arts, rep = fetch_feed(feed, cfg, now, parse=parse)
        all_articles.extend(arts)
        reports.append(rep)
    articles = dedupe(all_articles, cfg["title_similarity"])
    if len(articles) > cfg["max_articles_total"]:
        articles = articles[: cfg["max_articles_total"]]
    return articles, reports
