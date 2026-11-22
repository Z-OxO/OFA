# DATA — les tables Parquet

Deux familles, et le J4, le J5 et le J6 ne lisent qu'elles :

- **`data/parquet/`** (J2) — `games`, `participants`, `intents` : dérivées de `data/raw/`,
  reconstruites en entier à chaque `ofa normalize`. **Source de vérité** du projet.
- **`data/resim/`** (J3) — `player_ticks`, `events`, `players` : produites par le **moteur**,
  rejoué au commit du record. Incrémentales, et une partie n'y écrit que si sa re-sim est valide.

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

# Les quatre tables de re-simulation (J3) — `data/resim/`

Elles ne viennent pas de `data/raw/` mais du **moteur**, rejoué au commit exact du record
(ADR-0001, ADR-0002). Une partie n'y écrit **que si sa re-sim est valide** au sens de l'ADR-0003 :
tous les hashs concordent, le gagnant et toutes les stats du record sont identiques. Sinon elle est
classée par cause dans `data/state/index.sqlite` et n'écrit rien — jamais de données partielles.

```bash
uv run ofa engines                  # un moteur par commit de la selection (hors sandbox)
uv run ofa resim                    # rejoue, reprend ou elle s'est arretee
uv run ofa resim --report           # le tableau d'acceptation, par cause
duckdb -c "select count(*) from 'data/resim/player_ticks/**/*.parquet'"
```

| | lignes | taille | par ligne |
| --- | --- | --- | --- |
| `data/resim/player_ticks/month=YYYY-MM/part-*.parquet` | 10 518 824 | 118,7 Mo | 11,28 o |
| `data/resim/events/month=YYYY-MM/part-*.parquet` | 17 452 122 | 101,9 Mo | 5,84 o |
| `data/resim/players/month=YYYY-MM/part-*.parquet` | 188 166 | 4,6 Mo | 24,59 o |
| `data/resim/borders/month=YYYY-MM/part-*.parquet` | **46 155 545** | 53,2 Mo | **1,15 o** |

**La table `borders` ne coûte presque rien en disque** : 46 M de lignes pour 53 Mo, soit 1,15 o par
ligne, parce qu'elle est triée par `(a, tick, b)` et que trois de ses cinq colonnes sont des séries
quasi monotones que `DELTA_BINARY_PACKED` écrase. Son coût est en CPU, pas en octets (docs/PERF.md
et le jalon J4b).

