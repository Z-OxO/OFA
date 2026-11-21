# DATA — les tables Parquet du J2

Trois tables dérivées de `data/raw/`, **source de vérité** du projet : le J4, le J5 et le J6 ne
lisent qu'elles.

```bash
uv run ofa normalize                       # reconstruit tout depuis data/raw/{full,meta}
duckdb -c "select count(*) from 'data/parquet/games/**/*.parquet'"
```

| | fichiers | lignes | taille |
| --- | --- | --- | --- |
| `data/parquet/games/month=YYYY-MM/part.parquet` | 1 par mois | 55 952 | 5,0 Mo |
| `data/parquet/participants/month=YYYY-MM/part.parquet` | 1 par mois | 2 070 777 | 208 Mo |
| `data/parquet/intents/month=YYYY-MM/part.parquet` | 1 par mois | 1 141 612 | 12,1 Mo |

Partition par mois de `start` (UTC), compression zstd, un groupe de lignes par lot de 2 000 records.
Mesuré le 2026-11-22 sur les 55 952 records du J1, dont **300 records complets** (`ofa fetch-full`)
qui portent les 1 141 612 intents : 3 805 par partie.

## Les trois principes

**1. Reconstruction totale, jamais d'incrémental.** `ofa normalize` relit tout `data/raw/` et
réécrit tout (3 min 27 pour 55 952 records, projection ≈ 2 h 45 pour les 2,65 M de l'archive).
Écriture dans `data/parquet.tmp/` puis échange de répertoires : un kill ne laisse pas de demi-état.
C'est aussi **ce qui propage la purge des comptes supprimés** : `ofa purge-deleted` expurge
`data/raw/`, et il faut enchaîner `ofa normalize` pour que les Parquet suivent (timers du J9).

**2. Colonnes chaudes + catch-all verbatim.** Le record change de forme avec les versions du
moteur — `disableNPCs` a disparu de la config, `startingGold` est apparu, les intents `troop_ratio`
et `allianceRequestReply` n'existent plus dans `Schemas.ts`. Ce qui n'a pas de colonne est donc
conservé tel quel en JSON : `games.config_json`, `intents.params`. Aucune migration de schéma quand
le moteur bouge, rien de perdu sur l'archive ancienne. Voir `docs/adr/0007-colonnes-et-catch-all.md`.

**3. Noms de colonnes en `snake_case` anglais**, calqués sur les champs du record : la
correspondance avec le brut est mécanique, et ce document n'est qu'une table de traduction.

---

## `games` — 42 colonnes, une ligne par partie

| colonne | type | source | note |
| --- | --- | --- | --- |
| `game_id` | str | `info.gameID` | clé |
| `version` | str | `version` | `v0.0.2` sur 100 % de l'archive : **ne jamais router dessus** |
| `git_commit` | str | `gitCommit` | **clé du re-jeu déterministe** (J3). 6 valeurs distinctes en 7 jours |
| `domain`, `subdomain`, `site` | str | racine du record | `subdomain` ∈ {blue, green} : créneau de déploiement |
| `start`, `end` | ts(ms, UTC) | `info.start/end` | ms epoch dans le brut |
| `lobby_created_at`, `visible_at` | ts(ms, UTC) | idem | **null** sur les vieux records (`visible_at` : 86 % présent) |
| `duration_s` | i32 | `info.duration` | **secondes** |
| `num_turns` | i32 | `info.num_turns` | ticks joués. **1 tick = 100 ms** |
| `lobby_fill_time_ms` | i64 | `info.lobbyFillTime` | **millisecondes** |
| `mode` | str | `config.gameMode` | `Free For All` \| `Team`, **avec les espaces** |
| `map` | str | `config.gameMap` | 137 valeurs distinctes, longue traîne |
| `map_size` | str | `config.gameMapSize` | `Normal` \| `Compact` ; null sur les vieux records |
| `difficulty`, `game_type` | str | config | `Medium`/`Hard` ; `Public` |
| `ranked_type` | str | `config.rankedType` | `2v2` sur 1 108 parties, null sinon |
| `public_game_type` | str | `info.publicGameType` | `ffa`/`team`/`special` ; null sur 1 108 |
| `player_teams` | str | `config.playerTeams` | **union int\|str** → texte brut : `"2"`, `"Quads"`, `"Humans Vs Nations"` |
| `nations` | str | `config.nations` | **union int\|str** → `"default"`/`"disabled"` |
| `bots`, `max_players` | i32 | config | |
| `spawn_immunity_duration_ticks` | i32 | config | **ticks** |
| `infinite_gold`, `infinite_troops`, `instant_build`, `random_spawn`, `donate_gold`, `donate_troops`, `disable_alliances` | bool | config | nullables : absents des vieux records |
| `config_json` | str | `info.config` | **la config entière, verbatim.** Les 12 drapeaux `publicGameModifiers` sont dedans : `json_extract(config_json, '$.publicGameModifiers.isPeaceTime')` |
| `winner_kind` | str | `winner[0]` | `player` 37 182, `team` 17 875, `nation` 57, **null 838** |
| `winner_id` | str | `winner[1]` | clientID, nom d'équipe (`"Red"`) ou nom de nation |
| `winner_members` | list\<str\> | `winner[2:]` | **un équipier déconnecté peut y manquer** (`isEligibleForTeamWin`) |
| `stats_agreed` | bool | `info.statsAgreed` | seul `true` vaut accord. **N'existe que depuis ~2026-10-15** |
| `num_players` | i32 | `len(players)` | humains uniquement ; bots et nations ne sont pas dans `players[]` |
| `num_tribes` | i32 | `len(tribes)` | nations de la partie |
| `has_turns` | bool | — | `true` seulement pour les records de `data/raw/full` |
| `num_intents` | i32 | — | null quand `has_turns` est faux |
| `in_scope` | bool | calculé | périmètre d'**analyse** : 48 100 / 55 952 (86,0 %) |

`in_scope` = `ranked_type != '1v1'` ∧ `public_game_type != 'special'` ∧
`player_teams != 'Humans Vs Nations'`. Le hors-périmètre est normalisé quand même : filtrer est une
clause `where`, re-télécharger ne l'est pas.

---

## `participants` — 98 colonnes, une ligne par (partie, joueur)

`(game_id, client_id)` est unique : vérifié sur les 2 070 777 lignes.

### Identité et résultat

| colonne | type | source | présence mesurée |
| --- | --- | --- | --- |
| `game_id`, `client_id` | str | `info.gameID`, `clientID` | 100 % |
| `public_id` | str | `publicID` | 100 % sur l'archive ≥ 2025-09-12 (ADR-0005) ; **301 lignes `REDACTED`** ; **null** avant |
| `username` | str | `username` | 100 %. Jusqu'à 1000 caractères sur l'archive ancienne : **ne jamais valider avec `UsernameSchema`** |
| `clan_tag` | str | `clanTag` | 18,7 % |
| `flag` | str | `cosmetics.flag` | 26,2 % |
| `is_lobby_creator` | bool | `isLobbyCreator` | |
| `team_index` | i32 | `teamIndex` | **0,2 %** — ce n'est pas l'équipe (voir « ce que ces tables ne disent pas ») |
| `friends` | list\<str\> | `friends` | 1,2 % |
| `won` | bool | calculé | 10,0 % des lignes ; **null** quand aucun vainqueur n'est archivé (10 037 lignes) |
| `has_stats` | bool | — | **92,5 %** : 154 890 lignes sans aucune stat |

`persistentID` **n'est jamais lu** : la spec interdit de le stocker. Les cosmétiques ne sont pas
normalisés au-delà de `flag` (`patternData` fait 300 octets d'entropie par joueur pour zéro valeur
analytique ; `data/raw/` les garde).

`won` : pour `winner_kind = 'player'`, `client_id == winner_id` ; pour `team`/`nation`,
`client_id ∈ winner_members`. **Un équipier déconnecté absent du tuple a donc `won = false` alors
que son équipe a gagné** — le tuple brut reste dans `games.winner_members` pour qui veut recalculer.

### Stats aplaties — 86 colonnes `i64` + `kills`

Indices relus un par un dans `data/openfront/packages/engine-api/src/StatsSchemas.ts` au SHA épinglé
`1636e204`. Les valeurs sont des **bigint sérialisés en chaînes décimales** → `int()`, **jamais
`float`** (mesuré : 0 flottant, 0 négatif, 0 non-décimal sur 2 070 777 lignes).

| famille | dim. | colonnes | présence |
| --- | --- | --- | --- |
| `attacks` | 4 | `attacks_` + `sent`, `recv`, `cancel`, `max_recv` | 92,4 % |
| `tiles` | 3 | `tiles_` + `peak`, `drawdown_peak`, `drawdown_trough` | 92,5 % |
| `alliances` | 6 | `alliances_` + `formed`, `broken_by_other`, `expired`, `held_to_end`, `peak_concurrent`, `longest_held_ticks` (**ticks**) | 92,5 % |
| `conquests` | 3 | `conquests_` + `human`, `nation`, `bot` | 86,7 % |
| `gold` | 7 | `gold_` + `work`, `war`, `trade`, `steal`, `train_self`, `train_other`, `donate_recv` | 92,5 % |
| `donations` | 2 | `donations_` + `gold_recv`, `gold_recv_broke` | 2,3 % |
| `boats` | 2 × 5 | `boats_{trade,trans}_{sent,arrive,capture,destroy,lost}` | 79,1 % |
| `bombs` | 4 × 3 | `bombs_{abomb,hbomb,mirv,mirvw}_{launch,land,intercept}` | 27,9 % |
| `units` | 7 × 5 | `units_{city,defp,port,wshp,silo,saml,fact}_{built,destroy,capture,lost,upgrade}` | 85,0 % |
| scalaires | 4 | `betrayals`, `peak_troops`, `killed_at`, `final_tiles` | 17,6 / 92,5 / 63,2 / 29,3 % |
| détail | — | `kills` : `list<struct<victim: str, tick: i64>>`, jusqu'à 43 entrées | 32,1 % |

Maxima mesurés sur l'archive, qui justifient `i64` : `gold_trade` **22 202 217 393**,
`attacks_sent` 4 946 628 823, `peak_troops` 2 081 249 469. Un `i32` débordait.

### Les trois règles de remplissage

Parce que **0 et « inconnu » ne sont pas la même chose** :

1. **`stats` absent ou `null`** (154 890 lignes) → **toutes** les colonnes de stats à NULL,
   `kills` à NULL, `has_stats = false`.
2. **`stats` présent, clé de famille absente** → cette famille vaut **0**. Le moteur n'écrit une
   clé que quand il l'incrémente : `gold`/`tiles`/`alliances`/`peakTroops` sont présents sur
   1 915 885 des 1 915 887 lignes qui ont des stats (2 lignes près), `bombs` seulement sur
   578 630 — absence = aucune bombe.
3. **`killed_at` et `final_tiles` restent NULL** quand ils sont absents : ce ne sont pas des
   compteurs. `killed_at = 0` voudrait dire « tué au tick 0 », `final_tiles = 0` « éliminé ».

Et deux détails du format brut :

- **les tableaux sont tronqués à droite** : un index au-delà de `len()` vaut 0. Mesuré :
  `units.city` a 4 valeurs sur 1 534 804 lignes et 1 seule sur 58 951, pour une dimension de 5 ;
  `gold` va de 1 à 7 valeurs.
- **`conquests` était un scalaire** avant d'être un tableau (`ArchivedPlayerStatsSchema`) : un
  scalaire est lu comme `[v]`, donc dans `conquests_human`. Aucun cas dans l'archive actuelle, le
  code le gère quand même.

### Piège : un 0 peut vouloir dire « non suivi »

Une stat ajoutée au moteur **après** une partie vaut 0 sur cette partie, et rien ne distingue ce 0
d'un vrai zéro. Exemple mesuré : les records de juin 2025 n'ont que 3 des 4 compteurs d'attaques,
donc `attacks_max_recv = 0` alors que le moteur ne le suivait pas encore. Idem pour `boats_*_lost`
(`BOAT_INDEX_LOST`) et `gold_donate_recv` (`GOLD_INDEX_DONATE_RECV`), tous deux ajoutés en fin de
tableau. **`units_*_upgrade` vaut 0 partout** dans l'archive actuelle (longueur maximale observée :
4 sur 5). Pour toute comparaison dans le temps, se fier à `git_commit`.

