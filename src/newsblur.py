"""Source NewsBlur (API) : lit les articles d'un dossier NewsBlur.

Sert pour les journaux dont les flux sont bloqués pour le collecteur (ex. Le Soir, protégé par Akamai) mais que
NewsBlur sait lire. Nécessite un compte Premium : un compte gratuit n'obtient que 3 articles (voir design/architecture-feeder.md).
Identifiants : variables d'environnement NEWSBLUR_USERNAME et NEWSBLUR_PASSWORD (secrets GitHub), jamais dans le dépôt.

Entrée de config/feeds.yaml : `type: newsblur`, `folder` (nom du dossier NewsBlur) et, facultatif, `site` (texte que doit
contenir l'adresse du flux ou du site, ex. `lesoir.be`). Tous les articles lus portent le nom de l'entrée : un dossier qui
mélangerait plusieurs journaux est donc refusé, sauf si `site` désigne celui à lire.
"""
from __future__ import annotations

import http.client
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
    """Client minimal : connexion par cookie de session, lecture des dossiers et des articles.

    Un seul client sert toutes les entrées d'une exécution : une connexion, un téléchargement des abonnements.
    """

    def __init__(self, username: str, password: str, opener=None):
        self._username = username
        self._password = password
        self._opener = opener or urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._opener.addheaders = [("User-Agent", UA)]
        self._logged_in = False
        self._login_error: str | None = None   # un échec de connexion n'est pas retenté (pas de verrouillage du compte)
        self._subscriptions: dict | None = None
        self.last_status: int | None = None

    @classmethod
    def from_env(cls) -> "NewsBlurClient":
        """Client construit depuis l'environnement. Ne lève rien : des identifiants absents sont signalés à la connexion."""
        return cls(os.environ.get("NEWSBLUR_USERNAME", "").strip(), os.environ.get("NEWSBLUR_PASSWORD", ""))

    def _call(self, path: str, data: dict | None = None, params: dict | None = None) -> dict:
        url = BASE + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        try:
            with self._opener.open(url, body, timeout=TIMEOUT) as rep:
                self.last_status = getattr(rep, "status", None)
                payload = json.loads(rep.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            self.last_status = exc.code
            raise NewsBlurError(f"HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise NewsBlurError(f"réseau : {exc.reason}") from None
        except (OSError, http.client.HTTPException) as exc:   # délai de lecture, connexion coupée, lecture incomplète
            raise NewsBlurError(f"réseau : {type(exc).__name__}") from None
        except (ValueError, UnicodeDecodeError):
            raise NewsBlurError("réponse inattendue (ce n'est pas du JSON)") from None
        if not isinstance(payload, dict):
            raise NewsBlurError("réponse inattendue (objet JSON attendu)")
        return payload

    def login(self) -> None:
        if self._logged_in:
            return
        if self._login_error:
            raise NewsBlurError(self._login_error)
        try:
            if not self._username or not self._password:
                raise NewsBlurError("identifiants absents (variables NEWSBLUR_USERNAME et NEWSBLUR_PASSWORD)")
            rep = self._call("/api/login", data={"username": self._username, "password": self._password})
            if not rep.get("authenticated"):
                raise NewsBlurError("connexion refusée (identifiant ou mot de passe incorrect)")
        except NewsBlurError as exc:
            self._login_error = str(exc)
            raise
        self._logged_in = True

    def _load_subscriptions(self) -> dict:
        if self._subscriptions is None:
            self.login()
            data = self._call("/reader/feeds")
            if not isinstance(data.get("folders", []), list) or not isinstance(data.get("feeds", {}), (dict, list)):
                raise NewsBlurError("réponse inattendue (abonnements)")
            self._subscriptions = data
        return self._subscriptions

    def folder_feeds(self, folder: str) -> list[dict]:
        """Flux du dossier `folder` (sous-dossiers compris) : [{id, title, address, link}] ; liste vide si introuvable."""
        data = self._load_subscriptions()
        feeds = data.get("feeds", {})
        feeds = feeds if isinstance(feeds, dict) else {str(f.get("id")): f for f in feeds if isinstance(f, dict)}

        def tous(contenu) -> list[int]:
            ids: list[int] = []
            for el in contenu:
                if isinstance(el, int):
                    ids.append(el)
                elif isinstance(el, dict):
                    for sous in el.values():
                        ids += tous(sous) if isinstance(sous, list) else []
            return ids

        def cherche(contenu):
            for el in contenu:
                if isinstance(el, dict):
                    for nom, sous in el.items():
                        if not isinstance(sous, list):
                            continue
                        if nom.strip().lower() == folder.strip().lower():
                            return tous(sous)
                        trouve = cherche(sous)
                        if trouve is not None:
                            return trouve
            return None

        out = []
        for i in cherche(data.get("folders", [])) or []:
            meta = feeds.get(str(i)) if isinstance(feeds.get(str(i)), dict) else {}
            out.append({"id": i, "title": meta.get("feed_title") or "",
                        "address": meta.get("feed_address") or "", "link": meta.get("feed_link") or ""})
        return out

    def river_stories(self, feed_ids: list[int], page: int) -> list[dict]:
        self.login()
        # include_hidden : sans lui, NewsBlur retire les articles masqués par le filtrage personnel du compte
        # (et des pages peuvent revenir vides), ce qui ne doit pas influer sur une revue publique.
        rep = self._call("/reader/river_stories", params={
            "feeds": feed_ids, "page": page, "limit": PAGE_SIZE, "read_filter": "all", "order": "newest",
            "include_story_content": "false", "include_hidden": "true"})
        stories = rep.get("stories", [])
        if not isinstance(stories, list):
            raise NewsBlurError("réponse inattendue (articles)")
        return [s for s in stories if isinstance(s, dict)]


def _site(feed: dict) -> str:
    """Domaine d'un flux (deux derniers éléments du nom d'hôte), pour repérer un dossier qui mélange plusieurs journaux."""
    host = urllib.parse.urlsplit(feed.get("link") or feed.get("address") or "").hostname or ""
    return ".".join(host.split(".")[-2:])


def _story_date(story: dict) -> datetime | None:
    """Date UTC d'un article : `story_timestamp` (époque Unix) de préférence, sinon `story_date` lue comme UTC."""
    try:
        return datetime.fromtimestamp(int(float(story["story_timestamp"])), tz=timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        pass
    try:
        return datetime.strptime(story["story_date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def _select_feeds(feeds: list[dict], feed: dict, dossier: str) -> list[int]:
    """Identifiants à lire. Refuse un dossier de plusieurs journaux, car tous porteraient le nom de l'entrée."""
    if not feeds:
        raise NewsBlurError(f"dossier « {dossier} » introuvable ou vide")
    site = str(feed.get("site") or "").strip().lower()
    if site:
        feeds = [f for f in feeds if site in f["address"].lower() or site in f["link"].lower()]
        if not feeds:
            raise NewsBlurError(f"aucun flux du dossier « {dossier} » ne correspond à site: {site}")
    else:
        sites = sorted({s for s in map(_site, feeds) if s})
        if len(sites) > 1:
            raise NewsBlurError(
                f"dossier « {dossier} » mixte ({', '.join(sites)}) : tous les articles seraient étiquetés « {feed['name']} » ; "
                "mettre un journal par dossier ou préciser `site:`")
    return [f["id"] for f in feeds]


def _fetch(feed: dict, cfg: dict, now: datetime, client: NewsBlurClient, report: dict, dossier: str):
    ids = _select_feeds(client.folder_feeds(dossier), feed, dossier)
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

    report["entries"] = len(stories)
    if not stories:
        raise NewsBlurError("aucun article dans le dossier")
    report["ok"] = True
    articles = []
    for s in stories:
        title = strip_html(s.get("story_title"))
        link = str(s.get("story_permalink") or "").strip()
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
    return articles


def fetch_newsblur(feed: dict, cfg: dict, now: datetime, client: NewsBlurClient | None = None):
    """Renvoie (articles, rapport) pour une source `type: newsblur`.

    Ne lève jamais d'exception (dégradation contrôlée) : toute erreur est consignée dans le rapport et la revue continue.
    """
    dossier = feed.get("folder") or feed["name"]
    report = {"name": feed["name"], "url": f"newsblur:{dossier}", "ok": False, "entries": 0,
              "kept": 0, "with_summary": 0, "status": None, "error": None}
    client = client or NewsBlurClient.from_env()
    try:
        return _fetch(feed, cfg, now, client, report, dossier), report
    except NewsBlurError as exc:
        report["error"] = str(exc)
    except Exception as exc:  # filet de sécurité : une source défaillante ne doit jamais interrompre la revue
        report["error"] = f"erreur inattendue : {type(exc).__name__}"
    finally:
        report["status"] = getattr(client, "last_status", None)
    report["ok"], report["kept"] = False, 0
    return [], report
