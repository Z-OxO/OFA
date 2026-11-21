# RECON — J0, reconnaissance et mesures

Livrable du J0. Chaque fait porte un verdict **VÉRIFIÉE** (avec le fichier ou la commande qui le
prouve) ou **NON VÉRIFIÉE**. Les commandes sont au § 12 ; chaque requête API est journalisée dans
`scratch/requests.jsonl` (url, code, durée, octets), ce qui permet de tout recompter.

- Date : 2026-11-21.
- Clone de référence : `data/openfront/`, **SHA épinglé `1636e2049c3aee43384ac72521a142ef85314aba`**
  (`main` au 2026-11-20). Tous les extraits renvoient à ce SHA. 2,1 Go (`.git` 951 Mo,
  `resources/maps` 764 Mo). `git -C data/openfront status` est vide : jamais modifié.
- User-Agent : `ofa/0.1 (OpenFront Analyzer; contact: <alias du projet>)`. Le contact vient de
  `.ofa-contact` (hors git) ou de `OFA_CONTACT` ; il n'est jamais écrit dans le code.
- Débit : 1 requête/s, un seul client, backoff sur 429/403/5xx, pause après 5 erreurs d'affilée.
  **Aucun 429 ni 403 rencontré** sur 380 requêtes.

---

## 1. Les faits de « Ce qu'on sait déjà »

