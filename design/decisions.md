# Journal des décisions

Format : décision, contexte, justification, conséquences. À compléter au fil du projet (une entrée par décision
structurante, la plus récente en bas).

## D1 — Pipeline en étapes plutôt que prompt unique ou agent
- **Date** : 2026-10-06
- **Justification** : meilleure qualité et traçabilité, comportement déterministe, réglages localisés par étape.
- **Conséquences** : quatre fichiers de prompts, un format JSON par étape, coût en appels plus élevé mais maîtrisé
  (modèle léger à l'étape 1).

## D2 — Division du travail : code côté responsable technique, prompts côté responsable éditorial
- **Date** : 2026-10-06
- **Justification** : le responsable éditorial définit et affine l'éditorial ; le responsable technique fournit le soutien technique.
- **Conséquences** : prompts séparés du code ; format de sortie imposé par le code et ajouté automatiquement
  aux consignes (impossible à casser par une modification de prompt) ; commentaires `<!-- -->` ignorés.

## D3 — Hébergement : GitHub Actions + GitHub Pages
- **Date** : 2026-10-06
- **Justification** : aucune infrastructure à maintenir, gratuit, planification et historique intégrés.
- **Conséquences** : les données et les pages générées sont committées dans le dépôt ; Pages impose un dépôt
  public sur un compte gratuit ; les workflows planifiés peuvent être suspendus après 60 jours d'inactivité
  (à surveiller).

## D4 — Essais de prompts rejouables sur un corpus figé
- **Date** : 2026-10-06
- **Justification** : comparer deux versions de prompt sur les mêmes articles, pour que la différence vienne du prompt.
- **Conséquences** : cache de collecte versionné ; commandes `replay` et `compare` ; workflow « Essai de prompts »
  utilisable depuis l'interface GitHub sans rien installer.

## D5 — Pas d'URL générée par le modèle
- **Date** : 2026-10-06
- **Justification** : éviter les liens inventés, imposer la traçabilité des sources.
- **Conséquences** : le modèle cite des identifiants d'articles ; le programme ajoute les liens et ignore les
  identifiants inconnus en les signalant dans les avertissements.

## D6 — Regroupement par sujet confié au modèle (pas d'embeddings)
- **Date** : 2026-10-06
- **Justification** : plus simple à exécuter sur GitHub Actions, entièrement pilotable par prompt.
- **Conséquences** : à réévaluer si le volume dépasse quelques centaines d'articles par jour (pré-regroupement
  par embeddings envisageable).

## D7 — Titre et extrait des flux uniquement, pas de texte intégral
- **Date** : 2026-10-06
- **Justification** : paywalls, droit d'auteur, simplicité.
- **Conséquences** : la qualité dépend de la richesse des flux choisis ; la page ne republie que des synthèses
  courtes avec liens vers les sources, en `noindex`.

## D8 — Dépôt GitHub public
- **Date** : 2026-10-07
- **Justification** : aucun contenu confidentiel ; GitHub Pages gratuit exige un dépôt public.
- **Conséquences** : le code, les prompts, les caches de collecte et les pages générées sont publics ; ne jamais committer de clé API ; la page publiée reste en `noindex` et ne contient que des synthèses courtes avec liens.

## D9 — Fédération d'identité plutôt qu'une clé API statique
- **Date** : 2026-10-07
- **Justification** : pas de secret longue durée à stocker, faire tourner ni risquer de fuiter (dépôt public, D8) ;
  GitHub Actions présente un jeton OIDC de courte durée que la règle de fédération Anthropic échange contre un jeton d'accès
  rattaché à un compte de service et à un workspace. Validé par le workflow `anthropic-wif-test`.
- **Conséquences** : les workflows déclarent `id-token: write`, récupèrent le jeton (audience `https://api.anthropic.com`), l'écrivent
  dans un fichier (`ANTHROPIC_IDENTITY_TOKEN_FILE`) et lisent les identifiants dans des variables de dépôt `vars.ANTHROPIC_*`
  (ce ne sont pas des secrets). Aucun `ANTHROPIC_API_KEY` dans les workflows : il aurait priorité sur la fédération.
  SDK `anthropic>=0.99` (fédération depuis 0.98, ciblage d'un workspace depuis 0.99). Le jeton OIDC est obtenu une seule fois
  au début du job ; un job très long pourrait voir l'échange échouer si le SDK doit renouveler son jeton d'accès après
  l'expiration du jeton GitHub (jobs actuels limités à 20 min). L'usage local reste possible avec une clé API.

## À trancher
- Liste définitive des flux (belges notamment) après `check-feeds`.
- Modèles par étape, après vérification des identifiants disponibles.
