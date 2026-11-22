"""Re-simulation a grande echelle : selection, pool de workers, classification, Parquet.

Le critere de validite est celui de l'ADR-0003, et il n'est pas negociable : tous les hashs
du record concordent, le gagnant est identique, et toute stat presente dans le record l'est
aussi. Sinon la partie est classee par cause et **n'ecrit aucune ligne** : jamais de donnees
partielles silencieuses.

Les tables vivent sous `data/resim/`, pas sous `data/parquet/` : `normalize.rebuild()` echange
le repertoire entier, ce qui detruirait tout ce qui serait range dedans.
"""

import json
import multiprocessing
import os
import re
import subprocess
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from . import engine, index, raw

# Debit mesure au J0 : 159 a 253 ticks/s par coeur, median 172. Le timeout est large (6x)
# parce qu'une partie lente n'est pas une partie en echec.
TICKS_PER_SECOND = 172
TIMEOUT_FLOOR = 120.0

# Les 9 unites suivies par le runner, memes cles que les colonnes de stats de `participants`.
UNIT_KEYS = ("city", "defp", "port", "wshp", "silo", "saml", "fact", "trade", "trans")

RUNNER = Path(__file__).resolve().parent.parent / "resim" / "ofa_runner.ts"

# Champs que l'API n'archive JAMAIS (0 sur 993 lignes joueur, 27 records de juin 2025 a
# novembre 2026). Le moteur les calcule ; leur absence du record n'est pas une divergence.
JAMAIS_ARCHIVES = ("spawnTile", "killedBy", "deathPosition")


# --------------------------------------------------------------------------------------
# Schemas (declares, jamais inferes : voir ADR-0007 et le piege du J2)
# --------------------------------------------------------------------------------------

PLAYER_TICK_SCHEMA = pa.schema(
    [
        ("game_id", pa.string()),
        ("small_id", pa.int32()),
        ("tick", pa.int32()),
        ("tiles", pa.int32()),
        ("gold", pa.int64()),
        ("gold_earned", pa.int64()),
        ("gold_trade", pa.int64()),
        ("gold_train", pa.int64()),
        ("gold_piracy", pa.int64()),
        # peak_troops atteint 2 081 249 469 dans l'archive : un i32 serait a 3 % du debordement.
        ("troops", pa.int64()),
        ("allies", pa.int16()),
        ("betrayals", pa.int16()),
        ("traitor", pa.bool_()),
        ("disconnected", pa.bool_()),
        ("doomsday", pa.bool_()),
        ("atk_out", pa.int16()),
        ("atk_out_troops", pa.int64()),
        ("atk_in", pa.int16()),
        ("atk_in_troops", pa.int64()),
        *[(f"u_{k}", pa.int32()) for k in UNIT_KEYS],
        # Geographie (ADR-0011) : le detail par voisin est dans `borders`, ici les agregats
        # qui ne coutent aucune ligne. `border` est le perimetre, `border_front` la part
        # face a un joueur, `border_sea` la part cotiere, `border_longest` le plus long front.
        # La boite englobante du plus gros bloc est la seule mesure de FORME que le moteur
        # publie : etendue, allongement, centre approche. Nulle avant le premier carre.
        ("box_min_x", pa.int32()),
        ("box_min_y", pa.int32()),
        ("box_max_x", pa.int32()),
        ("box_max_y", pa.int32()),
        ("decaying", pa.bool_()),  # territoire qui pourrit : le joueur n'est plus la
        ("last_tile_change", pa.int32()),  # au tick EXACT : la stagnation sans l'arrondi a 10
        ("border", pa.int32()),
        ("neighbours", pa.int16()),
        ("border_front", pa.int32()),
        ("border_sea", pa.int32()),
        ("border_longest", pa.int32()),
        ("month", pa.string()),
    ]
)

BORDER_SCHEMA = pa.schema(
    [
        ("game_id", pa.string()),
        ("tick", pa.int32()),
        ("a", pa.int32()),  # l'observateur, toujours un humain (comme `player_ticks`)
        ("b", pa.int32()),  # le voisin, en smallID. 0 = TerraNullius, de la terre libre
        ("tiles", pa.int32()),  # MES tuiles de frontiere qui lui font face (asymetrique)
        ("month", pa.string()),
    ]
)

