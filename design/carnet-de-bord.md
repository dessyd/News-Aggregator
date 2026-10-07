# Carnet de bord éditorial (gabarit pour le responsable éditorial)

À remplir avant d'écrire les prompts, puis à chaque version. Ce carnet documente la démarche : chaque choix
éditorial doit pouvoir être expliqué et défendu.

## 1. Cahier des charges éditorial (à rédiger en premier)

- **Lectorat visé** (qui lit cette revue, quand, pour quoi faire ?) :
- **Périmètre** (Belgique, Europe, monde ; rubriques retenues) :
- **Longueur souhaitée** (chapeau, nombre de sujets, taille de chaque synthèse) :
- **Ton et registre** :
- **Ce qui mérite d'être retenu** (critères d'importance) :
- **Ce qui doit être écarté** (people, faits divers, opinions sans fait nouveau…) :
- **Règles de sourcing** (attribution, traitement des divergences entre sources) :
- **Langues des sources acceptées** :

## 2. Grille d'évaluation d'une revue

Noter chaque critère de 1 à 5 et justifier en une phrase.

| Critère | Question | Note | Observation |
|---|---|---|---|
| Exactitude | Chaque affirmation est-elle présente dans les sources citées ? | | |
| Couverture | Les sujets majeurs de la journée sont-ils présents ? Des sujets importants manquent-ils ? | | |
| Redondance | Un même événement apparaît-il plusieurs fois ? | | |
| Équilibre | Les sources divergentes sont-elles signalées ? Un média domine-t-il ? | | |
| Lisibilité | Le texte est-il clair, sobre, de bonne longueur ? | | |
| Traçabilité | Les liens mènent-ils bien aux articles utilisés ? | | |

## 3. Journal des versions de prompts

Une entrée par essai. Le nom de l'essai est celui donné au workflow « Essai de prompts » (`--tag`).

### Modèle d'entrée

- **Essai** : (ex. `v2-plus-sobre`)
- **Date de la collecte rejouée** :
- **Fichier(s) modifié(s)** :
- **Changement** (avant → après, en quelques mots) :
- **Hypothèse** (qu'est-ce que ce changement doit améliorer ?) :
- **Étape rejouée à partir de** : 1 / 2 / 3 / 4
- **Résultat observé** (comparaison avec l'essai de référence) :
- **Notes de la grille** (avant → après) :
- **Décision** : adopté / rejeté / à retester
- **Prochaine idée** :

### Entrées

*(à compléter)*

## 4. Défauts structurels à ne pas corriger par le prompt

Avant de retoucher un prompt, vérifier que le défaut ne vient pas d'ailleurs :

- flux pauvres (titre seul, extrait vide) → changer ou ajouter des flux ;
- doublons non fusionnés → ajuster `title_similarity` ou le prompt de regroupement ;
- sujets manquants → vérifier les flux en échec (avertissements au bas de la page) ;
- coût ou lenteur → `max_articles_total`, `batch_size`, modèles par étape.
