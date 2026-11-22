# Avancement

Spec : [docs/SPEC.md](SPEC.md). Décisions : [docs/adr/](adr/). Perf : [docs/PERF.md](PERF.md).

| Jalon | État | Date |
| --- | --- | --- |
| J0 Reconnaissance et mesures | **terminé** | 2026-11-21 |
| J1 Ingestion | **terminé** — les 5 critères atteints | 2026-11-21 |
| J2 Normalisation | **terminé** | 2026-11-22 |
| J3 Re-simulation | **terminé** — 513/514 parties valides (99,81 %) | 2026-11-22 |
| J4 Niveau | **terminé** — log-loss 0,637 contre 0,693, Spearman 0,311 | 2026-11-22 |
| J4b Optimisation mesurée | **à faire** — 20,6 h de CPU pour 489 parties, à diviser par 3 | |
| J5 Moteur d'analyse | à faire | |
| J6 Site web | à faire | |
| J7 Mes parties | à faire | |
| J8 Couche IA | à faire | |
| J9 Production | à faire | |

## J0 — Reconnaissance et mesures (livré)

Livrable : [docs/RECON.md](RECON.md) (470 lignes). Aucun code de production ; scripts jetables dans
`scratch/`. 380 requêtes API à 1 req/s, **aucun 429 ni 403**, toutes journalisées dans
`scratch/requests.jsonl`.

### Critère d'acceptation : ATTEINT

3 vraies parties rejouées au commit exact du record, **diff vide** (gagnant + toutes les stats
archivées de tous les joueurs) et **hashs 100 % identiques** :

| gameID | map | mode | joueurs | ticks | hashs | débit |
|---|---|---|---|---|---|---|
| `cPaLHdRvC2` | Gateway to the Atlantic | FFA | 25 | 9 701 | 970/970 | 159 t/s |
| `dtQhXS8owK` | Strait of Hormuz | Team | 24 | 5 931 | 593/593 | 253 t/s |
| `cDkWi2N1AT` | Great Lakes | FFA | 95 | 29 342 | 2934/2934 | 172 t/s |

### Les 8 faits de la spec

7 vérifiés, 1 **réfuté**. Trois corrections : `createGameRunner` est dans `GameRunner.ts` ; le viewer
de replay **n'accepte aucun tick de départ** ; `api.openfront.io/game/<id>` sans `/public` **n'est pas
bloqué** par Cloudflare (HTTP 200 depuis un script).

### Découvertes qui changent la suite

- **L'API fournit déjà `ffaRating.before`** par partie publique FFA — exactement le « rating d'avant
  la partie » du J4. Pas d'équivalent en Team. Devient l'étalon de validation du J4.
- **`deathPosition` n'est jamais archivé** (0 sur 993 lignes joueur) : la mesure de résultat principale
  en FFA n'existe que par re-simulation. `killedAt` n'en est qu'un proxy imparfait (Spearman −0,81 / −0,96).
- **Le ranked 1v1 fait 10 % des parties publiques** et doit être filtré explicitement ;
  **`rankedType=ffa` est vide** aujourd'hui.
- **La re-simulation est le facteur limitant**, pas le téléchargement : 14 150 parties/jour sur
  8 cœurs, contre 86 400 téléchargements/jour à 1 req/s.

### Chiffres clés

| Mesure | Valeur |
| --- | --- |
| Parties publiques/jour | 9 320 (6 619 FFA + 2 700 Team + 172 ranked 2v2) |
| Périmètre ofa (hors 1v1) | **8 388/jour** |
| Archive disponible | depuis le **2025-06-04** (535 jours, ~4,5 M parties) |
| Record complet / sans `turns`, zstd | 39,2 Ko / **5,3 Ko** (médiane) |
| Débit de re-sim | 159 à 253 ticks/s/cœur ; 0,8 Go par worker |
| Coût moteur par commit distinct | 48 Mo (3 commits distincts seulement sur 300 parties de 7 jours) |
| `statsAgreed = true` | 99,0 % |
| `publicID` null / REDACTED (7 j récents) | 0 % / 0,03 % — mais **100 % null** en juin 2025 |
| Maps distinctes | 103 sur 300 parties (longue traîne : 83 maps = 49 %) |

### Décisions prises (ADR)

- **ADR-0001** : cache moteur = worktree sparse (37 Mo) + `node_modules` par hash de lockfile copié en
  **liens durs** + maps par tree-sha. **Pas de bundle** (mesuré inutile).
- **ADR-0002** : le runner part de `tests/replay/ReplayGame.ts`, qui compare déjà les hashs.
- **ADR-0003** : définition du critère de validité d'une re-sim, avec les trois champs jamais archivés.
- **ADR-0004** : ingestion en deux étages (`data/raw/meta/` pour tout, `data/raw/full/` à la demande),
  imposée par les 100 Go de disque ; coût assumé : +2,8 % de requêtes.
- **ADR-0005** : l'archive exploitable commence au 2025-09-12, date d'apparition de `publicID`.

### Arbitrages (tous tranchés)

- **Périmètre** : FFA et Team classiques + ranked 2v2. Exclus : ranked 1v1, `publicGameType: "special"`,
  `playerTeams: "Humans Vs Nations"`. Les exclusions se recoupant, le périmètre vaut **74,7 %** des
  parties publiques.
- **Borne de l'archive : 2025-09-12** (ADR-0005). Mesuré : `publicID` est à 0 % le 2025-09-10 (3 parties)
  et à 100 % le 2025-09-12 (3 parties). Les 100 jours antérieurs sont abandonnés — sans `publicID`,
  aucune ligne joueur n'est attribuable à un compte.
- **Volume : 2 224 428 parties** sur 435 jours, intégré depuis 6 mesures d'activité réelles
  (4 036 parties/jour en octobre 2025 contre 9 791 en novembre 2026 : extrapoler le débit actuel
  surestimait de 60 %).
- **Ingestion en deux étages** (ADR-0004) : `data/raw/meta/` pour tout (11,2 Go),
  `data/raw/full/` pour les 100 k parties re-simulées (4,3 Go). **Total projeté ~54 Go sur 100 Go.**
