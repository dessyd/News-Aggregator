"""Source NewsBlur (API) : lit les articles d'un dossier NewsBlur.

Sert pour les journaux dont les flux sont bloqués pour le collecteur (ex. Le Soir, protégé par Akamai) mais que
NewsBlur sait lire. Nécessite un compte Premium : un compte gratuit n'obtient que 3 articles (voir design/architecture-feeder.md).
Identifiants : variables d'environnement NEWSBLUR_USERNAME et NEWSBLUR_PASSWORD (secrets GitHub), jamais dans le dépôt.
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .util import article_id, is_http_url, strip_html

BASE = "https://www.newsblur.com"
UA = "revue-de-presse/1.0 (projet pedagogique)"
PAGE_SIZE = 100   # le serveur accepte 100 articles par page (constaté le 9 octobre 2026)
MAX_PAGES = 5     # plafond de sécurité : 500 articles par source
PAUSE = 1.0       # secondes entre deux pages (courtoisie envers le serveur)
TIMEOUT = 30


class NewsBlurError(Exception):
    """Erreur d'accès à NewsBlur. Le message ne contient jamais d'identifiant."""


class NewsBlurClient:
    """Client minimal : connexion par cookie de session, lecture des dossiers et des articles."""

    def __init__(self, username: str, password: str, opener=None):
        self._username = username
        self._password = password
        self._opener = opener or urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._opener.addheaders = [("User-Agent", UA)]
        self._logged_in = False
        self.last_status: int | None = None

    @classmethod
    def from_env(cls) -> "NewsBlurClient":
        username = os.environ.get("NEWSBLUR_USERNAME", "").strip()
        password = os.environ.get("NEWSBLUR_PASSWORD", "")
        if not username or not password:
            raise NewsBlurError("identifiants absents (variables NEWSBLUR_USERNAME et NEWSBLUR_PASSWORD)")
        return cls(username, password)

    def _call(self, path: str, data: dict | None = None, params: dict | None = None) -> dict:
        url = BASE + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        try:
            with self._opener.open(url, body, timeout=TIMEOUT) as rep:
                self.last_status = getattr(rep, "status", None)
                return json.loads(rep.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            self.last_status = exc.code
            raise NewsBlurError(f"HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise NewsBlurError(f"réseau : {exc.reason}") from None
        except (ValueError, UnicodeDecodeError):
            raise NewsBlurError("réponse inattendue (ce n'est pas du JSON)") from None

    def login(self) -> None:
        if self._logged_in:
            return
        rep = self._call("/api/login", data={"username": self._username, "password": self._password})
        if not rep.get("authenticated"):
            raise NewsBlurError("connexion refusée (identifiant ou mot de passe incorrect)")
        self._logged_in = True

    def folder_feed_ids(self, folder: str) -> list[int]:
        """Identifiants des flux du dossier `folder` (sous-dossiers compris) ; liste vide si introuvable."""
        self.login()
        folders = self._call("/reader/feeds").get("folders", [])

        def tous(contenu) -> list[int]:
            ids: list[int] = []
            for el in contenu:
                if isinstance(el, int):
                    ids.append(el)
                elif isinstance(el, dict):
                    for sous in el.values():
                        ids += tous(sous)
            return ids

        def cherche(contenu):
            for el in contenu:
                if isinstance(el, dict):
                    for nom, sous in el.items():
                        if nom.strip().lower() == folder.strip().lower():
                            return tous(sous)
                        trouve = cherche(sous)
                        if trouve is not None:
                            return trouve
            return None

        return cherche(folders) or []

    def river_stories(self, feed_ids: list[int], page: int) -> list[dict]:
        self.login()
        rep = self._call("/reader/river_stories", params={
            "feeds": feed_ids, "page": page, "limit": PAGE_SIZE,
            "read_filter": "all", "order": "newest", "include_story_content": "false"})
        return rep.get("stories", []) or []


def _story_date(story: dict) -> datetime | None:
    """Date UTC d'un article : `story_timestamp` (époque Unix) de préférence, sinon `story_date` lue comme UTC."""
    try:
        return datetime.fromtimestamp(int(float(story["story_timestamp"])), tz=timezone.utc)
    except (KeyError, TypeError, ValueError):
        pass
    try:
        return datetime.strptime(story["story_date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def fetch_newsblur(feed: dict, cfg: dict, now: datetime, client: NewsBlurClient | None = None):
    """Renvoie (articles, rapport) pour une source `type: newsblur` ; ne lève jamais d'exception (dégradation contrôlée)."""
    dossier = feed.get("folder") or feed["name"]
    report = {"name": feed["name"], "url": f"newsblur:{dossier}", "ok": False, "entries": 0,
              "kept": 0, "with_summary": 0, "status": None, "error": None}
    try:
        client = client or NewsBlurClient.from_env()
        ids = client.folder_feed_ids(dossier)
        if not ids:
            raise NewsBlurError(f"dossier « {dossier} » introuvable ou vide")
        cutoff = now - timedelta(hours=cfg["max_age_hours"])
        stories: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            lot = client.river_stories(ids, page)
            if not lot:
                break
            stories += lot
            dates = [d for d in map(_story_date, lot) if d]
            if dates and min(dates) < cutoff:   # les articles arrivent du plus récent au plus ancien
                break
            if page < MAX_PAGES:
                time.sleep(PAUSE)
        report["status"] = client.last_status
    except NewsBlurError as exc:
        report["status"] = getattr(client, "last_status", None)
        report["error"] = str(exc)
        return [], report

    report["entries"] = len(stories)
    if not stories:
        report["error"] = "aucun article dans le dossier"
        return [], report
    report["ok"] = True
    articles = []
    for s in stories:
        title = strip_html(s.get("story_title"))
        link = (s.get("story_permalink") or "").strip()
        published = _story_date(s)
        if not title or not is_http_url(link):
            continue
        if published and published < cutoff:
            continue
        extrait = strip_html(s.get("story_content"))[: cfg["extrait_max_chars"]]
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