Mesuré le 2026-11-22 sur **513 parties** (les 514 `has_turns and stats_agreed and in_scope` de
l'archive, moins une solo sans hash), géographie comprise : **74 314 657 lignes, 278,4 Mo**, soit
**543 Ko par partie**. `player_ticks`, `events` et `players` portent exactement le même nombre de
lignes qu'avant l'ADR-0011 — c'est l'une des preuves que la géographie n'a pas perturbé la
simulation ; seules des colonnes s'y sont ajoutées, plus la table `borders`.

**Racine séparée, et c'est voulu** : `ofa normalize` écrit `data/parquet.tmp/` puis **échange le
répertoire entier**, donc tout ce qui vivrait sous `data/parquet/` serait détruit à la
normalisation suivante. Les fichiers sont nommés `part-<horodatage>-<lot>.parquet` : la re-sim est
**incrémentale** (des heures de calcul), contrairement à la normalisation.

**Encodage** : Parquet v2.6, `DELTA_BINARY_PACKED` sur tous les entiers, dictionnaire sur les
chaînes, zstd niveau 19. Mesuré sur 72 838 vraies lignes : **18,47 → 8,12 o par ligne** de
`player_ticks` (−56 %) et 5,75 → 4,50 pour `events`. Les colonnes sont des séries croissantes par
joueur (tick, or cumulé, troupes) : c'est l'écart entre deux valeurs successives qui se compresse,
pas la valeur. Les lignes sont triées par `(small_id, tick)` avant écriture, pour la même raison.

## `player_ticks` — 35 colonnes, une ligne par (partie, joueur, 10 ticks)

**Les humains seulement.** Un bot ou une nation n'est pas un sujet d'analyse et les inclure
doublerait le volume ; ils restent présents dans `events` et dans `players`.
Lu sur l'objet `Player` du moteur, jamais sur `PlayerView` (RECON § 5). Un joueur cesse d'être
échantillonné quand il meurt : la fin de sa série est sa mort, au multiple de 10 ticks près.

| colonne | type | source moteur | note |
| --- | --- | --- | --- |
| `game_id` | str | | |
| `small_id` | i32 | `smallID()` | la clé de jointure avec `events.a`/`b` et `players` ; `client_id` n'est que dans `players`, pour ne pas payer 20 o par ligne |
| `tick` | i32 | `ticks()` | multiple de 10 ; `tick / 10` = secondes de jeu |
| `tiles` | i32 | `numTilesOwned()` | |
| `gold` | i64 | `gold()` | le trésor **courant**, pas cumulé |
| `gold_earned` | i64 | `goldEarned()` | cumulé, toutes sources |
| `gold_trade` / `gold_train` / `gold_piracy` | i64 | `tradeGold()`, `trainGold()`, `piracyGold()` | cumulés ; mêmes sémantiques que `GOLD_INDEX_TRADE`, `TRAIN_*`, `STEAL` |
| `troops` | i64 | `troops()` | i64 et non i32 : `peak_troops` atteint 2 081 249 469 dans l'archive, à 3 % du débordement d'un i32 |
| `allies` | i16 | `allies().length` | les paires sont dans `events`, pas ici |
| `betrayals` | i16 | `betrayals()` | |
| `traitor`, `disconnected`, `doomsday` | bool | `isTraitor()`, `isDisconnected()`, `inDoomsdayClock()` | |
| `atk_out`, `atk_out_troops` | i16, i64 | `outgoingAttacks()` | nombre d'attaques en cours et total des troupes engagées |
| `atk_in`, `atk_in_troops` | i16, i64 | `incomingAttacks()` | |
| `u_city`, `u_defp`, `u_port`, `u_wshp`, `u_silo`, `u_saml`, `u_fact`, `u_trade`, `u_trans` | i32 | `unitCount(type)` | **possédées à cet instant**, pas construites : mêmes 9 clés que les colonnes `units_*` et `boats_*` de `participants`, pour que les deux se recoupent |
| `month` | str | partition | mois de `start` de la partie |

## `events` — 14 colonnes, une ligne par (partie, tick, événement)

Au **tick exact**, pas tous les 10 ticks. Trois sources, par ordre de simplicité : les tableaux en
clair de `GameUpdates` ; `unpackUnitUpdates()` pour les unités, parce que `GameRunner.sendUpdates`
**vide** `updates[Unit]` après compactage (c'est ce que fait le client, `GameView.ts`) ; et un diff
par tick sur les objets vivants pour les éliminations et les attaques.

| `kind` | `a` | `b` | autres colonnes |
| --- | --- | --- | --- |
| `unit_built` / `unit_lost` / `unit_captured` | propriétaire | ancien propriétaire (capture) | `unit_type`, `unit_id`, `params.lv` |
| `conquest` | conquérant | conquis | `gold` |
| `elimination` | éliminé | | |
| `attack_start` / `attack_end` | attaquant | cible (`0` = terre neutre) | `troops` |
| `alliance_request` / `alliance_reply` | demandeur | destinataire | `flag` = acceptée |
| `alliance_broken` | traître | trahi | |
| `alliance_expired` | joueur 1 | joueur 2 | |
| `alliance_extension` | joueur | | `params.ui` = id d'alliance |
| `donate` | donneur | receveur | `amount`, `what` = `gold` \| `troops` |
| `embargo` | joueur | visé | `what` = `start` \| `stop` |
| `target` | joueur | visé | |
| `bonus` | joueur | | `gold`, `troops` |
| `spawn_phase_end` | | | |

`a` et `b` sont des **`small_id`**, convertis depuis les `PlayerID` quand la source en donne : les
updates d'alliance, de cible et d'embargo portent déjà des `smallID`, mais conquête, don et bonus
portent des `PlayerID` (des chaînes). `params` garde en JSON tout champ non promu (ADR-0007).

**Les trains et les obus sont exclus** : 2 464 événements sur 5 090 dans la première partie mesurée,
aucune stat en face, aucun usage d'analyse. Les bombes et les missiles d'interception, eux, passent.

**Un id d'unité n'est jamais réutilisé**, et c'est ce qui rend la table juste : la même unité peut
apparaître deux fois dans un seul tick, inactive puis active. Un état qui l'oublierait compterait
6 villes construites au lieu de 3 (mesuré sur `c15Lo6e7na`). Après correction, les
`unit_built` recomptés par joueur sont **identiques aux stats archivées, 54 cellules sur 54**.

## `players` — 12 colonnes, une ligne par (partie, joueur)

Tous les joueurs, humains compris, **plus les nations et les bots**. C'est ici que vivent les
quatre champs qui n'existent que par re-simulation.

| colonne | type | note |
| --- | --- | --- |
| `game_id`, `small_id` | str, i32 | |
| `client_id` | str | `null` pour une nation ou un bot ; la jointure avec `participants` |
| `player_type` | str | `HUMAN` \| `NATION` \| `BOT` |
| `username` | str | |
| `team` | str | **le manque du J2** : `players[].team` n'est jamais archivé, `player.team()` le donne |
| `spawn_tile` | i64 | `TileRef` brut, jamais archivé (0 % sur 993 lignes) |
| `killed_by` | str | `clientID` de l'éliminateur ; `null` si l'éliminateur est un bot ou une nation |
| `death_position` | i32 | **le classement de fin de partie**, que `killed_at` n'approchait qu'imparfaitement (Spearman −0,81 / −0,96). `null` pour qui est encore vivant quand la partie se clôt |
| `final_tiles`, `alive_at_end` | i32, bool | à l'instant du `Win` |
| `month` | str | partition |

**`death_position` se compte parmi les humains ET les nations**, pas parmi les humains seuls
(`GameImpl.recordDeathPosition` : tous les joueurs vivants sauf les bots, plus un). Mesuré sur
`c15Lo6e7na` : 12 humains, et des positions allant jusqu'à 15. Deux joueurs éliminés au même tick
peuvent partager une position.

## `borders` — 6 colonnes, une ligne par (partie, observateur, voisin, 10 ticks)

La géographie (ADR-0011). **Les humains seulement comme observateur**, comme `player_ticks` ; le
voisin, lui, peut être n'importe qui.

| colonne | type | note |
| --- | --- | --- |
| `game_id`, `tick`, `month` | | `tick` est un multiple de 10, comme `player_ticks` |
| `a` | i32 | l'observateur, en `small_id`. Toujours un humain |
| `b` | i32 | le voisin, en `small_id`. **`0` = TerraNullius**, de la terre libre : de la place pour s'étendre, pas un adversaire |
| `tiles` | i32 | **mes** tuiles de frontière qui lui font face. **`0` = voisin par l'eau** (séparé de moi par une rivière de moins de 5 tuiles, donc attaquable en bateau : c'est le moteur qui le dit, pas nous). Mesuré sur une partie de 3 061 ticks : 310 lignes par l'eau contre 10 031 par la terre — le cas est réel, une adjacence naïve l'aurait manqué |

**La longueur est asymétrique, et c'est voulu** : « mes tuiles face à lui » n'est pas « ses tuiles
face à moi », parce qu'une de mes tuiles peut toucher deux des siennes. Ce qui est mesuré est mon
exposition à lui, donc une frontière entre deux humains produit **deux lignes**, une par point de
vue, avec deux valeurs qui peuvent différer.

L'**ensemble** des voisins vient de `player.nearby()`, la notion du moteur lui-même (rivières
comprises, montagnes et terre irradiée exclues) ; seule la **longueur** est recomptée par ofa.
Les agrégats correspondants sont sur `player_ticks` (`border`, `neighbours`, `border_front`,
`border_sea`, `border_longest`) : pour « était-il exposé », ils suffisent et ne coûtent aucune
ligne ; `borders` sert quand il faut savoir **lesquels**.

