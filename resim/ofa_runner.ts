/**
 * J3 — rejoue un record OpenFront et sort : gagnant, stats finales, trajectoires et evenements.
 *
 * Copie adaptee de tests/replay/ReplayGame.ts du repo OpenFront (l'original n'est JAMAIS
 * modifie, ADR-0002). Differences avec l'original, et elles seules :
 *   1. les maps sont lues depuis $OFA_MAPS_DIR (notre cache par tree-sha), pas resources/maps ;
 *   2. on capture l'update « Win » (gagnant + stats de tous les joueurs) ;
 *   3. --out  : un JSON de resume, bigint en chaines decimales ;
 *   4. --state: un NDJSON, une ligne par echantillon d'etat et par evenement ;
 *   5. la geographie (ADR-0011) : le graphe de voisinage a chaque echantillon, et la
 *      position de depart. Le moteur ne l'archive jamais, elle n'existe que par re-sim.
 *
 * Ce fichier est copie dans <worktree>/tests/replay/ a l'execution : c'est de la que
 * @openfront/* et ../perf/fullgame/NodeGameMapLoader resolvent vers le BON commit.
 *
 * Notes TypeScript pour un debutant :
 *   - `import type`, les annotations `: Type` et les `interface` disparaissent a
 *     l'execution ; tsx compile le TS a la volee, il n'y a pas d'etape de build.
 *   - `async`/`await` : une fonction `async` rend une Promise ; `await` attend sa valeur.
 *     Le `main().catch(...)` final attrape toute erreur asynchrone.
 *   - `??` = « si null ou undefined, alors » ; `??=` n'affecte que si vide.
 *   - `Map<K, V>` est un dictionnaire ordonne ; `Set<T>` un ensemble. Les deux gardent
 *     l'identite des cles (un nombre reste un nombre), contrairement a un objet JS.
 *   - les stats du moteur sont des `bigint` (entiers sans limite, notes `1n`) : JSON ne
 *     sait pas les ecrire, d'ou `bigintToString` ci-dessous.
 */
import { PlayerInfo, PlayerType, UnitType } from "@openfront/engine-api/game/GameTypes";
import {
  GameUpdateType,
  HashUpdate,
  UnitUpdate,
  WinUpdate,
} from "@openfront/engine-api/game/GameUpdates";
import { GameStartInfo } from "@openfront/engine-api/Schemas";
import { loadTerrainMap } from "@openfront/engine-lib/game/TerrainMapLoader";
import { PseudoRandom } from "@openfront/engine-lib/PseudoRandom";
import { simpleHash } from "@openfront/engine-lib/Util";
import { EngineConfig } from "@openfront/engine/configuration/EngineConfig";
import { Executor } from "@openfront/engine/execution/ExecutionManager";
import { createGame } from "@openfront/engine/game/GameImpl";
import { createNationsForGame } from "@openfront/engine/game/NationCreation";
import { GameRunner } from "@openfront/engine/GameRunner";
import { loadMapFiles } from "@openfront/shared/GameMapLoader";
import { decompressGameRecord, toWireGameStartInfo } from "@openfront/shared/SharedUtil";
import { GameRecord, GameRecordSchema } from "@openfront/shared/WireSchemas";
import { execSync } from "child_process";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { NodeGameMapLoader } from "../perf/fullgame/NodeGameMapLoader";

const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

/** Un point d'etat tous les SAMPLE_EVERY ticks : la spec du J3 demande 10. */
const SAMPLE_EVERY = 10;

/**
 * Tampon reutilise pour les 4 voisins cardinaux d'une tuile. `neighbors4` ecrit dedans et
 * rend le nombre de voisins valides (3 sur un bord de carte, 2 dans un coin) : on evite
 * ainsi d'allouer un tableau par tuile de frontiere, et il y en a des dizaines de milliers
 * par echantillon. Meme motif que `NEIGHBOR_SCRATCH` dans PlayerImpl.ts du moteur.
 */
const NEIGHBOR_SCRATCH: number[] = [0, 0, 0, 0];

