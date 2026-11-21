# ADR-0003 — Ce qui fait qu'une re-simulation est valide

- Date : 2026-11-21
- Statut : accepté (J0)

## Contexte

La spec exige qu'une re-sim ne soit retenue que si « les stats finales sont identiques au record »,
sinon « erreur classée, jamais de données partielles silencieuses ». En mesurant sur 3 vraies parties,
l'égalité stricte champ par champ échoue — mais **pas** à cause de la simulation.

## Ce que la mesure montre

1. **Trois champs ne sont jamais archivés.** `spawnTile`, `killedBy` et `deathPosition` sont absents de
   **993 lignes joueur sur 993**, dans 27 records étalés de juin 2025 à novembre 2026. Le moteur les
   calcule ; l'API ne les stocke pas. Les exiger ferait échouer 100 % des re-sims.
2. **Un joueur qui n'a jamais spawné n'a de stats ni d'un côté ni de l'autre.** 5 cas dans la partie à
   95 joueurs : absence des deux côtés = accord, pas divergence.
3. **Les hashs fournissent une preuve indépendante.** Le serveur les a enregistrés pendant la partie
   en direct, tous les 10 tours, et tous les clients actifs les avaient validés. Mesuré : **970/970,
   593/593 et 2934/2934 identiques**, zéro divergence.

## Choix

Une re-simulation est **valide** si, et seulement si :

1. **tous les hashs du record concordent** (0 divergence, pas « peu de divergences ») ;
2. le **gagnant** recalculé est identique, tuple compris ;
3. **tout champ de stats présent dans le record** est identique, après conversion des bigint en
   entiers (`int`, jamais `float`), pour **chaque** joueur ;
4. un joueur sans stats dans le record **et** sans stats recalculées compte comme concordant ;
5. `spawnTile`, `killedBy` et `deathPosition` sont **exclus de la comparaison quand ils sont absents
   du record** — et conservés comme données supplémentaires, puisque le record ne les a pas.

Tout le reste est une erreur, classée par cause : `commit_introuvable`, `commit_absent` (`DEV` ou
vide), `runner_echec`, `hash_divergent`, `stats_differentes`.

## Raison

Le point 1 est la vraie garantie de déterminisme et il ne dépend pas de nous : il compare notre
simulation à ce que des clients indépendants ont validé en direct. Les points 4 et 5 ne relâchent pas
le critère, ils retirent de la comparaison ce que la source ne contient pas — ce qui est vérifié par
une mesure, pas supposé. Sans le point 5, le critère de la spec serait inapplicable.

**Conséquence importante** : `deathPosition`, que la spec désigne comme mesure de résultat en FFA,
n'existe **que** par re-simulation. Son substitut gratuit `killedAt` ne reconstitue le classement
qu'imparfaitement (Spearman −0,81 et −0,96, relation non monotone, sur 2 parties). L'analyse de
population peut donc porter sur le **temps de survie** partout, mais sur le **classement exact**
seulement là où la re-sim a tourné.

## Comment vérifier

- `python3 -I scratch/resim_poc/drive.py <gameID>...` affiche, par partie, les hashs et la liste des
  écarts ; « diff VIDE » est le critère.
- Le taux de présence des champs se recompte sur les records téléchargés : si `spawnTile` apparaît un
  jour dans un record, le point 5 doit être retiré.
- Au J3, le critère est appliqué sur 500 parties `statsAgreed` avec un objectif de ≥ 99 % de réussite,
  et les échecs sont listés par cause.
