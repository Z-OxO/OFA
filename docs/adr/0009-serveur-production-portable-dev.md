# ADR-0009 — Le serveur produit, le portable calcule

- Date : 2026-11-22
- Statut : accepté (après le J3)

## Contexte

Deux machines : le **serveur** (Debian 12, 8 cœurs, 16 Go, **100 Go**) et le **portable** (WSL2,
20 cœurs, 8 Go, **922 Go libres**). Deux contraintes non négociables, posées par l'utilisateur :
l'ingestion doit être **continue** (7,7 jours à 4 req/s pour les 435 jours d'archive, puis
quotidienne), et le **site** du J6 doit être hébergé sur le serveur. Un portable qui dort coupe la
collecte.

La question n'était donc pas « où vit le dépôt » mais **quel poste de stockage vit où** : après le
J3, la projection sur 100 Go est passée de 71 à **90-99 Go**, parce que la sortie de re-sim coûte
322 Ko par partie et non 179 (PERF.md, mesuré sur 513 parties).

## Ce que la mesure dit

| poste | volume | mesuré |
|---|---|---|
| `data/raw/meta`, archive complète (2 654 420 parties) | **26 Go** | J1 |
| `data/raw/full`, 100 000 parties | 4,3 Go | J0 |
| `index.sqlite` | 2,5 Go | J1 (53 Mo pour 55 405) |
| `data/parquet` (games + participants + intents) | ~14 Go | J2 |
| `data/resim`, 100 000 parties | **32 Go** | **J3** |
| clone OpenFront | 2,1 Go | J0 |
| worktrees de moteurs (~370 commits sur 435 j) | 9 à **17,8 Go** | ADR-0008 |
| `data/maps` + `data/node_modules` | 0,6 Go | mesuré après le J3 |
| **tout sur une machine** | **90 à 99 Go** | |

## Choix

**Le serveur porte la collecte, la normalisation et le site. Le portable porte le moteur et la
re-simulation.**

| | serveur | portable |
|---|---|---|
| `ofa ingest`, `ofa purge-deleted`, `ofa fetch-full` | **oui** (tout appel à l'API) | non |
| `ofa normalize` | **oui**, depuis son propre `data/raw/` | non |
| `ofa engines`, `ofa resim` | non | **oui** |
| service web (J6) | **oui** | non |
| clone OpenFront, worktrees, maps | **absents** | oui |
| **total** | **79 Go sur 100**, 21 de marge | ~57 Go sur 922 |

Ce qui circule, et dans un seul sens chacun :

- **serveur → portable** : `data/raw/full/` et `data/parquet/games/` — le record complet pour
  rejouer, la table pour choisir quoi rejouer. **4,5 Go** à 100 000 parties, pas 30 : le portable
  n'a aucun besoin des 26 Go de `raw/meta`.
- **portable → serveur** : `data/resim/` (Parquet, 161 Mo aujourd'hui) et le code.

Les Parquet du J2 **ne circulent pas** : le serveur les reconstruit depuis son propre brut
(3 min 27 pour 55 952 records, ~2 h 45 projetées pour l'archive). Une table qui se reconstruit en
deux heures ne se transporte pas.

## Raison

Le poste qui faisait déborder le serveur est le **cache moteur** : 2,1 Go de clone + jusqu'à
17,8 Go de worktrees + 0,6 Go de maps, soit ~20 Go pour un travail qui ne sert qu'à la re-sim. En
le gardant sur la machine qui a 922 Go, le serveur retombe à **79 Go** et l'arbitrage d'éviction
que l'ADR-0008 reportait au J9 **devient sans objet** — on garde tous les moteurs, sans plafond.

Le portable, lui, perd son seul handicap : ses 8 Go de RAM limitent la re-sim à 6 workers contre 8
sur le serveur, soit 365 contre 486 parties/h. C'est 20 % sur un travail de fond, contre 20 Go de
disque sur la machine qui en a le moins.

## Conséquences

- **Il faut un remote git** : le dépôt n'en a aucun aujourd'hui, et c'est le seul chemin du code
  vers le serveur. À régler avant la première mise en service.
- Le serveur a besoin de `.ofa-contact` (hors git) et de `uv sync`. Il n'a **pas** besoin de
  `data/openfront` : `ofa ingest` et `ofa normalize` ne touchent jamais au moteur.
- **Le limiteur de débit vit dans le processus** (`ofa/client.py`, `RateLimiter` dans `Client`).
  Deux `ofa` qui appellent l'API en parallèle font **deux fois** le débit configuré, et rien ne le
  signale avant `ofa rate-check` après coup. Donc : un seul processus appelant l'API à la fois, et
  comme tous vivent sur le serveur, la règle est simple à tenir. À câbler dans les timers du J9.
- Le portable travaille sur un sous-ensemble : `ofa resim` sélectionne dans
  `data/parquet/games`, qu'il faut donc synchroniser avant chaque campagne de re-sim.
- Si un jour la re-sim doit tourner sur le serveur (panne du portable, parallélisme), l'ADR-0008
  redevient d'actualité : il faudra l'éviction des worktrees.

## Comment vérifier

```bash
# sur le serveur, apres chaque campagne : le disque, poste par poste
du -sh data/raw data/state data/parquet data/resim    # attendu : pas de data/engines
df -h .

# sur le portable : ce qui doit etre a jour avant un `ofa resim`
duckdb -c "select count(*) from 'data/parquet/games/**/*.parquet'"
find data/raw/full -name '*.json.zst' | wc -l
```
