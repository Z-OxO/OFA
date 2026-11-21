# Avancement

Spec : [docs/SPEC.md](SPEC.md). Décisions : [docs/adr/](adr/). Perf : [docs/PERF.md](PERF.md).

| Jalon | État | Date |
| --- | --- | --- |
| J0 Reconnaissance et mesures | **terminé** | 2026-11-21 |
| J1 Ingestion | **terminé** — les 5 critères atteints | 2026-11-21 |
| J2 Normalisation | à faire | |
| J3 Re-simulation | à faire | |
| J4 Niveau | à faire | |
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

### Prochaine étape : J2, normalisation sans re-simulation

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
