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

## À remesurer plus tard

- [ ] Les 5,00 o/ligne viennent de séries synthétiques réalistes : à confirmer sur la vraie sortie du J3.
- [ ] Débit de re-sim avec N workers en parallèle (contention mémoire et cache).
- [ ] Temps de lecture des Parquet par le site (cible : 95 % < 300 ms, J6).
