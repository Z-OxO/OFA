# PERF — mesures de performance

Règle : mesurer avant d'optimiser, et consigner ici avec la commande.
Machines : **local** WSL2, 20 cœurs / **15,7 Go RAM** / 927 Go ; **serveur** Debian 12, 8 cœurs /
**16 Go** / **100 Go SSD SATA**. Le local annonçait 7 Go jusqu'au J4b : c'était faux (`free -m`),
et sur les deux machines ce sont **les cœurs** qui plafonnent avant la RAM.

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

## Géographie (ADR-0011, mesures sur les 513 parties)

| mesure | J3 | avec la géographie |
| --- | --- | --- |
| parties valides | 513/514 (99,81 %) | **513/514 (99,81 %)** |
| ticks rejoués | 5 089 773 | **5 089 773** (à l'identique) |
| lignes | 28,2 M | **74,3 M** (×2,63) |
| taille | 161 Mo | **278,4 Mo** (×1,73), 543 Ko/partie |
| CPU | 8,44 h | **21,76 h** (×2,58) |
| débit | 168 ticks/s/cœur **à 6 workers** | **65 ticks/s/cœur à 12 workers** |
| pic RSS | 925 Mo | 906 Mo |

**Attention à la lecture de la dernière ligne : deux variables ont changé en même temps**, la
géographie et le nombre de workers. Le débit de la machine entière est passé de 1 008 ticks/s
(6 × 168) à 615 ticks/s réellement observés sur 130,6 min — **deux fois plus de processus pour
39 % de débit en moins**, ce qui est la signature d'une saturation de bande passante mémoire. La
part imputable à la géographie et celle imputable à la concurrence ne sont donc **pas** démêlées :
c'est la première expérience du jalon J4b, le même échantillon à 4, 6, 8, 12 et 16 workers.

Pour mémoire, la seule mesure isolée dont on disposait avant le run : sur la pire partie
(`cGYPygKBu8`, 28 601 ticks) rejouée **seule**, 374,3 → 401,8 s, soit **+7,4 %**. Extrapoler ce
chiffre à toute la chaîne était faux d'un facteur 33 — il ne couvrait que le processus Node, ni le
reparsing Python, ni la compression, ni la contention.

**Conséquence pratique, et piège à retenir** : `TICKS_PER_SECOND` valait 172 (mesure J0, une partie
seule, sans géographie), et le timeout d'une partie vaut `6 × ticks / TICKS_PER_SECOND`. Avec le
vrai débit de 65, **24 parties sur 514 ont timeouté** — les plus lourdes — et l'acceptation est
tombée à 95,1 %. La constante corrigée, les 24 sont revenues valides en 27,8 min.

## Rating chronologique (J4, mesures sur les 48 100 parties du périmètre)

| mesure | valeur |
| --- | --- |
| `ofa rating` : passe complète | **14,3 s** pour 48 100 parties, 1 684 613 lignes écrites |
| débit | **3 360 parties/s**, soit 118 000 participations/s, sur **un** cœur |
| pic RSS | **1,33 Go** (un mois de partition en mémoire : 2,07 M de participations lues) |
| chargement des Parquet | 2,1 s pour les 2,07 M de participations (pyarrow, 5 colonnes) |
| sortie | **20,7 Mo** pour 1 684 613 lignes = **12,3 o par ligne** |
| `ofa rating --report` | **27,9 s** : 9,24 M de paires d'évaluation sur l'échantillon de confirmation |

Le modèle est en Python pur, sans numpy : il n'y a rien à vectoriser entre parties (le rating est
chronologique par définition), et **à l'intérieur** d'une partie le gradient de Plackett-Luce est
O(n) au lieu du O(n²) d'un Elo par paires — 113 M de mises à jour au lieu de 1,4 milliard sur
l'archive projetée du serveur (ADR-0010). Les 14 s se répartissent à peu près moitié lecture,
moitié modèle.

**Le pic mémoire est la seule limite d'échelle** : un mois complet du serveur (~206 000 parties,
7,8 M de participations) projette **~5 Go**. Au-delà, découper par jour à l'intérieur de la
partition mensuelle (`shortcut:` dans `ofa/rating.py`) ; le modèle n'a pas à changer.

## J4b — Le nombre de workers : la saturation annoncée n'existe pas

Protocole : 16 parties réelles (`docs/perf-sample.txt`), la même liste à chaque passe, rejouée
entière par `scratch/bench_workers.py` dans un `OFA_DATA_DIR` jetable (liens vers les vraies
données, journal remis à zéro avant chaque passe, `--batch 1`).

