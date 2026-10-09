"""Tests du pipeline avec un faux modèle (aucun appel réseau, aucune clé API)."""
import http.client
import json
import re
import sys
import urllib.error
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import feedparser
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.collect
import src.newsblur
from src.collect import collect, dedupe, sample
from src.llm import call_json
from src.newsblur import NewsBlurClient, NewsBlurError, fetch_newsblur
from src.pipeline import Pipeline
from src.render import publish, render_compare, render_digest
from src.util import article_id, extract_json, fr_date, normalize_url, strip_html

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
DAY = date(2026, 10, 6)


def user_data(user: str):
    m = re.search(r"<donnees>\n(.*)\n</donnees>", user, re.S)
    return json.loads(m.group(1))


class FakeLLM:
    """Simule le modèle, avec volontairement quelques défauts à rattraper."""

    def __init__(self, fenced=False, bad_stage=None):
        self.usage = {}
        self.calls = []
        self.fenced = fenced
        self.bad_stage = bad_stage

    def complete(self, model, system, user, max_tokens):
        stage = int(re.search(r"\[\[stage:(\d)\]\]", system).group(1))
        self.calls.append(stage)
        if stage == self.bad_stage:
            return "désolé, je ne peux pas répondre en JSON"
        data = user_data(user)
        if stage == 1:
            out = {"articles": [
                {"id": a["id"], "resume": f"Résumé de {a['titre']}",
                 "rubrique": "Belgique" if "Bruxelles" in a["titre"] else "Rubrique inventée",
                 "importance": 5 if "Bruxelles" in a["titre"] else "4"} for a in data[:-1]]}
            # le dernier article est volontairement omis par le « modèle »
        elif stage == 2:
            ids = [a["id"] for a in data if "Bruxelles" in a["titre"]]
            others = [a["id"] for a in data if "Bruxelles" not in a["titre"]]
            out = {"sujets": [
                {"titre": "Incendie à Bruxelles", "ids": ids + ["a_inconnu"]},
                {"titre": "Autre sujet", "ids": others[:1] + ids[:1]},  # id déjà utilisé : doit être ignoré
            ]}
        elif stage == 3:
            ids = [a["id"] for a in data["articles"]]
            out = {"titre": data["sujet"], "texte": f"Synthèse de {data['sujet']}.",
                   "rubrique": "Belgique", "ids_sources": ids[:1] + ["faux-id"]}
        else:
            n = len(data)
            out = {"chapeau": "L'essentiel du jour.", "ordre": list(reversed(range(n))) + [99]}
        text = json.dumps(out, ensure_ascii=False)
        return f"```json\n{text}\n```" if self.fenced else text


def fake_parse_factory():
    def entry(title, link, summary="Extrait <b>HTML</b> &amp; texte", hour=6):
        return {"title": title, "link": link, "summary": summary,
                "published_parsed": (2026, 10, 6, hour, 0, 0, 0, 0, 0)}

    feeds = {
        "http://a.test/rss": [entry("Incendie à Bruxelles : un quartier évacué", "http://a.test/1?utm_source=x"),
                              entry("Élections : résultats attendus", "http://a.test/2")],
        "http://b.test/rss": [entry("Incendie à Bruxelles : un quartier évacué !", "http://b.test/9", summary=""),
                              entry("Vieille nouvelle", "http://b.test/old", hour=6) | {"published_parsed": (2026, 9, 1, 0, 0, 0, 0, 0, 0)},
                              entry("Lien dangereux", "javascript:alert(1)")],
        "http://c.test/rss": [],
    }

    def parse(url, agent=None):
        return feedparser.FeedParserDict({"entries": feeds[url]})
    return parse


FEEDS = [{"name": "A", "url": "http://a.test/rss"}, {"name": "B", "url": "http://b.test/rss"},
         {"name": "C", "url": "http://c.test/rss"}]
NOW = datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_retry_pause(monkeypatch):
    monkeypatch.setattr(src.collect, "RETRY_PAUSE", 0)
    monkeypatch.setattr(src.newsblur, "PAUSE", 0)


