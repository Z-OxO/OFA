# PERF — mesures de performance

Règle : mesurer avant d'optimiser, et consigner ici avec la commande.
Machine de mesure : WSL2 Ubuntu, **20 cœurs, 7 Go de RAM**, 927 Go libres (`nproc`, `free -g`, `df -h`).
La RAM est la ressource limitante, pas les cœurs.

## Préparation du moteur (J0)

| Mesure | Valeur | Commande |
|---|---|---|
| Clone complet d'OpenFront | 2,1 Go (`.git` 951 Mo, `resources/maps` 764 Mo) | `du -sh data/openfront` |
| Worktree sparse (sans `resources/maps`) | 393 Mo | `git sparse-checkout set '/*' '!/resources/maps/'` |
| `npm ci --ignore-scripts` | **15,4 s**, pic RSS 704 Mo, `node_modules` 362 Mo | `/usr/bin/time -f '%es %MkB' npm ci --ignore-scripts` |
| Matérialisation d'une map (pangaea) | 1,3 Mo | `git archive <tree> \| tar -x` |

Conséquence : un worktree + `node_modules` coûte ≈ 755 Mo. Sur un an, **82 versions de
`package-lock.json`** seulement (contre 2 611 commits) → `node_modules` partagé par hash de lockfile.
Les maps sont indexées par tree-sha : `resources/maps/world` est identique entre `dd85bcf` (18/11) et
`main` (20/11), donc une map n'est matérialisée qu'une fois par version réelle.

## Re-simulation (J0, POC partiel)

Partie de référence : `records/demoGame1.json` — 1 joueur, Pangaea Normal, FFA, 1 500 ticks.

| Mesure | Valeur |
|---|---|
| Simulation pure | **3,4 s** pour 1 500 ticks → ≈ **440 ticks/s** |
| Temps de mur total | 4,84 s |
| Surcoût de démarrage node + tsx | ≈ **1,4 s** par processus |
| Pic RSS | **243 Mo** |

**Décision qui en découle** : 1,4 s de démarrage pour une partie qui se rejoue en dizaines de
secondes, c'est < 5 % → **pas besoin de bundler le moteur** (voir `adr/0001-cache-moteur.md`).
À re-mesurer sur une vraie partie à 50 joueurs avant de figer.

Avec 243 Mo par worker sur cette partie minuscule, le plafond mémoire est le vrai sujet : une partie
à 50 joueurs sur une grande map consommera beaucoup plus. **À mesurer avant de fixer le nombre de
workers** : 7 Go / pic RSS réel, et non 20 workers par défaut.

## À mesurer (J0 non terminé)

- [ ] Débit et pic RSS sur 3 vraies parties (FFA, Team, grosse).
- [ ] ticks/s en fonction du nombre de joueurs et de la taille de map.
- [ ] Nombre de workers soutenable sous 7 Go.
- [ ] Taille d'un record après `zstd` (brut) et d'une partie normalisée en Parquet.
- [ ] Projection stockage et temps pour 1, 6 et 12 mois d'archive.
