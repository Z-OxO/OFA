# ADR-0007 — Colonnes chaudes + catch-all verbatim dans les tables Parquet

Date : 2026-11-22. Jalon : J2.

## Contexte

Le schéma des records d'OpenFront **n'est pas stable dans le temps**, et `version` vaut `v0.0.2` sur
100 % de l'archive malgré les changements : il n'y a aucun numéro de schéma sur lequel s'appuyer.
Mesuré sur les 55 952 records du J1 et les 27 fixtures (juin 2025 → novembre 2026) :

- `info.config` a **29 clés** observées, dont 13 sur moins de 5 % des parties. Des clés
  **apparaissent** (`startingGold` 2 454, `goldMultiplier` 1 428, `doomsdayClock` 917,
  `disableAlliances` 282) et **disparaissent** (`disableNPCs`, présent en juin 2025, absent
  aujourd'hui) ;
- `publicGameModifiers` porte 12 drapeaux, ajoutés au fil des versions ;
- deux **types d'intents** de l'archive n'existent plus dans `Schemas.ts` au SHA épinglé :
  `troop_ratio` (1 392 occurrences) et `allianceRequestReply` (486) ;
- des **champs d'intents** ont disparu : `x`, `y`, `flag`, `name`, `playerType` sur les vieux
  `spawn`, `srcX`/`srcY`/`dstX`/`dstY` sur `boat` ;
- deux orthographes coexistent pour la même chose : `unitId` et `unitID`.

Les Parquet sont la source de vérité du projet : le J4, le J5 et le J6 ne liront qu'eux. Un schéma
qui doit changer à chaque version du moteur casse les requêtes en aval et impose de tout réécrire.

## Options

1. **Une colonne par champ observé.** Lisible en SQL, mais le jeu de colonnes dépend de l'archive
   qu'on a sous les yeux : les 300 000 parties suivantes amèneront des champs qu'on ne connaît pas,
   et chaque ajout est une migration. Pour les intents, ce serait ~32 colonnes dont 30 à null sur
   chaque ligne.
2. **Tout en JSON** (4 colonnes + un blob). Aucune migration jamais, mais chaque requête du J5 et
   chaque page du J6 passent par `json_extract`, et on perd le typage et le filtre par colonne,
   c'est-à-dire l'intérêt du Parquet.
3. **Colonnes chaudes + catch-all verbatim** : les champs sur lesquels l'analyse filtre et agrège
   ont une colonne typée ; tout le reste est conservé tel quel dans une colonne JSON.

## Choix

**Option 3**, appliquée aux deux endroits où le schéma bouge :

- `games.config_json` = `info.config` **entière**, en JSON à clés triées, en plus des 21 colonnes
  de config promues ;
- `intents.params` = tous les champs non promus de l'intent, verbatim (null si vide), en plus des
  10 colonnes promues.

## Raison

- **Rien n'est perdu**, y compris ce qu'on ne sait pas encore lire. Un champ apparu en octobre 2026
  est dans `params` dès aujourd'hui ; le promouvoir plus tard est une colonne de plus, pas une
  re-collecte.
- **Aucune migration quand le moteur change.** La normalisation est une reconstruction totale
  (3 min 27 pour 56 000 records) : promouvoir un champ, c'est ajouter une ligne à une table et
  relancer.
- **Le typage là où il sert.** `select avg(troops) ... where type='attack'` ne touche qu'une
  colonne ; la longue traîne de la config n'est jamais dans un `group by`.
- **Le coût est nul en pratique** : `config_json` est hautement répétitif d'une partie à l'autre, et
  `intents.params` est null sur 60 % des lignes (les `attack`, de loin les plus nombreux, ont tous
  leurs champs promus).

Une exception assumée : **les clés de `stats` n'ont pas de catch-all.** Une nouvelle stat exige une
colonne entière typée, pas un JSON. `ofa normalize` compte les clés inconnues, les affiche et
**sort en code 1** ; mesuré 0 sur 2 070 777 lignes joueur, donc un signal ne peut vouloir dire
qu'une chose. C'est volontairement le cas de `killedBy`, `deathPosition` et `spawnTile`, déclarés
par le moteur mais jamais archivés : le jour où l'API les archive, la normalisation le dit.

## Comment vérifier

```bash
# aucune clé de stat inconnue, et la sortie est en code 0
uv run ofa normalize && echo "aucune cle inconnue"

# un champ non promu est bien là, et lisible
duckdb -c "select json_extract(config_json,'\$.publicGameModifiers') m, count(*)
           from 'data/parquet/games/**/*.parquet' group by 1 order by 2 desc limit 5"
duckdb -c "select json_extract(params,'\$.ratio') from 'data/parquet/intents/**/*.parquet'
           where type='troop_ratio' limit 3"
```

`tests/test_normalize.py::test_intents_par_type_identiques_au_brut` prouve qu'aucun intent n'est
perdu malgré les types disparus ; `test_cle_de_stat_inconnue_signalee` prouve que l'alarme sonne ;
`test_hors_perimetre_marque_pas_plante` prouve que `disableNPCs` survit dans `config_json`.
