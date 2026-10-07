"""Collecte des flux RSS, normalisation et dédoublonnage (sans LLM)."""
from __future__ import annotations

import difflib
import re
import socket
from datetime import datetime, timedelta, timezone

import feedparser
import yaml

from .util import article_id, is_http_url, strip_html

UA = "revue-de-presse/1.0 (projet pedagogique)"


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


def fetch_feed(feed: dict, cfg: dict, now: datetime, parse=feedparser.parse):
    """Renvoie (articles, rapport) pour un flux."""
    report = {"name": feed["name"], "url": feed["url"], "ok": False, "entries": 0,
              "kept": 0, "with_summary": 0, "error": None}
    socket.setdefaulttimeout(25)
    try:
        d = parse(feed["url"], agent=UA)
    except Exception as exc:  # réseau, certificat, etc.
        report["error"] = str(exc)
        return [], report
    entries = d.get("entries", []) or []
    if not entries:
        exc = d.get("bozo_exception")
        report["error"] = str(exc) if exc else "aucune entrée (flux vide ou URL incorrecte)"
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