EVENT_SCHEMA = pa.schema(
    [
        ("game_id", pa.string()),
        ("tick", pa.int32()),
        ("kind", pa.string()),
        ("a", pa.int32()),  # acteur, en smallID
        ("b", pa.int32()),  # cible / autre partie prenante
        ("unit_type", pa.string()),
        ("unit_id", pa.int32()),
        ("gold", pa.int64()),
        ("troops", pa.int64()),
        ("amount", pa.int64()),
        ("what", pa.string()),
        ("flag", pa.bool_()),
        # Ou l'evenement a eu lieu : position de l'unite (unit_*), tuile de depart de
        # l'attaque (attack_start). TileRef brut, comme `intents.tile` : les coordonnees se
        # calculent en SQL avec `players.map_width` (x = tile % width, y = tile / width).
        ("tile", pa.int64()),
        # Largeur du front d'une attaque : a son depart (attack_start), la plus large
        # atteinte (attack_end). Le moteur choisit ces tuiles, l'intent ne les porte pas.
        ("front", pa.int32()),
        ("params", pa.string()),  # catch-all verbatim (ADR-0007)
        ("month", pa.string()),
    ]
)

PLAYER_SCHEMA = pa.schema(
    [
        ("game_id", pa.string()),
        ("small_id", pa.int32()),
        ("client_id", pa.string()),
        ("player_type", pa.string()),
        ("username", pa.string()),
        ("team", pa.string()),  # le manque du J2 : n'existe que par re-sim
        ("spawn_tile", pa.int64()),
        ("killed_by", pa.string()),
        ("death_position", pa.int32()),  # le classement de fin de partie, idem
        ("final_tiles", pa.int32()),
        ("alive_at_end", pa.bool_()),
        # Geographie statique (ADR-0011). Les trois dernieres sont des constantes de la
        # partie, recopiees par joueur : sans elles une position de depart ne veut rien dire.
        ("spawn_x", pa.int32()),
        ("spawn_y", pa.int32()),
        ("spawn_shore", pa.bool_()),
        ("map_width", pa.int32()),
        ("map_height", pa.int32()),
        ("map_land", pa.int32()),
        ("disconnected_at", pa.int32()),  # au tick exact ; null s'il n'a jamais decroche
        ("month", pa.string()),
    ]
)

SCHEMAS = {
    "player_ticks": PLAYER_TICK_SCHEMA,
    "events": EVENT_SCHEMA,
    "players": PLAYER_SCHEMA,
    "borders": BORDER_SCHEMA,
}


def _encoding(schema: pa.Schema) -> dict:
    """Delta sur les entiers, dictionnaire sur les chaines : mesure sur 72 838 vraies lignes,
    18,47 -> 8,00 o par ligne de `player_ticks` (-57 %) et 5,75 -> 4,45 pour `events`.

    Les colonnes sont des series croissantes par joueur (tick, or cumule, troupes) : le
    dictionnaire de Parquet n'y sert a rien, l'ecart entre deux valeurs successives si. Le
    niveau 19 de zstd ajoute 7 % et ne coute rien ici (l'ecriture est 1 s contre 134 s de
    simulation), et la decompression ne depend pas du niveau.
    """
    return {
        "version": "2.6",
        "compression": "zstd",
        "compression_level": 19,
        "use_dictionary": [f.name for f in schema if pa.types.is_string(f.type)],
        "column_encoding": {
            f.name: "DELTA_BINARY_PACKED" for f in schema if pa.types.is_integer(f.type)
        },
    }


EVENT_PROMOTED = {
    "ut": "unit_type",
    "ui": "unit_id",
    "gold": "gold",
    "troops": "troops",
    "amount": "amount",
    "what": "what",
    "ok": "flag",
    "tile": "tile",
    "front": "front",
}