---

## `intents` — 14 colonnes, une ligne par action

Seulement pour les parties dont on a le **record complet** : `data/raw/meta` est téléchargé avec
`?turns=false` (ADR-0004), donc `intents` ne couvre que `data/raw/full`
(`uv run ofa fetch-full --sample N`).

| colonne | type | source | note |
| --- | --- | --- | --- |
| `game_id` | str | | |
| `tick` | i32 | `turns[].turnNumber` | **c'est le tick** ; vérifié `max(tick) = num_turns - 1` |
| `seq` | i32 | rang dans le tour | le moteur crée une Execution par intent **dans l'ordre** ; sans cette colonne, l'ordre ne serait porté que par l'ordre des lignes, que SQL ne garantit pas |
| `client_id` | str | `clientID` (estampillé serveur) | peut valoir `ADMINBOT`, ou un client absent de `players[]` (297 cas sur 91 784 : des joueurs partis avant la photo finale) |
| `type` | str | `type` | **texte libre, jamais un enum** : `troop_ratio` (1 392×) et `allianceRequestReply` (486×) ne sont plus dans `Schemas.ts` |
| `other_id` | str | voir ci-dessous | le joueur visé |
| `tile` | i64 | `tile` \| `dst` | **`TileRef` brut**, pas de coordonnées (voir plus bas) |
| `troops` | f64 | `troops` | **flottant dans le moteur**, contrairement aux stats |
| `gold` | i64 | `gold` | |
| `unit` | str | `unit` | `UnitType`, **avec les espaces** : `Atom Bomb`, `Missile Silo`, `Defense Post`… |
| `unit_id` | i64 | `unitId` \| `unitID` | les deux orthographes existent selon le type d'intent |
| `amount` | i32 | `amount` | |
| `action` | str | `action` | `start` \| `stop` pour les embargos |
| `params` | str | tout le reste | JSON compact à clés triées, **null si vide** |