| # | Fait de la spec | Verdict | Preuve |
|---|---|---|---|
| 1a | Fenêtre de 2 jours max | **VÉRIFIÉE, incomplète** | Fenêtre de 4 j → `400 "Time range cannot exceed 2 days"`. Ne vaut qu'en mode `start`/`end`. La spec **omet** que `start` et `end` sont **obligatoires** (400 sans eux) et la borne **`offset + limit ≤ 10000`**. |
| 1b | `limit ≤ 1000` | **VÉRIFIÉE** | `limit=1001` → `400 "Too big: expected number to be <=1000"`. Défaut = 50. |
| 1c | Parcours de l'archive par `afterId`, avec marge de relecture | **VÉRIFIÉE** | `afterId=0` → ids 1..5, première partie **2025-06-04**. `Content-Range` **absent** en mode `afterId`, comme documenté. Piège trouvé : `id` est une **chaîne décimale**, à comparer en entier (`"10" < "6"` en lexicographique). Constat cohérent avec l'archivage tardif : une partie du **2025-06-01** porte un id supérieur à une du 2025-06-04. |
| 1d | La liste ne donne pas la map | **VÉRIFIÉE** | 12 clés : `id, game, start, end, type, mode, difficulty, numPlayers, maxPlayers, lobbyFillTime, playerTeams, rankedType`. La map n'est que dans `info.config.gameMap`. **Nuance** : `/public/player/:id/games` renvoie bien `map`. |
| 1e | Aucune limite de débit documentée | **VÉRIFIÉE** | Rien dans `docs/API.md` ; aucun en-tête `RateLimit-*`/`Retry-After`. `server: cloudflare` en frontal → un 429/403 reste possible sans préavis, d'où le backoff. |
| 2 | `GameRecordSchema`, ses champs | **VÉRIFIÉE** | `packages/shared/src/WireSchemas.ts:882`. Détail au § 3. |
| 2b | `publicID` null sur les vieilles parties, `"REDACTED"` si compte supprimé | **VÉRIFIÉE sur données réelles** | 3 parties du 2025-06 : **publicID null pour 100 % des joueurs** (82/82, 36/36, 37/37). Sur 7 jours récents : **0 % null**, **0,03 % REDACTED** (3 sur 9 799 lignes joueur). `publicID` est **absent du schéma Zod** : c'est un champ ajouté par l'API. |
| 3 | Stats = tableaux `*_INDEX_*`, bigint en chaînes | **VÉRIFIÉE** | `packages/engine-api/src/StatsSchemas.ts`. Détail au § 6. |
| 4 | 1 turn = 1 tick = 100 ms | **VÉRIFIÉE, avec un écart constant** | Sur 300 parties : `duration − num_turns × 0,1` vaut **+2,8 s en médiane**, max 13,9 s, soit **< 0,6 % en relatif** (2 parties sur 300 au-delà de 1 %). Donc `tick × 0,1 s` est la bonne conversion ; `duration` inclut quelques secondes de mise en route. Exact au tick près sur le record de démo (1 500 ticks / 150 s). |
| 5 | Déterminisme seulement sur le build qui a joué la partie | **VÉRIFIÉE, dans les deux sens** | Au **bon** commit : hashs **970/970**, **593/593** et **2934/2934** identiques sur 3 vraies parties. Au **mauvais** commit : divergence dès le tour 210 (21 hashs sur 150). |
| 5b | `createGameRunner(...)` ; `WinnerReplayChild.ts` pour la map | **VÉRIFIÉE, avec correction** | `createGameRunner` est dans `packages/engine/src/GameRunner.ts`, pas dans `WinnerReplay.ts` (qui l'appelle). Signature : `(gameStart, clientID \| undefined, map, callBack, editor = false)`. |
| 5c | `git fetch --depth 1 origin <sha>` récupère le commit | **VÉRIFIÉE sous condition** | Ne marche que si le SHA est atteignable depuis une ref d'`origin` : pour le commit du record de démo, GitHub répond `upload-pack: not our ref`. **Mais en pratique le problème ne se pose pas** : sur 300 parties réelles, **3 gitCommit distincts, 3 présents dans le clone → 100 % rejouables**. Idem pour deux commits de juin 2025 (`27343047`, `09a51673`), présents. |
| 6 | Purger les joueurs de `/public/players/recently-deleted` | **VÉRIFIÉE** | Tableau plat `[{publicId, deletedAt}]`, **138 entrées** le 2026-11-21, fenêtre glissante de 7 jours, `since` rejeté (400) au-delà. |
| 7 | `api.openfront.io/game/<id>` (sans `/public`) bloque les scripts | **RÉFUTÉE** | Depuis notre script : **HTTP 200, 5 464 o**, charge utile identique. L'affirmation de `docs/Replays.md` ne tient pas aujourd'hui. On reste néanmoins sur `/public/`, qui est le chemin documenté et stable. |
| 8 | Le viewer accepte-t-il un tick de départ ? | **VÉRIFIÉE : non** | `src/client/replay/ReplayViewerRoute.ts` (15 lignes) ne parse que `#replay-viewer=<gameID>` validé par `GAME_ID_REGEX`. Le seek n'existe qu'au runtime. Un record d'un autre build est servi par un hôte versionné `replay.<domain>/<gameID>` (`src/client/VersionedReplay.ts`). |

---

## 2. Découvertes non prévues par la spec

1. **`Content-Range` donne le total** (`games 0-1/2741`) : compter une journée coûte **1 requête**
   (`limit=1`), pas un parcours. C'est la base de toutes les mesures de volume du § 7.
2. **L'API fournit déjà un rating FFA, et le rating d'AVANT la partie.**
   `/public/player/:id` renvoie `ffaRating = {rating, uncertainty, tier, games, wins, peak}`, et
   chaque ligne de `/public/player/:id/games` porte
   `ffaRating = {before, after, expected, deltaResult, deltaPlace}`.
   C'est exactement la grandeur que la spec demande de calculer au J4. Limites : `null` en partie
   privée, et **aucun équivalent pour le mode Team**. À arbitrer au J4 — a priori : notre rating
   chronologique pour la couverture (Team inclus), avec le `before` officiel comme **étalon de
   validation**, ce qui est un critère d'acceptation bien plus fort que « mieux qu'une référence naïve ».
3. **`deathPosition` n'est jamais archivé.** Sur **993 lignes joueur / 27 records** étalés de juin 2025
   à novembre 2026, `deathPosition`, `killedBy` et `spawnTile` sont absents **100 %** du temps ;
   le moteur les calcule. Donc la mesure de résultat principale de la spec en FFA **n'est accessible
   que par re-simulation**. Son substitut gratuit `killedAt` (présent sur 62 % des lignes) ne
   reconstitue le classement qu'imparfaitement : **Spearman −0,81 et −0,96** sur les 2 parties
   mesurées, relation non monotone. Le **temps de survie** est donc mesurable sur toute la population,
   le **classement exact** exige la re-sim. *(n = 2 parties, 27 paires : indication, pas mesure ;
   à refaire au J3.)*
4. **`deathPosition` compte tous les participants**, bots et nations inclus : jusqu'à **52** dans une
   partie à 25 humains avec 400 bots. À normaliser avant toute comparaison entre parties.
5. **Le ranked 1v1 représente 10 % des parties publiques** (mode `"Free For All"`, 2 joueurs). La spec
   l'exclut : il faut donc filtrer `rankedType != "1v1"`, sinon 1 partie sur 10 est hors périmètre.
6. **`rankedType=ffa` ne renvoie aucune partie** sur les 7 derniers jours, alors que le ladder FFA
   existe dans `/public/leaderboard/ranked`. Le périmètre « ranked ffa » de la spec est donc vide
   aujourd'hui — à revérifier avant de coder un cas particulier.
7. **`publicGameType: "special"`** (9,7 % de l'échantillon) et **`playerTeams: "Humans Vs Nations"`**
   (8,0 %) sont des parties publiques d'un genre à part (ex. Team HvN avec 100 bots). La spec ne dit
   pas si elles entrent dans le périmètre : **question ouverte**, elles sont pour l'instant incluses
   dans les comptages « Team ».
8. **Les champs du record sont apparus par vagues.** Mesuré par sondage mensuel sur toute l'archive :
   `publicID` passe de 0 % à 100 % le **2025-09-11/12** ; `lobbyCreatedAt` et `lobbyFillTime`
   apparaissent entre novembre et décembre 2025 ; `tribes` entre juillet et août 2026 ;
   `publicGameType` entre août et septembre 2026 ; **`statsAgreed` seulement vers le 2026-10-15**.
   Conséquences : l'archive exploitable commence au **2025-09-12** (ADR-0005), et **`statsAgreed`
   n'est pas un filtre utilisable** (5 semaines d'archive seulement).
9. **L'activité a plus que doublé en un an** : 4 036 parties publiques/jour le 2025-10-02, 3 499 le
   2025-12-01, 7 221 le 2026-02-01, 7 253 le 2026-05-01, 7 839 le 2026-08-01, 9 791 le 2026-11-01.
   Extrapoler le débit actuel sur le passé surestime le volume de ~60 %.
10. **`persistentID` est exposé comme clé mais vaut `null`** sur l'API publique (57/57 joueurs testés) :
   l'interdiction de le stocker est déjà satisfaite par la source.
11. **`access-control-allow-origin: https://openfront.io`** : usage serveur-à-serveur uniquement, le
   site ne pourra jamais appeler l'API depuis le navigateur.
12. Endpoints utiles non cités par la spec : `/public/player/:id/games` (**contient `map`** et
    `result`), `/public/player/:id/sessions` (**`clientId` par partie → jointure clientID ↔ publicID**),
    `/public/leaderboard/ranked` (elo, mais **top 50 × 2 pages par ladder** seulement).

---

## 3. Structure du record

`GameRecordSchema` (`packages/shared/src/WireSchemas.ts:882`) = `AnalyticsRecordSchema:836` + `turns[]`.

```
GameRecord
├─ version    : literal "v0.0.2"   — JAMAIS incrémenté malgré les changements de schéma
├─ gitCommit  : 40 hex | "DEV"     — clé du re-jeu déterministe
├─ subdomain? / domain? / site?
├─ turns[]    : { turnNumber, intents[], hash? }   — TABLEAU CREUX (§ 9)
└─ info
   ├─ gameID, lobbyCreatedAt, visibleAt?, listed?
   ├─ config    (§ ci-dessous)
   ├─ players[] : clientID, username, clanTag(nullable), cosmetics?, isLobbyCreator?,
   │              friends?, teamIndex?, persistentID(nullable), stats?, team?
   │              + publicID (ajouté par l'API, hors schéma Zod)
   ├─ tribes?, scenario?
   ├─ start / end (ms epoch), duration (SECONDES), num_turns (ticks joués)
   ├─ winner?   : tuple (ci-dessous)
   ├─ lobbyFillTime (MILLISECONDES)
   ├─ publicGameType? : "ffa" | "team" | "special" | "hosted"
   ├─ reports?        : scrubé par l'API
   └─ statsAgreed?    : seul `true` vaut accord
```

Sur les records de **juin 2025**, `info` est réduit à `config, duration, end, gameID, num_turns,
players, start, winner` : ni `lobbyCreatedAt`, ni `lobbyFillTime`, ni `publicGameType`, ni
`statsAgreed`, ni `tribes` (vérifié sur 3 records).

`winner` (`packages/engine-api/src/Schemas.ts`, `WinnerSchema`) est un **tuple**, pas un objet :

```ts
z.union([
  z.tuple([z.literal("player"), ID]).rest(ID),
  z.tuple([z.literal("team"),   SafeString]).rest(ID),
  z.tuple([z.literal("nation"), SafeString]).rest(ID),
]).optional()
```

Observé : `["player","jD44hEhv"]` en FFA, et
`["team","Red","KQCJ8nAn","f1bKupsc","rtV1iDme","UciRvx2w","qfP8Jc6Z","ZcaL8h96"]` en Team.
Un membre déconnecté **peut manquer** du tuple (`GameImpl.isEligibleForTeamWin`) → pour « qui a
gagné » en Team, croiser `winner[1]` avec `players[].team`, pas `winner[2:]`.

`info.config` — requis : `gameMap`, `difficulty` (`Easy|Medium|Hard|Impossible`), `gameType`
(`Singleplayer|Public|Private`), `gameMode` (**`"Free For All"` | `"Team"`**, avec les espaces),
`gameMapSize` (`Compact|Normal`), `nations` (uint 1..400 **ou** `"default"|"disabled"`), `bots`
(≤ 1000), `donateGold`, `donateTroops`, `infiniteGold`, `infiniteTroops`, `instantBuild`,
`randomSpawn`. Optionnels utiles : `rankedType?` (`1v1|2v2|ffa|clanwars`), `playerTeams?` (uint
**ou** `"Duos"|"Trios"|"Quads"|"Humans Vs Nations"`), `maxPlayers?`, `publicGameModifiers?`
(13 flags), `disabledUnits?`, `winThresholdTenths?`, `customAllianceDuration?`, `doomsdayClock?`,
`overtime?`.

---

## 4. Inventaire des intents (25)

Union discriminée sur `type` : `IntentSchema`, `packages/engine-api/src/Schemas.ts` (recompté à la
main au SHA épinglé). Dans un record, chaque intent porte en plus `clientID` (stamp serveur) ;
`clientID` peut valoir `"ADMINBOT"`, qui n'est pas un joueur de `info.players`.

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
| `move_warship` | `unitIds[]`, `tile` | déplacement de navires |
| `embargo` | `targetID`, `action` (`start`\|`stop`) | embargo ciblé |
| `embargo_all` | `action` | embargo général |
| `quick_chat` | `recipient`, `quickChatKey`, `target?` | chat rapide |
| `mark_disconnected` | `isDisconnected` | interne serveur |
| `kick_player` | `targetClientID?` \| `targetPublicID?` | expulsion |
| `toggle_pause` | `paused` | pause (host / admin bot) |
| `update_game_config` | `config` (patch partiel) | le host change la config du lobby |
| `toggle_game_start_timer` | — | bascule le timer de départ |

`UnitType` : `Transport, Warship, Shell, SAMMissile, Port, Atom Bomb, Hydrogen Bomb, Trade Ship,
Missile Silo, Defense Post, SAM Launcher, City, MIRV, MIRV Warhead, Train, Factory`
(attention aux espaces dans les valeurs).

---

## 5. Inventaire des `GameUpdateType` (24) et état par joueur

`packages/engine-api/src/game/GameUpdates.ts`, **enum numérique implicite** : l'index est l'ordre de
déclaration, donc on stocke les **noms**, jamais les index. Ordre exact (0 → 23), relu à la main :

`Tile, Unit, Player, DisplayEvent, DisplayChatEvent, AllianceRequest, AllianceRequestReply,
BrokeAlliance, AllianceExpired, AllianceExtension, TargetPlayer, Emoji, Win, Hash, UnitIncoming,
BonusEvent, RailroadDestructionEvent, RailroadConstructionEvent, RailroadSnapEvent, ConquestEvent,
EmbargoEvent, SpawnPhaseEnd, GamePaused, DonateEvent`

Les événements demandés au J3 y sont : conquêtes (`ConquestEvent{conquerorId, conqueredId, gold}`),
alliances (`AllianceRequest`, `AllianceRequestReply`, `BrokeAlliance{traitorID, betrayedID}`,
`AllianceExpired`, `AllianceExtension`), dons (`DonateEvent{donationType, senderId, recipientId,
amount}`), embargos (`EmbargoEvent`), fin de phase de spawn (`SpawnPhaseEnd`), victoire
(`Win{winner, allPlayersStats}`), hash (`Hash{tick, hash}`). Les constructions/destructions passent
par `Unit`, **vidé du tableau** par `GameRunner.sendUpdates` → à lire dans `packedUnitUpdates`.
Les éliminations se lisent sur `PlayerUpdate` (`isAlive`, `killedBy`, `deathPosition`).

**État par joueur pendant la simulation.** Le runner Node expose `runner.game`, donc on lit
directement `Player` (`packages/engine/src/game/Game.ts`) et **jamais `PlayerView`** :

- territoire : `numTilesOwned()`, `tiles()`, `borderTiles()` ;
- économie : `gold()`, `goldEarned()`, `tradeGold()`, `trainGold()`, `piracyGold()`, `troops()` ;
- unités : `units()`, `units(types)`, `unitCount(type)`, `unitsConstructed(type)`, `unitsOwned(type)` ;
- diplomatie : `allies()`, `alliances()`, `isAlliedWith()`, `relation()`, `betrayals()`,
  `incomingAllianceRequests()`, `outgoingAllianceRequests()`, `expiredAlliances()` ;
- attaques : `outgoingAttacks()`, `incomingAttacks()`, `isImmune()` ;
- cibles / embargos : `targets()`, `transitiveTargets()`, `getEmbargoes()`, `tradingPartners()` ;
- état : `isAlive()`, `isTraitor()`, `isDisconnected()`, `inDoomsdayClock()`, `hasSpawned()`, `spawnTile()`.

Côté `Game` : `ticks()`, `players()`, `allPlayers()`, `playerBySmallID()`, `inSpawnPhase()`,
`stats().stats()`.

---

## 6. Décodage des stats

`packages/engine-api/src/StatsSchemas.ts`. Tous les champs sont optionnels, et `stats` peut être
`undefined` ou `{}`.

| champ | indexé par | dim. | index |
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

Scalaires : `betrayals`, `killedAt`, `peakTroops`, `finalTiles`, `killedBy`, `deathPosition`,
`spawnTile`, `kills: [{victim, tick}]`.

Valeurs = **bigint sérialisés en chaînes décimales** (`"12345"`), parfois `null` qui vaut **`0`**.
En Python : `int(v) if v is not None else 0`, **jamais `float`**.
Les stats recalculées sont indexées par **`clientID`** (`src/server/GameServer.ts:2191`).

**Taux de présence réel** (993 lignes joueur, 27 records) — les stats archivées sont **lacunaires**,
il faudra l'expliquer au J2 :

| champ | présent | champ | présent |
|---|---|---|---|
| `gold` | 94,4 % | `killedAt` | 62,0 % |
| `attacks` | 93,8 % | `kills` | 25,7 % |
| `units` | 82,8 % | `bombs` | 20,4 % |
| `tiles` / `alliances` / `peakTroops` | 79,8 % | `betrayals` | 19,7 % |
| `boats` | 77,2 % | `finalTiles` | 17,7 % |
| `conquests` | 73,1 % | `donations` | 1,0 % |
| `spawnTile` / `killedBy` / `deathPosition` | **0 %** | | |

---

## 7. Volumes et distribution

### 7.1 Volume par mode (1 requête par jour et par mode, via `Content-Range`)

7 jours, du 2026-11-14 au 2026-11-20 :

| mode | somme 7 j | moyenne/jour |
|---|---|---|
| Public FFA | 46 334 | **6 619** |
| Public Team | 18 903 | **2 700** |
| Public ranked 2v2 | 1 202 | 172 |
| Public ranked ffa | **0** | 0 |
| **Tout public** | **65 237** | **9 320** |

Périmètre ofa (hors ranked 1v1, soit 10 % de l'échantillon) : **8 388 parties/jour**.
Archive disponible depuis le **2025-06-04**, soit **535 jours**.

### 7.2 Distribution (échantillon de 300 parties, graine fixe 20261121)

Tirage : pages à offset aléatoire, pondérées par le volume du jour. **Ce n'est pas un tirage
uniforme parfait** (biais possible à l'intérieur d'une journée) ; c'est un compromis assumé pour
éviter 65 000 requêtes de détail.

- mode : FFA 68,7 %, Team 31,3 %.
- `publicGameType` : ffa 55,7 %, team 23,3 %, **special 9,7 %**, absent 11,3 %.
- `rankedType` : aucun 88,7 %, **1v1 10,0 %**, 2v2 1,3 %.
- `playerTeams` : aucun 68,7 %, puis `2` (8,3 %), **`Humans Vs Nations` (8,0 %)**, `4`, `3`, `7`, `6`,
  `Quads`, `5`, `Duos`, `Trios`.
- difficulté des nations : **Medium 92,0 %**, Hard 8,0 % (ni Easy ni Impossible observés).
- taille de map : Normal 70,3 %, Compact 29,7 %.
- joueurs humains : 17-32 → 32,0 %, 49+ → 24,3 %, 1-8 → 20,7 %, 9-16 → 12,3 %, 33-48 → 10,7 %
  (moyenne 32,7).
- **103 maps distinctes sur 300 parties** : World 7,7 %, Australia 6,3 %, puis Four Islands, Europe
  et Asia à 3,7 %. **Longue traîne : 83 maps se partagent 49 %.** L'objectif « au moins 40 maps
  analysées séparément » demandera donc un gros volume : la 20ᵉ map ne pèse que ~1,3 %, soit
  ~110 parties/jour.
- `statsAgreed` : **true 99,0 %**, false 1,0 %.
- `gitCommit` : **3 distincts sur 300 parties**, les 3 présents dans le clone.
- `num_turns` : médiane 8 066, p90 17 761, max 29 342. `duration` : médiane 810 s, p90 1 779 s.

### 7.3 Tailles mesurées

| mesure | valeur |
|---|---|
| Record complet, JSON brut | médiane ~340 Ko ; max observé 1,51 Mo (125 joueurs, 28 574 ticks) |
| Record complet, **zstd -19** | **médiane 39,2 Ko** (×2,3 à ×9,1 selon la taille) |
| Record **sans `turns`**, zstd -19 | médiane 5,3 Ko sur 27 fixtures → **estimation 36 % trop basse**, corrigée au J1 : **8,3 Ko en moyenne** sur 55 405 vraies parties (médiane 7,3 ; p90 15,5 ; max 42 Ko) |
| Latence | 0,41 s pour un record, 1,52 s pour une liste |

---

## 8. Re-simulation : le POC (critère d'acceptation)

3 vraies parties `statsAgreed: true`, rejouées **au commit exact du record** :

| gameID | map | mode | joueurs | ticks | hashs | débit | diff |
|---|---|---|---|---|---|---|---|
| `cPaLHdRvC2` | Gateway to the Atlantic | FFA | 25 | 9 701 | **970/970** | 159 t/s | **VIDE** |
| `dtQhXS8owK` | Strait of Hormuz | Team | 24 | 5 931 | **593/593** | 253 t/s | **VIDE** |
| `cDkWi2N1AT` | Great Lakes | FFA | **95** | **29 342** | **2934/2934** | 172 t/s | **VIDE** |

« Diff vide » signifie : **gagnant identique** (y compris le tuple Team à 6 membres) et **toutes les
stats archivées de tous les joueurs identiques**, après conversion des bigint en entiers. **Zéro
divergence de valeur.**

Deux écarts ont été écartés, chacun justifié par une mesure :

1. `spawnTile`, `killedBy`, `deathPosition` : calculés par le moteur, **jamais archivés**
   (0 sur 993 lignes joueur). Leur absence du record n'est pas une divergence de simulation.
2. 5 joueurs de la grosse partie n'ont **de stats ni dans le record ni dans la re-sim** (jamais
   spawné) : c'est un accord. Mon premier comparateur les comptait à tort comme écarts ; corrigé.

Les hashs, eux, sont une preuve indépendante : le serveur les a enregistrés pendant la partie en
direct, et tous concordent.

---

## 9. Pièges confirmés

1. **`turns[]` est creux** : seuls les tours avec intents **ou** un hash sont écrits. Mesuré :
   151 tours stockés pour `num_turns = 1500`. Utiliser `decompressGameRecord`.
2. `version: "v0.0.2"` n'a jamais changé : router sur `gitCommit`, pas sur `version`.
3. **`id` de la liste est une chaîne décimale** : comparer en entier, jamais en lexicographique.
4. Unités hétérogènes : `duration` en **secondes**, `lobbyFillTime` en **millisecondes**,
   `start`/`end`/`lobbyCreatedAt` en **ms epoch**, `ALLIANCE_INDEX_LONGEST_HELD` et
   `spawnImmunityDuration` en **ticks**, `maxTimerValue`/`customAllianceDuration` en **minutes**.
5. Les tableaux de stats s'allongent par ajout en fin (`boats[*][4]`, `gold[6]` absents des vieux
   records) → **toujours tester `len()`**. `conquests` était un scalaire avant d'être un tableau.
