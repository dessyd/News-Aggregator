#!/usr/bin/env python3
"""Pilote cron-job.org (API REST) pour le déclenchement quotidien de la revue de presse.

La tâche appelle l'API GitHub `dispatches` du workflow `revue-quotidienne.yml` (POST, succès = HTTP 204).
À lancer par vous-même : la clé d'API de cron-job.org et le jeton GitHub sont demandés en saisie masquée
(ou lus dans CRONJOB_API_KEY et GITHUB_DISPATCH_TOKEN), jamais écrits sur disque ni affichés.
Bibliothèque standard uniquement. Voir le README (« Déclenchement quotidien par cron-job.org »).

    python3 scripts/cronjob_org.py lister
    python3 scripts/cronjob_org.py configurer [--heure 7 --minute 17 --fuseau Europe/Brussels] [--desactive] [--simuler]
    python3 scripts/cronjob_org.py historique [--nombre 10]

`configurer` est idempotent : il retrouve la tâche par son URL, la met à jour, ou la crée si elle n'existe pas.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

API = "https://api.cron-job.org"
REPO = "dessyd/News-Aggregator"
WORKFLOW = "revue-quotidienne.yml"
DISPATCH_URL = f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches"
TITRE = "Revue de presse : déclenchement du workflow GitHub"
STATUTS = {0: "pas encore exécuté", 1: "OK", 2: "échec (DNS)", 3: "échec (connexion)", 4: "échec (erreur HTTP)",
           5: "échec (délai dépassé)", 6: "échec (trop de données)", 7: "échec (URL invalide)",
           8: "échec (erreur interne)", 9: "échec (raison inconnue)", 10: "échec (page de défi)"}


class ErreurAPI(Exception):
    """Erreur lisible ; le message ne contient jamais de secret."""


def http(method: str, url: str, headers: dict | None = None, body: dict | None = None):
    """Renvoie (statut HTTP, JSON décodé ou None). Ne lève que pour une panne réseau."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as rep:
            brut = rep.read()
            return rep.status, (json.loads(brut) if brut else None)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"null")
        except ValueError:
            return exc.code, None
    except (urllib.error.URLError, OSError) as exc:
        raise ErreurAPI(f"réseau : {type(exc).__name__}") from None
    except ValueError:
        raise ErreurAPI("réponse inattendue (ce n'est pas du JSON)") from None


class CronJobOrg:
    def __init__(self, cle: str):
        self._cle = cle

    def appel(self, method: str, chemin: str, body: dict | None = None) -> dict:
        statut, rep = http(method, API + chemin, {"Authorization": f"Bearer {self._cle}",
                                                  "Content-Type": "application/json"}, body)
        if statut == 401:
            raise ErreurAPI("clé d'API refusée (401)")
        if statut == 403:
            raise ErreurAPI("clé d'API refusée depuis cette adresse IP (403) : vérifier la restriction d'adresse de la clé")
        if statut == 429:
            raise ErreurAPI("limite de requêtes de cron-job.org atteinte (429) : 100 par jour par défaut")
        if statut >= 400:
            detail = ""
            if isinstance(rep, dict):
                detail = str(rep.get("error") or rep.get("message") or "")[:120]
            raise ErreurAPI(f"erreur HTTP {statut} de cron-job.org {detail}".strip())
        return rep if isinstance(rep, dict) else {}

    def taches(self) -> list[dict]:
        jobs = self.appel("GET", "/jobs").get("jobs", [])
        return [j for j in jobs if isinstance(j, dict)] if isinstance(jobs, list) else []

    def tache_du_workflow(self) -> dict | None:
        return next((j for j in self.taches() if j.get("url") == DISPATCH_URL), None)


