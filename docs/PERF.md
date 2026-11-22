# PERF — mesures de performance

Règle : mesurer avant d'optimiser, et consigner ici avec la commande.
Machines : **local** WSL2, 20 cœurs / **7 Go RAM** / 927 Go ; **serveur** Debian 12, 8 cœurs /
**16 Go** / **100 Go SSD SATA**. Sur le local, la RAM plafonne avant les cœurs.

## Préparation du moteur

| Mesure | Valeur | Commande |
|---|---|---|
| Clone complet d'OpenFront | 2,1 Go (`.git` 951 Mo, `resources/maps` 764 Mo) | `du -sh data/openfront` |
| Worktree brut | 390 Mo | |
| Worktree **resserré** (sans `map-generator` 286 Mo, `proprietary`, assets de `resources/`) | **37 Mo** | `git sparse-checkout set '/*' '!/map-generator/' '!/proprietary/' '!/resources/*/' '/resources/*.json'` |
| `npm ci --ignore-scripts` (1ʳᵉ fois par lockfile) | **15,4 s**, pic RSS 704 Mo, 362 Mo | `/usr/bin/time -f '%es %MkB' npm ci --ignore-scripts` |
| `cp -al` d'un `node_modules` déjà installé | **1,46 s**, **~11 Mo** de delta disque | `cp -al <cache>/node_modules <worktree>/node_modules` |
| Matérialisation d'une map (pangaea) | 1,3 Mo | `git archive <tree> \| tar -x` |

**Coût par commit distinct ≈ 48 Mo** (37 Mo de code + ~11 Mo de delta `node_modules`).

**Corrigé après le J3 : ce chiffre cache le plus gros poste.** Les 11 Mo sont le *delta* d'un
`cp -al` ; la **première** installation d'une version de lockfile coûte **362 Mo**, partagés
ensuite en liens durs. Sur 435 jours (~98 versions de lockfile), c'est **35 Go** — invisible à
`du -sh data/node_modules`, qui affiche 22 Mo parce qu'il ne compte un lien dur qu'une fois.
Le cache moteur complet de l'archive vaut donc **2,1 + 17,8 + 35 + 1,5 = 56,4 Go**, d'où
l'éviction (ADR-0008, `ofa resim --evict`).

Et un worktree n'est pas un dépôt : il ne contient **aucun objet git** (son `.git` est un fichier
qui pointe vers celui du clone), donc sa taille est celle des fichiers en clair. Mesuré sur un
worktree resté non resserré : 393 Mo, dont `map-generator` **283 Mo**, assets de `resources/`
59 Mo, `proprietary/` 16 Mo, et **37 Mo de code utile**.
Mesuré : 2 611 commits/an mais seulement **82 versions de `package-lock.json`** et **223 versions de
maps** ; et surtout **3 commits distincts seulement sur 300 parties réelles de 7 jours**, soit de
l'ordre de 150 commits/an à provisionner → ~7 Go.

Vérification que le partage ne corrompt rien : les liens `node_modules/@openfront/*` sont **relatifs**
(`../../packages/engine`), donc `realpathSync` doit pointer dans le worktree courant. Vérifié.
Un lien symbolique sur le dossier entier résoudrait vers le **mauvais commit**, en silence.

## Re-simulation

Trois vraies parties, rejouées au commit exact du record (détail dans `RECON.md` § 8) :

| partie | joueurs | ticks | simulation | mur | débit | pic RSS |
|---|---|---|---|---|---|---|
| `dtQhXS8owK` (Team) | 24 | 5 931 | 23,4 s | — | **253 t/s** | — |
| `cPaLHdRvC2` (FFA) | 25 | 9 701 | 60,9 s | — | **159 t/s** | — |
| `cDkWi2N1AT` (FFA) | 95 | 29 342 | 170,8 s | 189,3 s | **172 t/s** | **782 Mo** |
| `demoGame1` (démo) | 1 | 1 500 | 2,5 s | 3,7 s | 600 t/s | 243 Mo |

