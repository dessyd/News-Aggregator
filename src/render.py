"""Génération des pages HTML statiques (Jinja2) et publication dans docs/."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .util import fr_date

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True,
                       trim_blocks=True, lstrip_blocks=True)


def render_digest(digest: dict, settings: dict, base: str = "") -> str:
    return _env().get_template("digest.html.j2").render(d=digest, site=settings["site"], base=base)


def render_archives(days: list[str], settings: dict) -> str:
    items = [{"iso": d, "label": fr_date(date.fromisoformat(d))} for d in days]
    return _env().get_template("archives.html.j2").render(items=items, site=settings["site"])


def render_compare(a: dict, b: dict, label_a: str, label_b: str, settings: dict) -> str:
    return _env().get_template("compare.html.j2").render(
        a=a, b=b, label_a=label_a, label_b=label_b, site=settings["site"])


def publish(digest: dict, docs_dir: Path, settings: dict) -> list[Path]:
    """Écrit docs/index.html (dernière revue), docs/archives/AAAA-MM-JJ.html et l'index des archives."""
    docs = Path(docs_dir)
    (docs / "archives").mkdir(parents=True, exist_ok=True)
    (docs / ".nojekyll").touch()
    written = []

    index = docs / "index.html"
    index.write_text(render_digest(digest, settings, base=""), encoding="utf-8")
    written.append(index)

    archive = docs / "archives" / f"{digest['date']}.html"
    archive.write_text(render_digest(digest, settings, base="../"), encoding="utf-8")
    written.append(archive)

    days = sorted((p.stem for p in (docs / "archives").glob("*.html")), reverse=True)
    arch_index = docs / "archives.html"
    arch_index.write_text(render_archives(days, settings), encoding="utf-8")
    written.append(arch_index)
    return written


def publish_test(html: str, docs_dir: Path, name: str) -> Path:
    """Page d'essai (non listée, noindex) : docs/tests/<name>.html."""
    path = Path(docs_dir) / "tests" / f"{name}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    (Path(docs_dir) / ".nojekyll").touch()
    path.write_text(html, encoding="utf-8")
    return path
