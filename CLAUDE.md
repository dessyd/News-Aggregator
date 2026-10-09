# News-Aggregator — revue de presse quotidienne par LLM

Projet : agréger des flux RSS d'actualité et produire chaque matin une revue de presse
synthétique, publiée sur GitHub Pages. Le responsable technique assure le code et l'infrastructure ; **le responsable éditorial définit et affine
les prompts** (c'est sa part éditoriale).

Langue du projet (code commenté, prompts, documentation, pages générées) : **français**.

## Architecture en bref

Collecte RSS et dédoublonnage (sans LLM) → 4 étapes LLM, chacune avec son fichier de consignes :
1. résumé + rubrique + importance par article (lots, modèle léger) — `prompts/1_resume_articles.md`
2. regroupement par sujet (1 appel) — `prompts/2_regroupement.md`
3. synthèse de chaque sujet (1 appel par sujet) — `prompts/3_synthese_sujet.md`
4. chapeau du jour + ordre de présentation — `prompts/4_digest_final.md`

Puis rendu HTML statique (`templates/`) publié dans `docs/` (GitHub Pages). Détails et alternatives écartées :
`design/architecture.md` ; choix et justifications : `design/decisions.md`.

## Carte du dépôt

- `src/` : `collect.py` (RSS, nettoyage, dédoublonnage), `pipeline.py` (4 étapes, garde-fous, formats de sortie),
  `llm.py` (client API + extraction JSON), `render.py` (Jinja2, publication), `main.py` (CLI), `util.py`.
- `prompts/` : consignes éditoriales (propriété du responsable éditorial). `config/` : `feeds.yaml`, `settings.yaml`.
- `data/` : caches de collecte et essais (`data/cache/<date>`, `data/runs/<date>/<tag>`), versionnés, purgés après `retention_days`.
- `docs/` : **sortie générée** servie par GitHub Pages. Ne pas éditer à la main. Ne pas y ranger de documentation de conception (voir `design/`).
- `design/` : documents de conception et gabarit de carnet de bord.
- `.github/workflows/` : `revue-quotidienne.yml` (cron) et `essai-prompts.yml` (essais manuels).

## Commandes

```bash
pip install -r requirements.txt
pytest                                                    # 28 tests, faux modèle, aucun appel réseau ni clé API
python -m src.main check-feeds                            # vérifier les flux
python -m src.main run --limit 30 --no-publish            # essai économique, rien n'est publié
python -m src.main replay --tag v2 --from-stage 3         # rejouer les étapes LLM sur le cache
python -m src.main compare --a main --b v2                # comparaison côte à côte
```

En local, la clé API se fournit via `ANTHROPIC_API_KEY` (via un gestionnaire de secrets, par exemple 1Password CLI : `op run -- python -m src.main ...`). Ne jamais la committer.
Sur GitHub Actions, pas de clé : fédération d'identité (jeton OIDC, variables de dépôt `vars.ANTHROPIC_*`, voir D9). Ne jamais ajouter `ANTHROPIC_API_KEY` aux workflows : elle masquerait la fédération.

## Règles à respecter

- **Prompts** : ne pas modifier le contenu éditorial de `prompts/*.md` sans demande explicite : c'est le travail du responsable éditorial.
  Le format de sortie JSON de chaque étape vit dans `FORMATS` (`src/pipeline.py`), jamais dans les prompts.
  Les commentaires `<!-- -->` des prompts ne sont pas envoyés au modèle. Variables : `{{date}}`, `{{rubriques}}`.
- **Pas d'URL générée par le modèle** : le modèle cite des identifiants d'articles, le code ajoute les liens.
  Conserver cette propriété (et son test) dans toute évolution.
- **Dégradation contrôlée** : une étape qui échoue doit se replier et ajouter un avertissement, jamais interrompre la revue.
- **Pas de scraping du texte intégral** (paywalls, droit d'auteur) : on travaille sur titre + extrait des flux.
- **Tests** : tout changement de pipeline ou de collecte s'accompagne d'un test avec `FakeLLM` (`tests/test_pipeline.py`).
- **Identifiants de modèles** (`config/settings.yaml`) : à vérifier dans la documentation Anthropic, ils évoluent.
- **Flux** (`config/feeds.yaml`) : seule l'URL RTBF a été validée ; les autres sont à contrôler avec `check-feeds`.
- GitHub Pages est public : n'y publier que des synthèses courtes avec liens, page en `noindex`.

## État

Squelette complet, testé avec un faux modèle. Première revue réelle le 2026-10-07 (6 flux, 135 articles, 12 sujets, aucun avertissement).
Fédération d'identité Claude ↔ GitHub Actions en service sur `revue-quotidienne.yml` (validée) et `essai-prompts.yml`
(converti, **pas encore lancé**) ; variables de dépôt `ANTHROPIC_*` créées (sans retour chariot final : un `\r` collé avec
la valeur fait échouer l'échange). La règle de fédération ne doit **pas** imposer `event_name` (cron = `schedule`, lancements manuels = `workflow_dispatch`).
Pages : source **GitHub Actions** (et non « branche `main` / `/docs` ») ; chaque workflow a un job `deploy` (`upload-pages-artifact` + `deploy-pages` sur `docs/`),
car `POST /pages/builds` est refusé (403) avec `GITHUB_TOKEN` et les pushs du bot ne déclenchent pas de construction. Validé en lancement manuel le 2026-10-08 ;
cron 05:17 UTC pas encore observé.
Prochaines étapes : valider les autres flux avec `check-feeds`, constater le premier cron, lancer un essai de prompts, puis itérer sur les prompts.
