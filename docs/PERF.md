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

| Mesure | Valeur |
|---|---|
| Record complet, zstd -19 | **médiane 39,2 Ko** (×2,3 à ×9,1 de compression) |
| Record sans `turns`, zstd -19 | **médiane 5,3 Ko** (×6 plus petit, **même nombre de requêtes**) |
| Sortie de re-sim, 1 point/joueur/10 ticks | **36 641 lignes/partie** en moyenne (médiane 21 554, max 357 175) |
| Coût d'une ligne en Parquet zstd | **13,6 o** (DuckDB, données aléatoires → **majorant**) |
| → sortie de re-sim par partie | **487 Ko** en moyenne |

Projections complètes et volume cible proposé : `RECON.md` § 10.

## À remesurer plus tard

- [ ] 13,6 o/ligne sur de vraies séries temporelles (J3) : ce sera probablement bien moins.
- [ ] Débit de re-sim avec N workers en parallèle (contention mémoire et cache).
- [ ] Temps de lecture des Parquet par le site (cible : 95 % < 300 ms, J6).
- [ ] zstd -19 est lent à la compression : comparer -9 et -12 sur le débit d'ingestion (J1).