## Les colonnes de géographie et d'état ajoutées par l'ADR-0011

Sur `player_ticks`, en plus des 5 agrégats de frontière :

| colonne | note |
| --- | --- |
| `box_min_x`, `box_min_y`, `box_max_x`, `box_max_y` | la boîte englobante du **plus gros bloc** de territoire : la seule mesure de forme que le moteur publie. **Nulle** avant le premier calcul de blocs (6,2 % des lignes sur la partie mesurée) : le moteur ne les recalcule que tous les 20 ticks au-dessus de 100 tuiles, et seulement si le territoire a bougé |
| `decaying` | le territoire pourrit : le joueur n'est plus là |
| `last_tile_change` | tick **exact** du dernier changement territorial, donc la stagnation sans l'arrondi à 10 ticks |

Sur `players` : `spawn_x`, `spawn_y`, `spawn_shore` (départ sur une côte océanique), `map_width`,
`map_height`, `map_land`, et `disconnected_at` (tick exact, nul s'il n'a jamais décroché).

**`map_width` est la clé des coordonnées de tout le projet** : un `TileRef` s'y convertit par
`x = tile % map_width` et `y = tile / map_width`. Cela vaut pour `players.spawn_tile`,
`events.tile` **et les 91 784 lignes d'`intents`** du J2, qui ne portaient qu'un `TileRef` brut.

