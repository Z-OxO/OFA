/**
 * J3 — rejoue un record OpenFront et sort : gagnant, stats finales, trajectoires et evenements.
 *
 * Copie adaptee de tests/replay/ReplayGame.ts du repo OpenFront (l'original n'est JAMAIS
 * modifie, ADR-0002). Differences avec l'original, et elles seules :
 *   1. les maps sont lues depuis $OFA_MAPS_DIR (notre cache par tree-sha), pas resources/maps ;
 *   2. on capture l'update « Win » (gagnant + stats de tous les joueurs) ;
 *   3. --out  : un JSON de resume, bigint en chaines decimales ;
 *   4. --state: un NDJSON, une ligne par echantillon d'etat et par evenement.
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
        event("unit_built", u.ownerID, null, { ...meta, lv: u.level });
        if (!u.isActive) event("unit_lost", u.ownerID, null, meta);
        continue;
      }
      if (u.ownerID !== known.owner) {
        event("unit_captured", u.ownerID, known.owner, meta);
        known.owner = u.ownerID;
      }
      if (!u.isActive && known.active) {
        known.active = false;
        event("unit_lost", known.owner, null, meta);
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
  const liveAttacks = new Map<string, { a: number; t: number | null; troops: number }>();
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
        if (known === undefined) {
          const target = atk.target().smallID();
          liveAttacks.set(id, { a: sid, t: target, troops });
          event("attack_start", sid, target, { troops });
        } else {
          known.troops = troops;
        }
      }
    }
    for (const [id, atk] of liveAttacks) {
      if (seenAttacks.has(id)) continue;
      liveAttacks.delete(id);
      event("attack_end", atk.a, atk.t, { troops: atk.troops });
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
    return {
      small_id: p.smallID(),
      client_id: cid,
      player_type: p.type(),
      username: p.name(),
      team: p.team(),
      spawn_tile: p.spawnTile() ?? s.spawnTile ?? null,
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
