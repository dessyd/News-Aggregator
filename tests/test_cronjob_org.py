"""Tests de cronjob_org.py avec un faux serveur cron-job.org et un faux GitHub (aucun réseau, secrets factices)."""
import getpass
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import cronjob_org as c  # noqa: E402

CLE, JETON = "CLE-FACTICE-cronjob-123", "jeton-github-factice-456"
EXISTANTE = {"jobId": 77, "enabled": True, "title": c.TITRE, "url": c.DISPATCH_URL, "lastStatus": 1,
             "lastExecution": 1791600000, "nextExecution": 1791687420,
             "schedule": {"timezone": "Europe/Brussels", "hours": [7], "minutes": [17]}}


class Faux:
    """Faux réseau : enregistre les appels et répond selon un scénario."""
    def __init__(self, jobs=(), github=200, cronjob_statut=200, histoire=()):
        self.jobs, self.github, self.cronjob_statut, self.histoire = list(jobs), github, cronjob_statut, list(histoire)
        self.appels = []

    def __call__(self, method, url, headers=None, body=None):
        self.appels.append((method, url, headers or {}, body))
        if url.startswith("https://api.github.com/"):
            return self.github, {}
        if self.cronjob_statut != 200:
            return self.cronjob_statut, {"error": "refusé"}
        if method == "GET" and url.endswith("/jobs"):
            return 200, {"jobs": self.jobs, "someFailed": False}
        if method == "PUT" and url.endswith("/jobs"):
            self.jobs.append({**body["job"], "jobId": 99, "lastStatus": 0, "nextExecution": 1791687420})
            return 200, {"jobId": 99}
        if method == "PATCH":
            return 200, {}
        if "/history" in url:
            return 200, {"history": self.histoire, "predictions": [1791687420]}
        raise AssertionError((method, url))

    def ecritures(self):
        return [a for a in self.appels if a[0] in ("PUT", "PATCH") and a[1].startswith(c.API)]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("CRONJOB_API_KEY", CLE)
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", JETON)
    monkeypatch.setattr(getpass, "getpass", lambda p="": (_ for _ in ()).throw(AssertionError("saisie inattendue")))


def lancer(monkeypatch, faux, capsys, *argv):
    monkeypatch.setattr(c, "http", faux)
    code = c.main(list(argv))
    sortie = capsys.readouterr().out
    assert CLE not in sortie and JETON not in sortie, "un secret a été affiché"
    return code, sortie


def test_creation_envoie_le_bon_payload(env, monkeypatch, capsys):
    faux = Faux()
    code, sortie = lancer(monkeypatch, faux, capsys, "configurer")
    assert code == 0 and "créée" in sortie
    (_, url, entetes, corps), = faux.ecritures()
    job = corps["job"]
    assert url.endswith("/jobs") and entetes["Authorization"] == f"Bearer {CLE}"
    assert job["url"] == c.DISPATCH_URL and job["requestMethod"] == 1 and job["enabled"] is True
    assert job["schedule"] == {"timezone": "Europe/Brussels", "expiresAt": 0, "hours": [7], "mdays": [-1],
                               "minutes": [17], "months": [-1], "wdays": [-1]}
    assert json.loads(job["extendedData"]["body"]) == {"ref": "main"}
    assert job["extendedData"]["headers"]["Authorization"] == f"Bearer {JETON}"
    assert job["notification"]["onFailure"] is True
    verif = [a for a in faux.appels if a[1].startswith("https://api.github.com/") and a[0] == "GET"]
    assert verif and verif[0][2]["Authorization"] == f"Bearer {JETON}"      # le jeton est vérifié avant d'être stocké


def test_idempotent_une_tache_existante_est_mise_a_jour_sans_doublon(env, monkeypatch, capsys):
    faux = Faux(jobs=[EXISTANTE])
    code, sortie = lancer(monkeypatch, faux, capsys, "configurer", "--heure", "6", "--minute", "30")
    (methode, url, _, corps), = faux.ecritures()
    assert code == 0 and methode == "PATCH" and url.endswith("/jobs/77") and "mise à jour" in sortie
    assert corps["job"]["schedule"]["hours"] == [6] and corps["job"]["schedule"]["minutes"] == [30]


