# ADR-0009 — Le serveur porte tout ; le portable est une machine de développement

- Date : 2026-11-22
- Statut : accepté (après le J3). **Première rédaction corrigée le même jour** : elle reposait sur
  921 Go libres sur le portable, chiffre faux (voir « Le piège de mesure » ci-dessous).

## Contexte

Deux contraintes non négociables, posées par l'utilisateur : l'ingestion doit être **continue**
(7,7 jours à 4 req/s pour les 435 jours d'archive, puis quotidienne) et le **site** du J6 doit être
hébergé sur le serveur. Un portable qui dort coupe la collecte. Le serveur doit donc **se suffire à
lui-même** : tout le projet y tient, le portable ne sert qu'au développement.

Reste à savoir si « tout le projet » tient dans **100 Go**. Après le J3, non : la projection passait
de 71 à 135 Go.

## Le piège de mesure, à ne jamais refaire

`df -h` dans WSL2 annonçait **921 Go libres** sur le portable. C'est la taille **provisionnée** du
disque virtuel (1007 Go), pas l'espace réel : le VHDX ne peut grandir que dans ce qui reste sur le
disque Windows, soit **118 Go** (`df -h /mnt/c` : 800 Go, 683 utilisés). Les deux machines sont donc
du même ordre, et la première version de cet ADR — « garder le cache moteur sur la machine qui a
922 Go » — n'avait pas de fondement. `fstrim` n'y est pour rien.

## Ce que la mesure dit

| poste | sans éviction | avec éviction | source |
|---|---|---|---|
| `data/raw/meta`, archive complète (2 654 420 parties) | 26 Go | 26 Go | J1 |
| `data/raw/full`, 100 000 parties | 4,3 | 4,3 | J0 |
| `index.sqlite` | 2,5 | 2,5 | J1 |
| `data/parquet` (games + participants + intents) | 14 | 14 | J2 |
| `data/resim`, 100 000 parties | **32** | 32 | **J3** |
| clone OpenFront | 2,1 | 2,1 | J0 |
| worktrees de moteurs (~370 commits sur 435 j) | 17,8 | **~0,5** | ADR-0008 |
| caches `node_modules` (~98 versions de lockfile × 362 Mo) | **35** | **~0,8** | J0 + J3 |
| `data/maps` (223 versions de maps par an) | 1,5 | 1,5 | mesuré après le J3 |
| **total** | **135 Go** | **84 Go sur 100** | |

Les **35 Go de `node_modules`** sont le poste que personne n'avait vu, parce qu'ils sont invisibles
à la mesure naïve : les worktrees les partagent en **liens durs**, et `du` ne compte un lien dur
qu'une fois par arborescence parcourue. Un `du -sh data/node_modules` affiche 22 Mo là où une
version de lockfile en coûte 362.

## Choix

**Le serveur porte tout** : collecte, normalisation, re-simulation, site. Le portable est une
machine de développement, qui travaille sur un sous-ensemble (`data/raw/full` + `parquet/games`
suffisent pour développer et rejouer quelques parties : 4,5 Go).

Ce qui rend ce choix possible, c'est **l'éviction des moteurs** (ADR-0008), qui vaut **51 Go** —
plus que n'importe quel autre levier, et sans rien sacrifier de la spec. Les deux autres leviers
envisagés sont donc écartés : réduire le nombre de parties re-simulées, et échantillonner tous les
20 ticks au lieu de 10 (ce qui s'écarterait de la spec pour 16 Go).

Ce qui circule, et dans un seul sens chacun :

- **serveur → portable** : `data/raw/full/` et `data/parquet/games/`, pour développer et tester sur
  de vraies parties ;
- **portable → serveur** : le code, par git.

Les Parquet ne circulent pas : le serveur les reconstruit depuis son propre brut (`ofa normalize`,
3 min 27 pour 55 952 records, ~2 h 45 projetées pour l'archive). Une table qui se reconstruit en
deux heures ne se transporte pas.

## Conséquences

- **Il faut un remote git** : le dépôt n'en a aucun aujourd'hui, et c'est le seul chemin du code
  vers le serveur. À régler avant la mise en service.
- Le serveur a besoin de `.ofa-contact` (hors git), de `uv sync`, **et** du clone OpenFront
  (`data/openfront`, 2,1 Go) puisqu'il re-simule.
- `ofa resim --evict` est ce que les timers du J9 doivent lancer. L'éviction **n'est pas** le
  défaut : elle détruit des worktrees, et sur une machine de développement ce serait une surprise
  désagréable.
- `git worktree remove` **n'est pas atomique** : il supprime les fichiers puis peut échouer sur la
  désinscription, laissant une inscription `prunable` qui fera refuser le prochain `worktree add`.
  `engine.prepare()` nettoie par `git worktree prune` et réessaie une fois ; `engine.evict()`
  rapporte ce qui a réellement été libéré, pas ce qu'il espérait.
- Le portable reste la machine la plus rapide pour une campagne de re-sim ponctuelle (20 cœurs,
  mais 8 Go de RAM, donc 6 workers : 365 parties/h contre 486 sur le serveur).

## Comment vérifier

```bash
# l'espace reel sous WSL2, et non la taille provisionnee du VHDX
df -h /mnt/c

# le cache moteur, poste par poste. Sans --exclude, du compte les node_modules
# partages en liens durs comme s'ils appartenaient a chaque worktree.
du -sm --exclude=node_modules data/engines/*      # attendu : 35 a 37 Mo chacun
du -sh data/node_modules                          # trompeur : liens durs
git -C data/openfront worktree list               # aucun `prunable`
```

## Mise à jour du 2026-11-22 — le remote existe

La conséquence « il faut un remote git, à régler avant la mise en service » est levée :
**https://github.com/Z-OxO/OFA** (public, branche `main`). Vérifié avant le premier push : 67
fichiers suivis, 1,7 Mo, `data/` et `tests/fixtures/` ignorés — donc aucun vrai pseudonyme, aucune
adresse réelle, ni `.env` ni `.ofa-contact` dans l'historique.

Et une exception, unique et datée, à « portable → serveur : le code » : l'**amorçage** de
`data/raw/` (566 Mo de meta + 29 Mo de full) et de `data/state/index.sqlite` (54 Mo) par `rsync`.
Ces 650 Mo ont déjà été payés à l'API ; les refaire télécharger au serveur serait 2 h de requêtes
pour un résultat identique. Tout ce qui se recalcule (`parquet`, `rating`, `resim`, `maps`,
`engines`) reste interdit au transport. La marche à suivre complète est dans le README.