6. `username` des vieux records : jusqu'à 1000 caractères, emoji autorisés → ne pas valider avec
   `UsernameSchema` (3..27) sur l'archive.
7. Trois états distincts : absent / `null` / valeur. Dans une stat bigint, `null` vaut **`0`**.
8. Unions non discriminées à décoder au runtime : `config.nations`, `config.playerTeams`,
   `emoji.recipient`, et `winner` (discriminé par `[0]`).
9. **`mapDirName` = clé de l'enum `GameMapType` en minuscules**, par reverse-lookup de la valeur
   affichée (`src/server/MapFiles.ts`) : `"Ottoman Empire"` → `ottomanempire`. L'enum est **généré**
   dans `packages/engine-api/src/game/Maps.gen.ts` (`Key = "Valeur affichée"`), donc la table doit
   être extraite **par commit**.
10. **Partager `node_modules` par un lien symbolique corromprait la re-sim en silence** : les liens
    `node_modules/@openfront/*` sont **relatifs** (`../../packages/engine`) et résoudraient vers le
    *mauvais* commit. D'où la copie par liens durs (`cp -al`), vérifiée par `realpathSync`.
11. **Environnement** : `GID` est spécial en zsh ; `${SHA}:chemin` doit être accolé (`$SHA:resources`
    déclenche le modificateur zsh `:r`) ; le CLI `tsx` échoue dans le sandbox (`listen EPERM` sur son
    pipe IPC) → `node --import tsx` ; `git fetch`/`worktree add` échouent dans le sandbox
    (`.git` en lecture seule) → hors sandbox ; le hook `rtk` **tronque la sortie à 50 lignes**, donc
    tout comptage doit venir de `git rev-list --count`, jamais de `| wc -l` ;
    `data/raw/` est protégé par la règle `Edit(./data/raw/**)` jusqu'au `mkdir` — **à régler au J1**.

