"""Pipeline en 4 étapes LLM.

  1. Résumé + rubrique + importance de chaque article        (modèle léger, par lots)
  2. Regroupement des articles par sujet                     (1 appel)
  3. Synthèse rédigée de chaque sujet                        (1 appel par sujet)
  4. Chapeau du jour + ordre de présentation                 (1 appel)

Les consignes éditoriales viennent des fichiers prompts/*.md (modifiables par le responsable éditorial).
Le FORMAT de sortie, lui, est imposé ici : il ne dépend donc pas des prompts.
Les liens vers les sources ne sont jamais écrits par le modèle : ils sont ajoutés
par le programme à partir des identifiants d'articles (aucune URL inventée possible).
"""
from __future__ import annotations

import hashlib
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .llm import call_json
from .util import clamp_int, fr_date, read_json, render_prompt, strip_comments, write_json

STAGE_PROMPTS = {1: "1_resume_articles.md", 2: "2_regroupement.md",
                 3: "3_synthese_sujet.md", 4: "4_digest_final.md"}

FORMATS = {
    1: """[[stage:1]]
FORMAT DE SORTIE (imposé par le programme) :
Réponds uniquement avec un objet JSON valide, sans texte autour :
{"articles": [{"id": "<id fourni>", "resume": "<texte>", "rubrique": "<une valeur parmi : {{rubriques}}>", "importance": <entier de 1 à 5>}]}
Une entrée par article reçu, en reprenant exactement l'id fourni.""",
    2: """[[stage:2]]
FORMAT DE SORTIE (imposé par le programme) :
Réponds uniquement avec un objet JSON valide, sans texte autour :
{"sujets": [{"titre": "<titre du sujet>", "ids": ["<id d'article>", "..."]}]}
N'utilise que des id fournis, chacun au plus une fois.""",
    3: """[[stage:3]]
FORMAT DE SORTIE (imposé par le programme) :
Réponds uniquement avec un objet JSON valide, sans texte autour :
{"titre": "<titre du sujet>", "texte": "<synthèse rédigée>", "rubrique": "<une valeur parmi : {{rubriques}}>", "ids_sources": ["<id des articles utilisés>"]}""",
    4: """[[stage:4]]
FORMAT DE SORTIE (imposé par le programme) :
Réponds uniquement avec un objet JSON valide, sans texte autour :
{"chapeau": "<2 à 3 phrases>", "ordre": [<index des sujets dans l'ordre de présentation, chacun une fois>]}""",
}


def _wrap(data) -> str:
    return "Voici les données à traiter (JSON) :\n<donnees>\n" + json.dumps(
        data, ensure_ascii=False) + "\n</donnees>"