Sur `events` : `tile` (position de l'unité pour `unit_*`, tuile de départ pour `attack_start`),
`front` (largeur du front d'attaque : initiale sur `attack_start`, **maximale atteinte** sur
`attack_end`), et `flag` sur `attack_end` qui vaut vrai si une **retraite** était en cours — là où
l'intent `cancel_attack` ne dit que la demande.

## Ce que les tables de re-sim ne disent pas

| manque | pourquoi |
| --- | --- |
| l'état entre deux multiples de 10 ticks | l'échantillonnage de la spec. Conséquence mesurée : `max(player_ticks.tiles)` est **inférieur ou égal** à `participants.tiles_peak`, jamais supérieur (238 102 contre 238 440 sur la plus grosse partie de `c15Lo6e7na`) |
| la trajectoire des bots et des nations | humains seulement (volume) |
| les trains, les obus, les niveaux d'unité après construction | exclus de `events` |
| ce qui se passe après le premier `Win` | le runner s'arrête là, comme `WinnerReplay.ts` : c'est l'état que le serveur a archivé |
| les coordonnées d'un `tile` | toujours des `TileRef` bruts : `x = tile % largeur`, et la largeur dépend de la map **et** du commit |
| les parties dont la re-sim n'est pas valide | elles n'écrivent rien ; `uv run ofa resim --report` les liste par cause |

Et, depuis l'ADR-0011, ce qu'elles ne disent **toujours** pas : la **forme** d'un territoire. On a
le périmètre (`border`) et l'aire (`tiles`), donc la compacité, mais ni les tuiles possédées ni le
centre de masse. Les stocker à chaque échantillon serait un autre ordre de grandeur.

## Vérifier soi-même (indépendamment du code de `ofa`)

```bash
# les unites construites, recomptees depuis les evenements, contre les stats du serveur
duckdb -c "
with ev as (select a small_id, unit_type, count(*) n
            from 'data/resim/events/**/*.parquet' where kind='unit_built' group by 1,2),
     pl as (select small_id, client_id from 'data/resim/players/**/*.parquet')
select p.client_id, sum(case when ev.unit_type='City' then n end) depuis_les_evenements,
       any_value(q.units_city_built) depuis_les_stats
from ev join pl p using (small_id)
     join 'data/parquet/participants/**/*.parquet' q using (client_id)
where q.game_id='c15Lo6e7na' group by 1"

# le pic de territoire echantillonne, contre le pic exact archive
duckdb -c "
select max(t.tiles) echantillonne, any_value(p.tiles_peak) exact
from 'data/resim/player_ticks/**/*.parquet' t
join 'data/resim/players/**/*.parquet' r using (game_id, small_id)
join 'data/parquet/participants/**/*.parquet' p using (game_id, client_id)
where t.game_id='c15Lo6e7na' group by t.small_id"
```

---

## Ce que les tables du J2 ne disent pas (et où c'est comblé)

| manque | pourquoi | où |
| --- | --- | --- |
| **l'équipe d'un joueur** en mode Team | `players[].team` n'est **jamais** archivé (0 sur 2 070 777). Il est calculé par `withRecordedTeams` → `humanTeams`, qui exige le nombre de nations par défaut de la map **au commit**. `team_index` n'est là que sur 0,2 % des lignes | **livré** : `resim/players.team` |
| `spawnTile`, `killedBy`, `deathPosition` | déclarés par le moteur, **jamais archivés** (0 %). `deathPosition` est la mesure de résultat principale en FFA | **livré** : `resim/players.spawn_tile`, `.killed_by`, `.death_position` |
| les **coordonnées** d'un `tile` | `x = tile % largeur`, et la largeur dépend de la map **et** du commit | toujours manquant : aucune table ne les porte |
| `intents` pour la plupart des parties | `data/raw/meta` est sans `turns` (ADR-0004) | 520 parties aujourd'hui (`ofa fetch-full`) |
| les **hashs** par tour | 31 % des tours en portent un ; preuve de validité d'une re-sim (ADR-0003), lue dans `data/raw/full` | comptés dans `index.sqlite` (`resim.hashes_compared` / `_matched`) |
| le **classement** d'une partie | `deathPosition` absent ; `killed_at` n'en est qu'un proxy (Spearman −0,81 / −0,96 sur 2 parties) | **livré** : `resim/players.death_position` |

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

---

# `ratings` — le niveau (J4) — `data/rating/`

Une ligne par (partie, joueur identifié classé) : **1 684 613 lignes, 20,7 Mo** pour les 48 100
parties du périmètre (7 jours, mesuré le 2026-11-22). Elle ne vient ni du brut ni du moteur : c'est
une passe chronologique sur `games` × `participants` (ADR-0010).

```bash
uv run ofa rating            # la passe complete : 14,3 s, reconstruit tout
uv run ofa rating --report   # l'acceptation sur l'echantillon de confirmation
duckdb -c "select count(*) from 'data/rating/**/*.parquet'"
```

| colonne | type | note |
| --- | --- | --- |
| `game_id` | str | clé avec `public_id` (unicité vérifiée sur les 1 684 613 lignes) |
| `public_id` | str | jamais nul, jamais `REDACTED` : un joueur sans identité n'a pas de ligne |
| `rating_before` | f32 | **le niveau d'AVANT la partie**, échelle Elo (a priori 1500). La seule colonne qu'une analyse doit lire : elle ne contient rien de ce qui s'est passé dans la partie |
| `rating_after` | f32 | après mise à jour. Redondant avec le `rating_before` de la partie suivante — sauf pour la dernière partie d'un joueur, c'est-à-dire son niveau courant |
| `games_before` | i32 | parties déjà notées. **Le seul indicateur de fiabilité** : il n'y a pas d'incertitude par joueur |
| `rank` | i32 | rang de compétition dans la partie : 1 + le nombre de joueurs strictement devant. En Team il ne vaut que 1 ou 1 + (taille de l'équipe gagnante) |
| `field_size` | i32 | participants classés, **anonymes compris** |
| `month` | partition | clé de partition, pas une colonne du fichier (comme au J2 et au J3) |

Mesuré : rating de **1 194,9 à 2 138,2**, moyenne 1 511,15 (un rating à somme nulle reste autour de
son a priori), **258 354 joueurs**.

## Ce que cette table ne dit pas

- **Elle ne classe pas tout le monde.** 728 parties sur 48 100 n'ont pas de classement exploitable
  (un seul groupe, ou Team sans vainqueur archivé) ; les participants sans stats (7,2 % en FFA)
  sont hors classement ; les 244 participations anonymes ne capitalisent rien. Une partie absente
  d'ici a quand même sa ligne dans `games`.
- **En Team, le niveau est celui de l'équipe** : un passager est surévalué, et le test par décile
  de classement y est muet par construction (deux rangs seulement). Le signal Team se lit dans le
  rapport d'acceptation, pas dans un décile.
- **Pas de tranches de niveau** : l'analyse les découpe au moment où elle sait combien il lui en
  faut (`ntile(...) over (order by rating_before)`).

## Vérifier soi-même (indépendamment du code de `ofa`)

```bash
# le premier du classement est-il bien le vainqueur archive ? -> 34 359 / 34 359
duckdb -c "with r as (select * from 'data/rating/**/*.parquet'),
  p as (select game_id, public_id, won from 'data/parquet/participants/**/*.parquet'),
  g as (select game_id from 'data/parquet/games/**/*.parquet'
        where in_scope and mode='Free For All' and winner_kind='player')
select count(*), sum(p.won::int) from r join g using(game_id)
 join p using(game_id, public_id) where r.rank=1"

# le niveau d'avant-partie decroit-il avec le classement final ? (FFA) -> 1577 -> 1478
duckdb -c "with g as (select game_id, mode from 'data/parquet/games/**/*.parquet' where in_scope),
  r as (select r.rating_before, r.rank, r.field_size from 'data/rating/**/*.parquet' r
        join g using(game_id) where g.mode='Free For All'),
  d as (select rating_before, ntile(10) over (order by rank::double/field_size) decile from r)
select decile, round(avg(rating_before),1) from d group by 1 order by 1"
```
