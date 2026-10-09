"""Tests du pipeline avec un faux modèle (aucun appel réseau, aucune clé API)."""
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import feedparser
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.collect
from src.collect import collect, dedupe, sample
from src.llm import call_json
from src.pipeline import Pipeline
from src.render import publish, render_compare, render_digest
from src.util import extract_json, fr_date, normalize_url, strip_html

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


# ------------------------------------------------------------------ flux dont l'URL est secrète (url_env)
SECRET_URL = "https://flux.example.test/output/jeton-tres-secret-123"
ENV_ENTRY = {"name": "Flux secret", "url_env": "FLUX_SECRET_URL", "lang": "fr"}


def secret_parse(entries=(), error=None, bozo=None):
    """Faux feedparser pour l'URL secrète ; garde la trace des adresses réellement lues."""
    calls = []

    def parse(url, agent=None):
        calls.append(url)
        if error:
            raise error
        if bozo:
            return feedparser.FeedParserDict({"entries": [], "bozo_exception": bozo})
        return feedparser.FeedParserDict({"entries": list(entries), "status": 200})
    parse.calls = calls
    return parse


def secret_entry(i):
    return {"title": f"Titre secret {i}", "link": f"http://soir.test/s{i}", "summary": "Extrait",
            "published_parsed": (2026, 10, 6, 6, 0, 0, 0, 0, 0)}


def test_url_env_reads_the_url_from_the_environment_and_never_exposes_it(monkeypatch):
    monkeypatch.setenv("FLUX_SECRET_URL", f"  {SECRET_URL}\n")           # espaces et fin de ligne ignorés
    parse = secret_parse([secret_entry(1), secret_entry(2)])
    arts, rep = fetch_feed_for_test(ENV_ENTRY, parse)
    assert parse.calls == [SECRET_URL] and len(arts) == 2 and rep["ok"]
    assert rep["url"] == "env:FLUX_SECRET_URL"                              # l'étiquette remplace l'adresse secrète
    assert SECRET_URL not in json.dumps(rep) and SECRET_URL not in json.dumps(arts)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_url_env_missing_or_empty_is_reported_without_network(monkeypatch, value):
    monkeypatch.delenv("FLUX_SECRET_URL", raising=False)
    if value is not None:
        monkeypatch.setenv("FLUX_SECRET_URL", value)
    parse = secret_parse([secret_entry(1)])
    arts, rep = fetch_feed_for_test(ENV_ENTRY, parse)
    assert arts == [] and not rep["ok"] and "FLUX_SECRET_URL" in rep["error"]
    assert "absente ou vide" in rep["error"]                                 # distinct d'une valeur invalide
    assert parse.calls == []                                                 # aucun appel réseau


def test_url_env_invalid_value_is_rejected_without_echo(monkeypatch):
    monkeypatch.setenv("FLUX_SECRET_URL", "ceci-n-est-pas-une-adresse-secrete")
    parse = secret_parse([secret_entry(1)])
    arts, rep = fetch_feed_for_test(ENV_ENTRY, parse)
    assert arts == [] and not rep["ok"] and "invalide" in rep["error"]
    assert "ceci-n-est-pas" not in rep["error"] and parse.calls == []


@pytest.mark.parametrize("make", [
    lambda: secret_parse(error=OSError(f"échec de connexion à {SECRET_URL}")),
    lambda: secret_parse(bozo=ValueError(f"XML mal formé dans {SECRET_URL}")),
])
def test_url_env_errors_never_contain_the_secret_url(monkeypatch, make):
    monkeypatch.setenv("FLUX_SECRET_URL", SECRET_URL)
    arts, rep = fetch_feed_for_test(ENV_ENTRY, make())
    assert arts == [] and not rep["ok"]
    assert SECRET_URL not in rep["error"] and "env:FLUX_SECRET_URL" in rep["error"]


def test_url_env_entry_goes_through_collect_and_stays_out_of_the_report(monkeypatch):
    monkeypatch.setenv("FLUX_SECRET_URL", SECRET_URL)
    base, secret = fake_parse_factory(), secret_parse([secret_entry(1)])
    arts, report = collect([FEEDS[0], ENV_ENTRY], SETTINGS["collect"], now=NOW,
                           parse=lambda url, agent=None: secret(url) if url == SECRET_URL else base(url))
    assert [r["name"] for r in report] == ["A", "Flux secret"] and all(r["ok"] for r in report)
    assert {a["source"] for a in arts} == {"A", "Flux secret"}
    assert SECRET_URL not in json.dumps(report)                             # collect_report.json est public


def test_feed_without_url_nor_url_env_is_reported_not_raised():
    arts, rep = fetch_feed_for_test({"name": "Sans adresse"}, secret_parse())
    assert arts == [] and not rep["ok"] and "url_env" in rep["error"]


def fetch_feed_for_test(feed, parse):
    return src.collect.fetch_feed(feed, SETTINGS["collect"], NOW, parse=parse)