- **Débit : 2 req/s**, 2 en parallèle → 12,9 jours pour toute l'archive. Jamais de recherche de la limite.
- **Suffisance statistique : largement acquise.** Re-sim de 100 k parties stratifiée sur 40 maps × 2 modes
  → 11 768 observations par (map, mode, tranche de niveau), soit **39× le seuil de 300**.

### Piège majeur : `statsAgreed` n'existe que depuis ~le 2026-10-15

Soit 5 semaines d'archive. Filtrer l'analyse dessus jetterait ~97 % des données. Le critère
d'acceptation du J3 reste satisfiable (~238 000 parties concernées), mais **la validité d'une re-sim
se prouve par les hashs**, présents sur tout l'historique (ADR-0003).

### Compression : exploré, rien à gagner

Dictionnaire zstd entraîné **+4,0 %**, archive solide **+5,1 %**, `xz -9e` **+3,7 %** pour 2,5× le
temps → `zstd -19` par fichier reste le bon choix. En revanche, trier la sortie de re-sim par
`(joueur, tick)` fait passer une ligne Parquet de **13,6 à 5,00 o** : 179 Ko par partie au lieu
de 487 Ko.

### À régler au J1

- `data/raw/` n'est pas inscriptible (règle `Edit(./data/raw/**)` + sandbox). Les 27 records du J0
  sont dans `scratch/records/`.
- Les fixtures sont déjà pré-choisies et téléchargées : 27 records couvrant FFA, Team, ranked 1v1 et
  2v2, Humans Vs Nations, `special`, 2 joueurs à 125 joueurs, `statsAgreed=false`, un compte
  **REDACTED**, et 3 parties de juin 2025 **sans publicID**.


## J1 — Ingestion (terminé)

### Code

| Module | Rôle |
| --- | --- |
| `ofa/config.py` | configuration par variables d'environnement, contact hors du code |
| `ofa/client.py` | **seul** chemin vers l'API : intervalle minimum garanti, backoff, coupe-circuit, journal JSONL |
| `ofa/api.py` | enveloppes des endpoints (`afterId`, fenêtre temporelle, record, comptes supprimés) |
| `ofa/raw.py` | stockage zstd, écriture atomique (`.tmp` + `os.replace`) |
| `ofa/index.py` | index sqlite publicID → parties, reconstructible, pour la purge |
| `ofa/ingest.py` | deux parcours : fenêtres de jours (récent d'abord) et `afterId` (toute l'archive) |
| `ofa/purge.py` | expurge les comptes supprimés de `data/raw` et de l'index |
| `ofa/fixtures.py` | les 27 vrais records du jeu de tests, avec la raison de chacun |
| `ofa/cli.py` | `ofa ingest`, `purge-deleted`, `fixtures`, `rate-check` |

`uv run pytest -q -m "not slow and not network"` : **28 tests, 3,7 s**. `ruff check .` propre.

### Critères d'acceptation : les 5 atteints

| Critère | Preuve |
| --- | --- |
| **7 jours des modes ciblés ingérés** | **55 405 parties, 560 Mo.** Re-listage de contrôle des 7 journées (64 requêtes de liste, aucun détail) : **0 partie manquante sur les 6 journées closes** (15 au 20 novembre, `unranked` et `2v2`). Les 178 manquantes sont **toutes sur la journée du jour**, encore ouverte : des parties archivées après le passage du collecteur, ce qui est précisément ce que couvre la règle « ne jamais sceller une journée de moins d'un jour ». |
| Une relance fait 0 requête pour les parties déjà présentes | Test + vraie exécution : 2ᵉ passage = **2 requêtes** (les 2 pages de liste). Sur la relance à 8 req/s : **17 108 parties sautées**, aucune requête de détail. |
| `kill -9` en plein milieu puis reprise, ni trou ni doublon | `test_kill_au_milieu_puis_reprise_sans_trou_ni_doublon` (crash ciblé sur une partie nommée, donc déterministe même en parallèle) ; coût exact : la requête en vol est refaite, rien d'autre. Vérifié en vrai : la première exécution a été arrêtée à 42 034 requêtes, la reprise a fini sans trou. |
| Test de purge sur fixture | `test_purge_de_bout_en_bout_sur_fixture` : le bon joueur et lui seul est expurgé, les 24 autres intacts, l'index l'oublie, la purge est tracée. |
| Le débit configuré n'est jamais dépassé | `ofa rate-check --rate 8` sur **55 931 requêtes** : avance maximale **+0,059 requête** (seuil 1). Contre-épreuve : la même mesure avec la référence 4 req/s répond **DÉBIT DÉPASSÉ**, donc elle détecte bien le passage à 8. **Aucun 429, aucun 403.** |

### Mesures réelles, qui corrigent le J0

| Mesure | J0 (27 fixtures) | J1 (55 405 parties) |
| --- | --- | --- |
| Taille d'un record `meta` | 5,3 Ko (médiane) | **8,3 Ko en moyenne** ; médiane 7,3 ; p90 15,5 ; max 42 |
| `meta` projeté pour 2 654 420 parties | 13,4 Go | **21 Go de données, ~26 Go sur disque** |
| Disque total projeté | ~54 Go | **~71 Go sur 100 Go** |
| Débit effectif | — | 3,56 req/s séquentiel ; **7,01 req/s** à 8 req/s et 4 connexions |
| Joueurs distincts en 7 jours | — | **298 774** |
| Index sqlite | — | 2 050 169 lignes pour 55 405 parties → ~2,5 Go à l'échelle de l'archive |

L'estimation du J0 était **36 % trop basse** : les 27 fixtures avaient été choisies pour couvrir des
cas limites, pas pour représenter la distribution. Toute projection tirée d'un échantillon choisi à
la main doit être refaite dès qu'on a du volume.

### Décisions

- **ADR-0006** : **8 req/s et 6 connexions** pour le rattrapage (3,8 jours pour 2 654 420 parties),
  **1 req/s** en régime permanent. Le repère chiffré est le limiteur que les auteurs d'OpenFront
  s'appliquent à eux-mêmes (`src/server/Master.ts` : 20 req/IP/s) ; 8 en représente 40 %, avec une
  marge de 2,5×. Ralentissement définitif sur 429 ou 403, **jamais remonté** : remonter pour
  re-tester serait la sonde que la spec interdit. Jamais de recherche de la limite.