- Surcoût de démarrage `node --import tsx` : **≈ 1,4 s** par processus, soit < 3 % d'une partie
  médiane → **pas de bundle** (ADR-0001). À revoir si ce rapport dépasse 10 %.
- Compter **0,8 Go par worker** : 8 workers sur le serveur (16 Go), **7 sur le local** (7 Go).
- Partie médiane (8 066 ticks) ≈ **49 s par cœur**.

| machine | workers | parties/jour | 1 mois d'archive (251 628 parties) |
|---|---|---|---|
| serveur 8 c / 16 Go | 8 | **14 150** | **17,8 jours** |
| local 20 c / 7 Go | 7 | ~12 400 | ~20 jours |

**La re-simulation est le facteur limitant du projet**, pas le téléchargement.

## Stockage

### Compression du brut : pistes explorées, rien de matériel à gagner

Sur les 27 records réels du J0 (1 507 536 o pour le plus gros) :

| Option | Résultat |
|---|---|
| `zstd -9` | 198 522 o en **0,03 s** |
| **`zstd -19`** (retenu) | **169 343 o** en 0,54 s |
| `xz -9e` | 163 116 o en 1,35 s → **+3,7 % de gain pour 2,5× le temps** |
| **Dictionnaire zstd** entraîné sur 13 records, appliqué aux 14 autres | **+4,0 % seulement** |
| **Archive solide** (27 records en un seul flux zstd) | **+5,1 %**, au prix de l'adressage par partie |

Conclusion : `zstd -19` par fichier est à ~5 % de la meilleure option réaliste, tout en gardant
un fichier par partie. Les dictionnaires n'aident pas ici parce que les records sont assez gros
pour que zstd trouve seul leur redondance.

### Tailles

| Mesure | Valeur |
|---|---|
| Record complet, zstd -19 | **médiane 39,2 Ko**, moyenne ~45 Ko (×2,3 à ×9,1) |
| Record sans `turns`, zstd -19 | **médiane 5,3 Ko** (×6 plus petit, **même nombre de requêtes**) |
| Sortie de re-sim, 1 point/joueur/10 ticks | **36 641 lignes/partie** en moyenne (médiane 21 554, max 357 175) |
| Coût d'une ligne, Parquet zstd, **données aléatoires entrelacées** | 13,6 o |
| Coût d'une ligne, Parquet zstd, **séries réalistes triées par (joueur, tick)** | **5,00 o** |
| → sortie de re-sim par partie | **179 Ko** en moyenne |

Le tri par `(small_id, tick)` et des séries monotones suffisent à diviser la taille par **2,7**.
C'est la seule optimisation de stockage qui change les ordres de grandeur : à retenir au J3,
avec un `ROW_GROUP_SIZE` de l'ordre de 100 000.

| parties re-simulées | sortie Parquet |
|---|---|
| 100 000 | **17,1 Go** |
| 200 000 | 34,1 Go |
| 1 mois d'archive (251 628) | 42,9 Go |

Projections complètes et volume cible : `RECON.md` § 10.

## Ingestion (J1, mesures sur la vraie API)

| Configuration | Débit effectif | Avance sur l'échéancier | Codes HTTP |
|---|---|---|---|
| `OFA_RATE=4`, 1 requête en vol (séquentiel) | **3,56 req/s** | +0,000 | 42 034 × 200 |
| `OFA_RATE=8`, `OFA_CONCURRENCY=4` | **7,01 req/s** | +0,000 | 601 × 200 |

Aucun 429, aucun 403 sur l'ensemble. Taille moyenne constatée : **5,1 Ko par partie** compressée
(42 426 parties pour 217 Mo), cohérent avec les 5,3 Ko médians mesurés au J0.

**Règle de dimensionnement de la concurrence.** Le débit atteignable est
`concurrency / latence`, et non le débit configuré :