# --------------------------------------------------------------------------------------
# Comparaison record / re-sim (ADR-0003)
# --------------------------------------------------------------------------------------


def normalize_values(x):
    """Les bigint arrivent en chaines decimales des deux cotes -> int, jamais float.

    `null` dans une stat vaut 0 (BigIntStringSchema), donc on l'aligne aussi.
    """
    if isinstance(x, str) and re.fullmatch(r"-?\d+", x):
        return int(x)
    if isinstance(x, dict):
        return {k: normalize_values(v) for k, v in x.items()}
    if isinstance(x, list):
        return [normalize_values(v) for v in x]
    return x


def diff_stats(record: dict, out: dict) -> list[str]:
    """Compare gagnant et stats de chaque joueur. Retourne la liste des ecarts."""
    problems = []
    rec_winner = normalize_values(record["info"].get("winner"))
    run_winner = normalize_values(out.get("winner"))
    if rec_winner != run_winner:
        problems.append(f"GAGNANT record={rec_winner} resim={run_winner}")
    recomputed = normalize_values(out.get("allPlayersStats") or {})
    for p in record["info"]["players"]:
        cid = p["clientID"]
        want = normalize_values(p.get("stats") or {})
        got = recomputed.get(cid)
        if got is None:
            # Un joueur qui n'a jamais spawne n'a de stats ni dans le record ni dans la
            # re-sim : c'est un accord (ADR-0003 pt 4), pas un ecart.
            if want:
                problems.append(f"{cid}: stats dans le record mais absent de la re-sim")
            continue
        for k in sorted(set(want) | set(got)):
            if want.get(k) == got.get(k):
                continue
            # ADR-0003 pt 5 : ce que la source ne contient pas ne peut pas diverger.
            if k in JAMAIS_ARCHIVES and k not in want:
                continue
            problems.append(f"{cid}.{k}: record={want.get(k)} resim={got.get(k)}")
    return problems


def classify(record: dict, out: dict) -> tuple[str, list[str]]:
    """Statut d'une re-sim terminee, et le detail de ce qui cloche."""
    if out.get("fatalError"):
        return "runner_echec", [str(out["fatalError"])[:500]]
    h = out.get("hashes") or {}
    compared = h.get("compared") or 0
    if compared == 0:
        return "hash_absent", ["le record ne porte aucun hash : le pt 1 de l'ADR-0003 est muet"]
    if h.get("matches") != compared:
        return "hash_divergent", [
            f"{h.get('matches')}/{compared}, 1er ecart {h.get('firstMismatch')}"
        ]
    problems = diff_stats(record, out)
    if problems:
        return "stats_differentes", problems
    return "ok", []


# --------------------------------------------------------------------------------------
# NDJSON du runner -> tables Arrow
# --------------------------------------------------------------------------------------


def _int(v) -> int | None:
    """Les bigint traversent le JSON en chaines decimales : int, jamais float."""
    return None if v is None else int(v)