@pytest.fixture
def articles():
    arts, report = collect(FEEDS, SETTINGS["collect"], now=NOW, parse=fake_parse_factory())
    return arts, report


# ------------------------------------------------------------------ collecte
def test_collect_filters_dedupes_and_reports(articles):
    arts, report = articles
    titles = [a["titre"] for a in arts]
    assert len(arts) == 2, titles                       # doublon fusionné, vieil article et lien javascript: écartés
    assert not any("javascript" in a["url"] for a in arts)
    incendie = next(a for a in arts if "Incendie" in a["titre"])
    assert incendie["extrait"] == "Extrait HTML & texte"  # HTML nettoyé, version la plus riche conservée
    assert incendie["autres_sources"][0]["source"] == "B"  # la source doublon n'est pas perdue
    assert [r["ok"] for r in report] == [True, True, False]  # flux vide signalé


def flaky_parse(failures, good="http://a.test/rss", error=None):
    """Échoue `failures` fois (entrées vides, ou exception si `error`) puis renvoie le flux normal."""
    ok, calls = fake_parse_factory(), []

    def parse(url, agent=None):
        calls.append(url)
        if len(calls) <= failures:
            if error:
                raise error
            return feedparser.FeedParserDict({"entries": [], "bozo_exception": ValueError("XML mal formé")})
        return ok(good)
    return parse, calls


@pytest.mark.parametrize("error", [None, OSError("réseau")])
def test_collect_retries_once_after_a_failed_read(error):
    parse, calls = flaky_parse(failures=1, error=error)
    arts, report = collect(FEEDS[:1], SETTINGS["collect"], now=NOW, parse=parse)
    assert len(calls) == 2                               # une seule relance
    assert report[0]["ok"] and report[0]["kept"] == 2 and report[0]["error"] is None
    assert len(arts) == 2


def test_collect_gives_up_after_one_retry():
    parse, calls = flaky_parse(failures=5)
    arts, report = collect(FEEDS[:1], SETTINGS["collect"], now=NOW, parse=parse)
    assert len(calls) == 2                               # jamais plus d'une relance
    assert arts == [] and not report[0]["ok"] and "XML mal formé" in report[0]["error"]


def test_collect_reports_http_status():
    def parse(url, agent=None):
        if "ko" in url:
            return feedparser.FeedParserDict({"status": 503, "entries": [], "bozo_exception": ValueError("XML mal formé")})
        if "down" in url:
            raise OSError("réseau")
        return feedparser.FeedParserDict({"status": 200, "entries": fake_parse_factory()("http://a.test/rss")["entries"]})

    feeds = [{"name": n, "url": f"http://{n}.test/rss"} for n in ("ok", "ko", "down")]
    _, report = collect(feeds, SETTINGS["collect"], now=NOW, parse=parse)
    assert [r["status"] for r in report] == [200, 503, None]   # None : pas de réponse (exception réseau)
    assert [r["ok"] for r in report] == [True, False, False]


def test_collect_does_not_retry_a_healthy_feed():
    parse, calls = flaky_parse(failures=0)
    collect(FEEDS[:1], SETTINGS["collect"], now=NOW, parse=parse)
    assert len(calls) == 1


