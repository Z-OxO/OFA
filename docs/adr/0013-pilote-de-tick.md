# ADR-0013 — Piloter les ticks nous-mêmes, et ne pas patcher le moteur

- Date : 2026-11-23
- Statut : accepté (J4c)

## Contexte

Le J4c demande ×3 sur la re-simulation, sans changer les données produites, et autorise
explicitement un patch du moteur — ce qui lèverait la règle de `.claude/rules/resim.md`.

Le profil CPU (docs/PERF.md § J4c, obligatoire avant toute optimisation) a mesuré sur trois vraies
parties que **60 à 66 % du mur est la simulation du jeu elle-même**. Le travail de vue, lui, se
répartit en deux endroits :

- dans `GameRunner.executeNextTick` (1,3-3,5 % en temps propre, plus ce qu'il appelle) :
  `placeName` sur tous les joueurs tous les 30 ticks, `placeSpawnName` pendant la phase de spawn,
  `packUnitUpdates` que notre runner déballait juste derrière, et une copie des données de vue ;
- dans `GameImpl.executeNextTick` (8-13 %) : `player.toUpdate()` pour **chaque** joueur à
  **chaque** tick, qui construit un objet de vue complet (vues d'alliance, embargos, cibles,
  emojis) et le diffe contre le précédent, pour un client qui n'existe pas.

## Décision

**1. On pilote les ticks nous-mêmes**, dans `resim/ofa_runner.ts`, au lieu d'appeler
`GameRunner.executeNextTick`. La boucle fait ce que faisait `GameRunner`, moins le travail de vue :
`game.addExecution(...executor.createExecs(turn))`, `game.executeNextTick()`, la lecture des
updates, et les quatre `drain*` qui ne se vident que par leur appel.

`GameRunner` est **conservé pour son seul `init()`** : il ajoute les exécutions de départ (timer de
spawn, nations, bots, vérification de victoire, horloge de l'apocalypse, grappes de rails) et cette
liste change d'un commit à l'autre. La recopier chez nous imposerait des `import` statiques de
modules absents des commits anciens, c'est-à-dire exactement le piège ERR_MODULE_NOT_FOUND vécu au
J3 sur le commit 79aed26b.

Conséquence heureuse : les unités se lisent **en clair** dans `updates[Unit]` — c'était
`GameRunner` qui compactait ce tableau puis le vidait — donc l'adaptateur de compactage
(`loadUnpackUnits`) disparaît. Le runner perd 50 lignes et gagne un chemin unique pour tous les
commits, au lieu d'un chemin par version d'interface.

**2. On ne patche pas le moteur**, bien que le J4c l'autorise. Le seul patch qui vaudrait son
chiffre est la suppression du `toUpdate` par joueur et par tick (8-13 %).

## Alternatives écartées

- **Patcher `GameImpl.executeNextTick`** pour sauter `toUpdate` : 8-13 %, mais le patch viserait
  des lignes qui diffèrent d'un commit à l'autre, dans la **boucle de simulation**, et il faudrait
  le réappliquer à chaque version du moteur de l'archive (7 localement, autant sur le serveur, et
  une nouvelle à chaque mise à jour du jeu). Un patch textuel qui s'applique de travers sur une
  version est une divergence silencieuse. Pour 10 %, et alors que le ×3 est hors d'atteinte de
  toute façon (voir ci-dessous), le rapport est mauvais.
- **Rendre `shoreReachableNeighbors` public** (un mot) pour éviter que `nearby()` refasse la passe
  sur les tuiles de frontière : écarté parce que le profil a mesuré `nearby()` à **2-3 %**, pas aux
  ~60 % que le ×2,58 de l'ADR-0011 laissait croire. Ce ×2,58 était un coût de volume, pas de CPU.
- **Recopier `GameRunner.init()`** chez nous pour se passer entièrement de `GameRunner` : imports
  statiques fragiles d'un commit à l'autre, pour zéro gain (`init()` ne coûte rien).
- **Un échantillonnage plus grossier des frontières** (100 ticks au lieu de 10) : refusé par
  l'utilisateur, et à juste titre — c'est une perte de donnée, pas une optimisation.

## Conséquences

- La re-simulation coûte **7,1 % de CPU en moins** à données **identiques** : vérifié par les
  sommes de contrôle de chaque colonne des quatre tables sur 100 parties déjà valides, et par la
  concordance des stats finales avec le record (ADR-0003) sur ces mêmes 100 parties.
- ADR-0002 (« partir de `ReplayGame.ts` ») reste la bonne décision pour l'installation du jeu, la
  carte, les joueurs et la lecture des updates ; elle ne vaut plus pour la boucle de ticks, qui
  est maintenant la nôtre. `.claude/rules/resim.md` (« ne jamais modifier le code du moteur »)
  reste intacte : rien n'a été patché.
- Le ×3 du jalon ne vient pas du code : il vient de la **piste 0 de la spec**, répartir une
  campagne sur les deux machines (le portable rend 1 046 t/s à 14 workers, le serveur ~520 t/s à
  8 vCPU). Le détail et les chiffres sont dans docs/PERF.md § J4c.
- Si un commit du moteur changeait `createExecs`, les `drain*` ou la forme de `GameUpdates`, la
  re-sim échouerait **bruyamment** (statut classé, stats non concordantes), jamais en silence.
