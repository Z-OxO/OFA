# ADR-0001 — Cache du moteur par commit : worktree + tsx, sans bundle

- Date : 2026-11-21
- Statut : accepté (J0)

## Contexte

Une partie ne se rejoue à l'identique que sur le build qui l'a jouée (`docs/Replays.md`, et constaté :
`demoGame1.json` rejoué au mauvais commit diverge dès le tour 210). Il faut donc un moteur par
`gitCommit` rencontré. OpenFront produit **2 611 commits par an**. La spec demandait « un moteur
compilé une seule fois par commit, puis mis en cache ».

## Options

1. **Bundle par commit** (esbuild) : un artefact autonome par sha. Conforme à la lettre de la spec.
2. **Worktree git + `npm ci` + `tsx`** : pas de build, le TS est exécuté tel quel.
3. Un seul checkout qu'on déplace par `git checkout` : simple, mais sérialise tout et interdit
   plusieurs workers sur des commits différents.

## Choix

Option 2, avec trois niveaux de cache :

- **code** : `git worktree add` par sha dans `data/engines/<sha>/`, en sparse-checkout excluant
  `resources/maps/` (393 Mo au lieu de 1,1 Go) ;
- **dépendances** : `node_modules` indexé par le **hash du `package-lock.json`** — 82 versions par an
  au lieu de 2 611 ;
- **maps** : chaque map matérialisée dans `data/maps/<tree-sha>/<map>/`, où `<tree-sha>` est le tree git
  de **ce dossier de map** à ce commit. Une partie n'a besoin que de sa map (1 à 5 Mo), et une map
  inchangée entre deux commits n'est stockée qu'une fois.

Node est épinglé à `/usr/bin/node` (v24.22.3) : OpenFront exige `node >=24.15 <25` avec
`engine-strict=true`, et nvm place un v22.22.3 devant dans le PATH.

## Raison

Les packages exportent le TypeScript brut (`"exports": { "./*": "./src/*.ts" }`) et tout le projet
tourne déjà sous `tsx` : un bundle serait du travail en plus pour un gain mesuré négligeable.
Le surcoût de démarrage node + tsx est de **1,4 s par processus**, contre des dizaines de secondes de
simulation par partie — moins de 5 %. `npm ci` coûte 15,4 s, payé une fois par version de lockfile.

## Comment vérifier

- `node --import tsx tests/replay/ofa_runner.ts <record>` rejoue une partie sans aucune étape de build.
- `git -C data/openfront rev-parse "<sha>:package-lock.json"` identique entre deux commits → même
  `node_modules`, donc un seul `npm ci`.
- `git -C data/openfront rev-parse "<sha>:resources/maps/<map>"` identique → map non recopiée.
- Si le surcoût de démarrage dépasse 10 % du temps d'une re-sim moyenne (mesure dans `docs/PERF.md`),
  reconsidérer l'option 1.