def test_helpers():
    assert normalize_url("HTTP://X.test/a/?utm_source=1&id=2#frag") == "http://x.test/a?id=2"
    assert strip_html("<p>a&nbsp;<b>b</b></p><script>x()</script>") == "a b"
    assert fr_date(date(2026, 10, 1)) == "jeudi 1er octobre 2026"
    assert extract_json('Voici :\n```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}
    with pytest.raises(ValueError):
        extract_json("pas de json")
    assert len(sample([{"source": s, "id": i} for i, s in enumerate("AAAABB")], 3)) == 3


def test_call_json_retries_once():
    class Flaky:
        n = 0

        def complete(self, *a):
            self.n += 1
            return "oups" if self.n == 1 else '{"ok": true}'
    llm = Flaky()
    assert call_json(llm, "m", "s", "u", 10) == {"ok": True}
    assert llm.n == 2


# ------------------------------------------------------------------ pipeline
def run_pipeline(tmp_path, articles, llm, **kw):
    arts, report = articles
    pipe = Pipeline(SETTINGS, ROOT / "prompts", llm, tmp_path / "run", DAY, log=lambda *_: None)
    return pipe, pipe.run(arts, collect_report=report, **kw)


def test_full_pipeline_guards(tmp_path, articles):
    pipe, digest = run_pipeline(tmp_path, articles, FakeLLM(fenced=True))
    assert llm_stages(pipe) == [1, 2, 3, 4]
    arts_urls = {a["url"] for a in articles[0]} | {s["url"] for a in articles[0] for s in a.get("autres_sources", [])}
    for t in digest["topics"]:
        assert t["sources"], "chaque sujet doit citer au moins une source"
        for s in t["sources"]:
            assert s["url"] in arts_urls, "aucune URL inventée"
        assert t["rubrique"] in SETTINGS["rubriques"]
    warns = " | ".join(digest["warnings"])
    assert "sans résumé du modèle" in warns          # article omis par le modèle
    assert "identifiant(s) inconnu(s)" in warns       # id inventé à l'étape 2
    assert "source inconnue" in warns                 # id inventé à l'étape 3
    assert digest["chapeau"] == "L'essentiel du jour."
    for name in ("stage1.json", "stage2.json", "stage3.json", "stage4.json", "digest.json"):
        assert (tmp_path / "run" / name).exists()
    assert (tmp_path / "run" / "prompts" / "1_resume_articles.md").exists()


def llm_stages(pipe):
    return sorted(set(pipe.llm.calls))


def test_degraded_when_model_returns_garbage(tmp_path, articles):
    pipe, digest = run_pipeline(tmp_path, articles, FakeLLM(bad_stage=2))
    assert any("regroupement impossible" in w for w in digest["warnings"])
    assert isinstance(digest["topics"], list)


def test_replay_from_stage_3_reuses_previous_stages(tmp_path, articles):
    _, _ = run_pipeline(tmp_path, articles, FakeLLM())
    llm2 = FakeLLM()
    pipe2 = Pipeline(SETTINGS, ROOT / "prompts", llm2, tmp_path / "essai", DAY, log=lambda *_: None)
    digest2 = pipe2.run(from_stage=3, base_dir=tmp_path / "run")
    assert set(llm2.calls) == {3, 4}                  # aucune relance des étapes 1 et 2
    assert digest2["topics"]


def test_replay_requires_base(tmp_path):
    pipe = Pipeline(SETTINGS, ROOT / "prompts", FakeLLM(), tmp_path / "x", DAY, log=lambda *_: None)
    with pytest.raises(ValueError):
        pipe.run(from_stage=2)


def test_prompt_comments_and_variables(tmp_path):
    pipe = Pipeline(SETTINGS, ROOT / "prompts", FakeLLM(), tmp_path / "x", DAY, log=lambda *_: None)
    system = pipe._system(1)
    assert "<!--" not in system                       # commentaires destinés à l'éditeur, non envoyés
    assert "mardi 6 octobre 2026" in system
    assert "Belgique, Europe" in system
    assert "{{" not in system.replace('{"articles"', "")  # toutes les variables remplacées


# ------------------------------------------------------------------ rendu
def test_render_and_publish(tmp_path, articles):
    _, digest = run_pipeline(tmp_path, articles, FakeLLM())
    digest["topics"][0]["texte"] = "<script>alert(1)</script> texte"
    html = render_digest(digest, SETTINGS)
    assert "<script>alert(1)</script>" not in html    # échappement HTML
    assert 'name="robots" content="noindex' in html
    docs = tmp_path / "docs"
    publish(digest, docs, SETTINGS)
    assert (docs / "index.html").exists()
    assert (docs / "archives" / "2026-10-06.html").exists()
    assert "Mardi 6 octobre 2026" in (docs / "archives.html").read_text(encoding="utf-8")
    cmp_html = render_compare(digest, digest, "main", "essai", SETTINGS)
    assert "main" in cmp_html and "essai" in cmp_html


# ------------------------------------------------------------------ NewsBlur (API)
NEWS = {"name": "Le Soir", "type": "newsblur", "folder": "Le Soir", "lang": "fr"}


def story(i, hours_ago=1, **kw):
    ts = int((NOW - timedelta(hours=hours_ago)).timestamp())
    return {"story_title": f"Titre {i}", "story_permalink": f"http://soir.test/{i}",
            "story_content": f"<p>Extrait <b>{i}</b> &amp; suite</p>", "story_timestamp": str(ts), **kw}


class FakeNewsBlur:
    """Faux client NewsBlur : `pages` est la liste des pages d'articles ; garde la trace des appels."""

    def __init__(self, pages, ids=(1,), error=None):
        self.pages, self.ids, self.error, self.calls, self.last_status = pages, list(ids), error, [], 200

    def folder_feeds(self, folder):
        if self.error:
            raise self.error
        self.calls.append(("dossier", folder))
        return [{"id": i, "title": f"Flux {i}", "address": "http://soir.test/rss", "link": "http://soir.test/"}
                for i in self.ids]

    def river_stories(self, ids, page):
        self.calls.append(("page", page))
        return self.pages[page - 1] if page <= len(self.pages) else []


def test_newsblur_maps_stories_to_articles():
    pages = [[story(1), story(2, hours_ago=40), story(3, story_permalink="javascript:alert(1)"),
              story(4, story_title="  "), story(5, hours_ago=2, story_content="x" * 2000)]]
    arts, rep = fetch_newsblur(NEWS, SETTINGS["collect"], NOW, client=FakeNewsBlur(pages))
    assert [a["titre"] for a in arts] == ["Titre 1", "Titre 5"]     # trop ancien, lien dangereux, titre vide : écartés
    first = arts[0]
    assert first["source"] == "Le Soir" and first["lang"] == "fr"
    assert first["extrait"] == "Extrait 1 & suite"                  # HTML nettoyé
    assert first["url"] == "http://soir.test/1" and first["id"] == article_id(first["url"])
    assert first["publie"] == (NOW - timedelta(hours=1)).isoformat()   # date UTC issue de story_timestamp
    assert len(arts[1]["extrait"]) == SETTINGS["collect"]["extrait_max_chars"]   # jamais de texte intégral (D7)
    assert rep == {"name": "Le Soir", "url": "newsblur:Le Soir", "ok": True, "entries": 5, "kept": 2,
                   "with_summary": 2, "status": 200, "error": None}


def test_newsblur_date_falls_back_to_story_date_as_utc():
    s = story(1)
    del s["story_timestamp"]
    s["story_date"] = "2026-10-06 07:00:00"
    arts, _ = fetch_newsblur(NEWS, SETTINGS["collect"], NOW, client=FakeNewsBlur([[s]]))
    assert arts[0]["publie"] == datetime(2026, 10, 6, 7, 0, tzinfo=timezone.utc).isoformat()


def test_newsblur_pagination_stops_when_stories_get_too_old_or_pages_run_out(monkeypatch):
    # une page contient un article plus vieux que la limite : la page suivante n'est pas demandée
    cli = FakeNewsBlur([[story(1), story(2)], [story(3), story(4, hours_ago=40)], [story(5)]])
    arts, _ = fetch_newsblur(NEWS, SETTINGS["collect"], NOW, client=cli)
    assert [c for c in cli.calls if c[0] == "page"] == [("page", 1), ("page", 2)]
    assert [a["titre"] for a in arts] == ["Titre 1", "Titre 2", "Titre 3"]
    # une page vide termine la lecture
    cli = FakeNewsBlur([[story(1)]])
    fetch_newsblur(NEWS, SETTINGS["collect"], NOW, client=cli)
    assert [c for c in cli.calls if c[0] == "page"] == [("page", 1), ("page", 2)]
    # plafond de pages
    monkeypatch.setattr(src.newsblur, "MAX_PAGES", 2)
    cli = FakeNewsBlur([[story(i)] for i in range(1, 6)])
    fetch_newsblur(NEWS, SETTINGS["collect"], NOW, client=cli)
    assert [c for c in cli.calls if c[0] == "page"] == [("page", 1), ("page", 2)]


def test_newsblur_failures_are_reported_not_raised(monkeypatch):
    arts, rep = fetch_newsblur(NEWS, SETTINGS["collect"], NOW,
                               client=FakeNewsBlur([], error=NewsBlurError("connexion refusée")))
    assert arts == [] and not rep["ok"] and "connexion refusée" in rep["error"]
    _, rep = fetch_newsblur(NEWS, SETTINGS["collect"], NOW, client=FakeNewsBlur([], ids=()))
    assert not rep["ok"] and "introuvable ou vide" in rep["error"]
    _, rep = fetch_newsblur(NEWS, SETTINGS["collect"], NOW, client=FakeNewsBlur([]))
    assert not rep["ok"] and "aucun article" in rep["error"]
    # identifiants absents : signalé, sans appel réseau
    monkeypatch.delenv("NEWSBLUR_USERNAME", raising=False)
    monkeypatch.delenv("NEWSBLUR_PASSWORD", raising=False)
    arts, rep = fetch_newsblur(NEWS, SETTINGS["collect"], NOW)
    assert arts == [] and not rep["ok"] and "NEWSBLUR_USERNAME" in rep["error"]


def test_collect_mixes_rss_and_newsblur_and_survives_a_newsblur_failure():
    cli = FakeNewsBlur([[story(1), story(2)]])
    arts, report = collect([FEEDS[0], NEWS], SETTINGS["collect"], now=NOW, parse=fake_parse_factory(), newsblur=cli)
    assert [r["name"] for r in report] == ["A", "Le Soir"] and all(r["ok"] for r in report)
    assert {a["source"] for a in arts} == {"A", "Le Soir"}
    down = FakeNewsBlur([], error=NewsBlurError("HTTP 503"))
    arts, report = collect([FEEDS[0], NEWS], SETTINGS["collect"], now=NOW, parse=fake_parse_factory(), newsblur=down)
    assert [r["ok"] for r in report] == [True, False] and arts and {a["source"] for a in arts} == {"A"}


class FakeHTTP:
    status = 200

    def __init__(self, data):
        self.body = json.dumps(data).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


class FakeOpener:
    """Faux opener urllib : `routes` associe un fragment d'URL à une réponse JSON (ou à une exception)."""
    addheaders = []

    def __init__(self, routes):
        self.routes, self.urls, self.bodies = routes, [], []

    def open(self, url, data=None, timeout=0):
        self.urls.append(url)
        self.bodies.append(data)
        for fragment, rep in self.routes.items():
            if fragment in url:
                if isinstance(rep, Exception):
                    raise rep
                return FakeHTTP(rep(url, data) if callable(rep) else rep)
        raise AssertionError(f"appel inattendu : {url}")

    def count(self, fragment):
        return sum(fragment in u for u in self.urls)


def test_newsblur_client_login_folders_and_stories():
    folders = [13, {"Belgique": [11, {"Sous-dossier": [12]}]}]
    opener = FakeOpener({"/api/login": {"authenticated": True}, "/reader/feeds": {"folders": folders},
                         "/reader/river_stories": {"stories": [story(1)]}})
    client = NewsBlurClient("utilisateur", "motdepasse-secret", opener=opener)
    assert [f["id"] for f in client.folder_feeds("belgique")] == [11, 12]   # insensible à la casse, sous-dossiers compris
    assert client.folder_feeds("Inconnu") == []
    assert client.river_stories([11, 12], 2) == [story(1)]
    assert sum("/api/login" in u for u in opener.urls) == 1          # une seule connexion par exécution
    assert "motdepasse-secret" not in " ".join(opener.urls)          # le mot de passe ne figure jamais dans une URL
    stories_url = next(u for u in opener.urls if "/reader/river_stories" in u)
    assert "feeds=11&feeds=12" in stories_url and "limit=100" in stories_url and "read_filter=all" in stories_url


def test_newsblur_client_errors_never_reveal_credentials():
    refused = NewsBlurClient("u", "motdepasse-secret", opener=FakeOpener({"/api/login": {"authenticated": False}}))
    with pytest.raises(NewsBlurError) as exc:
        refused.login()
    assert "motdepasse-secret" not in str(exc.value)
    http403 = urllib.error.HTTPError("https://www.newsblur.com/api/login", 403, "Forbidden", {}, None)
    blocked = NewsBlurClient("u", "motdepasse-secret", opener=FakeOpener({"/api/login": http403}))
    with pytest.raises(NewsBlurError, match="HTTP 403"):
        blocked.login()
    assert blocked.last_status == 403


# ------------------------------------------------------------------ NewsBlur : défauts relevés à la revue (PR #8)
def nb_server(folders, feeds, stories=()):
    """Faux serveur NewsBlur complet (connexion, abonnements, articles), accessible par un vrai NewsBlurClient."""
    return FakeOpener({
        "/api/login": {"authenticated": True},
        "/reader/feeds": {"folders": folders, "feeds": feeds},
        "/reader/river_stories": lambda url, data: {   # des articles à la page 1 seulement, comme un vrai dossier
            "stories": list(stories) if urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["page"] == ["1"] else []},
    })


def nb_feed(i, host):
    return {"id": i, "feed_title": f"Flux {i}", "feed_address": f"https://{host}/rss/{i}", "feed_link": f"https://{host}/"}


@pytest.mark.parametrize("exc", [TimeoutError("timed out"), ConnectionResetError("reset"),
                                 http.client.RemoteDisconnected("closed"), http.client.IncompleteRead(b"x")])
def test_newsblur_network_errors_are_reported_not_raised(exc):
    client = NewsBlurClient("u", "motdepasse-secret", opener=FakeOpener({"/api/login": exc}))
    arts, rep = fetch_newsblur(NEWS, SETTINGS["collect"], NOW, client=client)
    assert arts == [] and not rep["ok"]
    assert rep["error"].startswith("réseau")          # une panne réseau est nommée comme telle, pas comme un bogue
    assert "motdepasse-secret" not in rep["error"]


@pytest.mark.parametrize("opener", [
    FakeOpener({"/api/login": []}),                                                              # JSON : liste, pas objet
    FakeOpener({"/api/login": {"authenticated": True}, "/reader/feeds": {"folders": {"a": 1}}}),  # dossiers : pas une liste
    nb_server([{"Le Soir": [11]}], {"11": nb_feed(11, "www.lesoir.be")}, stories="pas une liste"),
    nb_server([{"Le Soir": [11]}], {"11": nb_feed(11, "www.lesoir.be")}, stories=["pas un objet", 3]),
])
def test_newsblur_malformed_payloads_are_reported_not_raised(opener):
    arts, rep = fetch_newsblur(NEWS, SETTINGS["collect"], NOW, client=NewsBlurClient("u", "p", opener=opener))
    assert arts == [] and not rep["ok"] and rep["error"]


def test_newsblur_unexpected_bug_is_contained_and_collect_goes_on():
    class Broken(FakeNewsBlur):
        def river_stories(self, ids, page):
            raise RuntimeError("boum")

    _, rep = fetch_newsblur(NEWS, SETTINGS["collect"], NOW, client=Broken([]))
    assert not rep["ok"] and "RuntimeError" in rep["error"]
    arts, report = collect([FEEDS[0], NEWS], SETTINGS["collect"], now=NOW, parse=fake_parse_factory(), newsblur=Broken([]))
    assert arts and [r["ok"] for r in report] == [True, False]       # la revue continue avec les autres sources


def test_newsblur_asks_for_hidden_stories_too():
    opener = nb_server([{"Le Soir": [11]}], {"11": nb_feed(11, "www.lesoir.be")}, stories=[story(1)])
    fetch_newsblur(NEWS, SETTINGS["collect"], NOW, client=NewsBlurClient("u", "p", opener=opener))
    stories_url = next(u for u in opener.urls if "/reader/river_stories" in u)
    assert "include_hidden=true" in stories_url      # le filtrage personnel du compte ne doit pas vider la revue publique


def test_newsblur_mixed_folder_is_refused_unless_a_site_is_given():
    folders = [{"Belgique": [11, 12]}]
    feeds = {"11": nb_feed(11, "www.lesoir.be"), "12": nb_feed(12, "www.lalibre.be")}
    # sans `site` : refusé, plutôt que d'étiqueter La Libre « Le Soir »
    opener = nb_server(folders, feeds, stories=[story(1)])
    arts, rep = fetch_newsblur({**NEWS, "folder": "Belgique"}, SETTINGS["collect"], NOW,
                               client=NewsBlurClient("u", "p", opener=opener))
    assert arts == [] and not rep["ok"]
    assert "mixte" in rep["error"] and "lesoir.be" in rep["error"] and "lalibre.be" in rep["error"]
    assert opener.count("/reader/river_stories") == 0
    # avec `site` : seul le flux correspondant est lu
    opener = nb_server(folders, feeds, stories=[story(1)])
    arts, rep = fetch_newsblur({**NEWS, "folder": "Belgique", "site": "lesoir.be"}, SETTINGS["collect"], NOW,
                               client=NewsBlurClient("u", "p", opener=opener))
    assert rep["ok"] and [a["source"] for a in arts] == ["Le Soir"]
    stories_url = next(u for u in opener.urls if "/reader/river_stories" in u)
    assert "feeds=11" in stories_url and "feeds=12" not in stories_url
    # `site` qui ne correspond à rien : erreur explicite
    arts, rep = fetch_newsblur({**NEWS, "folder": "Belgique", "site": "inconnu.be"}, SETTINGS["collect"], NOW,
                               client=NewsBlurClient("u", "p", opener=nb_server(folders, feeds, stories=[story(1)])))
    assert arts == [] and not rep["ok"] and "aucun flux" in rep["error"]


def test_collect_shares_one_newsblur_session_between_entries(monkeypatch):
    opener = nb_server([{"Soir": [11]}, {"Sud": [12]}],
                       {"11": nb_feed(11, "www.lesoir.be"), "12": nb_feed(12, "www.sudinfo.be")}, stories=[story(1)])
    monkeypatch.setattr(NewsBlurClient, "from_env", classmethod(lambda cls: cls("u", "p", opener=opener)))
    entries = [{"name": "Le Soir", "type": "newsblur", "folder": "Soir"},
               {"name": "Sudinfo", "type": "newsblur", "folder": "Sud"}]
    _, report = collect(entries, SETTINGS["collect"], now=NOW, parse=fake_parse_factory())
    assert all(r["ok"] for r in report)
    assert opener.count("/api/login") == 1             # une seule connexion par exécution
    assert opener.count("/reader/feeds") == 1          # la liste des abonnements n'est téléchargée qu'une fois
    assert opener.count("/reader/river_stories") >= 2  # mais chaque dossier est bien lu


def test_newsblur_failed_login_is_not_retried_for_every_entry(monkeypatch):
    opener = FakeOpener({"/api/login": {"authenticated": False}})
    monkeypatch.setattr(NewsBlurClient, "from_env", classmethod(lambda cls: cls("u", "mauvais", opener=opener)))
    entries = [{"name": "Le Soir", "type": "newsblur", "folder": "Soir"},
               {"name": "Sudinfo", "type": "newsblur", "folder": "Sud"}]
    _, report = collect(entries, SETTINGS["collect"], now=NOW, parse=fake_parse_factory())
    assert [r["ok"] for r in report] == [False, False] and all("refusée" in r["error"] for r in report)
    assert opener.count("/api/login") == 1             # pas de connexions répétées avec de mauvais identifiants