/**
 * Les 9 types d'unites qui ont une colonne de stat dans `participants` (RECON § 6) :
 * meme vocabulaire des deux cotes, donc la trajectoire se recoupe avec les stats finales.
 */
const TRACKED_UNITS: [string, UnitType][] = [
  ["city", UnitType.City],
  ["defp", UnitType.DefensePost],
  ["port", UnitType.Port],
  ["wshp", UnitType.Warship],
  ["silo", UnitType.MissileSilo],
  ["saml", UnitType.SAMLauncher],
  ["fact", UnitType.Factory],
  ["trade", UnitType.TradeShip],
  ["trans", UnitType.TransportShip],
];

/**
 * Adaptateur par **version d'interface**, jamais par commit : `PackedUnitUpdates` n'existe pas
 * dans les commits anciens de l'archive, et avant lui les updates d'unites arrivaient en clair
 * dans `updates[Unit]`. Un `import` statique ferait planter le runner au chargement (vecu :
 * ERR_MODULE_NOT_FOUND au commit 79aed26b). On detecte, on ne suppose pas.
 *
 * Note TypeScript : `await import(...)` est un import *dynamique*, evalue a l'execution, donc
 * il peut echouer sans empecher le fichier de se charger — contrairement a un `import` en tete.
 */
/**
 * Les champs qu'on lit sur les updates d'evenement. Tous optionnels : d'un commit a l'autre un
 * champ peut ne pas exister, et c'est la liste exacte de ce dont le runner depend.
 */
interface EventFields {
  conquerorId?: string;
  conqueredId?: string;
  gold?: bigint;
  requestorID?: number;
  recipientID?: number;
  request?: { requestorID?: number; recipientID?: number };
  accepted?: boolean;
  traitorID?: number;
  betrayedID?: number;
  player1ID?: number;
  player2ID?: number;
  playerID?: number;
  allianceID?: number;
  targetID?: number;
  embargoedID?: number;
  senderId?: string;
  recipientId?: string;
  donationType?: string;
  amount?: bigint;
  event?: string;
  player?: string;
  troops?: number;
}

type UnpackUnits = (packed?: Float64Array, timers?: Uint32Array) => UnitUpdate[];

async function loadUnpackUnits(): Promise<UnpackUnits | null> {
  try {
    const mod = await import("@openfront/engine-lib/game/PackedUnitUpdates");
    return mod.unpackUnitUpdates as UnpackUnits;
  } catch {
    return null;
  }
}

interface Options {
  source: string; // gameID ou chemin d'un record deja telecharge
  apiBase: string;
  out: string | null;
  state: string | null;
  teams: number[] | null;
}

function parseArgs(argv: string[]): Options {
  const opts: Options = {
    source: "",
    apiBase: "https://api.openfront.io/public",
    out: null,
    state: null,
    teams: null,
  };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    const next = () => {
      const v = argv[++i];
      if (v === undefined) throw new Error(`valeur manquante pour ${arg}`);
      return v;
    };
    switch (arg) {
      case "--api-base":
        opts.apiBase = next();
        break;
      case "--out":
        opts.out = next();
        break;
      case "--state":
        opts.state = next();
        break;
      case "--teams":
        opts.teams = next()
          .split(",")
          .map((v) => parseInt(v, 10));
        break;
      default:
        if (arg.startsWith("--")) throw new Error(`argument inconnu : ${arg}`);
        opts.source = arg;
    }
  }
  if (opts.source === "") {
    throw new Error("usage: ofa_runner.ts <gameID | record.json> [--out f.json] [--state f.ndjson]");
  }
  return opts;
}