---

## 10. Projections et volume cible proposé

### 10.1 Collecte (le détail coûte 1 requête par partie, quel que soit le format)

À 8 388 parties/jour dans le périmètre, et 39,2 Ko par record complet compressé :

| période | parties | stockage complet | stockage sans `turns` | 1 req/s | 3 req/s | 10 req/s |
|---|---|---|---|---|---|---|
| 1 mois | 251 628 | 10,8 Go | 1,3 Go | 2,9 j | 1,0 j | 0,3 j |
| 6 mois | 1 526 546 | 65,5 Go | 7,7 Go | 17,7 j | 5,9 j | 1,8 j |
| 12 mois | 3 061 479 | 131,3 Go | 15,5 Go | 35,4 j | 11,8 j | 3,5 j |
| archive (535 j) | 4 487 374 | 192,5 Go | 22,7 Go | 51,9 j | 17,3 j | 5,2 j |

Les requêtes de liste sont négligeables (1 pour 1 000 parties). Le volume suppose le débit actuel
constant dans le passé : l'activité était probablement plus faible en 2025, donc c'est un **majorant**.

### 10.2 Re-simulation — c'est elle qui limite le projet

Débit mesuré : **159 à 253 ticks/s par cœur** (médiane 172). Partie médiane (8 066 ticks) ≈ **49 s
par cœur**, démarrage node+tsx inclus (1,4 s).