def tables_from_stream(path: Path, game_id: str, month: str) -> dict[str, pa.Table]:
    """Lit le NDJSON du runner et rend les quatre tables, triees pour le stockage.

    Le tri par (small_id, tick) n'est pas cosmetique : mesure J0, il ramene la ligne de
    13,6 o a 5,00 o en Parquet zstd, parce que les series deviennent monotones.
    """
    ticks: list[dict] = []
    events: list[dict] = []
    players: list[dict] = []
    borders: list[dict] = []
    with path.open() as f:
        for line in f:
            row = json.loads(line)
            kind = row.pop("k")
            if kind == "s":
                out = {
                    "game_id": game_id,
                    "small_id": row.pop("p"),
                    "tick": row.pop("t"),
                    "month": month,
                }
                for key in ("gold", "gold_earned", "gold_trade", "gold_train", "gold_piracy"):
                    out[key] = _int(row.pop(key))
                out["troops"] = _int(row.pop("troops"))
                out["atk_out_troops"] = _int(row.pop("atk_out_troops"))
                out["atk_in_troops"] = _int(row.pop("atk_in_troops"))
                out.update(row)
                ticks.append(out)
            elif kind == "e":
                out = {
                    "game_id": game_id,
                    "tick": row.pop("t"),
                    "kind": row.pop("ev"),
                    "a": row.pop("a"),
                    "b": row.pop("b"),
                    "month": month,
                }
                for src, dest in EVENT_PROMOTED.items():
                    value = row.pop(src, None)
                    if dest in ("gold", "troops", "amount", "unit_id", "tile"):
                        value = _int(value)
                    out[dest] = value
                out["params"] = json.dumps(row, separators=(",", ":")) if row else None
                events.append(out)
            elif kind == "b":
                borders.append(
                    {
                        "game_id": game_id,
                        "tick": row["t"],
                        "a": row["a"],
                        "b": row["b"],
                        "tiles": row["n"],
                        "month": month,
                    }
                )
            elif kind == "p":
                for p in row["players"]:
                    players.append({"game_id": game_id, "month": month, **p})
    tables = {
        "player_ticks": pa.Table.from_pylist(ticks, schema=PLAYER_TICK_SCHEMA),
        "events": pa.Table.from_pylist(events, schema=EVENT_SCHEMA),
        "players": pa.Table.from_pylist(players, schema=PLAYER_SCHEMA),
        "borders": pa.Table.from_pylist(borders, schema=BORDER_SCHEMA),
    }
    tables["player_ticks"] = tables["player_ticks"].sort_by(
        [("small_id", "ascending"), ("tick", "ascending")]
    )
    tables["events"] = tables["events"].sort_by([("tick", "ascending")])
    # Meme raison que player_ticks : trier par (a, tick) rend les series monotones, et
    # c'est ce que `DELTA_BINARY_PACKED` compresse.
    tables["borders"] = tables["borders"].sort_by(
        [("a", "ascending"), ("tick", "ascending"), ("b", "ascending")]
    )
    return tables


# --------------------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------------------


def progress_line(ok: int, total: int, echecs: int, watched: float, rate: float | None) -> str:
    """La ligne de suivi. Pure : ni horloge ni terminal, donc testable.

    `rate` est en parties par seconde, mesure depuis le debut du SUIVI et non du run : un
    suivi lance en cours de route donne donc une estimation juste, pas une moyenne faussee
    par les minutes qu'il n'a pas vues.
    """
    width = 24
    filled = width * ok // total if total else width
    pct = 100 * ok / total if total else 100.0
    reste = f"~{(total - ok) / rate / 60:.0f} min" if rate and ok < total else "-"
    return (
        f"[{'#' * filled}{'-' * (width - filled)}] {ok}/{total} {pct:5.1f}%  "
        f"echecs {echecs}  suivi {watched / 60:.1f} min  reste {reste}"
    )


def candidates(parquet_dir: Path) -> list[dict]:
    """Les parties re-simulables : record complet, stats validees en direct, dans le perimetre.

    `stats_agreed` est la condition de la spec pour les 500 parties ; `has_turns` dit qu'on a
    le record complet (etage 2 de l'ADR-0004) ; `in_scope` exclut special et Humans Vs Nations.
    """
    dataset = ds.dataset(parquet_dir / "games", format="parquet", partitioning="hive")
    table = dataset.to_table(
        columns=["game_id", "git_commit", "map", "num_turns", "month", "num_players"],
        filter=(
            (ds.field("has_turns") == True)  # noqa: E712 - pyarrow veut l'egalite explicite
            & (ds.field("stats_agreed") == True)  # noqa: E712
            & (ds.field("in_scope") == True)  # noqa: E712
        ),
    )
    return table.to_pylist()


def by_commit(games: list[dict]) -> dict[str, list[dict]]:
    """Grouper par commit, c'est la preparation : 6 worktrees au lieu de 514 (ADR-0008)."""
    groups: dict[str, list[dict]] = {}
    for g in games:
        groups.setdefault(g["git_commit"], []).append(g)
    return dict(sorted(groups.items(), key=lambda kv: -len(kv[1])))


