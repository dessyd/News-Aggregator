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
| Feeder (offre gratuite) | Lit Le Soir, **à jour** | Pas d'accès programmatique sans plan Plus (connecteur) |
| Inoreader (offre gratuite) | Lit Le Soir, **à jour** (constaté le 9 octobre en fin d'après-midi) | Flux de sortie et API : Pro ; plafond d'articles et nom du journal conservé **non vérifiés** |
| NewsBlur | Lit Le Soir **mais en retard** : doit être forcé pour se mettre à jour | Voir « Essai de l'API NewsBlur » : voie suspendue |
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
| **NewsBlur** | Flux Atom d'un dossier : `/reader/folder_rss/<utilisateur>/<jeton>/<filtre>/<dossier>` (code source public) | **20 articles au maximum** (`limit: 20`), cache de 60 s ; **plan Premium Archive requis** (99 $/an) : sans lui le flux ne contient qu'un message d'erreur (*« You must have a premium archive subscription… »*), constaté le 9 octobre sur un dossier réel. **API** (mot de passe, ou OAuth sur demande par e-mail) : **testée en Premium, 100 articles par page** (voir ci-dessous) |
| **Feedbin** | Écarté pour Le Soir (anciens articles) | — |

**Comparaison des coûts annuels** pour un flux de dossier : NewsBlur Archive 99 $, Inoreader Pro environ 90 $, Feeder Plus environ 96 $ (7,99 $/mois facturés à l'année),
à ajouter aux environ 8 $ par mois d'API Anthropic déjà dépensés. NewsBlur Premium à 36 $/an ne suffit pas **pour le flux RSS de dossier**, mais suffit **par l'API** (ci-dessous).

### Essai de l'API NewsBlur (compte Premium, 9 octobre 2026)

Script d'essai lancé par le responsable éditorial lui-même (le mot de passe n'a jamais été communiqué) : connexion `POST /api/login`, lecture des dossiers `GET /reader/feeds`,
puis `GET /reader/river_stories` (`read_filter=all`, `order=newest`, `limit=100`).

- **Plan** : `is_premium: True`, `is_archive: False`. Le code source de NewsBlur limite un compte gratuit à une seule page de 3 articles ; Premium ouvre la lecture par dossier.
- **Débit** : le serveur accepte 100 articles par page ; 797 articles en 10 requêtes et 12 secondes (dossier de six flux), du 2 au 9 octobre.
- **Le Soir** : un seul flux ajouté, `https://www.lesoir.be/rss2/9/cible_principale` (3 abonnés NewsBlur), **10 articles, tous du 9 octobre, de 09:20 à 14:59** : environ deux articles par heure.
  Le dernier date de moins d'une heure : le flux est frais. Les titres lus portent sur l'actualité belge ; la rubrique du flux n'est pas confirmée.
- **Champs** : titre, adresse de l'article, date, auteurs, flux d'origine (`story_feed_id`) et `story_content` sont présents. `story_content` fait en médiane 401 caractères (maximum 429) : c'est l'extrait fourni par le flux, pas le texte intégral (D7).
  La troncature à `extrait_max_chars` doit être conservée.
- **Horodatage** : `story_date` est donné en « heure du serveur », sans fuseau explicite. Le code utilise `story_timestamp` (époque Unix) quand il existe ; à confirmer sur un cas réel.
- **Retard constaté (9 octobre, 16:51 UTC)** : le dernier article vu par NewsBlur datait toujours de 14:59, soit près de deux heures sans nouvel article alors que le flux en donnait environ deux par heure
  le matin. En comparant avec Feeder, Inoreader et la page du Soir, **seuls Feeder et Inoreader étaient à jour** : NewsBlur ne se met à jour que si l'on **force** la relecture du flux. NewsBlur sait donc lire le flux
  (ce n'est pas un blocage), mais sa relecture automatique est trop lente pour ce flux (3 abonnés seulement).
- **Relecture forcée par l'API** : `GET /reader/refresh_feed/<id>` exécute `feed.update(force=True)` (code source public). Elle **ne résout pas** le problème : le flux ne contient qu'une fenêtre d'une dizaine d'articles
  (environ cinq heures de publication), donc une relecture forcée par jour au moment de la revue ne ramènerait que les dernières heures, et tout ce qui est sorti de la fenêtre serait perdu. Il faudrait des relectures
  forcées environ toutes les heures depuis un job dédié, dépendant du cron de GitHub (déjà en retard de plusieurs heures le 9 octobre) et sollicitant NewsBlur de façon intensive.
- **Conclusion** : voie NewsBlur **suspendue pour Le Soir** ; la branche `feat/newsblur-api` et la demande #8 restent ouvertes, sans fusion, le code étant correct mais la source non validée.
- **Couverture** : un seul des sept flux du Soir est suivi. Les six autres (2, 10, 11, 13, 31867, 31876) restent à ajouter, et leurs rubriques à identifier d'après les titres.
- **Authentification d'un job planifié** (choix en attente) : mot de passe du compte personnel en secret GitHub (simple, mais accès à tout le compte et secret longue durée, contraire à l'esprit de D9) ;
  compte NewsBlur dédié (cloisonné, mais un second Premium à 36 $/an, car un compte gratuit n'obtient que 3 articles) ; OAuth, dont les identifiants s'obtiennent en écrivant au développeur.

## Autres sources à tester via un lecteur (9 octobre 2026)

Lecture directe depuis le collecteur du projet, une requête par site, agent utilisateur du projet :

| Source | Résultat | Suite |
|---|---|---|
| Sudinfo | 403, `AkamaiGHost` (même protection que Le Soir) | **À tester en priorité** dans NewsBlur (ajouter le site, détection automatique du flux) |
| BX1 | 403, Cloudflare | À tester |
| Brussels Times | 200 mais une page HTML, pas un flux | À tester ; la bonne adresse est peut-être ailleurs |
| RTL Info, Belga News Agency | 404 sur les adresses essayées | Trouver d'abord le bon flux (adresses essayées : suppositions, non vérifiées) |
| Le Parisien | L'ancienne adresse est bloquée (403, Akamai), mais **`https://feeds.leparisien.fr/leparisien/rss` répond (100 entrées)** | Lisible directement |
| La DH, L'Avenir, 7sur7, HLN, Bruzz, Le Figaro, Libération, Courrier international, Euronews FR, The Guardian | 200 avec des articles | Lisibles directement. À valider avant activation : pertinence, doublons, extraits (7sur7 sans extrait) ; l'adresse de La DH essayée est un flux Arc général, différent de celui noté « Les Sports+ » |

## Autres lecteurs possibles

Le point décisif est **qui va chercher le flux**. Un lecteur **hébergé** le lit depuis ses serveurs (Feeder, Inoreader, NewsBlur, Feedbin) ; un lecteur **local ou auto-hébergé**
(Miniflux, FreshRSS, CommaFeed, Tiny Tiny RSS, NetNewsWire, Reeder) le lit depuis l'adresse de son propriétaire et se heurtera au même 403 que le collecteur.

| Lecteur | Prix | API | Remarque |
|---|---|---|---|
| **BazQux Reader** | environ 30 $/an d'après son forum (la page officielle ne l'indique pas), essai de 30 jours | Google Reader et Fever ; 3 000 flux | Meilleur candidat de secours |
| **The Old Reader** | gratuit jusqu'à 100 flux ; Premium environ 25 à 30 $/an (sources contradictoires) | De type Google Reader, **documentation officielle non vérifiée** | Utile en test gratuit |
| **Feedly** | gratuit jusqu'à 100 flux | Enterprise seulement | Test de diagnostic uniquement |
| **Readwise Reader** | environ 10 $/mois | Ne gère pas les abonnements aux flux | Ne convient pas |
| Miniflux, FreshRSS, CommaFeed (auto-hébergés ou chez PikaPods, DINAO…) | Gratuit, ou hébergement dès environ 1 $/mois | REST, Fever | Adresses de centre de données : le blocage du Soir les vise peut-être aussi ; **pari à tester**, pas un point de départ |

Tant que NewsBlur fonctionne, tester d'autres lecteurs a peu d'intérêt : BazQux puis The Old Reader ne servent que de secours si l'accumulation des articles échoue.

**Raccordement technique (commun aux lecteurs à flux de sortie).** L'URL d'un flux de sortie contient un jeton secret : elle **ne doit jamais figurer dans le dépôt**
(public, D8). Il faudrait la lire depuis un secret GitHub, via une variable d'environnement résolue dans `collect.py`, avec un test.
Ce changement n'est **pas réalisé** ; il serait indépendant du fournisseur choisi. À vérifier sur un exemple : le flux de sortie conserve-t-il le nom du journal d'origine
(sinon la revue afficherait le nom du lecteur comme source) ?

**Contournement exclu.** Se faire passer pour un autre robot ou piloter un navigateur automatisé pour franchir la protection d'Akamai contourne un contrôle d'accès de l'éditeur :
non retenu. La voie légitime est de demander à l'éditeur (Rossel) un flux partenaire ou l'autorisation d'un agent utilisateur ; une liste blanche par adresse IP ne fonctionnerait pas avec les runners GitHub.

## Recommandation (provisoire)

Pour **Le Soir** : seuls **Feeder et Inoreader** sont à jour (constat du 9 octobre). NewsBlur est suspendu (relecture trop lente, voir plus haut) et Feedbin écarté.
La piste à tester est **Inoreader Pro** (environ 90 $/an), qui offre l'essai gratuit de 14 jours annoncé par Inoreader (durée exacte à vérifier dans *Préférences → Facturation*) :
mettre le flux du Soir dans un dossier, activer son **flux de sortie RSS**, puis mesurer le nombre d'articles, la fraîcheur et la conservation du nom du journal avant tout achat.
Raccordement prévu, indépendant du fournisseur : une entrée de flux RSS ordinaire dont l'URL est lue dans une variable d'environnement secrète (`url_env`, branche `feat/url-env`).
Secours, dans cet ordre : BazQux (essai de 30 jours), Feeder Plus (seule voie confirmée à jour, via le connecteur), et Google Actualités comme solution d'attente (titres seuls).

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
5. **Le Soir** : faut-il l'intégrer, et par quelle voie (NewsBlur par l'API, Inoreader, Feeder Plus, Google Actualités) ? Vérifier d'abord les conditions d'utilisation du Soir
   sur la reprise de titres et d'extraits dans une page publique, même courte, et envisager de demander un accès à l'éditeur.
6. **Voie NewsBlur** : abandon, ou reprise avec des relectures forcées fréquentes (voir « Essai de l'API NewsBlur ») ; l'authentification du job (compte personnel, compte dédié, OAuth) ne se pose que dans ce second cas.
7. **Essai Inoreader Pro** : durée réelle de l'essai, plafond d'articles du flux de sortie, nom du journal conservé ; décision d'achat ensuite.
8. **Couverture** : quels flux du Soir (et de Sudinfo, BX1…) suivre une fois le lecteur choisi.

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
- Autres lecteurs : BazQux ([FAQ](https://bazqux.com/faq), [forum, prix](https://discourse.bazqux.com/t/subscription-fee/94)), [Readwise Reader](https://readwise.io/pricing/reader),
  CommaFeed ([hébergement DINAO](https://dinao.com/en/conteneur/commafeed)) ; les prix de BazQux et de The Old Reader viennent de pages tierces ou de forums, à vérifier avant tout achat.
- Annuaire Feeder du Parisien : [feeder.co/discover/site/leparisien.fr](https://feeder.co/discover/site/leparisien.fr) (les pages Sudinfo, BX1, Brussels Times et RTL n'existent pas dans l'annuaire).