- Le débit atteignable est **`concurrency / latence`**, pas le débit configuré : mesuré, 4 workers
  pour 0,57 s de latence ne donnent que 7,01 req/s. Règle : `concurrency >= rate x latence`.
- `data/raw/` est **en lecture seule depuis la session Claude** (règle `Edit(./data/raw/**)`
  appliquée par le sandbox). C'est voulu : l'ingestion tourne hors session, en tmux ou sous systemd,
  comme le prescrit la spec. Les scripts de vérification, eux, ne font que lire.
- Deux parcours, pas deux modes : `--days N` (récent d'abord, pour l'incrémental et les
  journées récentes) et `--backfill` (`afterId`, du plus ancien au plus récent, pour l'historique).
  Relancer suffit : une journée terminée ou une partie déjà là ne coûte aucune requête.
- Le périmètre d'**ingestion** (89,1 % du public, tout sauf ranked 1v1) est plus large que le
  périmètre d'**analyse** (74,7 %) : `publicGameType` étant absent de la liste, `special` ne peut
  pas être filtré avant téléchargement.

### Trois pièges trouvés en route

1. **Un lien symbolique sur `node_modules` corromprait la re-sim en silence** : les liens
   `@openfront/*` sont relatifs et résoudraient vers le mauvais commit. D'où `cp -al`.
2. **Mesurer un débit est plus subtil qu'il n'y paraît** — trois versions avant la bonne, chacune
   corrigée par une vraie exécution, et chaque fois c'était la mesure qui avait tort, pas le limiteur :
   - comptage sur fenêtre glissante fermée : annonce R+1 dès que l'espacement vaut exactement 1/R ;
   - intervalle minimum : descend sous 1/R à cause de la gigue de l'ordonnanceur (6,6 % des
     intervalles sur 21 446, le pire à −5 %) ;
   - **avance sur l'échéancier en fenêtre glissante** : `max((j − R·t_j) − min_{i≤j}(i − R·t_i))`.
     C'est la bonne. Calculée depuis le seul début du journal, elle est aveugle à une rafale tardive
     dès que le début a pris du retard : un test le vérifie.
3. **Les fixtures choisies pour couvrir des cas limites ne représentent pas la distribution** :
   5,3 Ko sur 27 records, 8,3 Ko sur 55 405.

## J2 — Normalisation (terminé)

### Code

| Module | Rôle |
| --- | --- |
| `ofa/normalize.py` | les 3 tables Parquet : tables déclaratives des stats et des intents, schémas pyarrow explicites, reconstruction totale avec échange de répertoires |
| `ofa/ingest.py` | `fetch_full` : etage 2 de l'ADR-0004, le record complet d'un echantillon (tirage a graine fixe) |
| `ofa/cli.py` | `ofa normalize`, `ofa fetch-full` |
| `docs/DATA.md` | les schémas, les 3 règles de remplissage, et **ce que les tables ne disent pas** |
| `docs/adr/0007-colonnes-et-catch-all.md` | l'arbitrage de schéma qui engage le J4, le J5 et le J6 |

`uv run pytest -q -m "not slow and not network"` : **52 tests, 12 s** (28 du J1 + 24 du J2).
`ruff check .` propre. Dépendance ajoutée : `pyarrow>=26`.

### La méthode : inventorier avant de dessiner le schéma

Le J1 a appris qu'une projection faite sur 27 fixtures choisies à la main se trompe de 36 %. Le
schéma Parquet étant ce qu'il y a de plus coûteux à rattraper (le J4, le J5 et le J6 le liront
pendant des mois), il a été dessiné sur **l'inventaire exhaustif des 55 952 records** —
2 070 777 lignes joueur, **10,1 s** en 16 processus — et non sur un échantillon.

**Zéro anomalie** sur les invariants testés : jamais de `clientID` dupliqué dans une partie, jamais
de `clientID` ni de `gitCommit` manquant, **jamais de `persistentID` non nul**, jamais de valeur de
stat négative, flottante ou non décimale, jamais de partie sans joueur, aucun record illisible.

### Ce que l'inventaire a changé dans le schéma

| Fait mesuré | Ce que le schéma naïf aurait fait |
| --- | --- |
| Les tableaux de stats sont **tronqués à droite** : `units.city` a 4 valeurs sur 1 534 804 lignes et 1 seule sur 58 951, pour une dimension de 5 | lire `v[4]` et planter, ou décaler les colonnes |
| Les clés de stats sont écrites **paresseusement** : `bombs` sur 578 630 lignes contre 1 915 887 pour `gold` | prendre une absence pour un trou, alors que c'est un zéro |
| `stats` est **absent ou `null` sur 154 890 lignes (7,5 %)** | écrire 0 partout et mentir : d'où `has_stats` et les colonnes à NULL |
| `killedAt` (63,2 %) et `finalTiles` (29,3 %) ne sont **pas** des compteurs | `killed_at = 0` voudrait dire « tué au tick 0 » |
| `gold_trade` monte à **22 202 217 393** (35 bits) | un `i32` débordait silencieusement |
| `winner` peut valoir **`nation`** (57 parties) et est absent sur 838 | 2 valeurs attendues au lieu de 3, et `won = false` au lieu de « inconnu » |
| `players[].team` **n'existe jamais** (0 / 2 070 777) ; `teamIndex` sur 0,2 % | croire qu'on peut connaître les équipes sans le moteur |
| `config.playerTeams` est une **union int\|str** : 13 463 int, 4 871 chaînes | un `i32` sur `"Humans Vs Nations"` |
| La config a **29 clés** dont des **éteintes** (`disableNPCs` en juin 2025) et des **apparues** (`startingGold`) | une migration de schéma à chaque version du moteur |
| 2 **types d'intents** de l'archive ne sont plus dans `Schemas.ts` : `troop_ratio` (1 392×), `allianceRequestReply` (486×) | un enum, et 1 878 intents perdus |
| **56 % des références joueur d'un intent** ne sont pas dans `players[]` (51 304 / 91 784) : bots et nations | une contrainte d'intégrité qui échoue sur un cas majoritaire |
| Les ids de bots sont tous en minuscules — **mais 12 humains sur 993 aussi** | router sur la casse et se tromper 12 fois |

