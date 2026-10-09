# Variantes d'architecture avec Feeder (Plus/Pro) et un LLM local ou par API

*Document de conception — octobre 2026. Exploration, **aucune décision prise** (voir « À trancher » de `decisions.md`).
Contexte : la revue actuelle (`architecture.md`, option 2) collecte les flux RSS elle-même et appelle Claude via la fédération d'identité (D9).
Question : que gagne-t-on à s'appuyer sur les fonctions de tri de [Feeder](https://feeder.co/pricing) et à varier le LLM (Claude, Mistral, modèle local de type Qwen) ?*

## Ce qui a été vérifié (au 9 octobre 2026)

- **Connecteur Feeder (MCP).** Outils observés : `get_posts` (filtre toutes/non lues/étoilées, par flux, dossier ou période ; résumé de 50 mots),
  `expand_post` (corps complet *stocké*), `search_posts` (recherche plein texte titre + corps), collections, dossiers, marquage lu, ajout de flux.
  Un appel de test (`list_feeds`) a été **refusé** : le connecteur exige un abonnement **Plus ou Professional**, que le compte n'a pas.
- **Offres Feeder**, d'après la [page tarifs](https://feeder.co/pricing) : Plus = 2 500 flux, mise à jour toutes les 5 min, règles et filtres,
  résumés par e-mail ; Professional = 10 000 flux, mise à jour toutes les minutes, intégrations logicielles, création de flux RSS.
- **Accès programmatique.** Une source tierce indique que l'API REST est accessible sur demande ou en Enterprise : **à confirmer auprès de Feeder**.
  Le connecteur MCP est conçu pour un client interactif ou une routine Claude ; rien ne documente un usage depuis un runner GitHub.