def evictions(remaining: dict[str, int], lockfiles: dict[str, str]) -> tuple[list[str], set[str]]:
    """Les commits dont le lot est fini, et les lockfiles qu'il ne faut PAS evincer.

    Un lockfile reste utile tant qu'un commit en attente le partage : 5 des 6 commits de
    l'archive courante partagent le meme, donc l'evincer au premier commit fini obligerait a
    refaire un `npm ci` de 15 s pour chacun des quatre suivants.
    """
    finis = sorted(sha for sha, n in remaining.items() if n <= 0)
    utiles = {lockfiles[sha] for sha, n in remaining.items() if n > 0 and sha in lockfiles}
    return finis, utiles


def workers_default() -> int:
    """0,8 Go par worker mesure au J0 : ici c'est la RAM qui plafonne, pas les 20 coeurs."""
    cpus = os.cpu_count() or 1
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                gb = int(line.split()[1]) // (1024 * 1024)
                return max(1, min(cpus, gb))
    except OSError:
        pass
    return max(1, cpus // 2)


# --------------------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------------------

RSS_RE = re.compile(r"RESIM wall=([\d.]+)s rss_max=(\d+)kB")


def run_one(task: dict) -> dict:
    """Rejoue UNE partie dans un sous-processus node. Tourne dans un worker, donc tout est
    passe par valeur et rien n'est partage.
    """
    # Absolu, toujours : le runner tourne avec cwd=<worktree>, donc un chemin relatif
    # s'y resoudrait et donnerait un ERR_MODULE_NOT_FOUND au chemin double.
    data_dir = Path(task["data_dir"]).resolve()
    game_id = task["game_id"]
    sha = task["git_commit"]
    tmp = Path(task["tmp"])
    result = {
        "game_id": game_id,
        "status": "runner_echec",
        "detail": [],
        "rows": 0,
        "month": task["month"],
        "git_commit": sha,
    }

    body = raw.read(data_dir / "raw" / "full", game_id)
    record = json.loads(body)
    # Le brut fait foi sur le commit : le Parquet peut etre en retard d'une normalisation.
    sha = record.get("gitCommit") or ""
    result["git_commit"] = sha
    if not sha or sha == "DEV":
        return {**result, "status": "commit_absent", "detail": [f"gitCommit={sha!r}"]}
    if not engine.commit_exists(data_dir, sha):
        return {**result, "status": "commit_introuvable", "detail": [sha]}
    if not engine.is_ready(data_dir, sha):
        return {**result, "status": "commit_non_provisionne", "detail": [sha]}

    wt = engine.engine_dir(data_dir, sha)
    game_map = record["info"]["config"]["gameMap"]
    maps_dir = engine.ensure_map(data_dir, sha, engine.map_dir_name(wt, game_map))
    runner = engine.install_runner(wt, RUNNER)

    # Le runner lit un JSON sur disque : on decompresse a cote, sans jamais toucher data/raw.
    plain = tmp / f"{game_id}.json"
    plain.write_bytes(body)
    out_path = tmp / f"{game_id}.out.json"
    state_path = tmp / f"{game_id}.ndjson"
    timeout = max(TIMEOUT_FLOOR, 6 * (task["num_turns"] or 0) / TICKS_PER_SECOND)

    started = time.monotonic()
    try:
        proc = subprocess.run(
            [
                "/usr/bin/time",
                "-f",
                "RESIM wall=%es rss_max=%MkB",
                engine.NODE,
                "--import",
                "tsx",
                str(runner),
                str(plain),
                "--out",
                str(out_path),
                "--state",
                str(state_path),
            ],
            cwd=wt,
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout,
            env={**engine.ENV, "OFA_MAPS_DIR": str(maps_dir)},
        )
    except subprocess.TimeoutExpired:
        plain.unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)
        return {**result, "status": "timeout", "detail": [f"> {timeout:.0f} s"]}
    wall = time.monotonic() - started
    rss_kb = 0
    for m in RSS_RE.finditer(proc.stderr or ""):
        rss_kb = max(rss_kb, int(m.group(2)))

    if not out_path.exists():
        plain.unlink(missing_ok=True)
        return {
            **result,
            "detail": [(proc.stderr or proc.stdout or "")[-500:]],
            "wall_s": wall,
            "rss_kb": rss_kb,
        }
    out = json.loads(out_path.read_text())
    status, detail = classify(record, out)
    hashes = out.get("hashes") or {}
    summary = {
        "game_id": game_id,
        "status": status,
        "detail": detail,
        "wall_s": wall,
        "rss_kb": rss_kb,
        "ticks": out.get("ticksRun") or 0,
        "hashes_compared": hashes.get("compared") or 0,
        "hashes_matched": hashes.get("matches") or 0,
        "rows": 0,
        "month": task["month"],
        "git_commit": sha,
    }
    if status == "ok":
        tables = tables_from_stream(state_path, game_id, task["month"])
        summary["rows"] = sum(t.num_rows for t in tables.values())
        summary["tables"] = {name: t for name, t in tables.items()}
    plain.unlink(missing_ok=True)
    out_path.unlink(missing_ok=True)
    state_path.unlink(missing_ok=True)
    return summary