### Critères d'acceptation : les 3 atteints

| Critère | Preuve |
| --- | --- |
| `docs/DATA.md` documente les schémas | 42 + 98 + 14 colonnes, avec unité, nullabilité, taux de présence **mesurés sur 2 070 777 lignes**, les 3 règles de remplissage, la correspondance champ d'intent → colonne, et la liste de ce que les tables ne disent pas |
| Tests de cohérence sur les fixtures | **24 tests**. `test_intents_par_type_identiques_au_brut` : les 23 types et les **91 784** intents recomptés sur le JSON brut, à l'identique. `test_integrite_referentielle` : `(game_id, client_id)` unique, `num_intents` égal au compte réel. `test_bigint_sans_perte` : 10 000+ valeurs comparées à `int(chaîne brute)`, aucune colonne flottante. `test_schema_stable_entre_lots` : avec `--chunk 1`, un seul schéma par table |
| Vérification dans le viewer | deux moments de `cR4EhwxE2N` sortis des tables (8:02 et 10:16, voir ci-dessous) : **vérifiés dans le viewer, les deux exacts** |

Deux contre-épreuves indépendantes du code :

- les chiffres de `duckdb` (55 952 parties, 2 070 777 participants, 1 915 887 avec stats, `player`
  37 182 / `team` 17 875 / `nation` 57 / null 838, `max(peak_troops)` 2 081 249 469,
  `max(gold_trade)` 22 202 217 393) sont **identiques** à ceux de l'inventaire, qui n'utilise pas
  une ligne de `normalize.py` ;
- `jq` sur le record brut décompressé contre `duckdb` sur le Parquet, sur la partie `c15Lo6e7na` :
  **863 intents, même répartition par type** (`attack` 632, `build_unit` 78, `boat` 51, `spawn` 36,
  `allianceRequest` 30). Deux outils, deux formats, le même compte.

Un troisième recoupement tombe tout seul : `num_turns / 10 = 642,2 s` contre `duration_s = 644`
sur `cR4EhwxE2N` — **1 tick = 100 ms**, confirmé par les données.

### Mesures

| Mesure | Valeur |
| --- | --- |
| `ofa normalize` sur 55 952 records | **3 min 27** (270 records/s/cœur) → ~2 h 45 projetées pour l'archive |
| `ofa fetch-full --sample 300` | 299 téléchargés, 47 écartés hors périmètre, **0 échec**, 16,7 Mo |
| Débit du `fetch-full` à `OFA_RATE=1` | avance max **+0,000** requête sur 301, intervalle minimum 0,9999 s |
| `intents` | **1 141 612 lignes** sur 300 parties — **3 805 par partie**, 12,1 Mo (11,2 o/ligne) |
| Lecture du brut seule | 885 records/s/cœur, 5 540 sur 16 cœurs |
| `games` | 55 952 lignes, **5,0 Mo** (94 o/ligne) |
| `participants` | 2 070 777 lignes, **208 Mo** (105 o/ligne pour 98 colonnes) |
| Projection archive | ~237 Mo de `games` + ~9,6 Go de `participants` + ~4,0 Go d'`intents` pour les 100 000 parties du J3 |
| Périmètre d'analyse | **48 100 / 55 952 = 86,0 %** des parties ingérées |

### Décisions

- **ADR-0007** : **colonnes chaudes + catch-all verbatim**. `games.config_json` garde la config
  entière, `intents.params` tous les champs non promus. Rien n'est perdu quand le moteur change, et
  aucune migration n'est nécessaire. Exception assumée : **les stats n'ont pas de catch-all** — une
  clé inconnue est comptée, affichée et fait **sortir en code 1**, parce qu'une nouvelle stat exige
  une colonne typée. Mesuré 0 clé inconnue sur 2 070 777 lignes, donc l'alarme ne peut vouloir dire
  qu'une chose. C'est volontairement le cas de `killedBy`, `deathPosition` et `spawnTile`, déclarés
  par le moteur mais jamais archivés.
- **Reconstruction totale, jamais d'incrémental.** 3 min 27 aujourd'hui. C'est aussi ce qui propage
  la purge jusqu'aux Parquet : `ofa purge-deleted` touche `data/raw/`, il faut enchaîner
  `ofa normalize` (à câbler dans les timers du J9).
- **pyarrow pour écrire, pas Polars.** Un `pa.schema` déclaré et un `ParquetWriter` par (table,
  mois) rendent impossible le piège qui aurait coûté le plus cher : avec une inférence par lot, une
  colonne entièrement nulle dans un lot prend le type `Null` et `int64` au lot suivant, et les deux
  fichiers deviennent illisibles ensemble par `read_parquet('**')`. Un test le vérifie avec
  `--chunk 1`. Polars viendra au J4, pour lire.
- **Noms de colonnes en `snake_case` anglais**, calqués sur les champs du record. La doc reste en
  français, et DATA.md n'est qu'une table de traduction.
- **L'équipe est reportée au J3.** Réimplémenter `humanTeams` + le nombre de nations par map **au
  commit** dupliquerait de la logique moteur pour un champ que `player.team()` donnera gratuitement
  quand le moteur tournera.
- **Colonne `seq` dans `intents`** : le moteur crée une Execution par intent dans l'ordre du tour.
  Sans cette colonne, cet ordre n'aurait été porté que par l'ordre des lignes, que SQL ne garantit
  pas — et il aurait été irrécupérable sans re-normaliser.
- **Le hors-périmètre est normalisé**, avec `in_scope`. Filtrer est une clause `where` ;
  re-télécharger ne l'est pas.

### Les deux moments à vérifier dans le viewer

`https://replay.openfront.io/cR4EhwxE2N` (ou `https://openfront.io/#replay-viewer=cR4EhwxE2N`).
Partie *Surrounded*, FFA 10 joueurs, 6 422 ticks (10 min 42), gagnée par **kdc17**.