- **Coût actuel de la revue** (7 octobre : environ 60 000 tokens en entrée, 19 000 en sortie, Haiku pour l'étape 1, Sonnet pour les suivantes) :
  de l'ordre de **0,25 à 0,30 $ par jour**, soit environ 8 $ par mois. Estimation d'après les tarifs publics, non comparée à une facture.
  L'étape 1 (volume) représente environ 0,09 $ par jour.

## Architecture A — Agent Claude branché sur Feeder (sans code)

`Feeder (règles, dossiers) → routine cloud Claude + connecteur Feeder → revue dans une collection Feeder, un e-mail ou un commit`

La routine lit les articles non lus d'un dossier, ouvre les plus importants (`expand_post`) et rédige la revue.

| Avantages | Inconvénients |
|---|---|
| Aucun code à maintenir | Moins déterministe : les garde-fous du projet (pas d'URL générée par le modèle, identifiants cités, repli contrôlé) ne s'appliquent plus |
| Le tri de Feeder (règles, étoiles, non lus) devient un signal éditorial | Claude uniquement (ni Qwen ni Mistral) |
| `search_posts` permet des suivis thématiques | Dépend du plan Plus/Pro et de la disponibilité du connecteur côté routines (non vérifiée) |
| Résultat directement lisible dans Feeder | Difficile à tester sans appel réel ; un modèle léger a déjà mal appliqué des critères simples (essai de la routine de surveillance avec Haiku) |

## Architecture B — Pipeline actuel, Feeder en entrée, LLM interchangeable

`Feeder (tri) → adaptateur de source → étapes 1 à 4 → GitHub Pages`

Le pipeline Python est conservé. Deux modifications : une source Feeder à la place de (ou en complément de) `collect.py`,
et un `llm.py` à plusieurs fournisseurs, avec un **routage par étape** dans `config/settings.yaml`.

| Étape | Rôle | Candidats |
|---|---|---|
| 1 | Résumé, rubrique, importance (gros volume) | Haiku, Mistral Small, ou Qwen local |
| 2 à 4 | Regroupement, synthèse, chapeau | Claude (Sonnet) ou Mistral Large |

| Avantages | Inconvénients |
|---|---|
| Réutilise le code, les tests avec `FakeLLM`, la fédération et tous les garde-fous | **Gain financier d'un modèle local faible** : l'étape 1 coûte environ 2,70 $ par mois. L'intérêt est l'indépendance et la confidentialité |
| Chaque étape change de modèle indépendamment ; `replay` et `compare` servent déjà à comparer | **Mistral ne passe pas par la fédération** (à ma connaissance) : secret statique, ce qui annule en partie D9 |
| Le responsable éditorial garde la main sur les prompts | **Qwen sur un runner GitHub** : sans GPU, un modèle de 7 milliards de paramètres met probablement des dizaines de minutes pour l'étape 1 (estimation à mesurer) ; un runner auto-hébergé est **déconseillé sur un dépôt public** |
| Migration progressive, étape par étape | Les prompts sont réglés pour Claude : autre modèle = revalidation éditoriale de `prompts/*.md` ; les petits modèles produisent davantage de JSON invalide |
| | Feeder devient une dépendance de production sans API documentée utilisable en CI |

## Architecture C — Local d'abord, revue personnelle et privée

`Feeder (étoilés, non lus) → Mac (Ollama + Qwen, planifié par launchd) → e-mail ou note ; Claude ou Mistral en option pour la synthèse`

Produit différent de la revue publique : une revue **personnalisée** à partir de ce qui est suivi et étoilé dans Feeder.

| Avantages | Inconvénients |
|---|---|
| Confidentialité maximale, coût marginal nul | Le Mac doit être allumé à l'heure voulue ; réintroduit de l'infrastructure (contraire à D3) |
| Le signal de lecture de Feeder (non lus, étoilés) personnalise réellement la revue | Qualité des synthèses en français d'un Qwen local inférieure à celle de Claude |
| Rien n'est publié : pas de question de droit d'auteur ni de page publique | Pas d'historique ni de pages archivées, sauf à les construire |

## Comparaison

| Critère | A. Agent Claude + Feeder | B. Pipeline + Feeder en entrée | C. Local, personnel |
|---|---|---|---|
| Effort de mise en œuvre | Faible | Moyen à élevé | Moyen |
| Déterminisme et tests | Faible | **Fort** | Moyen |
| Qualité de rédaction | Forte (Claude) | Forte si Claude aux étapes 2 à 4 | Moyenne (Qwen) |
| Coût mensuel | Plan Feeder + usage | Plan Feeder + environ 6 à 8 $ d'API | Plan Feeder seul |
| Confidentialité | Moyenne | Moyenne | **Forte** |
| Dépend d'une API Feeder | Connecteur | **Oui, à vérifier** | Connecteur |
| Garde-fous actuels conservés | Non | **Oui** | Partiellement |

## Source bloquée : Le Soir (constats du 9 octobre 2026)

**Blocage.** Tous les flux de `lesoir.be` répondent `403 Access Denied` avec `server: AkamaiGHost` (protection anti-robots d'Akamai, et non Cloudflare),
avec l'agent utilisateur du projet comme avec celui d'un navigateur ; quatre adresses essayées (`/rss`, `/rss2/9/…`, `/rss2/2/…`, `/rss/81853/…`).
Le site est aussi fermé aux outils web de Claude. Le collecteur du projet ne peut donc pas lire Le Soir directement (`config/feeds.yaml`, flux désactivé).

**Lecteurs essayés par le responsable éditorial sur ces flux :**

| Lecteur | Résultat | Remarque |
|---|---|---|
| Feeder (offre gratuite) | Lit Le Soir | Pas d'accès programmatique sans plan Plus (connecteur) |
| Inoreader | Lit Le Soir | À contrôler : fraîcheur, plafond d'articles du flux de sortie, nom du journal conservé |
| NewsBlur | Lit Le Soir | Le flux RSS d'un dossier est inutilisable en dessous du plan Archive (voir ci-dessous) |
| Feedbin | **Anciens articles seulement** | Écarté pour Le Soir ; reste valable pour les autres sources (API incluse) |

La fraîcheur des articles doit être vérifiée pour chaque lecteur : « lit Le Soir » ne garantit pas des articles du jour.

**Flux du Soir** (annuaire de Feeder, au 9 octobre 2026) : les identifiants 2, 9, 10, 11, 13, 31867 et 31876 répondent
(`https://www.lesoir.be/rss2/<identifiant>/cible_principale`) ; le 31868 ne répond plus et l'ancien flux FeedBurner est arrêté.
Aucune rubrique n'est indiquée (tous s'intitulent « Actualité - Le Soir ») : le tri de l'actualité belge se fait dans un lecteur.
Un fichier d'import est fourni : `config/feeds.opml` (flux du projet + sept flux du Soir dans un dossier « à trier »).

### Comment récupérer ces articles dans le pipeline

| Voie | Constat | Limites |
|---|---|---|
| **Google Actualités** (`news.google.com/rss/search?q=site:lesoir.be…`) | Testé : 100 entrées, source « Le Soir », HTTP 200, sans lire lesoir.be | Pas d'extrait (titre seul) ; liens redirigés par Google ; suffixe « - Le Soir » à retirer ; conditions de Google à vérifier |
| **Feeder Plus** | Lit déjà Le Soir ; connecteur Claude (MCP) sur plan Plus ou Professional | Pas de flux de sortie ; accès programmatique à confirmer ; environ 96 $/an |
| **Inoreader Pro** | Flux de sortie RSS, JSON ou OPML d'un dossier ou d'une étiquette ; API réservée à Pro (portail développeur), 100 requêtes par jour en lecture par défaut | Fonction de flux de sortie : Pro d'après Inoreader, sans phrase explicite ; plafond d'articles et nom des sources **non vérifiés** ; environ 90 $/an |
| **NewsBlur** | Flux Atom d'un dossier : `/reader/folder_rss/<utilisateur>/<jeton>/<filtre>/<dossier>` (code source public) | **20 articles au maximum** (`limit: 20`), cache de 60 s ; **plan Premium Archive requis** (99 $/an) : sans lui le flux ne contient qu'un message d'erreur (*« You must have a premium archive subscription… »*), constaté le 9 octobre sur un dossier réel. API : OAuth sur demande par e-mail, ou mot de passe ; pages d'une douzaine d'articles |
| **Feedbin** | Écarté pour Le Soir (anciens articles) | — |

**Comparaison des coûts annuels** pour un flux de dossier : NewsBlur Archive 99 $, Inoreader Pro environ 90 $, Feeder Plus environ 96 $ (7,99 $/mois facturés à l'année),
à ajouter aux environ 8 $ par mois d'API Anthropic déjà dépensés. NewsBlur Premium à 36 $/an ne suffit pas.

**Raccordement technique (commun aux lecteurs à flux de sortie).** L'URL d'un flux de sortie contient un jeton secret : elle **ne doit jamais figurer dans le dépôt**
(public, D8). Il faudrait la lire depuis un secret GitHub, via une variable d'environnement résolue dans `collect.py`, avec un test.
Ce changement n'est **pas réalisé** ; il serait indépendant du fournisseur choisi. À vérifier sur un exemple : le flux de sortie conserve-t-il le nom du journal d'origine
(sinon la revue afficherait le nom du lecteur comme source) ?

**Contournement exclu.** Se faire passer pour un autre robot ou piloter un navigateur automatisé pour franchir la protection d'Akamai contourne un contrôle d'accès de l'éditeur :
non retenu. La voie légitime est de demander à l'éditeur (Rossel) un flux partenaire ou l'autorisation d'un agent utilisateur ; une liste blanche par adresse IP ne fonctionnerait pas avec les runners GitHub.

## Recommandation (provisoire)

Pour **Le Soir** : ne rien payer avant d'avoir fait avec **Inoreader** le test déjà fait avec NewsBlur (flux de sortie d'un dossier : nombre d'articles, fraîcheur,
nom du journal). NewsBlur n'est pas retenu sauf besoin du plan Archive. Si Inoreader échoue, Feeder Plus reste la seule voie confirmée ; Google Actualités sert de solution d'attente.

Commencer par **B sans modèle local** : Feeder en entrée, Claude pour toutes les étapes, et un `llm.py` prêt à accueillir d'autres fournisseurs.
Ajouter Qwen ou Mistral plus tard, sur l'étape 1 uniquement, si la confidentialité ou l'indépendance l'exigent.
**C** peut se greffer ensuite comme second produit privé, plutôt que de remplacer la revue publique.

Un levier de coût indépendant de Feeder : les modèles de `config/settings.yaml` (`claude-haiku-4-5`, `claude-sonnet-4-5`) sont plus anciens que `claude-sonnet-5-5`,
facturé 2 $ / 10 $ par million de tokens (entrée / sortie) contre 3 $ / 15 $ pour Sonnet 4.6 ; le tarif de Sonnet 4.5 est à vérifier. Le choix relève du responsable éditorial (qualité à revalider avec `replay` et `compare`).

## Points à trancher avant de choisir

1. **Plan Feeder** : passer en Plus (ou Pro) pour tester le connecteur ; demander à Feeder s'il existe un accès programmatique utilisable depuis un runner GitHub.
2. **Texte stocké par Feeder** : `expand_post` renvoie le corps « stocké ». Si c'est davantage que l'extrait du flux, vérifier la conformité avec D7
   (pas de texte intégral : paywalls, droit d'auteur) avant de s'en servir.
3. **Objectif** : économiser, gagner en confidentialité, ou personnaliser ? La réponse écarte généralement deux options sur trois.
4. **Fournisseur hors Anthropic** : accepter ou non un secret statique (Mistral) alors que D9 l'a évité.
5. **Le Soir** : faut-il l'intégrer, et par quelle voie (Inoreader, Feeder Plus, Google Actualités) ? Vérifier d'abord les conditions d'utilisation du Soir
   sur la reprise de titres et d'extraits dans une page publique, même courte, et envisager de demander un accès à l'éditeur.

## Sources

- [feeder.co/pricing](https://feeder.co/pricing) — offres Plus et Professional (le détail des lignes varie selon les variantes de la page : vérifier la page en ligne).
- Outils du connecteur Feeder (MCP) observés dans la session du 9 octobre 2026.
- Mention de l'API sur demande / Enterprise : site tiers, **non confirmé par Feeder**.
- [Annuaire Feeder des flux de lesoir.be](https://feeder.co/discover/site/lesoir.be) — identifiants et état des flux (consulté le 9 octobre 2026, sans rubriques).
- Inoreader : [flux de sortie](https://innoreader.com/blog/2026/01/connect-tools-and-distribute-content.html), [limites de l'API](https://InoReader.com/developers/rate-limiting), [enregistrement d'une application](https://InoReader.com/developers/register-app), [tarifs](https://inoreader.com/pricing).
- NewsBlur : [tarifs](https://hwww.newsblur.com/pricing), [API](https://newsblur.com/api), [MacStories](https://www.macstories.net/linked/newsblur-adds-rss-feeds-for-folders/),
  code source ([`views.py`](https://github.com/samuelclay/NewsBlur/blob/master/apps/reader/views.py), fonction `folder_rss_feed`, et [`urls.py`](https://github.com/samuelclay/NewsBlur/blob/master/apps/reader/urls.py)), branche principale lue le 9 octobre 2026 ;
  la production peut différer, mais le plan Archive a été confirmé par un essai réel.
- Essais de blocage (`403`, `AkamaiGHost`) et du flux Google Actualités : session du 9 octobre 2026.