class Pipeline:
    def __init__(self, settings: dict, prompts_dir: Path, llm, outdir: Path, day, log=print):
        self.s = settings
        self.prompts_dir = Path(prompts_dir)
        self.llm = llm
        self.outdir = Path(outdir)
        self.day = day
        self.log = log
        self.warnings: list[str] = []
        self.rubriques = list(settings["rubriques"])
        self.workers = settings["llm"].get("workers", 4)

    # ---------- utilitaires ----------
    def warn(self, msg: str):
        self.warnings.append(msg)
        self.log(f"  ! {msg}")

    def _prompt_text(self, stage: int) -> str:
        return strip_comments((self.prompts_dir / STAGE_PROMPTS[stage]).read_text(encoding="utf-8")).strip()

    def _system(self, stage: int) -> str:
        text = self._prompt_text(stage) + "\n\n" + FORMATS[stage]
        return render_prompt(text, date=fr_date(self.day), rubriques=", ".join(self.rubriques))

    def prompts_fingerprint(self) -> str:
        blob = "".join(self._prompt_text(n) for n in sorted(STAGE_PROMPTS))
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:8]

    def _call(self, stage: int, data):
        return call_json(self.llm, self.s["models"][f"stage{stage}"], self._system(stage),
                         _wrap(data), self.s["llm"][f"max_tokens_stage{stage}"])

    def _rubrique(self, value) -> str:
        return value if value in self.rubriques else "Autres"

    # ---------- étape 1 ----------
    def stage1(self, articles: list[dict]) -> list[dict]:
        self.log(f"Étape 1 : résumé et tri de {len(articles)} articles")
        size = self.s["llm"]["batch_size"]
        batches = [articles[i:i + size] for i in range(0, len(articles), size)]

        def work(batch):
            payload = [{"id": a["id"], "source": a["source"], "titre": a["titre"],
                        "extrait": a["extrait"], "publie": a.get("publie")} for a in batch]
            try:
                data = self._call(1, payload)
                return {e["id"]: e for e in data.get("articles", [])
                        if isinstance(e, dict) and "id" in e}
            except Exception as exc:
                self.warn(f"Étape 1 : un lot de {len(batch)} articles a échoué ({exc})")
                return {}

        merged: dict[str, dict] = {}
        with ThreadPoolExecutor(self.workers) as ex:
            for result in ex.map(work, batches):
                merged.update(result)

        out, fallback = [], 0
        for a in articles:
            b = dict(a)
            e = merged.get(a["id"])
            if e and str(e.get("resume", "")).strip():
                b["resume"] = str(e["resume"]).strip()
                b["rubrique"] = self._rubrique(e.get("rubrique"))
                b["importance"] = clamp_int(e.get("importance"), 1, 5, 3)
            else:
                b.update(resume=a["extrait"] or a["titre"], rubrique="Autres", importance=2, repli=True)
                fallback += 1
            out.append(b)
        if fallback:
            self.warn(f"Étape 1 : {fallback} article(s) sans résumé du modèle (repli sur l'extrait)")
        return out

    # ---------- étape 2 ----------
    def stage2(self, articles: list[dict]) -> dict:
        cfg = self.s["digest"]
        pool = [a for a in articles if a["importance"] >= cfg["min_importance"]]
        by_id = {a["id"]: a for a in pool}
        self.log(f"Étape 2 : regroupement de {len(pool)} articles par sujet")
        sujets: list[dict] = []
        seen: set[str] = set()
        try:
            payload = [{"id": a["id"], "source": a["source"], "titre": a["titre"],
                        "resume": a["resume"], "rubrique": a["rubrique"],
                        "importance": a["importance"]} for a in pool]
            data = self._call(2, payload)
            unknown = 0
            for s in data.get("sujets", []):
                raw_ids = s.get("ids", []) if isinstance(s, dict) else []
                ids = []
                for i in raw_ids:
                    if i in by_id and i not in seen and i not in ids:
                        ids.append(i)
                    elif i not in by_id:
                        unknown += 1
                if not ids:
                    continue
                seen.update(ids)
                titre = str(s.get("titre", "")).strip() or by_id[ids[0]]["titre"]
                sujets.append({"titre": titre, "ids": ids})
            if unknown:
                self.warn(f"Étape 2 : {unknown} identifiant(s) inconnu(s) ignoré(s)")
        except Exception as exc:
            self.warn(f"Étape 2 : regroupement impossible ({exc}) ; repli sur les articles d'importance ≥ 4")
            sujets = [{"titre": a["titre"], "ids": [a["id"]]} for a in pool if a["importance"] >= 4]
            seen = {i for s in sujets for i in s["ids"]}

        for s in sujets:
            arts = [by_id[i] for i in s["ids"]]
            s["importance"] = max(a["importance"] for a in arts)
            s["score"] = s["importance"] * 10 + len({a["source"] for a in arts})
        sujets.sort(key=lambda s: -s["score"])
        kept, overflow = sujets[:cfg["max_topics"]], sujets[cfg["max_topics"]:]

        breves = []
        for s in overflow:
            first = by_id[s["ids"][0]]
            breves.append({"titre": s["titre"], "url": first["url"], "source": first["source"],
                           "importance": s["importance"]})
        for a in pool:
            if a["id"] not in seen and a["importance"] >= cfg["breves_min_importance"]:
                breves.append({"titre": a["titre"], "url": a["url"], "source": a["source"],
                               "importance": a["importance"]})
        breves.sort(key=lambda b: -b["importance"])
        return {"sujets": kept, "breves": breves[:cfg["breves_max"]]}

    # ---------- étape 3 ----------
    @staticmethod
    def _sources(arts: list[dict], cited: list[str]) -> list[dict]:
        out, urls = [], set()
        for a in arts:
            if a["id"] not in cited:
                continue
            for src in [{"source": a["source"], "titre": a["titre"], "url": a["url"]}] + \
                    list(a.get("autres_sources", [])):
                if src["url"] not in urls:
                    urls.add(src["url"])
                    out.append(src)
        return out

    def stage3(self, by_id: dict, sujets: list[dict]) -> list[dict]:
        self.log(f"Étape 3 : rédaction de {len(sujets)} sujets")
        cap = self.s["digest"]["max_articles_per_topic"]

        def work(s):
            arts = [by_id[i] for i in s["ids"] if i in by_id][:cap]
            ids = [a["id"] for a in arts]
            payload = {"sujet": s["titre"], "articles": [
                {"id": a["id"], "source": a["source"], "titre": a["titre"],
                 "extrait": a["extrait"], "resume": a["resume"], "publie": a.get("publie")}
                for a in arts]}
            try:
                data = self._call(3, payload)
                if not isinstance(data, dict):
                    raise ValueError("réponse non conforme")
            except Exception as exc:
                self.warn(f"Étape 3 : sujet « {s['titre']} » : échec du modèle ({exc}) ; repli sur les résumés")
                data = {}
            texte = str(data.get("texte", "")).strip()
            if not texte:
                texte = " ".join(a["resume"] for a in arts[:3])
            cited = []
            for i in data.get("ids_sources", []) or []:
                if i in ids and i not in cited:
                    cited.append(i)
                elif i not in ids:
                    self.warn(f"Étape 3 : sujet « {s['titre']} » : source inconnue « {i} » ignorée")
            if not cited:
                self.warn(f"Étape 3 : sujet « {s['titre']} » : aucune source valide citée ; toutes les sources de l'étape 2 sont utilisées")
                cited = ids
            rub = data.get("rubrique")
            if rub not in self.rubriques:
                rubs = [a["rubrique"] for a in arts]
                rub = max(set(rubs), key=rubs.count) if rubs else "Autres"
            return {"titre": str(data.get("titre") or s["titre"]).strip(), "texte": texte,
                    "rubrique": rub, "ids_sources": cited, "sources": self._sources(arts, cited),
                    "importance": s.get("importance", 3), "score": s.get("score", 0),
                    "nb_articles": len(s["ids"])}

        with ThreadPoolExecutor(self.workers) as ex:
            return list(ex.map(work, sujets))

    # ---------- étape 4 ----------
    def stage4(self, topics: list[dict]) -> dict:
        self.log("Étape 4 : chapeau et ordre de présentation")
        if not topics:
            return {"chapeau": "", "ordre": []}
        payload = [{"index": i, "titre": t["titre"], "rubrique": t["rubrique"],
                    "importance": t["importance"], "nb_sources": len(t["sources"]),
                    "texte": t["texte"]} for i, t in enumerate(topics)]
        try:
            data = self._call(4, payload)
            if not isinstance(data, dict):
                raise ValueError("réponse non conforme")
        except Exception as exc:
            self.warn(f"Étape 4 : échec du modèle ({exc}) ; ordre par score, sans chapeau")
            data = {}
        ordre: list[int] = []
        for i in data.get("ordre", []) or []:
            if isinstance(i, int) and 0 <= i < len(topics) and i not in ordre:
                ordre.append(i)
        missing = [i for i in range(len(topics)) if i not in ordre]
        if missing and data:
            self.warn(f"Étape 4 : {len(missing)} sujet(s) absent(s) de l'ordre proposé, ajoutés à la fin")
        ordre += missing
        chapeau = str(data.get("chapeau", "")).strip()
        if data and not chapeau:
            self.warn("Étape 4 : chapeau vide")
        return {"chapeau": chapeau, "ordre": ordre}

    # ---------- orchestration ----------
    def run(self, articles: list[dict] | None = None, from_stage: int = 1,
            base_dir: Path | None = None, collect_report: list[dict] | None = None) -> dict:
        out = self.outdir
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        shutil.copytree(self.prompts_dir, out / "prompts")  # trace des prompts utilisés

        if from_stage > 1 and base_dir is None:
            raise ValueError("--from-stage > 1 nécessite un essai de référence (--base-tag)")
        base = Path(base_dir) if base_dir else None

        if from_stage == 1:
            s1 = self.stage1(articles or [])
            write_json(out / "stage1.json", s1)
        else:
            s1 = read_json(base / "stage1.json")
            write_json(out / "stage1.json", s1)
        by_id = {a["id"]: a for a in s1}

        if from_stage <= 2:
            s2 = self.stage2(s1)
        else:
            s2 = read_json(base / "stage2.json")
        write_json(out / "stage2.json", s2)

        if from_stage <= 3:
            s3 = self.stage3(by_id, s2["sujets"])
        else:
            s3 = read_json(base / "stage3.json")
        write_json(out / "stage3.json", s3)

        if from_stage <= 4:
            s4 = self.stage4(s3)
        else:
            s4 = read_json(base / "stage4.json")
        write_json(out / "stage4.json", s4)

        digest = self.assemble(s1, s2, s3, s4, collect_report or [])
        write_json(out / "digest.json", digest)
        return digest

    def assemble(self, s1, s2, s3, s4, collect_report) -> dict:
        topics = [s3[i] for i in s4["ordre"]] if s3 else []
        return {
            "date": self.day.isoformat(),
            "date_fr": fr_date(self.day),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "chapeau": s4["chapeau"],
            "topics": topics,
            "breves": s2["breves"],
            "stats": {
                "articles": len(s1),
                "sujets": len(topics),
                "sources": len({a["source"] for a in s1}),
                "flux_en_echec": sum(1 for r in collect_report if not r.get("ok")),
            },
            "meta": {"models": {k: v for k, v in self.s["models"].items()},
                     "prompts": self.prompts_fingerprint(),
                     "usage": getattr(self.llm, "usage", {})},
            "warnings": self.warnings,
        }