def run_batch(tasks: list[dict]) -> list[dict]:
    """Un lot de parties par tache : les ecritures Parquet sont faites dans le worker, donc
    les 37 000 lignes d'une partie ne traversent jamais un pipe de processus.
    """
    out_dir = Path(tasks[0]["out_dir"])
    tag = tasks[0]["tag"]
    writers: dict[tuple[str, str], pq.ParquetWriter] = {}
    summaries = []
    try:
        for task in tasks:
            summary = run_one(task)
            for name, table in summary.pop("tables", {}).items():
                if table.num_rows == 0:
                    continue
                key = (name, summary["month"])
                writer = writers.get(key)
                if writer is None:
                    directory = out_dir / name / f"month={summary['month']}"
                    directory.mkdir(parents=True, exist_ok=True)
                    writer = writers[key] = pq.ParquetWriter(
                        directory / f"part-{tag}.parquet", SCHEMAS[name], **_encoding(SCHEMAS[name])
                    )
                writer.write_table(table, row_group_size=100_000)
            summaries.append(summary)
    finally:
        for writer in writers.values():
            writer.close()
    return summaries


def resim(
    cfg,
    *,
    limit: int | None = None,
    commit: str | None = None,
    games: list[str] | None = None,
    workers: int | None = None,
    batch: int = 8,
    dry_run: bool = False,
    evict: bool = False,
) -> dict:
    """Rejoue la selection et ecrit les quatre tables. Reprend ou elle s'est arretee."""
    conn = index.connect(cfg.index_db)
    try:
        done = index.resim_done(conn)
        selected = [
            g
            for g in candidates(cfg.data_dir / "parquet")
            if (games is None or g["game_id"] in games)
            and (commit is None or g["git_commit"] == commit)
            and g["game_id"] not in done
        ]
        # Par commit (le groupement dont depend `--evict`), puis les PLUS LONGUES d'abord :
        # une geante qui demarre en dernier laisse 5 workers inoccupes jusqu'au bout. Les
        # 10 % les plus lentes pesent 28 % du temps total, l'ordre n'est donc pas neutre.
        selected.sort(key=lambda g: (g["git_commit"], -(g["num_turns"] or 0), g["game_id"]))
        if limit is not None:
            selected = selected[:limit]
        groups = by_commit(selected)
        report: dict = {
            "selected": len(selected),
            "already_done": len(done),
            "commits": {sha[:8]: len(v) for sha, v in groups.items()},
            "by_status": {},
        }
        if dry_run or not selected:
            return report

        out_dir = cfg.data_dir / "resim"
        tmp = Path(os.environ.get("TMPDIR", "/tmp")) / "ofa-resim"
        tmp.mkdir(parents=True, exist_ok=True)
        tag = f"{int(time.time())}"
        tasks = [
            {
                "data_dir": str(cfg.data_dir),
                "out_dir": str(out_dir),
                "tmp": str(tmp),
                "game_id": g["game_id"],
                "git_commit": g["git_commit"],
                "num_turns": g["num_turns"],
                "month": g["month"],
            }
            for g in selected
        ]
        batches = [tasks[i : i + batch] for i in range(0, len(tasks), batch)]
        for i, b in enumerate(batches):
            for t in b:
                t["tag"] = f"{tag}-{i:04d}"

        statuses: Counter = Counter()
        rows = 0
        started = time.monotonic()
        # Les taches sont contigues par commit (tri ci-dessus) et `pool.map` rend les lots dans
        # l'ordre : quand le compteur d'un commit tombe a zero, plus aucune tache en vol ne
        # l'utilise, et son moteur peut partir.
        restants = Counter(g["git_commit"] for g in selected)
        lockfiles = (
            {sha: engine.lockfile_sha(cfg.data_dir, sha) for sha in groups if sha} if evict else {}
        )
        report["freed"] = []
        # Python 3.14 demarre en forkserver, dont le socket est refuse par le sandbox.
        ctx = multiprocessing.get_context("fork")
        with ProcessPoolExecutor(max_workers=workers or workers_default(), mp_context=ctx) as pool:
            # `as_completed`, pas `pool.map` : map rend les resultats dans l'ordre de
            # SOUMISSION, donc avec les plus longues parties d'abord le premier lot a rendre
            # est le plus lent -- mesure : aucun avancement visible, ni a l'ecran ni dans la
            # table, pendant les 35 premieres minutes d'un run de 45.
            lots = {pool.submit(run_batch, b): b for b in batches}
            for fut in as_completed(lots):
                b = lots[fut]
                summaries = fut.result()
                for s in summaries:
                    index.resim_record(conn, s)
                    statuses[s["status"]] += 1
                    rows += s.get("rows") or 0
                conn.commit()
                for t in b:
                    restants[t["git_commit"]] -= 1
                print(
                    f"{sum(statuses.values())}/{len(tasks)} "
                    f"ok={statuses['ok']} lignes={rows} "
                    f"{(time.monotonic() - started) / 60:.1f} min",
                    flush=True,
                )
                if not evict:
                    continue
                finis, utiles = evictions(restants, lockfiles)
                for sha in finis:
                    del restants[sha]
                    freed = engine.evict(cfg.data_dir, sha, utiles)
                    report["freed"].append(freed)
                    print(f"  evince {freed['commit']} : {json.dumps(freed)}", flush=True)
        report["by_status"] = dict(statuses.most_common())
        report["rows"] = rows
        report["wall_min"] = round((time.monotonic() - started) / 60, 1)
        report["freed_mb"] = sum(
            (f["worktree_mb"] or 0) + (f["node_modules_mb"] or 0) for f in report["freed"]
        )
        return report
    finally:
        conn.close()


def engines_report(cfg, selected: list[dict]) -> dict:
    """Ce que coute la preparation des moteurs de la selection, commit par commit."""
    data_dir = cfg.data_dir
    out: dict = {"commits": [], "to_prepare": []}
    for sha, games in by_commit(selected).items():
        row = {
            "commit": sha[:8],
            "games": len(games),
            "exists": engine.commit_exists(data_dir, sha),
            "ready": engine.is_ready(data_dir, sha),
        }
        if row["exists"]:
            lock = engine.lockfile_sha(data_dir, sha)
            row["lockfile"] = lock[:8]
            row["lockfile_cached"] = (data_dir / "node_modules" / lock).exists()
            wt = engine.engine_dir(data_dir, sha)
            if wt.exists():
                # Sans --exclude, `du` compte les node_modules en liens durs comme s'ils
                # etaient a nous : le cout marginal d'un commit, c'est le code seul.
                du = subprocess.run(
                    ["du", "-sm", "--exclude=node_modules", str(wt)],
                    check=False,
                    text=True,
                    capture_output=True,
                )
                row["mb"] = int(du.stdout.split()[0]) if du.stdout else None
        out["commits"].append(row)
        if not row["ready"]:
            out["to_prepare"].append(sha)
    return out
