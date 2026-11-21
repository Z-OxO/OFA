# RECON — J0, reconnaissance et mesures

**En cours.** Ce document est le livrable du J0. Chaque fait porte un verdict
**VÉRIFIÉE** (avec le fichier ou la commande qui le prouve) ou **NON VÉRIFIÉE**.
Les sections encore ouvertes sont listées au § 10.

- Date : 2026-11-21.
- Clone de référence : `data/openfront/`, **SHA épinglé `1636e2049c3aee43384ac72521a142ef85314aba`**
  (`main` au 2026-11-20 19:17:57 -0800). Tous les numéros de ligne et extraits ci-dessous
  renvoient à ce SHA. Le clone fait 2,1 Go (dont 951 Mo de `.git` et 764 Mo de `resources/maps`).
- Commit de production cité par la spec, `dd85bcfb8b20e599fae15298bf56db1e92947f61` : présent dans
  le clone, daté 2026-11-18 15:26:00 +0000 (`git log -1 dd85bcf…`).
- User-Agent utilisé pour les requêtes de reconnaissance :
  `ofa-recon/0.1 (OpenFront Analyzer; contact: github.com/openfrontio/OpenFrontIO/issues)`.
  **Provisoire** : à remplacer par l'alias e-mail du projet avant toute collecte.

## 1. Les faits de « Ce qu'on sait déjà »