| machine | workers soutenables | parties/jour | 1 mois d'archive |
|---|---|---|---|
| serveur 8 cœurs / 16 Go | **8** | 14 150 | **17,8 jours** |
| local 20 cœurs / 7 Go | **7** (limite mémoire) | ~12 400 | ~20 jours |

Pic RSS mesuré : 243 Mo (1 joueur) à **782 Mo** (95 joueurs) → compter **0,8 Go par worker**.
Sur le local, c'est la **RAM** qui plafonne, pas les 20 cœurs.

Sortie de re-sim (1 point par joueur toutes les 10 ticks) : **36 641 lignes par partie en moyenne**
(médiane 21 554, max 357 175). Coût d'une ligne en Parquet zstd, mesuré avec DuckDB :
**13,6 o** sur des données aléatoires entrelacées, mais **5,00 o** sur des séries réalistes triées
par `(small_id, tick)` — le tri et la monotonie divisent la taille par **2,7**.
Retenu : **5,00 o/ligne → 179 Ko par partie en moyenne.**

| parties rejouées | sortie Parquet |
|---|---|
| 100 000 | **17,1 Go** |
| 200 000 | 34,1 Go |
| 1 mois d'archive (251 628) | 42,9 Go |

**Compression du brut : pistes explorées, rien de matériel à gagner.** Dictionnaire zstd entraîné
→ **+4,0 %** sur des records jamais vus ; archive solide (tous les records en un flux) → **+5,1 %**
au prix de l'adressage par partie ; `xz -9e` → **+3,7 %** pour 2,5× le temps. `zstd -19` par fichier
est donc le bon choix. Détail dans `PERF.md`.