**L'échantillon est choisi à coût MESURÉ voisin, pas à ticks égaux** : à 8 500 ticks, le coût réel
du J3 va de **16 s à 241 s** selon la map et le nombre de joueurs. Un échantillon « stratifié sur
les ticks » mesure surtout sa propre queue de fin de passe. Les 16 parties coûtaient 123 s chacune
au J3, d'où des vagues pleines et 33 min de CPU par passe.

| workers | mur | débit machine | débit par partie | occupation | RSS max |
|---|---|---|---|---|---|
| 1 | 46 s | 162 t/s | 171 t/s | 0,94 | 472 Mo |
| 4 | 254 s | 612 t/s | 188 t/s | 0,81 | 605 Mo |
| 6 | 209 s | 746 t/s | 171 t/s | 0,73 | 620 Mo |
| 8 | 173 s | 900 t/s | 150 t/s | 0,75 | 607 Mo |
| 12 | 163 s | 954 t/s | 144 t/s | 0,55 | 607 Mo |
| 12 (refait) | 178 s | 876 t/s | 134 t/s | 0,54 | 620 Mo |
| 16 | 135 s | **1 150 t/s** | 118 t/s | 0,61 | 624 Mo |
| 20 | 134 s | 1 160 t/s | 120 t/s | 0,48 | 598 Mo |

`uv run python scratch/bench_workers.py 1 4 6 8 12 16` (18 min de mur, les points 20 et 12-refait
ajoutés après).

**Conclusion : l'hypothèse du J3 est réfutée.** 12 workers ne saturent pas : ils battent 6 de
17 à 28 % selon la passe, et 16 battent 12 de 20 %. La contention mémoire est bien là — le débit
**par partie** perd 37 % de 4 à 16 workers — mais elle est plus lente que le gain de parallélisme.
Le « 1 008 → 615 ticks/s » du J3 ne venait donc pas des workers : la géographie seule a payé,
ce qui recoupe son ×2,58 déjà mesuré au-dessus. Deux variables dans un run, et c'est l'innocente
qu'on accuse.

**Ce que ça change dans le code : rien.** `workers_default()` rend déjà `min(cœurs, Go dispo)`,
soit 14 en local et 8 sur le serveur — dans le plateau mesuré des deux côtés. La piste n'avait pas
besoin d'une optimisation, elle avait besoin d'une mesure.

Déterminisme, vérifié au passage : les 16 parties ont été rejouées **8 fois** à des concurrences
différentes, 8 × 16 statuts `ok` (stats comparées au record), et les 307 788 lignes de
`player_ticks` sont identiques à celles du J3, nombre de lignes comme sommes d'or et de troupes.
La concurrence n'entame pas le déterminisme.

| | |
|---|---|
| 20 000 parties ≈ 198 M de ticks | **848 h de CPU** au débit réel du J3 (65 t/s/cœur, mélange complet) |
| sur les 8 cœurs du serveur | **~4,4 jours** de tâche de fond, sans personne devant |
| budget écrit | ces 4,4 jours. Pas de ×3 à chercher. |

Trois limites à connaître :
- le point à **20 workers ne veut rien dire** : l'échantillon n'a que 16 parties, donc 4 workers
  restent inoccupés et la passe est la même qu'à 16. Le plateau au-delà de 16 n'est pas mesuré ;
- ces 16 parties sont de coût médian, **sans les géantes** (906 Mo de RSS) qui tournaient en
  concurrence pendant le J3. Un mélange réel pèse plus lourd en mémoire, et à 16 workers on
  monterait vers 14 Go sur les 15,7 du local. C'est pourquoi la borne RAM de `workers_default()`
  reste la bonne garde-fou.
- non expliqué : les mêmes parties coûtent **67 s** ici à 12 workers contre **123 s** au J3, à
  concurrence égale. Candidats : les géantes en vol pendant le J3, et l'éviction (`--evict`,
  `rm -rf` de worktrees et `npm ci` pendant que les workers tournent). Sans conséquence sur la
  conclusion — l'écart est le même à tous les points — mais la projection ci-dessus garde les
  65 t/s du run réel, pas les 134 du banc.

## À remesurer plus tard

- [x] ~~Les 5,00 o/ligne viennent de séries synthétiques réalistes~~ → **8,24 o** pour
  `player_ticks`, **4,46 o** pour `events`, mesurés sur 28,2 M de vraies lignes.
- [x] ~~Débit de re-sim avec N workers en parallèle~~ → **365 parties/h à 6 workers**, pic 925 Mo.
- [ ] Temps de lecture des Parquet par le site (cible : 95 % < 300 ms, J6).
- [ ] Le même run sur le serveur (8 workers, 16 Go) : vérifier les 486 parties/h projetées.
- [ ] La courbe de workers au-delà de 16, et sur un mélange qui contient les géantes : les deux
      points que le banc du J4b ne couvre pas.
- [ ] `ofa rating` sur un mois complet du serveur : vérifier les ~5 Go de pic projetés.