### Correspondance champ → colonne

| colonne | champs source, par priorité | types concernés |
| --- | --- | --- |
| `other_id` | `targetID` → `recipient` → `requestor` → `target` | `attack` (null = terre non revendiquée), `boat`, `embargo` ; `allianceRequest`/`Extension`, `breakAlliance`, `emoji`, `donate_gold`/`troops`, `quick_chat` ; `allianceReject`, `allianceRequestReply` ; `targetPlayer` |
| `tile` | `tile` → `dst` | `spawn`, `build_unit`, `move_warship` ; `boat` |
| `unit_id` | `unitId` → `unitID` | `upgrade_structure`, `move_warship`, `delete_unit` ; `cancel_boat` |
| `params` | tout le reste, verbatim | `attackID`, `emoji`, `accept`, `ratio`, `isDisconnected`, `rocketDirectionUp`, `quickChatKey`, `variables`, `unitIds`, `x`, `y`, `dstX`, `dstY`, `srcX`, `srcY`, `flag`, `name`, `playerType`, et le `target` de `quick_chat` (qui porte déjà un `recipient`) |

```sql
-- un champ non promu se lit directement
select json_extract(params, '$.ratio') from 'data/parquet/intents/**/*.parquet'
where type = 'troop_ratio';
```

### `other_id` n'a pas d'intégrité référentielle, et c'est normal