1. **tick 4823 (8:02) — `kdc17` élimine `ASALVATI`.** Vient de `participants.kills`.
2. **tick 6168 (10:16) — `kdc17` construit un MIRV**, 26 s avant la fin. Vient de `intents`.

**Les deux ont été vérifiés dans le viewer le 2026-11-22 : exacts.** C'est la seule preuve que les
tables décrivent bien la partie qui a eu lieu, et non une partie cohérente avec elle-même.

```sql
-- moment 1
with x as (select game_id, client_id, unnest(kills) as k
           from 'data/parquet/participants/**/*.parquet' where game_id='cR4EhwxE2N')
select x.k.tick::int tick, p.username tueur, v.username victime
from x join 'data/parquet/participants/**/*.parquet' p
       on p.game_id=x.game_id and p.client_id=x.client_id
     left join 'data/parquet/participants/**/*.parquet' v
       on v.game_id=x.game_id and v.client_id=x.k.victim
order by tick;

-- moment 2
select i.tick, p.username, i.type, i.unit
from 'data/parquet/intents/**/*.parquet' i
join 'data/parquet/participants/**/*.parquet' p
  on p.game_id=i.game_id and p.client_id=i.client_id
where i.game_id='cR4EhwxE2N' and i.unit in ('Atom Bomb','Hydrogen Bomb','MIRV')
order by i.tick;
```

Rappel : `tick / 10` = secondes de jeu, donc `mm:ss = tick//600 : (tick%600)//10`.

### Limites connues, écrites dans DATA.md

- `intents` ne couvre que les parties dont on a le **record complet** : `data/raw/meta` est
  téléchargé sans `turns` (ADR-0004). **300 parties** le sont aujourd'hui (`ofa fetch-full`), soit
  0,5 % de ce qui est ingéré ; les 27 fixtures, elles aussi complètes (91 784 intents), portent les
  tests. Les 21 types d'intents vus sur ces 300 parties récentes **n'incluent pas** `troop_ratio`
  ni `allianceRequestReply` : ces deux-là n'existent que dans l'archive ancienne, et c'est bien
  pour eux que `type` est du texte libre.
- Pas d'**équipe** en mode Team, pas de `spawnTile`/`killedBy`/`deathPosition`, pas de coordonnées
  de `tile`, pas de classement de fin de partie : tout cela est du J3.