### 10.3 Volume cible — arrêté

**Périmètre** : FFA et Team classiques + ranked 2v2. Exclus : ranked 1v1, `publicGameType: "special"`,
`playerTeams: "Humans Vs Nations"`. Les exclusions se recoupent (union 76 parties sur 300, somme 83),
donc le périmètre vaut **74,7 %** des parties publiques, pas 72 %.

**Borne basse de l'archive : 2025-09-12** (ADR-0005), date à laquelle `publicID` passe de 0 % à 100 %.
Les 100 jours antérieurs sont abandonnés : sans `publicID`, aucune ligne joueur n'est attribuable à
un compte, donc ni rating, ni comparaison d'un joueur avec lui-même, ni bootstrap par joueur.

**Volume intégré à partir de 6 mesures réelles** (l'activité a plus que doublé : 4 036 parties
publiques/jour le 2025-10-02 contre 9 791 le 2026-11-01, donc extrapoler le débit actuel surestimait
de 60 %) :

| | valeur |
|---|---|
| Période exploitable | **435 jours** (2025-09-12 → 2026-11-21) |
| Parties publiques sur la période | 2 979 145 |
| **Parties à ingérer** (tout le public sauf ranked 1v1, seul filtre possible depuis la liste) | **2 654 420** |
| Parties du périmètre d'**analyse** (sans `special` ni Humans Vs Nations) | 2 224 428 |