**56 % des références joueur d'un intent ne sont pas dans `participants`** (51 304 sur 91 784
intents de fixtures) : ce sont les **bots et les nations**, qui jouent mais ne figurent pas dans
`info.players`. 7 553 ids distincts, tous de la même forme que les clientID humains.

**La casse n'est pas un critère** : les 7 553 sont en minuscules, mais 12 des 993 humains le sont
aussi (8 caractères alphanumériques, ça arrive). Un `other_id` absent de `participants` est un bot
ou une nation, et on ne peut pas dire lequel avant le J3.

`other_id` vaut aussi `AllPlayers` pour un émoji public (44 cas).

---

## Ce que ces tables ne disent pas

| manque | pourquoi | quand |
| --- | --- | --- |
| **l'équipe d'un joueur** en mode Team | `players[].team` n'est **jamais** archivé (0 sur 2 070 777). Il est calculé par `withRecordedTeams` → `humanTeams`, qui exige le nombre de nations par défaut de la map **au commit**. `team_index` n'est là que sur 0,2 % des lignes | J3 : `player.team()` se lit directement sur le moteur |
| `spawnTile`, `killedBy`, `deathPosition` | déclarés par le moteur, **jamais archivés** (0 %). `deathPosition` est la mesure de résultat principale en FFA | J3, par re-simulation |
| les **coordonnées** d'un `tile` | `x = tile % largeur`, et la largeur dépend de la map **et** du commit | J3 |
| `intents` pour la plupart des parties | `data/raw/meta` est sans `turns` (ADR-0004) | au fil des `ofa fetch-full` et du J3 |
| les **hashs** par tour | 31 % des tours en portent un ; c'est la preuve de validité d'une re-sim (ADR-0003), lue directement dans `data/raw/full` | J3 |
| le **classement** d'une partie | `deathPosition` absent ; `killed_at` n'en est qu'un proxy (Spearman −0,81 / −0,96 sur 2 parties) | J3 |

---

## Vérifier soi-même

```bash
# comptage des intents, indépendant du code de ofa
zstd -dc data/raw/full/cp/cPaLHdRvC2.json.zst \
  | jq '[.turns[].intents[].type] | group_by(.) | map({(.[0]): length}) | add'
duckdb -c "select type, count(*) from 'data/parquet/intents/**/*.parquet'
           where game_id='cPaLHdRvC2' group by 1 order by 2 desc"

# une stat, du brut à la colonne
zstd -dc data/raw/meta/cp/cPaLHdRvC2.json.zst \
  | jq '.info.players[0] | {clientID, gold: .stats.gold, peakTroops: .stats.peakTroops}'
duckdb -c "select client_id, gold_work, gold_war, peak_troops
           from 'data/parquet/participants/**/*.parquet' where game_id='cPaLHdRvC2'"
```