- **Un 0 peut vouloir dire « non suivi »** quand la stat a été ajoutée au moteur après la partie
  (`attacks_max_recv` sur juin 2025, `boats_*_lost`, `gold_donate_recv`, et `units_*_upgrade` qui
  vaut 0 sur toute l'archive actuelle). Pour comparer dans le temps, se fier à `git_commit`.
- `won = false` pour un équipier déconnecté absent du tuple `winner` ; le tuple brut reste dans
  `games.winner_members`.
- **`ofa rate-check` n'a pas de fenêtre** : le journal est partagé par toutes les commandes, donc
  vérifier le débit d'une exécution à 1 req/s dans un journal où le J1 a tourné à 8 req/s demande
  de filtrer à la main. Les requêtes de `fetch-full` sont les seules `/game/<id>` **sans**
  `?turns=false`, ce qui suffit pour les isoler. À reprendre au J9, avec les timers.

### Prochaine étape : J3, re-simulation à grande échelle (fait, voir plus bas)

### Note du J1 : ce qui avait été anticipé pour le J2

Tables Parquet `games`, `participants` (stats aplaties en colonnes nommées) et `intents`
(une ligne par action : tick, joueur, type, cible, paramètres décodés), plus `docs/DATA.md`.
Matière première déjà sur disque : **55 405 records** dans `data/raw/meta/`.

Points connus à traiter, tous mesurés au J0 et au J1 :

- les stats archivées sont **lacunaires** (`finalTiles` sur 17,7 % des lignes joueur seulement) ;
- `spawnTile`, `killedBy` et `deathPosition` n'y sont **jamais** ;
- `turns[]` est **creux** : reprendre la logique de `decompressGameRecord` ;
- `winner` est un **tuple**, et un membre d'équipe déconnecté peut en manquer ;
- unions à décoder au runtime : `config.nations`, `config.playerTeams`, `emoji.recipient` ;
- les tableaux de stats s'allongent avec les versions : toujours tester `len()` ;
- bigint en chaînes décimales → `int`, jamais `float` ;
- `"Free For All"` avec les espaces, pas `"FFA"`.

**Premier arbitrage du J2** : les records `meta` ne contiennent **pas** `turns`, donc la table
`intents` ne pourra être remplie que pour les parties dont on aura le record complet (étage `full`,
ADR-0004). Il faudra décider si le J2 livre `intents` sur un sous-ensemble, ou s'il attend le J3.

---

## J3 — Re-simulation à grande échelle (terminé)

### Code

| Module | Rôle |
| --- | --- |
| `resim/ofa_runner.ts` | le runner : copie de `tests/replay/ReplayGame.ts` (ADR-0002) + état par joueur toutes les 10 ticks + 16 types d'événements au tick exact + l'adaptateur de version d'interface |
| `ofa/engine.py` | un moteur par commit (ADR-0001) : worktree sparse, `node_modules` par hash de lockfile en liens durs, map par tree-sha ; `NeedsShell` quand le sandbox refuse |
| `ofa/resim.py` | sélection, pool de workers, critère de validité (ADR-0003), les 3 tables Parquet |
| `ofa/index.py` | table `resim` : le statut par partie, mutable, qui porte la reprise et le décompte par cause |
| `ofa/cli.py` | `ofa engines`, `ofa resim`, `ofa resim --report` |
| `docs/adr/0008-moteurs-par-commit.md` | l'arbitrage des commits, chiffré |

`uv run pytest -q -m "not slow and not network"` : **66 tests, 9,9 s** (52 des J1-J2 + 14 du J3).
2 tests `slow`. `ruff check` propre. Type-check du runner dans un worktree : **0 erreur sur
`ofa_runner.ts`** (les 17 que `tsc` rapporte sont préexistantes — le sparse-checkout exclut
`resources/lang/`, du code client que le runner ne touche pas). Aucune dépendance ajoutée.

### Critères d'acceptation : les 3 atteints

| Critère | Preuve |
| --- | --- |
| ≥ 99 % de 500 parties `statsAgreed` avec des stats finales identiques au record | **513 / 514 = 99,81 %**. Sélection : toutes les parties `has_turns and stats_agreed and in_scope` de l'archive. 5 089 773 ticks rejoués, **1 307 hashs sur 1 307** concordants sur la plus grosse partie, et zéro écart de stats sur les 513 |
| Les échecs sont listés par cause | `uv run ofa resim --report`. Un seul échec : `hash_absent`, **une partie solo** (1 joueur) dont le record ne porte **aucun** hash — aucun client indépendant n'a pu en valider un. Ses stats concordent pourtant **sans un seul écart** : c'est le point 1 de l'ADR-0003 qui est muet, pas la re-sim qui est fausse. Elle n'écrit donc rien, et c'est voulu |
| Débit et pic mémoire dans `docs/PERF.md` | **168 ticks/s/cœur, 60,8 parties/h/cœur, 365 parties/h à 6 workers, pic RSS 925 Mo** |

### Trois contre-épreuves indépendantes du code de `ofa`

Comme au J2, le seul moyen de savoir si les tables décrivent bien la partie qui a eu lieu est de
les recouper avec une source qui ne leur doit rien — ici, les stats que **le serveur de jeu** a
archivées pendant la partie :

1. **Les unités construites, recomptées depuis les événements** (une transition d'unité à la fois)
   contre `participants.units_*_built` : **18 083 joueurs × 6 types = 108 498 comparaisons,
   0 écart.**
2. **Le pic de territoire échantillonné** contre `participants.tiles_peak`, le pic exact :
   **19 038 comparaisons, 0 dépassement**, 9 179 égalités strictes, et 99,86 % du pic en moyenne —
   exactement ce que l'échantillonnage à 10 ticks doit donner.
3. **Zéro doublon** sur `(game_id, small_id, tick)` à travers 10,5 M de lignes.

### Les deux manques du J2, comblés

| | |
| --- | --- |
| `team` | rempli sur les **147 parties Team** (5 769 lignes humaines), nul sur les 366 FFA. `player.team()` le donne gratuitement, là où le J2 aurait dû réimplémenter `humanTeams` au commit |
| `spawn_tile`, `killed_by`, `death_position` | 186 704 / 13 487 / 14 240 lignes. `death_position` est **le classement de fin de partie**, que `killed_at` n'approchait qu'imparfaitement (Spearman −0,81 / −0,96). Il se compte parmi les humains **et les nations** : 12 humains dans `c15Lo6e7na`, et des positions jusqu'à 15 |

### Mesures

| Mesure | Valeur |
| --- | --- |
| Run principal, 6 workers | 462 parties en **78,3 min** |
| Relance après préparation des 3 moteurs manquants | 52 parties en **11,1 min** |
| Pire cas : 170 joueurs, 13 071 ticks | 134,6 s, 713 Mo, 97 ticks/s |
| Coût du relevé d'état (deux mesures sur ce pire cas) | 133,1 → 141,2 s, **+6,1 %** |
| Sortie | **28,2 M de lignes, 161 Mo** pour 513 parties — **322 Ko par partie** |
| `player_ticks` / `events` / `players` | 10 518 824 / 17 452 122 / 188 166 lignes |
| Coût d'une ligne, avec l'encodage retenu | **8,24 o** / **4,46 o** / 18,0 o |
| Coût d'un commit provisionné | **37 Mo** (6 worktrees sur 7 à 35-37 Mo) |

### Décisions

- **ADR-0008 : grouper par commit est la préparation, pas un arbitrage.** 6 worktrees au lieu de
  514 préparations. Les 514 parties se répartissent 199 / 98 / 83 / 82 / 51 / **1**, et le commit à
  une partie est le seul dont le `package-lock.json` diffère : 399 Mo au lieu de 48. Il a été
  provisionné quand même — 0,04 % du disque libre, et le chemin « nouveau lockfile » de l'ADR-0001
  s'en trouve exercé pour de vrai. La règle pour les 100 Go du serveur est écrite dans l'ADR
  (~370 commits sur 435 jours, 17,8 Go : réviser le budget ou évincer), pas implémentée.
- **Racine `data/resim/`, séparée de `data/parquet/`.** `normalize.rebuild()` échange le
  répertoire entier ; tout ce qui vivrait sous `data/parquet/` disparaîtrait à la normalisation
  suivante. La re-sim, elle, est **incrémentale** : des heures de calcul ne se jettent pas.
- **Le statut par partie va en SQLite, pas en Parquet**, parce qu'il est **mutable** : c'est lui
  qui porte la reprise (`--limit`, `--commit`, relance à blanc) et le décompte par cause. Une
  partie n'écrit ses lignes **que** si elle est valide.
- **`DELTA_BINARY_PACKED` sur les entiers, dictionnaire sur les chaînes, Parquet v2.6, zstd 19.**
  Mesuré : 18,47 → 8,00 o par ligne de `player_ticks` (−57 %). Le J0 projetait 5,00 o en comptant
  sur le tri seul ; le tri est nécessaire (il rend les séries monotones) mais ne suffit pas, parce
  qu'un dictionnaire ne sert à rien sur une série croissante. Sans cette mesure, la sortie des
  100 000 parties du J3 aurait pesé **68 Go au lieu de 32**.
- **Les trajectoires ne portent que les humains.** Un bot ou une nation n'est pas un sujet
  d'analyse, et les inclure multipliait le volume par 9 (188 166 lignes `players` pour 20 465
  humains). Ils restent dans `events` et dans `players`.
- **Trains et obus exclus des événements** : 2 464 sur 5 090 dans la première partie mesurée,
  aucune stat en face, aucun usage d'analyse. Bombes et missiles d'interception conservés.
- **Pas d'`--force`.** Rejouer une partie déjà valide dupliquerait ses lignes ; pour tout refaire,
  on vide `data/resim/` et la table `resim`. C'est une limite assumée, pas un oubli : un
  dédoublonnage à l'écriture serait du code mort tant que la re-sim est monotone.

### Quatre pièges trouvés en route, tous par la mesure

1. **6 villes construites au lieu de 3.** Une même unité peut apparaître **deux fois dans un seul
   tick**, inactive puis active. Un état qui supprimait l'id à l'inactivité comptait la seconde
   apparition comme une construction. Un id d'unité n'étant jamais réutilisé, la règle juste est :
   une première apparition est une construction, un id déjà connu n'en est jamais une. Trouvé par
   la contre-épreuve n° 1, pas par un test — et c'est précisément pour ça qu'elle existe.
2. **`PackedUnitUpdates.ts` n'existe pas dans les commits anciens.** Un `import` statique faisait
   planter le runner au chargement (`ERR_MODULE_NOT_FOUND`, commit `79aed26b`). D'où un
   **adaptateur par version d'interface, pas par commit** (règle du projet) : import dynamique, et
   tout accès à `gu.updates[...]` passe par un `each` qui tolère un `GameUpdateType` absent, parce
   que l'enum est numérique implicite et que son indice dépend de l'ordre de déclaration **au
   commit**.
3. **Les ids des updates ne sont pas tous de la même nature.** Alliance, cible et embargo portent
   des `smallID` (`requestorID: number`) ; conquête, don et bonus portent des `PlayerID` (des
   chaînes). Tout convertir ou ne rien convertir donnait des événements silencieusement faux.
4. **Un chemin relatif passé au worker** se résolvait dans le worktree (`cwd=<worktree>`), avec un
   `ERR_MODULE_NOT_FOUND` au chemin doublé. `run_one` fait maintenant un `.resolve()`. Et
   `install_runner` écrit par fichier temporaire + `os.replace` : six workers copient le même
   runner au même endroit, et une lecture d'un fichier à moitié écrit était possible.

### Ce qui reste ouvert pour la suite

- Le worktree `dd85bcfb` fait **393 Mo au lieu de 37** : son sparse-checkout n'a pas été appliqué
  au J0. Sans conséquence (il a rejoué ses 83 parties), 356 Mo à récupérer hors sandbox si besoin
  (commande dans l'ADR-0008).
- **`intents` et les trajectoires ne couvrent que 520 parties** sur 55 952 : c'est
  `ofa fetch-full` qui décide du volume, pas la re-sim. Le J4 et le J5 travailleront donc sur deux
  populations de tailles très différentes — les stats finales de 48 100 parties du périmètre, et
  les trajectoires de 513.
- `players.death_position` compte les nations : à retraiter si le J5 veut un classement
  « entre humains ».
- Projection de stockage à corriger dans `RECON.md` § 10.3 : **32 Go** pour 100 000 parties
  re-simulées, et non 17,1 Go (total projeté ~86 Go sur 100).

---

## J4 — Niveau (terminé)

Le niveau n'est pas une fin : c'est le contrôle que la SPEC impose pour qu'une fiche tactique du
J5 ne confonde pas « ce comportement fait gagner » avec « les bons joueurs ont ce comportement ».
Le J4 livre donc **une colonne et rien de plus** : `rating_before`, le niveau d'avant-partie de
chaque (partie, joueur).

### Code

| Module | Rôle |
| --- | --- |
| `ofa/rating.py` | le classement d'une partie, le gradient de Plackett-Luce, la passe chronologique, les métriques |
| `ofa/config.py` | `RATING_LR0`, `RATING_HALFLIFE`, l'échelle Elo, et `CONFIRMATION_START` — la coupure du protocole |
| `ofa/cli.py` | `ofa rating`, `ofa rating --report` |
| `docs/adr/0010-rating-chronologique.md` | les trois arbitrages (résultat, modèle, rangement), chiffrés |

`uv run pytest -q -m "not slow and not network"` : **88 tests, 11,0 s** (66 des J1-J3 + 22 du J4).
`ruff check` propre. **Aucune dépendance ajoutée** — ni numpy, ni polars.

### Critères d'acceptation : les 2 atteints

`uv run ofa rating --report`, sur l'échantillon de confirmation (depuis le 2026-11-20, 11 703
parties, **lu une seule fois**) :

| mode | ≥ n parties | parties | joueurs | paires | log-loss | naïf | Spearman | expérience |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FFA | 0 | 8 245 | 70 805 | 8 118 522 | **0,6316** | 0,6931 | **0,373** | 0,122 |
| FFA | 10 | 8 048 | 24 535 | 3 075 856 | **0,6200** | 0,6931 | **0,402** | 0,015 |
| Team | 0 | 3 224 | 37 893 | 1 122 403 | 0,6739 | 0,6931 | 0,153 | 0,076 |
| Team | 10 | 3 122 | 13 503 | 377 675 | 0,6666 | 0,6931 | 0,165 | 0,036 |
| **tous** | **0** | **11 469** | **94 055** | **9 240 925** | **0,6367** | 0,6931 | **0,311** | 0,109 |
| tous | 10 | 11 170 | 30 521 | 3 453 531 | 0,6251 | 0,6931 | 0,336 | 0,021 |

**Deux références naïves, pas une.** « Naïf » = aucune information (tous égaux, p = 0,5,
log-loss ln 2). « Expérience » = le nombre de parties déjà jouées, en corrélation de rang : c'est
elle qui compte, parce que sans elle un rating qui ne mesurerait que l'ancienneté passerait pour un
rating. Le rating la bat d'un facteur 3 (0,311 contre 0,109) et l'écart **grandit** quand on exige
plus de parties antérieures, alors que celui de l'expérience **s'effondre** (0,015 à ≥ 10) —
exactement ce qu'on attend si le rating mesure le niveau et non l'assiduité.

| Critère | Preuve |
| --- | --- |
| Le rating d'avant-partie prédit le classement mieux qu'une référence naïve | log-loss **0,6367 contre 0,6931** (−8,1 %) et Spearman **0,311 contre 0,109**, sur 9,24 M de paires et 11 469 parties de confirmation |
| Les joueurs sans publicID sont traités explicitement | ils **jouent sans capitaliser** : comptés dans la normalisation (sinon la taille du terrain est fausse et tous les autres sont mal notés), restés à l'a priori, sans ligne ni mémoire. 244 participations anonymes, 0 ligne. Trois tests sur de vraies fixtures : la partie entièrement anonyme n'écrit rien, `REDACTED` n'a pas de ligne mais compte dans `field_size`, et retirer un anonyme **change** le gradient des autres |

### Deux contre-épreuves indépendantes du code de `ofa`

1. **Le premier du classement est-il le vainqueur archivé ?** `rank = 1` sur les FFA du périmètre à
   vainqueur joueur : **34 359 lignes, 34 359 fois le joueur `won`**. Le classement est calculé sans
   lire `won` (ADR-0010) : c'est donc une vraie vérification, pas une tautologie.
2. **Le chaînage temporel** : `rating_after` d'une partie = `rating_before` de la suivante pour le
   même joueur, et `games_before` s'incrémente de 1. **1 426 259 transitions, 0 rupture** des deux.
   C'est la preuve que le rating écrit est bien celui d'**avant** la partie.

Et, en SQL pur, le niveau d'avant-partie décroît **strictement** avec le classement final en FFA,
par décile : **1 577 → 1 478** points d'Elo. En Team ce test est muet par construction (deux rangs
seulement) : le signal Team se lit dans le tableau ci-dessus, et il est trois fois plus faible.

