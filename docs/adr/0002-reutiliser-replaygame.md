# ADR-0002 — Partir de `tests/replay/ReplayGame.ts` au lieu d'écrire un runner

- Date : 2026-11-21
- Statut : accepté (J0)

## Contexte

Le J3 demande un runner Node qui rejoue un record et exporte l'état de chaque joueur toutes les
10 ticks, plus les événements au tick exact. La spec citait `packages/engine/src/WinnerReplay.ts` et
`src/server/WinnerReplayChild.ts` comme points de départ.

## Options

1. Écrire un runner à partir de `createGameRunner`, en s'inspirant de `WinnerReplay.ts`.
2. Adapter `tests/replay/ReplayGame.ts`, qui existe déjà dans le repo (script npm `replay:game`).

## Choix

Option 2. `ReplayGame.ts` (280 lignes) fait déjà, en Node et sans navigateur : téléchargement du
record depuis l'API, parse tolérant aux anciens schémas, `decompressGameRecord`, chargement de la map
par le système de fichiers (`NodeGameMapLoader`), construction du jeu, boucle
`addTurn`/`executeNextTick`, et **comparaison des hashs** du record — exactement le contrôle de
déterminisme dont on a besoin.

Notre copie (`scratch/resim_poc/ofa_runner.ts`, puis `resim/` au J3) n'ajoute que trois choses :
le répertoire de maps par variable d'environnement, la capture de l'update `Win`, et une sortie JSON
avec les bigint en chaînes décimales. L'URL `/public/` s'obtient par l'option `--api-base` existante,
sans modifier de code.

## Raison

Le code qui existe est celui que les développeurs du jeu utilisent pour vérifier qu'un record rejoue ;
le réécrire reviendrait à reproduire ses pièges (ordre d'initialisation, `toWireGameStartInfo`,
`PseudoRandom` semé par `gameID`, `teamIndex` absent des vieux records de team) sans les connaître.
`WinnerReplay.ts` ne sert, lui, qu'à retrouver le gagnant : il s'arrête au premier `Win` et ne compare
aucun hash.

## Comment vérifier

- Le fichier reste une **copie** : `data/openfront/` n'est jamais modifié
  (`git -C data/openfront status` propre).
- `diff data/openfront/tests/replay/ReplayGame.ts resim/ofa_runner.ts` ne doit montrer que les trois
  adaptations ci-dessus. Si l'original change en amont, le diff le révèle.
- Critère de validité d'une re-sim : hashs identiques **et** stats finales identiques au record.