async function loadRecord(opts: Options): Promise<GameRecord> {
  let raw: unknown;
  if (fs.existsSync(opts.source)) {
    raw = JSON.parse(fs.readFileSync(opts.source, "utf8"));
  } else {
    const url = `${opts.apiBase}/game/${opts.source}`;
    console.error(`fetch ${url}`);
    const res = await fetch(url, {
      headers: { "User-Agent": process.env.OFA_USER_AGENT ?? "ofa/0.1" },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status} sur ${url}`);
    raw = await res.json();
  }
  const parsed = GameRecordSchema.safeParse(raw);
  if (!parsed.success) {
    // Les vieux records precedent des resserrages de schema : on rejoue tel quel.
    console.error("ATTENTION: le record ne valide pas GameRecordSchema actuel");
    return raw as GameRecord;
  }
  return parsed.data;
}

// Les stats du moteur sont des bigint ; JSON ne sait pas les ecrire. L'API les serialise
// en chaines decimales : on fait pareil, jamais en float.
function bigintToString(_key: string, value: unknown): unknown {
  return typeof value === "bigint" ? value.toString() : value;
}

/** Ecriture NDJSON tamponnee : pas de backpressure a gerer, et la memoire reste bornee. */
class Sink {
  private readonly fd: number | null;
  private lines: string[] = [];
  public count = 0;

  constructor(filePath: string | null) {
    this.fd = filePath === null ? null : fs.openSync(filePath, "w");
  }

  write(row: Record<string, unknown>): void {
    if (this.fd === null) return;
    this.lines.push(JSON.stringify(row, bigintToString));
    this.count++;
    if (this.lines.length >= 2000) this.flush();
  }

  flush(): void {
    if (this.fd === null || this.lines.length === 0) return;
    fs.writeSync(this.fd, this.lines.join("\n") + "\n");
    this.lines = [];
  }

  close(): void {
    this.flush();
    if (this.fd !== null) fs.closeSync(this.fd);
  }
}

async function main(): Promise<void> {
  const opts = parseArgs(process.argv.slice(2));
  console.debug = () => {}; // coupe le log par tick

  const record = decompressGameRecord(await loadRecord(opts));
  const info = record.info;

  let gitCommit = "unknown";
  try {
    gitCommit = execSync("git rev-parse HEAD", { cwd: PROJECT_ROOT, encoding: "utf8" }).trim();
  } catch {
    /* pas un checkout git */
  }
  const commitMatch = record.gitCommit === gitCommit;

  const players = info.players.map((p, i) => {
    if (opts.teams === null) return p;
    const teamIndex = opts.teams[i];
    if (teamIndex === undefined || Number.isNaN(teamIndex)) {
      throw new Error("--teams doit donner un index par joueur");
    }
    return { ...p, teamIndex };
  });

  const gameStart: GameStartInfo = toWireGameStartInfo({
    gameID: info.gameID,
    lobbyCreatedAt: info.lobbyCreatedAt,
    config: info.config,
    players,
    tribes: info.tribes,
  });

  // Reproduit createGameRunner() avec un chargeur de map sur disque.
  const mapsDir = process.env.OFA_MAPS_DIR ?? path.join(PROJECT_ROOT, "resources/maps");
  const config = new EngineConfig(info.config, false);
  const terrain = await loadTerrainMap(
    await loadMapFiles(
      new NodeGameMapLoader(mapsDir),
      info.config.gameMap,
      info.config.gameMapSize,
    ),
  );
  const random = new PseudoRandom(simpleHash(gameStart.gameID));
  const humans = gameStart.players.map(
    (p) =>
      new PlayerInfo(
        p.username,
        PlayerType.Human,
        p.clientID,
        random.nextID(),
        p.isLobbyCreator ?? false,
        p.clanTag,
        p.friends ?? [],
        p.teamIndex ?? null,
      ),
  );
  const nations = createNationsForGame(
    gameStart,
    terrain.nations,
    terrain.additionalNations,
    humans.length,
    random,
  );
  const game = createGame(
    humans,
    nations,
    terrain.gameMap,
    terrain.miniGameMap,
    config,
    terrain.teamGameSpawnAreas,
  );

  const sink = new Sink(opts.state);
  const computedHashes = new Map<number, number>();
  let win: WinUpdate | null = null;
  let fatalError: string | undefined;

  // Les ids d'evenement sont des PlayerID (chaines) ; tout ce qu'on ecrit est en smallID.
  // Les nations et les bots naissent a l'init, donc une seule reconstruction suffit, mais
  // on la refait sur un manque plutot que de le supposer.
  let smallByID = new Map<string, number>();
  const rebuildIndex = () => {
    smallByID = new Map(game.allPlayers().map((p) => [p.id(), p.smallID()]));
  };
  const small = (pid: string | null | undefined): number | null => {
    if (pid === null || pid === undefined) return null;
    let s = smallByID.get(pid);
    if (s === undefined) {
      rebuildIndex();
      s = smallByID.get(pid);
    }
    return s ?? null;
  };

  const event = (kind: string, actor: number | null, other: number | null, extra = {}) => {
    sink.write({ k: "e", t: game.ticks(), ev: kind, a: actor, b: other, ...extra });
  };

  /**
   * Les trains et les obus sont du mouvement, pas du jeu : 2 464 evenements sur 5 090 dans la
   * premiere partie mesuree, aucune stat en face, aucun usage d'analyse. Tout le reste passe,
   * bombes et missiles d'interception compris.
   */
  const IGNORED_UNITS = new Set<string>([UnitType.Train, UnitType.Shell]);

  // Transitions d'unites : GameRunner.sendUpdates VIDE updates[Unit] apres compactage, donc
  // on decompacte comme le fait le client (GameView.ts). On n'ecrit que les transitions.
  // Un id d'unite n'est JAMAIS reutilise : une premiere apparition est donc une construction,
  // et un id deja connu n'en est jamais une. C'est ce qui evite le piege mesure sur
  // c15Lo6e7na : la meme unite peut etre vue deux fois dans un seul tick, inactive puis
  // active, et un etat qu'on oublierait ferait compter 6 villes construites au lieu de 3.
  const unitState = new Map<number, { owner: number; active: boolean }>();
  const onUnits = (unitUpdates: UnitUpdate[]) => {
    for (const u of unitUpdates) {
      if (IGNORED_UNITS.has(u.unitType)) continue;
      const meta = { ut: u.unitType, ui: u.id };
      const known = unitState.get(u.id);
      if (known === undefined) {
        unitState.set(u.id, { owner: u.ownerID, active: u.isActive });
        // `pos` est la tuile de l'unite. Une construction a deja son intent `build_unit`,
        // mais une perte ou une capture n'a aucun intent en face : sans ca on ne sait pas OU
        // le front a cede. TileRef brut, comme `intents.tile` (x = ref % map_width).
        event("unit_built", u.ownerID, null, { ...meta, lv: u.level, tile: u.pos });
        if (!u.isActive) event("unit_lost", u.ownerID, null, { ...meta, tile: u.pos });
        continue;
      }
      if (u.ownerID !== known.owner) {
        event("unit_captured", u.ownerID, known.owner, { ...meta, tile: u.pos });
        known.owner = u.ownerID;
      }
      if (!u.isActive && known.active) {
        known.active = false;
        event("unit_lost", known.owner, null, { ...meta, tile: u.pos });
      }
    }
  };

  // `null` = ce commit est anterieur au compactage des unites (voir loadUnpackUnits).
  const unpackUnits = await loadUnpackUnits();

  const runner = new GameRunner(
    game,
    new Executor(game, gameStart.gameID, undefined, gameStart.tribes?.map((t) => t.name)),
    (gu) => {
      if ("errMsg" in gu) {
        fatalError = `${gu.errMsg}\n${gu.stack ?? ""}`;
        return;
      }
      // Un GameUpdateType absent du commit rejoue vaut `undefined`, et `updates[undefined]`
      // aussi : toute lecture passe donc par `each`, qui ne suppose l'existence de rien.
      const updatesByType = gu.updates as unknown as Record<number, unknown[] | undefined>;
      const each = <T>(type: GameUpdateType | undefined, fn: (e: T) => void): void => {
        const slot = type === undefined ? undefined : updatesByType[type as number];
        if (slot === undefined) return;
        for (const e of slot as T[]) fn(e);
      };

      each<HashUpdate>(GameUpdateType.Hash, (hu) => computedHashes.set(hu.tick, hu.hash));
      // Premier Win seulement : c'est celui qui a clos la partie.
      each<WinUpdate>(GameUpdateType.Win, (w) => {
        win ??= w;
      });

      if (opts.state === null) return;
      // Interface recente : les unites arrivent compactees. Ancienne : en clair.
      onUnits(
        gu.packedUnitUpdates !== undefined && unpackUnits !== null
          ? unpackUnits(gu.packedUnitUpdates, gu.packedMissileTimers)
          : ((updatesByType[GameUpdateType.Unit as number] ?? []) as UnitUpdate[]),
      );
      each<EventFields>(GameUpdateType.ConquestEvent, (e) => {
        event("conquest", small(e.conquerorId), small(e.conqueredId), { gold: e.gold });
      });
      // Attention : les updates d'alliance, de cible et d'embargo portent deja des smallID
      // (`requestorID: number`, AllianceRequestImpl.toUpdate), alors que conquete, don et
      // bonus portent des PlayerID (des chaines). D'ou `small()` sur les seconds seulement.
      each<EventFields>(GameUpdateType.AllianceRequest, (e) => {
        event("alliance_request", e.requestorID ?? null, e.recipientID ?? null);
      });
      each<EventFields>(GameUpdateType.AllianceRequestReply, (e) => {
        event("alliance_reply", e.request?.requestorID ?? null, e.request?.recipientID ?? null, {
          ok: e.accepted,
        });
      });
      each<EventFields>(GameUpdateType.BrokeAlliance, (e) => {
        event("alliance_broken", e.traitorID ?? null, e.betrayedID ?? null);
      });
      each<EventFields>(GameUpdateType.AllianceExpired, (e) => {
        event("alliance_expired", e.player1ID ?? null, e.player2ID ?? null);
      });
      each<EventFields>(GameUpdateType.AllianceExtension, (e) => {
        event("alliance_extension", e.playerID ?? null, null, { ui: e.allianceID });
      });
      each<EventFields>(GameUpdateType.DonateEvent, (e) => {
        event("donate", small(e.senderId), small(e.recipientId), {
          what: e.donationType,
          amount: e.amount,
        });
      });
      each<EventFields>(GameUpdateType.EmbargoEvent, (e) => {
        event("embargo", e.playerID ?? null, e.embargoedID ?? null, { what: e.event });
      });
      each<EventFields>(GameUpdateType.TargetPlayer, (e) => {
        event("target", e.playerID ?? null, e.targetID ?? null);
      });
      each<EventFields>(GameUpdateType.BonusEvent, (e) => {
        event("bonus", small(e.player), null, { gold: e.gold, troops: e.troops });
      });
      each<unknown>(GameUpdateType.SpawnPhaseEnd, () => {
        event("spawn_phase_end", null, null);
      });
    },
  );
  runner.init();
  rebuildIndex();

  const recordedHashes = new Map<number, number>();
  for (const turn of record.turns) {
    if (turn.hash !== null && turn.hash !== undefined) {
      recordedHashes.set(turn.turnNumber, turn.hash);
    }
  }

  // Etat observe tick par tick, sur les objets vivants : ni PlayerUpdate ni AttackUpdate
  // n'arrivent en clair (les deux sont compactes), et le moteur n'expose pas de
  // decompacteur d'attaques. Un diff suffit, et il donne le tick exact.
  const wasAlive = new Set<number>();
  const liveAttacks = new Map<
    string,
    { a: number; t: number | null; troops: number; front: number; retreating: boolean }
  >();
  // La grille de tuiles ne change pas d'identite pendant la partie : on la prend une fois.
  const map = game.map();
  // smallID d'un voisin -> nombre de mes tuiles de frontiere qui lui font face. Reutilise
  // d'un joueur a l'autre (`clear()`), parce qu'il est rempli des milliers de fois.
  const facing = new Map<number, number>();
  const sampleAndDiff = () => {
    const tick = game.ticks();
    const seenAttacks = new Set<string>();
    for (const p of game.allPlayers()) {
      const sid = p.smallID();
      const alive = p.isAlive() && p.hasSpawned();
      if (alive) wasAlive.add(sid);
      else if (wasAlive.delete(sid)) event("elimination", sid, null);
      if (!alive) continue;
      for (const atk of p.outgoingAttacks()) {
        const id = atk.id();
        seenAttacks.add(id);
        const troops = atk.troops();
        const known = liveAttacks.get(id);
        // `borderSize()` est la largeur du front de CETTE attaque, et `sourceTile()` l'endroit
        // d'ou elle part : ni l'un ni l'autre n'est dans l'intent, c'est le moteur qui choisit
        // les tuiles. `retreating()` dit qu'une retraite est en cours — l'intent
        // `cancel_attack` dit qu'elle a ete demandee, pas qu'elle s'est faite.
        const front = atk.borderSize();
        if (known === undefined) {
          const target = atk.target().smallID();
          liveAttacks.set(id, {
            a: sid,
            t: target,
            troops,
            front,
            retreating: atk.retreating(),
          });
          event("attack_start", sid, target, { troops, front, tile: atk.sourceTile() });
        } else {
          known.troops = troops;
          // On garde le front le PLUS LARGE atteint : le front initial est deja dans
          // attack_start, et c'est l'ampleur maximale qui dit la taille de l'offensive.
          if (front > known.front) known.front = front;
          if (atk.retreating()) known.retreating = true;
        }
      }
    }
    for (const [id, atk] of liveAttacks) {
      if (seenAttacks.has(id)) continue;
      liveAttacks.delete(id);
      event("attack_end", atk.a, atk.t, {
        troops: atk.troops,
        front: atk.front,
        ok: atk.retreating,
      });
    }
    if (tick % SAMPLE_EVERY !== 0) return;
    for (const p of game.players()) {
      // Les trajectoires ne portent que les humains : un bot ou une nation n'est pas un
      // sujet d'analyse, et les inclure doublerait le volume (voir docs/DATA.md).
      if (p.type() !== PlayerType.Human || !p.hasSpawned()) continue;
      const row: Record<string, unknown> = {
        k: "s",
        t: tick,
        p: p.smallID(),
        tiles: p.numTilesOwned(),
        gold: p.gold(),
        gold_earned: p.goldEarned(),
        gold_trade: p.tradeGold(),
        gold_train: p.trainGold(),
        gold_piracy: p.piracyGold(),
        troops: p.troops(),
        allies: p.allies().length,
        betrayals: p.betrayals(),
        traitor: p.isTraitor(),
        disconnected: p.isDisconnected(),
        doomsday: p.inDoomsdayClock(),
      };
      let atkOut = 0;
      let atkOutTroops = 0;
      for (const a of p.outgoingAttacks()) {
        atkOut++;
        atkOutTroops += a.troops();
      }
      let atkIn = 0;
      let atkInTroops = 0;
      for (const a of p.incomingAttacks()) {
        atkIn++;
        atkInTroops += a.troops();
      }
      row.atk_out = atkOut;
      row.atk_out_troops = atkOutTroops;
      row.atk_in = atkIn;
      row.atk_in_troops = atkInTroops;
      for (const [name, type] of TRACKED_UNITS) row[`u_${name}`] = p.unitCount(type);

      // --- Geographie (ADR-0011) ---------------------------------------------------
      // Deux sources, parce qu'elles ne disent pas la meme chose.
      //
      // 1. L'ENSEMBLE des voisins vient de `p.nearby()`, la notion du moteur lui-meme :
      //    les proprietaires des tuiles voisines de ma frontiere (terre franchissable, et
      //    pas de la terre libre irradiee), PLUS les joueurs separes de moi par une
      //    riviere de moins de 5 tuiles, que je peux attaquer en bateau. Reimplementer
      //    l'adjacence a la main manquerait ces derniers, et silencieusement : c'est la
      //    lecon du J3 (on prend `player.team()`, on ne recalcule pas `humanTeams`).
      // 2. La LONGUEUR de chaque front vient d'une passe sur mes tuiles de frontiere.
      //    `borderTiles()` est un ensemble que le moteur tient a jour en continu, il n'y a
      //    rien a recalculer ; pour chacune on regarde ses 4 voisins cardinaux. C'est le
      //    motif de `sharesBorderWith` (PlayerImpl.ts), etendu a tous les voisins au lieu
      //    de s'arreter au premier, et avec les memes regles de terrain que `computeNearby`.
      //
      // La longueur est volontairement asymetrique : « mes tuiles face a lui » n'est pas
      // « ses tuiles face a moi », parce qu'une de mes tuiles peut toucher deux des siennes.
      // C'est bien mon exposition a lui qu'on veut mesurer.
      const mine = p.smallID();
      facing.clear();
      let borderSea = 0;
      let borderFront = 0;
      for (const tile of p.borderTiles()) {
        let touchesWater = false;
        let touchesPlayer = false;
        // Dedoublonnage sans allocation : au plus 3 proprietaires etrangers sur 4 voisins.
        let seen0 = -1;
        let seen1 = -1;
        let seen2 = -1;
        const count = map.neighbors4(tile, NEIGHBOR_SCRATCH);
        for (let i = 0; i < count; i++) {
          const n = NEIGHBOR_SCRATCH[i];
          if (map.isWater(n)) {
            touchesWater = true;
            continue;
          }
          // Une montagne ne se conquiert jamais (`conquer` leve dessus), et de la terre
          // libre irradiee non plus tant que la retombee dure : ni l'une ni l'autre n'est
          // une frontiere. Memes deux tests que `computeNearby` dans le moteur.
          if (map.isImpassable(n)) continue;
          const owner = map.ownerID(n);
          if (owner === 0 && map.hasFallout(n)) continue;
          if (owner === mine || owner === seen0 || owner === seen1 || owner === seen2) continue;
          if (seen0 < 0) seen0 = owner;
          else if (seen1 < 0) seen1 = owner;
          else seen2 = owner;
          facing.set(owner, (facing.get(owner) ?? 0) + 1);
          // smallID 0 = TerraNullius, de la terre libre : de la place, pas un voisin.
          if (owner !== 0) touchesPlayer = true;
        }
        if (touchesWater) borderSea++;
        if (touchesPlayer) borderFront++;
      }
      // Les voisins que le moteur voit et que l'adjacence terrestre ne donne pas : ceux
      // d'en face d'une riviere. Ils sortent avec une longueur de front de 0, et c'est
      // exactement ce que ce 0 veut dire dans la table (docs/DATA.md).
      for (const other of p.nearby()) {
        const owner = other.smallID();
        if (!facing.has(owner)) facing.set(owner, 0);
      }
      let neighbours = 0;
      let longest = 0;
      for (const [owner, tiles] of facing) {
        if (owner !== 0) {
          neighbours++;
          if (tiles > longest) longest = tiles;
        }
        sink.write({ k: "b", t: tick, a: mine, b: owner, n: tiles });
      }
      // La boite englobante du plus gros bloc de territoire : la seule mesure de FORME que
      // le moteur expose (il calcule les blocs a chaque tick pour la decomposition, mais
      // n'en publie que celle-la). Donne l'etendue, l'allongement et le centre approche.
      // Teste la valeur, pas `=== null` : l'interface declare `| null`, mais le champ n'est
      // AFFECTE qu'au premier passage de PlayerExecution, donc il vaut `undefined` avant.
      // Mesure : le re-jeu plantait a 0,7 s sur `cXvvUGMfYS` avec un test strict.
      const box = p.largestClusterBoundingBox;
      row.box_min_x = box ? box.min.x : null;
      row.box_min_y = box ? box.min.y : null;
      row.box_max_x = box ? box.max.x : null;
      row.box_max_y = box ? box.max.y : null;
      // `isDecaying` = territoire qui pourrit (joueur absent) ; `lastTileChange` = le dernier
      // changement territorial au tick EXACT, donc la stagnation sans l'arrondi a 10 ticks.
      //
      // Deux accesseurs ecartes APRES mesure, pas par oubli : `tradingPartners()` vaut 102 sur
      // 104 joueurs (`canTrade` ne teste que l'absence d'embargo — le vrai commerce, c'est
      // `gold_trade`), et `doomsdayClockTicks()` compterait pour un etat jamais atteint
      // (`doomsday` est faux sur les 10 518 824 lignes du J3).
      row.decaying = p.isDecaying();
      row.last_tile_change = p.lastTileChange();
      row.border = p.borderTiles().size;
      row.neighbours = neighbours;
      row.border_front = borderFront;
      row.border_sea = borderSea;
      row.border_longest = longest;
      sink.write(row);
    }
  };

  const t0 = performance.now();
  let firstMismatch: number | null = null;
  let matches = 0;
  let compared = 0;
  let ticksRun = 0;
  for (const turn of record.turns) {
    runner.addTurn(turn);
    if (!runner.executeNextTick()) {
      console.error(`tick en echec au turn ${turn.turnNumber}: ${fatalError}`);
      break;
    }
    ticksRun++;
    if (opts.state !== null) sampleAndDiff();
    const c = computedHashes.get(turn.turnNumber);
    const r = recordedHashes.get(turn.turnNumber);
    if (c !== undefined && r !== undefined) {
      compared++;
      if (c === r) matches++;
      else if (firstMismatch === null) firstMismatch = turn.turnNumber;
    }
    // Comme WinnerReplay.ts : on s'arrete au premier Win, c'est l'etat archive par le serveur.
    if (win !== null) break;
  }
  const wallSeconds = (performance.now() - t0) / 1000;

  // Les trois champs que l'API n'archive jamais (ADR-0003) : ils n'existent que par re-sim.
  const stats = game.stats().stats();
  const perPlayer = game.allPlayers().map((p) => {
    const cid = p.clientID();
    // `PlayerStats` est entierement optionnel dans le schema : on le lit comme un
    // dictionnaire quelconque, ce qui evite d'importer le type pour trois champs.
    const s: Record<string, unknown> = (cid === null ? undefined : stats[cid]) ?? {};
    // `spawnTile()` est vide pour un joueur qui n'a jamais pose son premier carre ; le
    // moteur garde alors la valeur dans ses stats. `?? undefined` normalise les deux.
    const spawnTile = (p.spawnTile() ?? (s.spawnTile as number | undefined)) ?? undefined;
    return {
      small_id: p.smallID(),
      client_id: cid,
      player_type: p.type(),
      username: p.name(),
      team: p.team(),
      spawn_tile: spawnTile ?? null,
      // Position de depart en coordonnees, et nature du terrain : la map seule ne suffit
      // pas a savoir si un joueur a demarre au centre, dans un coin ou sur une cote.
      spawn_x: spawnTile === undefined ? null : map.x(spawnTile),
      spawn_y: spawnTile === undefined ? null : map.y(spawnTile),
      spawn_shore: spawnTile === undefined ? null : map.isOceanShore(spawnTile),
      // Constantes de la partie, recopiees sur chaque joueur : sans elles une position de
      // depart ne veut rien dire, et il n'y a pas de table « partie » du cote de la re-sim.
      map_width: map.width(),
      map_height: map.height(),
      map_land: map.numLandTiles(),
      disconnected_at: p.disconnectedAtTick(),
      killed_by: s.killedBy ?? null,
      death_position: s.deathPosition ?? null,
      final_tiles: p.numTilesOwned(),
      alive_at_end: p.isAlive(),
    };
  });
  sink.write({ k: "p", players: perPlayer });
  sink.close();

  // Meme logique que packages/engine/src/WinnerReplay.ts.
  const w = win as WinUpdate | null;
  const result = {
    gameID: info.gameID,
    recordGitCommit: record.gitCommit,
    runGitCommit: gitCommit,
    commitMatch,
    map: info.config.gameMap,
    gameMode: info.config.gameMode,
    numPlayers: info.players.length,
    recordNumTurns: info.num_turns,
    storedTurns: record.turns.length,
    ticksRun,
    wallSeconds,
    stateRows: sink.count,
    hashes: { compared, matches, firstMismatch },
    fatalError: fatalError ?? null,
    winner: w !== null ? w.winner : undefined,
    allPlayersStats: w !== null ? w.allPlayersStats : stats,
    stoppedOnWin: w !== null,
  };

  const json = JSON.stringify(result, bigintToString, 1);
  if (opts.out !== null) fs.writeFileSync(opts.out, json);
  else console.log(json);
  console.error(
    `${info.gameID}: ${ticksRun} ticks en ${wallSeconds.toFixed(1)}s, ` +
      `hash ${matches}/${compared}, mismatch=${firstMismatch}, lignes=${sink.count}`,
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