### Mesures

| Mesure | Valeur |
| --- | --- |
| Passe complète | **14,3 s** pour 48 100 parties — 3 360 parties/s sur un cœur |
| Pic RSS | **1,33 Go** (un mois de partition en mémoire) |
| Sortie | **1 684 613 lignes, 20,7 Mo** (12,3 o/ligne), **258 354 joueurs** |
| Parties sans classement exploitable | **728 / 48 100** (1,5 %) : un seul groupe, ou Team sans vainqueur archivé |
| Étendue du rating | 1 194,9 à 2 138,2, moyenne **1 511,15** (un rating à somme nulle reste près de son a priori) |
| Rapport d'acceptation | 27,9 s, 9,24 M de paires |

### Décisions (ADR-0010)

- **Le classement sort des tables du J2, pas de la re-sim.** Mesuré : un participant FFA est dans
  exactement un de trois états (vivant à la fin / mort / sans stats), et le vainqueur est l'argmax
  de `final_tiles` **34 380 fois sur 34 380**. `death_position` du J3 serait plus précis, mais sur
  513 parties au lieu de 48 100. Les 7,2 % **sans stats sont hors classement** : les placer derniers
  inventerait une défaite à quelqu'un qui a surtout perdu sa connexion.
- **Plackett-Luce en ligne, pas d'Elo par paires.** Le gradient est O(n) par partie au lieu de
  O(n²) : **113 M de mises à jour au lieu de 1,4 milliard** sur l'archive projetée du serveur. Et
  sur deux joueurs il redonne exactement l'Elo par paire (testé), donc on ne perd rien à l'échanger.
