# ADR-0008 — Grouper les parties par commit, et que faire de la traîne

- Date : 2026-11-22
- Statut : accepté (J3)

## Contexte

Une partie ne se rejoue qu'au commit qui l'a jouée (ADR-0001), et chaque commit distinct coûte un
worktree. La question posée au J3 : les 514 parties re-simulables se répartissent sur **6 commits,
dont un avec une seule partie**. À 48 Mo de cache moteur par commit, provisionner un commit qui ne
sert qu'une fois coûte-t-il plus que la partie ?

## Ce que la mesure dit

| | valeur | commande |
|---|---|---|
| Parties utilisables (`has_turns and stats_agreed and in_scope`) | **514** | `uv run ofa resim --dry-run` |
| Commits distincts sur les 48 100 parties du périmètre | **6** | `group by git_commit` |
| Répartition des 514 | **199 / 98 / 83 / 82 / 51 / 1** | idem |
| Les 5 premiers commits | **513 parties, soit 99,8 %** | idem |
| Coût d'un commit dont le lockfile est déjà installé | **37 Mo** de code + ~11 Mo de delta `node_modules` (liens durs), **1,5 s** | `du -sm --exclude=node_modules`, PERF.md |
| Coût d'un commit dont le lockfile est nouveau | 37 Mo + **362 Mo** + **15,4 s** de `npm ci` | PERF.md |
| Lockfiles des 6 commits | **5 partagent `97b6f495`** ; `79aed26b`, le commit à 1 partie, est le seul avec `ba0deb3e` | `git rev-parse <sha>:package-lock.json` |
| Cadence de déploiement observée | **~0,85 commit par jour** (5 commits actifs sur 6 jours de parties) | `min(start)`/`max(start)` par commit |
| Disque libre, local / serveur | **922 Go** / 100 Go, dont **9 Go budgétés** pour clone + moteurs | `df -h`, RECON § 10.3 |

La traîne n'est donc pas « un commit par partie » : elle est d'**un commit sur six**, et sa
particularité n'est pas d'avoir peu de parties, c'est d'avoir **son propre lockfile**. C'est le
lockfile, pas le commit, qui fait le prix : 399 Mo contre 48.

## Options

1. **Un worktree par partie.** 514 × 48 Mo = 24 Go et 514 préparations. Jamais sérieusement
   envisagé, mais c'est ce qu'on obtient sans regrouper.
2. **Grouper par commit et tout provisionner.** 6 worktrees, 1,7 Go.
3. **Grouper, et abandonner les commits sous un seuil de parties.** 5 worktrees, 1,3 Go,
   et 1 partie perdue sur 514 (0,19 %).

## Choix

**Regrouper par commit n'est pas un arbitrage, c'est la préparation** : les moteurs sont prêts
avant le premier run, et l'exécution reste un pool plat sans affinité worker↔commit. Option 1
écartée sans discussion.

**Au J3, tout est provisionné** (option 2). Les 399 Mo du commit à une partie valent 0,04 % du
disque libre local, le `npm ci` coûte 15 s une fois, et le résultat est un dénominateur de
**514/514 sans astérisque** — alors qu'abandonner aurait produit un « 513 sur 514, et la dernière
n'a pas été tentée » à expliquer dans chaque tableau d'acceptation. Bénéfice secondaire réel : le
chemin « nouveau lockfile » de l'ADR-0001 est exercé en vraie grandeur une fois, au lieu d'être
supposé.

**La règle pour le serveur est écrite ici, pas dans le code.** Le budget de 9 Go arrêté au J0
(RECON § 10.3) tient ~140 commits permanents à 48 Mo. À 0,85 commit déployé par jour, les 435 jours
d'archive en comptent **~370, soit 17,8 Go**. Deux sorties, à trancher au J9 avec les timers :

- **réviser le budget** à ~20 Go : le total projeté passe de 71 à ~80 Go sur 100 ;
- **évincer** le worktree d'un commit quand son lot est fini (`git worktree remove`), et son
  `node_modules` quand aucun commit en attente ne partage son lockfile. Le cache redevient
  transitoire, le plafond devient `--keep-engines`.

**Corrigé le 2026-11-22, le jour même : l'éviction est implémentée.** Les deux prémisses de ce
report étaient fausses.

1. Le portable n'a pas 922 Go libres mais **118** : `df` dans WSL2 annonce la taille *provisionnée*
   du disque virtuel (ADR-0009). Il n'existe donc aucune machine où le cache moteur est gratuit.
2. Le coût n'est pas de 17,8 Go mais de **52,8** : les caches `node_modules` pèsent
   **362 Mo par version de lockfile**, soit ~98 versions sur 435 jours = **35 Go**, et ils sont
   invisibles à la mesure naïve parce que les worktrees les partagent en liens durs.

`ofa resim --evict` retire donc le moteur d'un commit **dès que son lot est fini**. C'est quasi
gratuit parce qu'on rejoue commit par commit : un worktree sert une fois, puis plus jamais. Le
cache `node_modules`, lui, n'est retiré que si **aucun commit en attente ne partage son lockfile** —
5 des 6 commits de l'archive courante partagent le même, et l'évincer trop tôt coûterait un
`npm ci` de 15 s pour chacun des suivants (`resim.evictions()`, testé).

L'éviction **n'est pas le défaut** : elle détruit des worktrees, ce qui sur une machine de
développement serait une surprise désagréable. Les timers du J9 passent le drapeau.

## Conséquences

- `ofa engines` prépare un moteur par commit de la sélection et chiffre le coût avant de le payer.
  Dans le sandbox, `git worktree add` et `npm ci` échouent (`.git` en lecture seule) : la commande
  les remonte en liste à lancer hors session, au lieu d'un traceback.
- Un commit disparu d'upstream (8 branches ont été en *forced update* au dernier fetch) donne
  `commit_introuvable` : vérifié **commit par commit** avec `git cat-file -e`, jamais une fois pour
  toute l'archive.
- La sélection étant faite par commit, rien n'empêchera le J5 de stratifier autrement (par map,
  par mode, par tranche de niveau) : le groupement ne contraint que la préparation.

## Comment vérifier

```bash
uv run ofa engines --dry-run      # parties, présence, lockfile, Mo par commit
git -C data/openfront worktree list
du -sm --exclude=node_modules data/engines/*   # 37 Mo par commit si le sparse a été appliqué
```

Un worktree à ~400 Mo au lieu de 37 Mo signale un **sparse-checkout non appliqué**. Mesuré après
le run : 6 des 7 worktrees faisaient 35 à 37 Mo, et `dd85bcfb` **393** — ses 353 Mo de trop étant
``map-generator`` (283 Mo), les assets de `resources/` (59) et `proprietary/` (16), dont le moteur
n'importe rien. Il a été **évincé**, ce qui a servi de premier essai réel à `engine.evict()` et a
rendu les 393 Mo.

Attention au piège de la mesure : sans `--exclude=node_modules`, `du` compte les `node_modules`
partagés en liens durs comme s'ils appartenaient à chaque worktree, et les six worktrees sobres
affichent eux aussi ~400 Mo. C'est ce piège qui a caché les 35 Go de caches de lockfile.