**Architecture retenue : ingestion en deux étages** (ADR-0004), imposée par les 100 Go de disque.

| poste | volume |
|---|---|
| `data/raw/meta/` — tout le public sauf ranked 1v1, en `?turns=false` | **21 Go** de données, **~26 Go sur disque** (55 405 parties réelles = 450 Mo de données pour 560 Mo occupés : surcoût de blocs sur beaucoup de petits fichiers) |
| `data/state/index.sqlite` — index publicID → parties | **~2,5 Go** (mesuré : 53 Mo pour 55 405 parties) |
| `data/raw/full/` — records complets des 100 k parties re-simulées | 4,3 Go |
| Sortie de re-sim, 100 k parties à 179 Ko | 17,1 Go |
| Parquet normalisés | ~12 Go |
| Clone OpenFront + moteurs en cache | ~9 Go |
| **Total** | **~71 Go sur 100 Go** (projection révisée au J1 avec les tailles réelles ; 54 Go était calculé sur des fixtures biaisées vers les petites parties) |

**Durée de collecte** : 30,7 jours à 1 req/s, 15,4 jours à 2 req/s, **7,7 jours à 4 req/s** (retenu,
ADR-0006). `publicGameType` étant absent de la liste, `special` ne peut pas être filtré avant
téléchargement : on l'ingère et on l'exclut à l'analyse.
Non bloquant : l'ingestion part du plus récent vers le passé, tourne en tâche de fond, et l'analyse
commence sur les données récentes dès les premières heures. **La re-simulation reste le facteur
limitant** : 100 k parties = 7 jours sur 8 cœurs.