| # | Fait de la spec | Verdict | Preuve |
|---|---|---|---|
| 1a | `GET /public/games` : fenêtre de 2 jours max | **VÉRIFIÉE, incomplète** | Fenêtre de 4 j → `HTTP 400 {"message":"Time range cannot exceed 2 days"}`. Ne vaut qu'en mode `start`/`end` ; en mode `afterId` il n'y a aucune fenêtre. **La spec omet que `start` et `end` sont obligatoires** (400 sans eux) **et la borne `offset + limit ≤ 10000`** (`docs/API.md`). |
| 1b | `limit ≤ 1000` | **VÉRIFIÉE** | `limit=1001` → `HTTP 400 "Too big: expected number to be <=1000"`. Défaut = 50. |
| 1c | Parcours de toute l'archive avec `afterId`, en relisant une marge sous le curseur | **NON VÉRIFIÉE en live** | Documenté mot pour mot dans `docs/API.md` (« A game that finishes archiving late can get an `id` below one you have already passed, so re-read a small margin below your cursor and skip games you have already seen »). La requête `afterId=0` n'a pas encore été exécutée. |
| 1d | La liste ne donne pas la map | **VÉRIFIÉE** | Clés d'un élément : `id, game, start, end, type, mode, difficulty, numPlayers, maxPlayers, lobbyFillTime, playerTeams, rankedType`. La map n'est que dans `info.config.gameMap` du détail. **Nuance** : `/public/player/:id/games`, lui, renvoie bien un champ `map`. |
| 1e | Aucune limite de débit documentée | **VÉRIFIÉE** | Rien dans `docs/API.md` ; aucun en-tête `RateLimit-*`, `Retry-After` ni `Cache-Control` observé. **Mais** `server: cloudflare` + `cf-ray` en frontal → un 429/403 peut tomber sans préavis. |
| 2 | `GameRecordSchema` dans `packages/shared/src/WireSchemas.ts`, avec `gitCommit`, `info.config`, `info.players[]`, `winner`, `duration`, `num_turns`, `statsAgreed`, `turns[]` | **VÉRIFIÉE** | Fichier présent (892 lignes). `GameRecordSchema` = `AnalyticsRecordSchema.extend({ turns })`. Détail au § 3. |
| 2b | `publicID` null sur les vieilles parties, `"REDACTED"` si compte supprimé | **VÉRIFIÉE (doc) / NON VÉRIFIÉE (observé)** | `docs/API.md` : « It is `null` for some older games. A player who has since deleted their account has `publicID` and `username` set to `"REDACTED"` ». **`publicID` n'existe pas dans le schéma Zod** : c'est un champ ajouté par l'API. Aucun cas `null`/`REDACTED` encore observé sur un vrai record. |
| 3 | Stats = tableaux indexés par `*_INDEX_*`, bigint sérialisés en chaînes | **VÉRIFIÉE** | `packages/engine-api/src/StatsSchemas.ts` (219 lignes) ; `BigIntStringSchema` accepte `string` `/^-?\d+$/`, `bigint`, et `null → 0n`. Détail au § 6. |
| 4 | 1 turn = 1 tick = 100 ms | **VÉRIFIÉE** | Sur `data/openfront/records/demoGame1.json` : `num_turns = 1500`, `duration = 150` (secondes) → exactement 0,1 s/tick. |
| 5 | Le moteur n'est déterministe que sur le build qui a joué la partie | **VÉRIFIÉE, par l'expérience** | `docs/Replays.md` : « the core is only deterministic within one build, so a record only replays on the build that played it ». Constaté : `demoGame1.json` (joué au commit `4444c9c5…`) rejoué au commit `dd85bcf…` donne **21 hash sur 150 identiques, première divergence au tour 210**. |
| 5b | `createGameRunner(...)` puis `addTurn`/`executeNextTick` ; `WinnerReplayChild.ts` montre le chargement de map | **VÉRIFIÉE, avec correction** | `createGameRunner` est dans `packages/engine/src/GameRunner.ts`, pas dans `WinnerReplay.ts` (qui l'appelle). Signature : `(gameStart, clientID \| undefined, map, callBack, editor = false)`. |
| 5c | Le commit de prod se récupère avec `git fetch --depth 1 origin <sha>` | **VÉRIFIÉE sous condition** | Ne marche que si le SHA est atteignable depuis une ref de `origin`. Pour `4444c9c5…` (commit du record de démo), GitHub répond `upload-pack: not our ref` → **certains `gitCommit` de records sont irrécupérables**, donc irrejouables. À quantifier sur de vrais records. |
| 6 | Purger chaque jour les joueurs de `/public/players/recently-deleted` | **VÉRIFIÉE** | Tableau plat `[{publicId, deletedAt}]`, **138 entrées** le 2026-11-21, du 2026-11-14 au 2026-11-21 (fenêtre glissante de 7 j). Paramètre `since` rejeté (400) au-delà de 7 j. `docs/API.md` : « Poll this daily and delete those players from any data you have collected. » |
| 7 | `api.openfront.io/game/<id>` (sans `/public`) bloque les scripts | **NON VÉRIFIÉE** | Affirmé par `docs/Replays.md` (« Cloudflare blocks scripts »), **mais** `tests/replay/ReplayGame.ts` fait un `fetch` direct sur cette URL. Non testé. Notre chemin reste `/public/game/<id>`, qui répond bien en curl (HTTP 200). |
| 8 | Le viewer accepte-t-il un tick de départ ? | **VÉRIFIÉE : non** | `src/client/replay/ReplayViewerRoute.ts` (15 lignes) ne parse que `#replay-viewer=<gameID>`, validé par `GAME_ID_REGEX` ; tout le reste du hash est pris comme id et rejeté. Le seek n'existe qu'au runtime. Un record d'un autre build est servi par un hôte versionné `replay.<domain>/<gameID>` (`src/client/VersionedReplay.ts`). |

## 2. Endpoints publics (inventaire)

Tous documentés dans `data/openfront/docs/API.md` (534 lignes).

| Endpoint | Intérêt pour ofa | Testé |
|---|---|---|
| `GET /public/games` | parcours de l'archive (`afterId`), filtres `type`, `mode`, `rankedType`, `playerTeams` | oui (sauf `afterId`) |
| `GET /public/game/:id[?turns=false]` | le record complet | oui |
| `GET /public/players/recently-deleted[?since=]` | purge obligatoire | oui |
| `GET /public/player/:playerId` | résolution `pseudo.1234` → publicID | non |
| `GET /public/player/:id/games` | historique perso, **contient `map`** et `result` | non |
| `GET /public/player/:id/sessions` | **donne le `clientId` par partie → jointure clientID ↔ publicID** | non |
| `GET /public/leaderboard/ranked?page=1\|2` | elo, peakElo, wins/losses — **top 50 × 2 pages par ladder seulement** | non |
| `GET /public/clan/...`, `/public/clanmap/history` | hors périmètre | non |
| `POST /public/identity_token/validate` | hors périmètre (preuve de propriété de compte) | non |

`access-control-allow-origin: https://openfront.io` → usage serveur-à-serveur uniquement.
`persistentID` est exposé comme clé mais vaut `null` sur 57/57 joueurs du record testé : l'interdiction
de stocker `persistentID` est déjà satisfaite par la source.

## 3. Structure du record

`GameRecordSchema` (`packages/shared/src/WireSchemas.ts:882`) = `AnalyticsRecordSchema:836` + `turns[]`.

```
GameRecord
├─ version    : literal "v0.0.2"   — JAMAIS incrémenté malgré les changements de schéma : ne pas s'en servir
├─ gitCommit  : 40 hex | "DEV"     — requis ; clé du re-jeu déterministe
├─ subdomain? / domain? / site?
├─ turns[]    : { turnNumber, intents[], hash? }  — TABLEAU CREUX (§ 9)
└─ info
   ├─ gameID, lobbyCreatedAt, visibleAt?, listed?
   ├─ config   : voir ci-dessous
   ├─ players[]: clientID, username, clanTag(nullable), cosmetics?, isLobbyCreator?,
   │             friends?, teamIndex?, persistentID(nullable), stats?, team?
   │             + publicID (ajouté par l'API, hors schéma Zod)
   ├─ tribes?, scenario?
   ├─ start / end (ms epoch), duration (SECONDES), num_turns (ticks joués)
   ├─ winner?  : tuple, voir ci-dessous
   ├─ lobbyFillTime (MILLISECONDES)
   ├─ publicGameType? : "ffa" | "team" | "special" | "hosted"
   ├─ reports?        : scrubé par l'API
   └─ statsAgreed?    : seul `true` vaut accord ; `false` et absent = non vérifié
```

`winner` (`packages/engine-api/src/Schemas.ts`, `WinnerSchema`) est un **tuple hétérogène**, pas un objet :

```ts
z.union([
  z.tuple([z.literal("player"), ID]).rest(ID),
  z.tuple([z.literal("team"),   SafeString]).rest(ID),
  z.tuple([z.literal("nation"), SafeString]).rest(ID),
]).optional()
```

- FFA humain → `["player", clientID]` ; FFA gagné par une nation → `["nation", nom]` ;
- Équipe → `["team", nomEquipe, ...clientIDs éligibles]`. Un membre déconnecté **peut manquer** du
  tuple (`GameImpl.ts`, `isEligibleForTeamWin`) → pour « qui a gagné » en Team, croiser `winner[1]`
  avec `players[].team`, ne pas se fier à `winner[2:]`.
- Absent = pas de gagnant (ranked annulé ; `result = incomplete` côté API).

`info.config` (`GameConfigSchema`) — requis : `gameMap`, `difficulty` (`Easy|Medium|Hard|Impossible`),
`gameType` (`Singleplayer|Public|Private`), `gameMode` (**`"Free For All"` | `"Team"`**, avec les
espaces), `gameMapSize` (`Compact|Normal`), `nations` (uint 1..400 **ou** `"default"|"disabled"`),
`bots` (≤ 1000), `donateGold`, `donateTroops`, `infiniteGold`, `infiniteTroops`, `instantBuild`,
`randomSpawn`. Optionnels utiles : `rankedType?` (`1v1|2v2|ffa|clanwars`), `playerTeams?`
(uint **ou** `"Duos"|"Trios"|"Quads"|"Humans Vs Nations"`), `maxPlayers?`, `publicGameModifiers?`
(13 flags), `disabledUnits?`, `winThresholdTenths?`, `customAllianceDuration?`, `doomsdayClock?`,
`overtime?`.

## 4. Inventaire des intents (25)

Union discriminée sur `type` : `IntentSchema`, `packages/engine-api/src/Schemas.ts`.
Dans un record, chaque intent porte en plus `clientID` (stamp serveur) = l'auteur.
`clientID` peut valoir `"ADMINBOT"` (`ADMIN_BOT_CLIENT_ID`), qui n'est **pas** un joueur de `info.players`.

| `type` | champs | ce que ça encode |
|---|---|---|
| `spawn` | `tile` | pose du spawn en phase de spawn |
| `attack` | `targetID` (nullable), `troops` (float, nullable) | attaque terrestre ; `targetID=null` = terre non revendiquée |
| `cancel_attack` | `attackID` | annule une attaque en cours |
| `boat` | `troops`, `dst` | débarquement / attaque navale |
| `cancel_boat` | `unitID` | rappelle un bateau |
| `allianceRequest` | `recipient` | demande d'alliance |
| `allianceReject` | `requestor` | refus d'alliance |
| `allianceExtension` | `recipient` | prolongation d'alliance |
| `breakAlliance` | `recipient` | trahison (compte dans `betrayals`) |
| `targetPlayer` | `target` | désigne une cible aux alliés |
| `emoji` | `recipient` (ID \| `"AllPlayers"`), `emoji` (0..59) | émoji ; index dans `flattenedEmojiTable` |
| `donate_gold` | `recipient`, `gold` (nullable) | don d'or |
| `donate_troops` | `recipient`, `troops` (nullable) | don de troupes |
| `build_unit` | `unit` (UnitType), `tile`, `rocketDirectionUp?`, `amount?` | construction |
| `upgrade_structure` | `unit`, `unitId`, `amount?` | amélioration d'une structure |
| `delete_unit` | `unitId` | suppression / vente d'unité |
| `move_warship` | `unitIds[]`, `tile` | déplacement de navires de guerre |
| `embargo` | `targetID`, `action` (`start`\|`stop`) | embargo ciblé |
| `embargo_all` | `action` | embargo général |
| `quick_chat` | `recipient`, `quickChatKey`, `target?` | chat rapide |
| `mark_disconnected` | `isDisconnected` | interne serveur |
| `kick_player` | `targetClientID?` \| `targetPublicID?` | expulsion |
| `toggle_pause` | `paused` | pause (host / admin bot) |
| `update_game_config` | `config` (patch partiel) | le host change la config du lobby |
| `toggle_game_start_timer` | — | bascule le timer de départ |

`UnitType` (`GameTypes.ts`) : `Transport, Warship, Shell, SAMMissile, Port, Atom Bomb, Hydrogen Bomb,
Trade Ship, Missile Silo, Defense Post, SAM Launcher, City, MIRV, MIRV Warhead, Train, Factory`
(attention aux espaces dans les valeurs).

## 5. Inventaire des `GameUpdateType` (24) et état par joueur

`packages/engine-api/src/game/GameUpdates.ts`. **Enum numérique implicite** : l'index est l'ordre de
déclaration, donc on stocke les **noms**, jamais les index (une réorganisation rendrait les données illisibles).

Ordre exact (0 → 23) : `Tile, Unit, Player, DisplayEvent, DisplayChatEvent, AllianceRequest,
AllianceRequestReply, BrokeAlliance, AllianceExpired, AllianceExtension, TargetPlayer, Emoji, Win,
Hash, UnitIncoming, BonusEvent, RailroadDestructionEvent, RailroadConstructionEvent,
RailroadSnapEvent, ConquestEvent, EmbargoEvent, SpawnPhaseEnd, GamePaused, DonateEvent`.

Les événements demandés par le J3 s'y trouvent : conquêtes (`ConquestEvent{conquerorId, conqueredId, gold}`),
alliances (`AllianceRequest`, `AllianceRequestReply`, `BrokeAlliance{traitorID, betrayedID}`,
`AllianceExpired`, `AllianceExtension`), dons (`DonateEvent{donationType, senderId, recipientId, amount}`),
embargos (`EmbargoEvent`), fin de phase de spawn (`SpawnPhaseEnd`), victoire (`Win{winner, allPlayersStats}`),
hash de vérification (`Hash{tick, hash}`). Les constructions/destructions passent par `Unit` —
**vidé du tableau** par `GameRunner.sendUpdates`, donc à lire dans `packedUnitUpdates`.
Les éliminations se lisent sur `PlayerUpdate` (`isAlive`, `killedBy`, `deathPosition`).

**Données par joueur pendant la simulation.** Le runner Node expose `runner.game`, donc on lit
directement `Player` du moteur (`packages/engine/src/game/Game.ts`) et **jamais `PlayerView`** (couche
client qui reconstruit l'état depuis les diffs) :

- territoire : `numTilesOwned()`, `tiles()`, `borderTiles()` ;
- économie : `gold()`, `goldEarned()`, `tradeGold()`, `trainGold()`, `piracyGold()`, `troops()` ;
- unités : `units()`, `units(types)`, `unitCount(type)`, `unitsConstructed(type)`, `unitsOwned(type)` ;
- diplomatie : `allies()`, `alliances()`, `isAlliedWith()`, `relation()`, `betrayals()`,
  `incomingAllianceRequests()`, `outgoingAllianceRequests()`, `expiredAlliances()` ;
- attaques : `outgoingAttacks()`, `incomingAttacks()`, `isImmune()` ;
- cibles / embargos : `targets()`, `transitiveTargets()`, `getEmbargoes()`, `tradingPartners()` ;
- état : `isAlive()`, `isTraitor()`, `isDisconnected()`, `inDoomsdayClock()`, `hasSpawned()`, `spawnTile()`.

Côté `Game` : `ticks()`, `players()`, `allPlayers()`, `playerBySmallID()`, `inSpawnPhase()`, `stats().stats()`.

## 6. Décodage des stats

`packages/engine-api/src/StatsSchemas.ts`. Tous les champs sont optionnels, et `stats` lui-même peut
être `undefined` ou `{}`.

| champ | indexé par | dimension | index |
|---|---|---|---|
| `attacks` | `ATTACK_INDEX_*` | 4 | SENT=0, RECV=1, CANCEL=2, MAX_RECV=3 |
| `tiles` | `TILE_INDEX_*` | 3 | PEAK=0, DRAWDOWN_PEAK=1, DRAWDOWN_TROUGH=2 |
| `alliances` | `ALLIANCE_INDEX_*` | 6 | FORMED=0, BROKEN_BY_OTHER=1, EXPIRED=2, HELD_TO_END=3, PEAK_CONCURRENT=4, LONGEST_HELD=5 (**ticks**) |
| `conquests` | `PLAYER_INDEX_*` | 3 | HUMAN=0, NATION=1, BOT=2 |
| `boats` | `BOAT_INDEX_*` par `"trade"`/`"trans"` | 5 | SENT=0, ARRIVE=1, CAPTURE=2, DESTROY=3, LOST=4 *(ajouté après)* |
| `bombs` | `BOMB_INDEX_*` par `"abomb"`/`"hbomb"`/`"mirv"`/`"mirvw"` | 3 | LAUNCH=0, LAND=1, INTERCEPT=2 |
| `units` | `OTHER_INDEX_*` par `"city"`/`"defp"`/`"port"`/`"wshp"`/`"silo"`/`"saml"`/`"fact"` | 5 | BUILT=0, DESTROY=1, CAPTURE=2, LOST=3, UPGRADE=4 |
| `gold` | `GOLD_INDEX_*` | 7 | WORK=0, WAR=1, TRADE=2, STEAL=3, TRAIN_SELF=4, TRAIN_OTHER=5, DONATE_RECV=6 *(ajouté après)* |
| `donations` | `DONATION_INDEX_*` | 2 | GOLD_RECV=0, GOLD_RECV_BROKE=1 |

Scalaires : `betrayals`, `killedAt`, `peakTroops`, `finalTiles`, `killedBy` (clientID ou `null`),
`deathPosition`, `spawnTile`, `kills: [{victim, tick}]`.

Valeurs = **bigint sérialisés en chaînes décimales** (`"12345"`), parfois `null` qui vaut **`0`**.
En Python : `int(v) if v is not None else 0`, **jamais `float`**.
Les stats recalculées par le moteur sont indexées par **`clientID`**
(`src/server/GameServer.ts:2191` : `winner?.allPlayersStats[player.clientID]`), ce qui rend le diff
avec `info.players[i].stats` direct.

## 7. Re-simulation : ce qui existe et ce qu'on mesure

`tests/replay/ReplayGame.ts` (280 lignes, script npm `replay:game`) **est déjà** un harnais headless :
il télécharge un record, le parse avec `GameRecordSchema.safeParse` (tolérant aux vieux records),
le décompresse (`decompressGameRecord`), charge la map via `NodeGameMapLoader` + `loadMapFiles` +
`loadTerrainMap`, construit le jeu (`createGame`, `createNationsForGame`) et boucle
`addTurn` / `executeNextTick` en comparant les hashs. → **on l'adapte, on ne le réécrit pas** (ADR-0002).

Trois adaptations seulement, dans `scratch/resim_poc/ofa_runner.ts` :
maps lues depuis `$OFA_MAPS_DIR`, capture de l'update `Win`, sortie JSON avec bigint en chaînes.
L'URL `/public/` s'obtient sans toucher au code : `--api-base https://api.openfront.io/public`.

**Aucun bundle n'est nécessaire** : les packages exportent le TS brut
(`packages/*/package.json` → `"exports": { "./*": "./src/*.ts" }`) et tout tourne sous `tsx`.
Mesures et chiffres : `docs/PERF.md`.

## 8. Mesures

Mesures locales (toutes reproductibles, commandes au § 11) :

| Mesure | Valeur |
|---|---|
| Clone OpenFront complet | 2,1 Go (`.git` 951 Mo, `resources/maps` 764 Mo) |
| Nombre de maps | **148**, 5 fichiers chacune (`manifest.json`, `map.bin`, `map4x.bin`, `map16x.bin`, `thumbnail.webp`) |
| Worktree sans `resources/maps` (sparse) | 393 Mo |
| `npm ci --ignore-scripts` | **15,4 s**, `node_modules` **362 Mo**, pic RSS 704 Mo |
| Une map matérialisée (pangaea) | 1,3 Mo (map.bin 977 Ko + map4x 244 Ko + map16x 61 Ko + manifest 2,9 Ko) |
| Re-sim de 1 500 ticks (1 joueur, Pangaea) | **3,4 s** de simulation, 4,84 s de mur, pic RSS **243 Mo** |
| Surcoût de démarrage node + tsx | ≈ **1,4 s** par processus |
| Commits OpenFront : 7 j / 30 j / 90 j / 365 j | 57 / 272 / 852 / 2 611 |
| …dont touchant `package-lock.json` | 1 / 4 / 14 / **82** |
| …dont touchant `resources/maps` | 3 / 27 / 62 / **223** |

Mesures API (12 requêtes réelles, 1 req/s, **pas encore loggées en JSONL** — à refaire à l'étape suivante) :

| Mesure | Valeur |
|---|---|
| `GET /public/games?…&limit=5` | HTTP 200, **1,52 s**, 1 322 o |
| `GET /public/game/<id>` (57 joueurs, 12 239 turns) | HTTP 200, **0,41 s**, **804 156 o** |
| idem `?turns=false` | **48 314 o** (**×16,6 plus petit**) |
| `/public/players/recently-deleted` | 138 entrées sur 7 jours |
| Parties **publiques Team** le 2026-11-18 (1 jour) | **2 741** (`Content-Range: games 0-1/2741`) |

`Content-Range` donne le total : **compter une journée coûte 1 requête** (`limit=1`), pas un parcours.
C'est la base de la projection de volume du § 10.

## 9. Pièges confirmés

1. **`turns[]` est creux** : seuls les tours avec intents **ou** un hash sont écrits.
   Mesuré : `demoGame1.json` stocke **151 tours pour `num_turns = 1500`**. Utiliser `decompressGameRecord`.
2. `version: "v0.0.2"` n'a jamais changé malgré les évolutions de schéma : router sur `gitCommit`, pas sur `version`.
3. Unités hétérogènes : `duration` en **secondes**, `lobbyFillTime` en **millisecondes**,
   `start`/`end`/`lobbyCreatedAt` en **ms epoch**, `ALLIANCE_INDEX_LONGEST_HELD` et
   `spawnImmunityDuration` en **ticks**, `maxTimerValue`/`customAllianceDuration` en **minutes**.
4. Les tableaux de stats s'allongent par ajout en fin : `boats[*][4]`, `gold[6]` sont absents des
   vieux records → **toujours tester `len()`**. `conquests` était un scalaire avant d'être un tableau.
5. `username` des vieux records : jusqu'à 1000 caractères, emoji autorisés → ne pas valider avec
   `UsernameSchema` (3..27) sur l'archive.
6. Trois états distincts : absent / `null` / valeur. Dans une stat bigint, `null` vaut **`0`**, pas « inconnu ».
7. Unions non discriminées à décoder au runtime : `config.nations`, `config.playerTeams`,
   `emoji.recipient`, et `winner` (discriminé par `[0]`).
8. `mapDirName` = **clé de l'enum `GameMapType` en minuscules**, trouvée par reverse-lookup de la
   valeur affichée (`src/server/MapFiles.ts`) : `"Ottoman Empire"` → `ottomanempire`.
   Il faut donc extraire la table `valeur affichée → dossier` par commit.
9. **Environnement** : `GID` est une variable spéciale en zsh ; `${SHA}:chemin` doit être accolé
   (`$SHA:resources` déclenche le modificateur zsh `:r`) ; le CLI `tsx` échoue dans le sandbox
   (`listen EPERM` sur son pipe IPC) → utiliser `node --import tsx` ; `git fetch`/`worktree add`
   échouent dans le sandbox (`.git` en lecture seule) → hors sandbox ; le hook `rtk` **tronque la
   sortie à 50 lignes**, donc tout comptage doit venir de `git rev-list --count`, pas de `| wc -l`.

## 10. Ce qui reste à faire (J0 non terminé)

Bloqué sur : l'alias e-mail du User-Agent, mon publicID, les specs du serveur.

- [ ] `afterId` exercé en live, avec vérification de la marge de relecture.
- [ ] Journal JSONL de toutes les requêtes (une mesure = une ligne reproductible).
- [ ] Latences et tailles par mode (FFA, Team, ranked ffa, ranked 2v2), petite et grosse partie,
      **et taille après `zstd`** (c'est elle qui compte pour le stockage).
- [ ] Répartition sur 7 jours : parties par mode / map / taille / difficulté, `gitCommit` distincts,
      % `statsAgreed`, % `publicID` null, % `"REDACTED"` — par comptage `Content-Range` + échantillon
      aléatoire à graine fixe pour les champs qui n'existent que dans le détail.
- [ ] % de `gitCommit` de records réellement récupérables depuis GitHub (cause d'échec du § 1, ligne 5c).
- [ ] POC de re-sim sur **3 vraies parties** (FFA, Team, grosse) au bon commit, avec diff vide sur le
      gagnant et les stats de chaque joueur. **C'est le critère d'acceptation du J0.**
- [ ] Débit de re-sim par cœur, nombre de workers soutenable sous 7 Go, projection stockage et temps
      pour 1, 6 et 12 mois, volume cible proposé.
- [ ] Vérifier dans le viewer 2 moments d'une vraie partie.

## 11. Commandes

```bash
# Clone de référence et SHA épinglé
git clone https://github.com/openfrontio/OpenFrontIO.git data/openfront
git -C data/openfront rev-parse HEAD

# Churn des commits (jamais `| wc -l` : le hook rtk tronque à 50 lignes)
for d in 7 30 90 365; do
  echo "${d}j commits=$(git -C data/openfront rev-list --count --since=${d}.days HEAD)" \
       "lock=$(git -C data/openfront rev-list --count --since=${d}.days HEAD -- package-lock.json)" \
       "maps=$(git -C data/openfront rev-list --count --since=${d}.days HEAD -- resources/maps)"
done

# Worktree du moteur à un commit, sans les 764 Mo de maps (hors sandbox)
SHA=dd85bcfb8b20e599fae15298bf56db1e92947f61
git -C data/openfront worktree add --no-checkout "$PWD/data/engines/$SHA" "$SHA"
cd data/engines/$SHA
git sparse-checkout init --no-cone && git sparse-checkout set '/*' '!/resources/maps/' && git checkout
PATH=/usr/bin:$PATH /usr/bin/time -f 'wall=%es rss=%MkB' npm ci --ignore-scripts

# Matérialiser une seule map, indexée par son tree-sha (accolade obligatoire en zsh)
TREE=$(git -C data/openfront rev-parse "${SHA}:resources/maps/pangaea")
mkdir -p data/maps/${TREE}/pangaea
git -C data/openfront archive --format=tar "${TREE}" | tar -x -C data/maps/${TREE}/pangaea

# Re-sim (node --import tsx : le CLI tsx ne passe pas le sandbox)
cp scratch/resim_poc/ofa_runner.ts data/engines/$SHA/tests/replay/
cd data/engines/$SHA
PATH=/usr/bin:$PATH OFA_MAPS_DIR=$PWD/../../maps/${TREE} \
  /usr/bin/time -f 'wall=%es rss=%MkB' \
  node --import tsx tests/replay/ofa_runner.ts <gameID|record.json> --out out.json

# Mesures API (UA provisoire ; 1 req/s, sleep 1 entre chaque)
UA='ofa-recon/0.1 (OpenFront Analyzer; contact: github.com/openfrontio/OpenFrontIO/issues)'
B=https://api.openfront.io/public
W='start=2026-11-18T00:00:00Z&end=2026-11-19T00:00:00Z'
curl -sS -A "$UA" -w '\n%{http_code} %{time_total}s %{size_download}B\n' "$B/games?$W&limit=5"
curl -sS -A "$UA" -D- "$B/games?$W&type=Public&mode=Team&limit=1"   # Content-Range = total
curl -sS -A "$UA" -w '\nHTTP=%{http_code} T=%{time_total}s SIZE=%{size_download}B\n' "$B/game/<id>"
curl -sS -A "$UA" "$B/game/<id>?turns=false" | wc -c
curl -sS -A "$UA" -w '\nHTTP=%{http_code}\n' "$B/games?$W&limit=1001"               # 400
curl -sS -A "$UA" -w '\nHTTP=%{http_code}\n' "$B/games?start=2026-11-14T00:00:00Z&end=2026-11-18T00:00:00Z&limit=2"  # 400
curl -sS -A "$UA" "$B/players/recently-deleted" | python3 -I -c "import sys,json;o=json.load(sys.stdin);print(len(o),list(o[0]))"
```