- séquentiel : 1 / (0,25 + 0,03) = 3,6 req/s — c'est ce qui était mesuré ;
- 4 workers, latence 0,57 s par record sous charge parallèle : 4 / 0,57 = **7,0 req/s**.

Pour viser réellement R req/s, il faut donc `concurrency >= R x latence`, plus une marge :
à 8 req/s et 0,57 s de latence, **6 workers** au lieu de 4. À appliquer au rattrapage historique,
où les 12 % manquants représentent une demi-journée.

Le limiteur, lui, n'est jamais en cause : l'avance sur l'échéancier reste à +0,000 dans les deux
configurations, donc aucune rafale n'a eu lieu.

## Normalisation (J2, mesures sur les 55 952 records du J1)

| Étape | Débit | Total |
|---|---|---|
| Lecture zstd + `json.loads` seuls | **885 records/s/cœur** | 63 s pour 55 952 |
| Idem sur 16 cœurs (`ProcessPoolExecutor`) | **5 540 records/s** | **10,1 s** (134 s CPU) |
| `ofa normalize` complet (parse + aplatissement + Parquet) | **270 records/s/cœur** | **3 min 27** |

Soit **~2 h 45 projetées** pour les 2 654 420 parties de l'archive, en un seul cœur. C'est
acceptable pour une reconstruction quotidienne sur une machine allumée en permanence, et c'est la
raison pour laquelle il n'y a pas de normalisation incrémentale : paralléliser par préfixe de 2
caractères (comme l'inventaire ci-dessus, 16× mesuré) reste un changement de 10 lignes si le besoin
apparaît.

L'aplatissement coûte **3,3× la lecture** (270 contre 885 records/s) : c'est la conversion des
2 070 777 × 86 valeurs de stats et l'encodage Arrow, pas le JSON.

### Tailles Parquet (zstd, 1 fichier par mois et par table)

| Table | Lignes | Taille | Par ligne |
|---|---|---|---|
| `games` | 55 952 | **5,0 Mo** | 94 o |
| `participants` | 2 070 777 | **208 Mo** | **105 o** |
| `intents` | 1 141 612 (300 records complets) | **12,1 Mo** | **11,2 o** |

Les **11,2 o par ligne d'`intents`** encadrent la mesure du J0 (5,00 o/ligne pour une sortie de
re-sim triee, 13,6 o non triee). A **3 805 intents par partie** (mesure sur 300 parties, contre
3 400 estimes), une partie coute **41 Ko** d'intents : **4,0 Go** pour les 100 000 parties
re-simulees du J3.

Les 105 o par ligne de `participants` portent **98 colonnes**, dont 86 compteurs de stats souvent
nuls : le zstd colonne par colonne les ramène à presque rien. Projection pour l'archive :
2 654 420 parties → **~237 Mo de `games` et ~9,6 Go de `participants`**, plus **~4,0 Go d'`intents`**
pour les 100 000 parties du J3, à ajouter aux 26 Go de `data/raw/meta` et aux 4,3 Go de
`data/raw/full` (budget : 100 Go).

## Re-simulation à grande échelle (J3, mesures sur 514 vraies parties)

Toutes les parties `has_turns and stats_agreed and in_scope` de l'archive courante, rejouées au
commit exact, sur le portable (20 cœurs / 8 Go, **6 workers** : `min(cœurs, Go disponibles)`).

| Mesure | Valeur |
|---|---|
| Parties valides (ADR-0003) | **513 / 514 = 99,81 %** |
| Run principal | 462 parties en **78,3 min** |
| Relance après préparation des 3 moteurs manquants | 52 parties en **11,1 min** |
| Ticks simulés | **5 089 773** en **8,44 h** de CPU |
| Débit par cœur | **168 ticks/s**, soit **60,8 parties/h** |
| Débit à 6 workers | **365 parties/h** |
| Pic RSS observé sur l'ensemble du run | **925 Mo** |
| Pire cas isolé : 170 joueurs, 13 071 ticks | **134,6 s**, 713 Mo, 97 ticks/s |
| Coût du relevé d'état, mesuré deux fois sur ce pire cas | 133,1 s → **141,2 s : +6,1 %** |
| Surcoût de démarrage `node --import tsx` | ~1,4 s, inchangé → **toujours pas de bundle** (ADR-0001) |