- **Aucune dépendance ajoutée.** Un rating chronologique ne se vectorise pas entre parties ; une
  fois le O(n²) écarté, Python pur suffit (14 s). Polars et DuckDB attendent un vrai usage de
  lecture (J5/J6).
- **Pas d'apprentissage réglé sur l'exploration seule**, grille dans l'ADR : le plateau est large
  (0,636 de log-loss de lr0 = 0,2 à 0,4), mais la **décroissance** du pas compte (0,688 sans elle).
  D'où deux constantes figées et **aucune commande de calibration** à maintenir.
- **`data/rating/`, sa propre racine, reconstruction totale.** Jamais sous `data/parquet/`, que
  `normalize` échange en bloc ; et contrairement à `data/resim/`, pas d'incrémental : 14 s de calcul
  se jettent, et une purge de compte doit pouvoir réécrire toute l'histoire.
- **Un seul rating, tous modes confondus**, échelle Elo (1500 + 173,7 · θ) pour que le J6 affiche
  un nombre lisible. Si Team décroche trop, c'est la clé du dictionnaire qui devient
  `(public_id, mode)` — une ligne. Mesuré : Team est plus faible mais bat les deux références.
- **Coupure du protocole : `CONFIRMATION_START = 2026-11-20T00:00:00Z`** (36 397 parties avant,
  11 703 après). Le pas a été réglé en deçà, la confirmation n'a été lue qu'une fois. **Déplacer
  cette date invalide toute confirmation passée** : c'est écrit dans l'ADR et dans `config.py`.

### Un piège trouvé en route

`month` était à la fois une colonne du fichier et la clé de partition Hive : pyarrow refuse de
relire la table (`string vs dictionary`). Les tables du J2 et du J3 ne stockent `month` que dans le
chemin — la règle valait aussi ici.

### Ce qui reste ouvert pour la suite

- **7 jours d'archive, donc des ratings à peine chauds** : médiane de **1 partie** par joueur
  (258 354 joueurs, 6,5 parties en moyenne, 45 494 joueurs à ≥ 10 parties, 420 au maximum).
  C'est pour ça que le rapport est ventilé par `games_before`, et aussi pourquoi il **s'améliore**
  quand on monte le seuil.
  Le serveur (ADR-0009) porte l'archive longue : le J4 local est la répétition en costume.
- **En Team, le rating est celui de l'équipe** : un passager est surévalué (Spearman 0,16 contre
  0,37 en FFA). Les équipes des perdants sont inconnues sans re-sim des 12 965 parties Team.
- **Le pic mémoire est la seule limite d'échelle** : ~5 Go projetés pour un mois complet du serveur.
  Le découpage par jour est décrit en `shortcut:` dans `ofa/rating.py` ; le modèle n'a pas à changer.
- **Pas d'incertitude par joueur** (σ à la TrueSkill) : `games_before` est le seul indicateur de
  fiabilité. Le J5 s'en servira pour écarter les débutants plutôt que pour pondérer.
- Ce que le J5 a maintenant en main : `rating_before` pour « à niveau égal », les trajectoires du
  J3 pour « à état égal à t1 », `games` pour le contexte, et `public_id` pour comparer un joueur
  avec lui-même. Les quatre contrôles de la SPEC sont couverts.