def verifier_jeton_github(jeton: str) -> None:
    """Vérifie que le jeton lit le workflow (il ne prouve pas la permission d'écriture, qu'un vrai déclenchement testera)."""
    statut, _ = http("GET", f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}",
                     {"Authorization": f"Bearer {jeton}", "Accept": "application/vnd.github+json",
                      "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "revue-de-presse-config"})
    if statut == 200:
        return
    raison = {401: "jeton refusé ou expiré", 403: "jeton sans droit suffisant", 404: "dépôt ou workflow introuvable pour ce jeton"}
    raise ErreurAPI(f"jeton GitHub inutilisable (HTTP {statut}) : {raison.get(statut, 'erreur inattendue')}")


def charge_utile(heure: int, minute: int, fuseau: str, actif: bool, jeton: str | None) -> dict:
    job = {
        "url": DISPATCH_URL, "enabled": actif, "title": TITRE, "requestMethod": 1,   # 1 = POST
        "saveResponses": True, "requestTimeout": 30, "redirectSuccess": False,
        "schedule": {"timezone": fuseau, "expiresAt": 0, "hours": [heure], "mdays": [-1],
                     "minutes": [minute], "months": [-1], "wdays": [-1]},
        "auth": {"enable": False, "user": "", "password": ""},
        "notification": {"onFailure": True, "onFailureCount": 1, "onSuccess": False, "onDisable": True,
                         "onSslCertExpiry": False, "mode": 1, "selectedChannels": []},
    }
    if jeton:   # sans jeton (mise à jour d'une tâche existante), les en-têtes enregistrés sont conservés
        job["extendedData"] = {"body": json.dumps({"ref": "main"}),
                               "headers": {"Authorization": f"Bearer {jeton}", "Accept": "application/vnd.github+json",
                                           "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
                                           "User-Agent": "cron-job.org (revue-de-presse)"}}
    return {"job": job}


def horodatage(ts) -> str:
    if not ts:
        return "—"
    d = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    return f"{d:%Y-%m-%d %H:%M:%S} UTC ({d.astimezone(ZoneInfo('Europe/Brussels')):%H:%M} à Bruxelles)"


def resume(j: dict) -> str:
    s = j.get("schedule") or {}
    heures, minutes = s.get("hours", []), s.get("minutes", [])
    quand = ", ".join(f"{h:02d}:{m:02d}" for h in heures for m in minutes if h >= 0 and m >= 0) or "(motif complexe)"
    return (f"#{j.get('jobId')} {'activée' if j.get('enabled') else 'DÉSACTIVÉE'} | {j.get('title') or '(sans titre)'}\n"
            f"    URL : {j.get('url')}\n"
            f"    horaire : {quand} {s.get('timezone', '?')} | dernier statut : {STATUTS.get(j.get('lastStatus'), '?')}"
            f" | dernière exécution : {horodatage(j.get('lastExecution'))}\n"
            f"    prochaine exécution : {horodatage(j.get('nextExecution'))}")


def demander(nom_env: str, invite: str, obligatoire: bool = True) -> str:
    valeur = os.environ.get(nom_env) or getpass.getpass(invite)
    valeur = valeur.strip("\r\n \t")
    if obligatoire and not valeur:
        raise ErreurAPI("valeur vide")
    return valeur


def cmd_lister(api: CronJobOrg, args) -> int:
    taches = api.taches()
    print(f"{len(taches)} tâche(s) sur cron-job.org :")
    for j in taches:
        print("  " + resume(j))
    return 0


def cmd_configurer(api: CronJobOrg, args) -> int:
    existante = api.tache_du_workflow()
    if existante:
        print("Tâche existante trouvée :\n  " + resume(existante))
        jeton = demander("GITHUB_DISPATCH_TOKEN", "Nouveau jeton GitHub (Entrée = conserver l'actuel) : ", obligatoire=False) or None
    else:
        print("Aucune tâche pour ce workflow : création.")
        jeton = demander("GITHUB_DISPATCH_TOKEN", "Jeton GitHub fine-grained, droit « Actions : écriture » (non affiché) : ")
    if jeton:
        verifier_jeton_github(jeton)
        print("Jeton GitHub : lit bien le workflow (la permission d'écriture sera prouvée par le premier déclenchement).")
    payload = charge_utile(args.heure, args.minute, args.fuseau, not args.desactive, jeton)
    if args.simuler:
        vu = json.loads(json.dumps(payload))
        if "extendedData" in vu["job"]:
            vu["job"]["extendedData"]["headers"]["Authorization"] = "Bearer ***"
        print("SIMULATION, rien n'est écrit :")
        print(json.dumps(vu, indent=2, ensure_ascii=False))
        return 0
    if existante:
        api.appel("PATCH", f"/jobs/{existante['jobId']}", payload)
        job_id = existante["jobId"]
        print(f"Tâche #{job_id} mise à jour" + ("" if jeton else " (jeton conservé)") + ".")
    else:
        job_id = api.appel("PUT", "/jobs", payload).get("jobId")
        print(f"Tâche #{job_id} créée.")
    apres = next((j for j in api.taches() if j.get("jobId") == job_id), None)
    if apres:
        print("État actuel :\n  " + resume(apres))
    return 0


def cmd_historique(api: CronJobOrg, args) -> int:
    tache = api.tache_du_workflow()
    if not tache:
        print("Aucune tâche pour ce workflow sur cron-job.org.")
        return 1
    rep = api.appel("GET", f"/jobs/{tache['jobId']}/history")
    historique = [h for h in rep.get("history", []) if isinstance(h, dict)][: args.nombre]
    print(f"Tâche #{tache['jobId']} : {len(historique)} dernière(s) exécution(s)")
    for h in historique:
        print(f"  {horodatage(h.get('date'))} | prévue {horodatage(h.get('datePlanned')).split(' UTC')[0]} | "
              f"{STATUTS.get(h.get('status'), '?')} | HTTP {h.get('httpStatus')} | {h.get('duration')} ms")
    if rep.get("predictions"):
        print("Prochaines exécutions prévues : " + " ; ".join(horodatage(t).split(" (")[0] for t in rep["predictions"]))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sous = p.add_subparsers(dest="cmd", required=True)
    sous.add_parser("lister")
    c = sous.add_parser("configurer")
    c.add_argument("--heure", type=int, default=7)
    c.add_argument("--minute", type=int, default=17)
    c.add_argument("--fuseau", default="Europe/Brussels")
    c.add_argument("--desactive", action="store_true")
    c.add_argument("--simuler", action="store_true")
    h = sous.add_parser("historique")
    h.add_argument("--nombre", type=int, default=10)
    args = p.parse_args(argv)
    try:
        if args.cmd == "configurer":
            if not (0 <= args.heure <= 23 and 0 <= args.minute <= 59):
                raise ErreurAPI("heure (0-23) ou minute (0-59) invalide")
            try:
                ZoneInfo(args.fuseau)
            except Exception:
                raise ErreurAPI(f"fuseau horaire inconnu : {args.fuseau}") from None
        api = CronJobOrg(demander("CRONJOB_API_KEY", "Clé d'API cron-job.org (non affichée) : "))
        return {"lister": cmd_lister, "configurer": cmd_configurer, "historique": cmd_historique}[args.cmd](api, args)
    except ErreurAPI as exc:
        print("Erreur :", exc)
        return 1
    except Exception as exc:   # affichage court, sans trace pouvant contenir des secrets
        print("Erreur inattendue :", type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
