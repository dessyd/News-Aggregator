"""Ligne de commande.

  python -m src.main check-feeds
  python -m src.main run [--limit 30] [--no-publish] [--tag main] [--reuse-cache]
  python -m src.main replay --date AAAA-MM-JJ --tag essai1 [--from-stage 2] [--base-tag main] [--publish-test]
  python -m src.main compare --date AAAA-MM-JJ --a main --b essai1 [--publish-test]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .collect import collect, fetch_feed, load_feeds, sample
from .pipeline import Pipeline
from .render import publish, publish_test, render_compare, render_digest
from .util import read_json, write_json

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
PROMPTS = ROOT / "prompts"
DATA = ROOT / "data"
DOCS = ROOT / "docs"


def load_settings() -> dict:
    with open(CONFIG / "settings.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def today(settings: dict) -> date:
    return datetime.now(ZoneInfo(settings["site"]["timezone"])).date()


def latest_cache_date() -> str | None:
    days = sorted(p.name for p in (DATA / "cache").glob("*") if (p / "articles.json").exists())
    return days[-1] if days else None


def cleanup(settings: dict, day: date):
    """Supprime les caches et essais plus anciens que la durée de rétention."""
    limit = day - timedelta(days=settings["site"]["retention_days"])
    for sub in ("cache", "runs"):
        for p in (DATA / sub).glob("*"):
            try:
                if date.fromisoformat(p.name) < limit:
                    shutil.rmtree(p)
            except ValueError:
                continue


def make_llm(settings: dict):
    from .llm import LLM
    return LLM(temperature=settings["llm"].get("temperature"))


def print_usage(llm):
    for model, u in getattr(llm, "usage", {}).items():
        print(f"  {model}: {u['calls']} appels, {u['input_tokens']} tokens en entrée, "
              f"{u['output_tokens']} en sortie")


# ---------------------------------------------------------------- commandes
def cmd_check_feeds(args, settings):
    feeds = load_feeds(CONFIG / "feeds.yaml")
    bad = 0
    for feed in feeds:
        arts, rep = fetch_feed(feed, {**settings["collect"], "max_age_hours": 24 * 365}, datetime.now(timezone.utc))
        if rep["ok"]:
            print(f"OK    {feed['name']:<20} {rep['entries']:>3} entrées, {rep['with_summary']} avec extrait")
        else:
            bad += 1
            print(f"ÉCHEC {feed['name']:<20} {rep['error']}")
    print(f"\n{len(feeds) - bad}/{len(feeds)} flux valides.")
    return 1 if bad == len(feeds) else 0


def cmd_run(args, settings):
    day = date.fromisoformat(args.date) if args.date else today(settings)
    cache_dir = DATA / "cache" / day.isoformat()
    if args.reuse_cache and (cache_dir / "articles.json").exists():
        articles = read_json(cache_dir / "articles.json")
        report = read_json(cache_dir / "collect_report.json") if (cache_dir / "collect_report.json").exists() else []
        print(f"Cache réutilisé : {len(articles)} articles")
    else:
        feeds = load_feeds(CONFIG / "feeds.yaml")
        print(f"Collecte de {len(feeds)} flux...")
        articles, report = collect(feeds, settings["collect"])
        for r in report:
            if not r["ok"]:
                print(f"  ! {r['name']} : {r['error']}")
        if not articles:
            print("Aucun article collecté : arrêt (rien n'est publié).")
            return 1
        write_json(cache_dir / "articles.json", articles)
        write_json(cache_dir / "collect_report.json", report)
        print(f"{len(articles)} articles après dédoublonnage")
    if args.limit:
        articles = sample(articles, args.limit)
        print(f"Mode économique : {len(articles)} articles")

    llm = make_llm(settings)
    outdir = DATA / "runs" / day.isoformat() / args.tag
    pipe = Pipeline(settings, PROMPTS, llm, outdir, day)
    digest = pipe.run(articles, collect_report=report)
    html = render_digest(digest, settings)
    (outdir / "digest.html").write_text(html, encoding="utf-8")

    if args.no_publish:
        print(f"Revue écrite dans {outdir / 'digest.html'} (non publiée)")
    else:
        for p in publish(digest, DOCS, settings):
            print(f"Publié : {p.relative_to(ROOT)}")
    cleanup(settings, day)
    print_usage(llm)
    return 0


def cmd_replay(args, settings):
    day_s = args.date or latest_cache_date()
    if not day_s:
        print("Aucun cache disponible : lancez d'abord « run ».")
        return 1
    day = date.fromisoformat(day_s)
    tag = args.tag or "essai-" + datetime.now().strftime("%H%M%S")
    if tag == args.base_tag:
        print("Le tag de l'essai doit différer du tag de référence.")
        return 1
    runs = DATA / "runs" / day.isoformat()
    cache_dir = DATA / "cache" / day.isoformat()
    report = read_json(cache_dir / "collect_report.json") if (cache_dir / "collect_report.json").exists() else []

    articles = None
    if args.from_stage == 1:
        if not (cache_dir / "articles.json").exists():
            print(f"Pas de cache pour le {day}.")
            return 1
        articles = read_json(cache_dir / "articles.json")
        if args.limit:
            articles = sample(articles, args.limit)
    elif not (runs / args.base_tag).exists():
        print(f"Essai de référence introuvable : {runs / args.base_tag}")
        return 1

    llm = make_llm(settings)
    outdir = runs / tag
    pipe = Pipeline(settings, PROMPTS, llm, outdir, day)
    digest = pipe.run(articles, from_stage=args.from_stage,
                      base_dir=runs / args.base_tag, collect_report=report)
    html = render_digest(digest, settings)
    (outdir / "digest.html").write_text(html, encoding="utf-8")
    print(f"Essai « {tag} » écrit dans {outdir / 'digest.html'}")
    if args.publish_test:
        p = publish_test(html, DOCS, f"{day.isoformat()}-{tag}")
        print(f"Page d'essai : {p.relative_to(ROOT)}")
    print_usage(llm)
    return 0


def cmd_compare(args, settings):
    day_s = args.date or latest_cache_date()
    runs = DATA / "runs" / day_s
    a, b = read_json(runs / args.a / "digest.json"), read_json(runs / args.b / "digest.json")
    html = render_compare(a, b, args.a, args.b, settings)
    out = runs / f"comparaison_{args.a}_vs_{args.b}.html"
    out.write_text(html, encoding="utf-8")
    print(f"Comparaison écrite dans {out}")
    if args.publish_test:
        p = publish_test(html, DOCS, f"{day_s}-comparaison-{args.a}-vs-{args.b}")
        print(f"Page de comparaison : {p.relative_to(ROOT)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="revue-de-presse")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check-feeds", help="vérifie que chaque flux répond")

    p = sub.add_parser("run", help="collecte + pipeline + publication")
    p.add_argument("--date")
    p.add_argument("--tag", default="main")
    p.add_argument("--limit", type=int, help="nombre max d'articles (essais économiques)")
    p.add_argument("--no-publish", action="store_true")
    p.add_argument("--reuse-cache", action="store_true")

    p = sub.add_parser("replay", help="rejoue les étapes LLM sur une collecte déjà en cache")
    p.add_argument("--date", help="défaut : dernier cache disponible")
    p.add_argument("--tag", help="nom de l'essai (défaut : essai-HHMMSS)")
    p.add_argument("--from-stage", type=int, choices=[1, 2, 3, 4], default=1)
    p.add_argument("--base-tag", default="main", help="essai dont on reprend les étapes précédentes")
    p.add_argument("--limit", type=int)
    p.add_argument("--publish-test", action="store_true")

    p = sub.add_parser("compare", help="compare deux essais côte à côte")
    p.add_argument("--date")
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.add_argument("--publish-test", action="store_true")

    args = ap.parse_args(argv)
    settings = load_settings()
    return {"check-feeds": cmd_check_feeds, "run": cmd_run,
            "replay": cmd_replay, "compare": cmd_compare}[args.cmd](args, settings)


if __name__ == "__main__":
    sys.exit(main())