Les **168 ticks/s** confirment les 172 projetés au J0 sur 3 parties, relevé d'état inclus. La
mémoire, elle, était sous-estimée : **compter 1 Go par worker, pas 0,8 Go** — 925 Mo observés sur
une population dont la plus grosse partie compte 170 joueurs (le J0 n'était monté qu'à 95).

| machine | workers | parties/h | 100 000 parties |
|---|---|---|---|
| local 20 c / 8 Go | **6** (limite mémoire) | 365 | **11,4 jours** |
| serveur 8 c / 16 Go | **8** | 486 | **8,6 jours** |

### Coût d'une ligne : l'encodage compte plus que le tri

Le J0 projetait 5,00 o/ligne sur des séries synthétiques triées. Sur la vraie sortie, avec
l'encodage Parquet par défaut (dictionnaire, v1), une ligne de `player_ticks` coûte **18,47 o** :
ses colonnes sont des séries croissantes par joueur (tick, or cumulé, troupes), que le
dictionnaire n'aide pas. Mesuré sur 72 838 vraies lignes :

| Encodage | `player_ticks` | `events` |
|---|---|---|
| dictionnaire, Parquet v1, zstd | 18,47 o/ligne | 5,75 o/ligne |
| sans dictionnaire, encodage par défaut v2.6 | 12,75 o/ligne | — |
| **`DELTA_BINARY_PACKED` sur les entiers + dictionnaire sur les chaînes, v2.6** | **8,61 o/ligne** | **4,45 o/ligne** |
| idem, zstd niveau 19 (retenu) | **8,00 o/ligne** | **4,45 o/ligne** |

Le niveau 19 ne coûte rien ici (l'écriture est de l'ordre d'une seconde contre 134 s de
simulation) et la décompression ne dépend pas du niveau. Le tri par `(small_id, tick)` est
conservé : c'est lui qui rend les séries monotones, donc le delta petit.

### Tailles réelles, sur les 513 parties valides

| Table | Lignes | Taille | Par ligne | Par partie |
|---|---|---|---|---|
| `player_ticks` | **10 518 824** | **86,7 Mo** | **8,24 o** | 20 500 lignes |
| `events` | **17 452 122** | **77,9 Mo** | **4,46 o** | 34 000 lignes |
| `players` | 188 166 | 3,4 Mo | 18,0 o | 367 lignes (nations et bots compris) |
| **Total** | 28,2 M | **161 Mo** | | **322 Ko** |

**322 Ko par partie contre 179 Ko projetés au J0** : les trajectoires sont deux fois plus légères
que prévu (20 500 lignes au lieu de 36 641, parce qu'on n'échantillonne que les humains), mais le
J0 n'avait pas chiffré les événements, qui coûtent presque autant. Projection à revoir dans
RECON § 10.3 : **32 Go pour 100 000 parties**, et non 17,1 Go. Sur le budget de 100 Go du serveur,
cela reste tenable (total projeté ~86 Go), mais la marge passe de 29 à 14 Go.

## À remesurer plus tard

- [x] ~~Les 5,00 o/ligne viennent de séries synthétiques réalistes~~ → **8,24 o** pour
  `player_ticks`, **4,46 o** pour `events`, mesurés sur 28,2 M de vraies lignes.
- [x] ~~Débit de re-sim avec N workers en parallèle~~ → **365 parties/h à 6 workers**, pic 925 Mo.
- [ ] Temps de lecture des Parquet par le site (cible : 95 % < 300 ms, J6).
- [ ] Le même run sur le serveur (8 workers, 16 Go) : vérifier les 486 parties/h projetées.