def test_mise_a_jour_sans_nouveau_jeton_conserve_les_en_tetes(env, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_DISPATCH_TOKEN")
    monkeypatch.setattr(getpass, "getpass", lambda p="": "")                  # l'utilisateur appuie sur Entrée : conserver l'actuel
    faux = Faux(jobs=[EXISTANTE])
    code, sortie = lancer(monkeypatch, faux, capsys, "configurer")
    (_, _, _, corps), = faux.ecritures()
    assert code == 0 and "extendedData" not in corps["job"] and "jeton conservé" in sortie
    assert not [a for a in faux.appels if a[1].startswith("https://api.github.com/")]


@pytest.mark.parametrize("statut", [401, 403, 404])
def test_jeton_github_invalide_arrete_avant_toute_ecriture(env, monkeypatch, capsys, statut):
    faux = Faux(github=statut)
    code, sortie = lancer(monkeypatch, faux, capsys, "configurer")
    assert code == 1 and "jeton GitHub inutilisable" in sortie and faux.ecritures() == []


def test_simulation_n_ecrit_rien_et_masque_le_jeton(env, monkeypatch, capsys):
    faux = Faux()
    code, sortie = lancer(monkeypatch, faux, capsys, "configurer", "--simuler")
    assert code == 0 and faux.ecritures() == [] and "Bearer ***" in sortie and "SIMULATION" in sortie


@pytest.mark.parametrize("statut,mot", [(401, "401"), (403, "adresse IP"), (429, "limite")])
def test_erreurs_de_cron_job_org_sont_lisibles(env, monkeypatch, capsys, statut, mot):
    code, sortie = lancer(monkeypatch, Faux(cronjob_statut=statut), capsys, "lister")
    assert code == 1 and mot in sortie


def test_arguments_invalides(env, monkeypatch, capsys):
    for argv, mot in ((("configurer", "--heure", "25"), "invalide"), (("configurer", "--fuseau", "Mars/Olympus"), "fuseau")):
        code, sortie = lancer(monkeypatch, Faux(), capsys, *argv)
        assert code == 1 and mot in sortie


def test_lister_et_historique(env, monkeypatch, capsys):
    histoire = [{"date": 1791600005, "datePlanned": 1791600000, "status": 1, "httpStatus": 204, "duration": 310},
                {"date": 1791513605, "datePlanned": 1791513600, "status": 4, "httpStatus": 401, "duration": 290}]
    faux = Faux(jobs=[EXISTANTE], histoire=histoire)
    code, sortie = lancer(monkeypatch, faux, capsys, "lister")
    assert code == 0 and "#77" in sortie and "07:17 Europe/Brussels" in sortie
    code, sortie = lancer(monkeypatch, faux, capsys, "historique")
    assert code == 0 and "HTTP 204" in sortie and "OK" in sortie and "HTTP 401" in sortie and "échec (erreur HTTP)" in sortie
    code, sortie = lancer(monkeypatch, Faux(), capsys, "historique")
    assert code == 1 and "Aucune tâche" in sortie


def test_saisie_masquee_quand_les_variables_sont_absentes(monkeypatch, capsys):
    monkeypatch.delenv("CRONJOB_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_DISPATCH_TOKEN", raising=False)
    reponses = iter([CLE, JETON])
    monkeypatch.setattr(getpass, "getpass", lambda p="": next(reponses))
    faux = Faux()
    code, _ = lancer(monkeypatch, faux, capsys, "configurer")
    assert code == 0 and faux.ecritures()[0][2]["Authorization"] == f"Bearer {CLE}"


def test_les_secrets_ne_sont_jamais_dans_une_erreur_reseau(env, monkeypatch, capsys):
    def panne(*a, **k):
        raise c.ErreurAPI("réseau : TimeoutError")
    monkeypatch.setattr(c, "http", panne)
    code = c.main(["lister"])
    sortie = capsys.readouterr().out
    assert code == 1 and "TimeoutError" in sortie and CLE not in sortie