### 10.4 A-t-on assez de données ? Oui, largement

Seuil de la spec : ≥ 300 observations **et** ≥ 100 joueurs distincts par groupe analysé.
Dans le périmètre, une partie compte **37,7 joueurs humains** en moyenne.

| | parties disponibles dans l'archive |
|---|---|
| map de rang 1 (World, 8,0 %) | ~178 700 |
| map de rang 10 (1,8 %) | ~39 700 |
| map de rang 20 (1,3 %) | ~29 800 |
| map de rang 40 (0,9 %) | ~19 900 |

Avec une re-sim de 100 000 parties **stratifiée sur 40 maps × 2 modes** : 1 250 parties par cellule,
soit **47 070 observations**, et **11 768 observations par (map, mode, tranche de niveau)** avec
4 tranches — soit **39 fois le seuil de 300**. L'objectif « au moins 40 maps analysées séparément »
est donc atteignable sans tension.

Réserve honnête : les parts des maps de rang > 10 sont estimées sur un échantillon de 224 parties,
donc la part du rang 40 (0,9 %) ne repose que sur ~2 parties observées. Même en divisant cette
estimation par 10, il resterait ~2 000 parties disponibles pour cette map, de quoi en re-simuler
quelques centaines — ce qui suffit encore au seuil.

## 11. Ce qui reste ouvert

- Périmètre **arrêté** : FFA et Team classiques + ranked 2v2 ; exclus le ranked 1v1,
  `publicGameType: "special"` et `playerTeams: "Humans Vs Nations"`.
- `rankedType=ffa` est vide aujourd'hui : à revérifier avant d'écrire le moindre cas particulier.
- La corrélation `killedAt` ↔ `deathPosition` ne repose que sur 2 parties (27 paires) : à refaire sur
  quelques centaines de parties au J3.
- Le biais du tirage à offset aléatoire à l'intérieur d'une journée n'est pas quantifié.
- Les 5,00 o/ligne de la sortie de re-sim viennent de séries synthétiques réalistes : à confirmer
  sur la vraie sortie du J3.
- `data/raw/` n'est pas inscriptible (règle `Edit(./data/raw/**)` + sandbox) : à régler au J1.
  Les 27 records du J0 sont dans `scratch/records/`.

---

## 12. Commandes

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

# Mesures API (1 req/s, journal dans scratch/requests.jsonl)
python3 -I scratch/ofa_recon.py show /player/u8hpwHpJ          # publicID, ffaRating
python3 -I scratch/ofa_recon.py afterid 0 5 3                  # parcours d'archive
python3 -I scratch/ofa_recon.py counts 7                       # volumes par jour et par mode
python3 -I scratch/ofa_recon.py sample 300                     # echantillon (graine fixe)
python3 -I scratch/ofa_recon.py fetch <gameID>...              # record complet, zstd
python3 -I scratch/analyse_sample.py                           # agregats du § 7.2
python3 -I scratch/projection.py                               # projections du § 10

# POC de re-simulation (hors sandbox : worktree + npm ci)
python3 -I scratch/resim_poc/drive.py cPaLHdRvC2 dtQhXS8owK cDkWi2N1AT

# Mesure du cout d'une ligne de sortie Parquet
duckdb -no-agent -c "COPY (SELECT ... FROM range(278740) AS t(i)) TO 'state.parquet' \
  (FORMAT parquet, COMPRESSION zstd);"
```
