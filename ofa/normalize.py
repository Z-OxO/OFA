"""Normalisation des records bruts en trois tables Parquet : la source de vérité du projet.

Reconstruction totale à chaque exécution (mesuré : 885 records/s/cœur, soit ~1 min pour les
56 000 records du J1). Un parcours incrémental serait du code et des bugs pour rien, et la
reconstruction est aussi ce qui propage la purge des comptes supprimés jusqu'aux Parquet :
`ofa purge-deleted` touche `data/raw/`, `ofa normalize` doit suivre.

Principe tenu partout : **colonnes chaudes + catch-all verbatim**. Le schéma du record change
avec les versions du moteur (`disableNPCs` a disparu, `startingGold` est apparu, les intents
`troop_ratio` et `allianceRequestReply` n'existent plus), donc tout ce qui n'a pas de colonne
est conservé tel quel en JSON (`games.config_json`, `intents.params`) au lieu d'être perdu.

Les schémas pyarrow sont déclarés explicitement, jamais inférés : avec une inférence par lot,
une colonne entièrement nulle dans un lot donne le type `Null` et `int64` au lot suivant, et
les deux fichiers deviennent illisibles ensemble par `read_parquet('**')`.
"""

import json
import shutil
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from . import raw

TABLES = ("games", "participants", "intents")

# --------------------------------------------------------------------------------------
# Stats : indices relus un par un dans packages/engine-api/src/StatsSchemas.ts.
# Les tableaux sont tronqués à droite dans les records (un index au-delà de len() vaut 0),
# et une clé de famille absente alors que `stats` existe vaut 0 : le moteur n'écrit une clé
# que quand il l'incrémente.
# --------------------------------------------------------------------------------------

ARRAYS: dict[str, tuple[str, ...]] = {
    "attacks": ("sent", "recv", "cancel", "max_recv"),
    "tiles": ("peak", "drawdown_peak", "drawdown_trough"),
    "alliances": (
        "formed",
        "broken_by_other",
        "expired",
        "held_to_end",
        "peak_concurrent",
        "longest_held_ticks",
    ),
    "conquests": ("human", "nation", "bot"),
    "gold": ("work", "war", "trade", "steal", "train_self", "train_other", "donate_recv"),
    "donations": ("gold_recv", "gold_recv_broke"),
}

GROUPS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "boats": (("trade", "trans"), ("sent", "arrive", "capture", "destroy", "lost")),
    "bombs": (("abomb", "hbomb", "mirv", "mirvw"), ("launch", "land", "intercept")),
    "units": (
        ("city", "defp", "port", "wshp", "silo", "saml", "fact"),
        ("built", "destroy", "capture", "lost", "upgrade"),
    ),
}

# Compteurs : absents = 0.
SCALAR_COUNTERS = {"betrayals": "betrayals", "peakTroops": "peak_troops"}
# Pas des compteurs : absents = NULL (0 voudrait dire « tué au tick 0 », « 0 tuile à la fin »).
SCALAR_NULLABLE = {"killedAt": "killed_at", "finalTiles": "final_tiles"}

# `killedBy`, `deathPosition` et `spawnTile` sont déclarés par le moteur mais n'ont JAMAIS été
# archivés (0 sur 2 070 777 lignes). Ils restent volontairement inconnus ici : le jour où l'API
# les archive, la normalisation le signale au lieu de les ignorer.
KNOWN_STATS = set(ARRAYS) | set(GROUPS) | set(SCALAR_COUNTERS) | set(SCALAR_NULLABLE) | {"kills"}


def stat_columns() -> list[str]:
    """Les 86 colonnes entières de stats, dans un ordre stable."""
    cols: list[str] = []
    for family, names in ARRAYS.items():
        cols += [f"{family}_{n}" for n in names]
    for family, (kinds, names) in GROUPS.items():
        cols += [f"{family}_{k}_{n}" for k in kinds for n in names]
    cols += list(SCALAR_COUNTERS.values()) + list(SCALAR_NULLABLE.values())
    return cols


STAT_COLUMNS = stat_columns()


def _big(value) -> int:
    """bigint sérialisé en chaîne décimale -> int. `null` vaut 0. Jamais `float`."""
    if value is None:
        return 0
    if isinstance(value, bool):
        raise ValueError(f"valeur de stat booleenne : {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)  # leve ValueError si ce n'est pas un entier decimal
    raise ValueError(f"valeur de stat non entiere : {value!r}")


def flatten_stats(stats: dict | None, unknown: Counter | None = None) -> dict:
    """Aplatit `players[].stats` en colonnes nommées.

    Trois règles, parce que 0 et « inconnu » ne sont pas la même chose :
      1. `stats` absent ou `null` -> toutes les colonnes à NULL ;
      2. `stats` présent, clé de famille absente -> cette famille vaut 0 ;
      3. `killed_at` et `final_tiles` restent NULL quand ils sont absents.

    `unknown` collecte ce que le schéma ne couvre pas : clé de stat inconnue, sous-clé
    inconnue, ou tableau plus long que la dimension déclarée par le moteur.
    """
    if not stats:
        out = dict.fromkeys(STAT_COLUMNS)
        out["kills"] = None
        return out

    out: dict = dict.fromkeys(STAT_COLUMNS, 0)
    for column in SCALAR_NULLABLE.values():
        out[column] = None
    out["kills"] = []

    for key in stats:
        if key not in KNOWN_STATS and unknown is not None:
            unknown[key] += 1

    for family, names in ARRAYS.items():
        values = stats.get(family)
        if values is None:
            continue
        if not isinstance(values, list):
            values = [values]  # `conquests` etait un scalaire avant d'etre un tableau
        for i, name in enumerate(names):
            out[f"{family}_{name}"] = _big(values[i]) if i < len(values) else 0
        if len(values) > len(names) and unknown is not None:
            unknown[f"{family}[{len(values)}]"] += 1

    for family, (kinds, names) in GROUPS.items():
        group = stats.get(family)
        if group is None:
            continue
        for kind, values in group.items():
            if kind not in kinds:
                if unknown is not None:
                    unknown[f"{family}.{kind}"] += 1
                continue
            if not isinstance(values, list):
                values = [values]
            for i, name in enumerate(names):
                out[f"{family}_{kind}_{name}"] = _big(values[i]) if i < len(values) else 0
            if len(values) > len(names) and unknown is not None:
                unknown[f"{family}.{kind}[{len(values)}]"] += 1

    for key, column in list(SCALAR_COUNTERS.items()) + list(SCALAR_NULLABLE.items()):
        if key in stats:
            out[column] = _big(stats[key])

    kills = stats.get("kills")
    if kills is not None:
        out["kills"] = [{"victim": k["victim"], "tick": _big(k.get("tick"))} for k in kills]
    return out


# --------------------------------------------------------------------------------------
# games
# --------------------------------------------------------------------------------------

CONFIG_STR = {
    "gameMode": "mode",
    "gameMap": "map",
    "gameMapSize": "map_size",
    "difficulty": "difficulty",
    "gameType": "game_type",
    "rankedType": "ranked_type",
}
# Unions int|str a decoder au runtime : `playerTeams` vaut 2 ou "Quads" ou "Humans Vs Nations",
# `nations` vaut un entier ou "default"/"disabled". On garde le texte brut.
CONFIG_TEXT = {"playerTeams": "player_teams", "nations": "nations"}
CONFIG_INT = {
    "bots": "bots",
    "maxPlayers": "max_players",
    "spawnImmunityDuration": "spawn_immunity_duration_ticks",
}
CONFIG_BOOL = {
    "infiniteGold": "infinite_gold",
    "infiniteTroops": "infinite_troops",
    "instantBuild": "instant_build",
    "randomSpawn": "random_spawn",
    "donateGold": "donate_gold",
    "donateTroops": "donate_troops",
    "disableAlliances": "disable_alliances",
}

HUMANS_VS_NATIONS = "Humans Vs Nations"


def in_scope(info: dict) -> bool:
    """Périmètre d'ANALYSE (74,7 % du public), plus étroit que le périmètre d'ingestion."""
    config = info.get("config") or {}
    return (
        config.get("rankedType") != "1v1"
        and info.get("publicGameType") != "special"
        and str(config.get("playerTeams")) != HUMANS_VS_NATIONS
    )


def _ts(ms) -> datetime | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, UTC)


def month_of(info: dict) -> str:
    start = _ts(info.get("start"))
    return start.strftime("%Y-%m") if start else "unknown"


def winner_of(info: dict) -> tuple[str | None, str | None, list[str]]:
    """`winner` est un tuple discriminé par [0], pas un objet. Absent sur 1,5 % des parties."""
    winner = info.get("winner")
    if not isinstance(winner, list) or len(winner) < 2:
        return None, None, []
    return winner[0], winner[1], [x for x in winner[2:] if isinstance(x, str)]


def game_row(record: dict) -> dict:
    info = record["info"]
    config = info.get("config") or {}
    turns = record.get("turns") or []
    kind, winner_id, members = winner_of(info)
    row = {
        "game_id": info["gameID"],
        "version": record.get("version"),
        "git_commit": record.get("gitCommit"),
        "domain": record.get("domain"),
        "subdomain": record.get("subdomain"),
        "site": record.get("site"),
        "start": _ts(info.get("start")),
        "end": _ts(info.get("end")),
        "lobby_created_at": _ts(info.get("lobbyCreatedAt")),
        "visible_at": _ts(info.get("visibleAt")),
        "duration_s": info.get("duration"),
        "num_turns": info.get("num_turns"),
        "lobby_fill_time_ms": info.get("lobbyFillTime"),
        "public_game_type": info.get("publicGameType"),
        "config_json": json.dumps(config, separators=(",", ":"), sort_keys=True),
        "winner_kind": kind,
        "winner_id": winner_id,
        "winner_members": members,
        "stats_agreed": info.get("statsAgreed"),
        "num_players": len(info.get("players") or []),
        "num_tribes": len(info["tribes"]) if info.get("tribes") is not None else None,
        "has_turns": bool(turns),
        "num_intents": sum(len(t.get("intents") or []) for t in turns) if turns else None,
        "in_scope": in_scope(info),
    }
    for key, column in CONFIG_STR.items():
        row[column] = config.get(key)
    for key, column in CONFIG_TEXT.items():
        value = config.get(key)
        row[column] = None if value is None else str(value)
    for key, column in list(CONFIG_INT.items()) + list(CONFIG_BOOL.items()):
        row[column] = config.get(key)
    return row


# --------------------------------------------------------------------------------------
# participants
# --------------------------------------------------------------------------------------


def participant_rows(record: dict, unknown: Counter | None = None) -> list[dict]:
    """Une ligne par (partie, joueur). `persistentID` n'est jamais lu : on ne le stocke pas."""
    info = record["info"]
    kind, winner_id, members = winner_of(info)
    winners = set(members)
    rows = []
    for player in info.get("players") or []:
        client_id = player["clientID"]
        if kind is None:
            won = None  # partie sans vainqueur archive : inconnu, pas perdu
        elif kind == "player":
            won = client_id == winner_id
        else:
            won = client_id in winners
        stats = player.get("stats")
        row = {
            "game_id": info["gameID"],
            "client_id": client_id,
            "public_id": player.get("publicID"),
            "username": player.get("username"),
            "clan_tag": player.get("clanTag"),
            "flag": (player.get("cosmetics") or {}).get("flag"),
            "is_lobby_creator": player.get("isLobbyCreator"),
            "team_index": player.get("teamIndex"),
            "friends": player.get("friends"),
            "won": won,
            "has_stats": bool(stats),
        }
        row.update(flatten_stats(stats, unknown))
        rows.append(row)
    return rows


# --------------------------------------------------------------------------------------
# intents
# --------------------------------------------------------------------------------------

# Colonne -> champs source, par ordre de priorité. `quick_chat` porte `recipient` ET `target` :
# `recipient` gagne, `target` part dans `params`.
PROMOTED: dict[str, tuple[str, ...]] = {
    "other_id": ("targetID", "recipient", "requestor", "target"),
    "tile": ("tile", "dst"),
    "troops": ("troops",),
    "gold": ("gold",),
    "unit": ("unit",),
    "unit_id": ("unitId", "unitID"),
    "amount": ("amount",),
    "action": ("action",),
}


def intent_rows(record: dict) -> list[dict]:
    """Une ligne par action. `tick` = `turnNumber` ; `seq` = rang dans le tick.

    `seq` existe parce que le moteur crée une Execution par intent dans l'ordre du tour :
    sans colonne, cet ordre ne serait porté que par l'ordre des lignes, que SQL ne garantit pas.
    """
    game_id = record["info"]["gameID"]
    rows = []
    for turn in record.get("turns") or []:
        tick = turn["turnNumber"]
        for seq, intent in enumerate(turn.get("intents") or []):
            rest = dict(intent)
            rest.pop("type", None)
            rest.pop("clientID", None)
            row = {
                "game_id": game_id,
                "tick": tick,
                "seq": seq,
                "client_id": intent.get("clientID"),
                "type": intent["type"],
            }
            for column, sources in PROMOTED.items():
                row[column] = None
                for field in sources:
                    if field in intent:
                        row[column] = rest.pop(field)
                        break
            row["troops"] = None if row["troops"] is None else float(row["troops"])
            row["params"] = (
                json.dumps(rest, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
                if rest
                else None
            )
            rows.append(row)
    return rows


# --------------------------------------------------------------------------------------
# Schémas Parquet, déclarés une fois, générés depuis les tables ci-dessus.
# --------------------------------------------------------------------------------------

SCHEMAS: dict[str, pa.Schema] = {
    "games": pa.schema(
        [
            ("game_id", pa.string()),
            ("version", pa.string()),
            ("git_commit", pa.string()),
            ("domain", pa.string()),
            ("subdomain", pa.string()),
            ("site", pa.string()),
            ("start", pa.timestamp("ms", tz="UTC")),
            ("end", pa.timestamp("ms", tz="UTC")),
            ("lobby_created_at", pa.timestamp("ms", tz="UTC")),
            ("visible_at", pa.timestamp("ms", tz="UTC")),
            ("duration_s", pa.int32()),
            ("num_turns", pa.int32()),
            ("lobby_fill_time_ms", pa.int64()),
            ("mode", pa.string()),
            ("map", pa.string()),
            ("map_size", pa.string()),
            ("difficulty", pa.string()),
            ("game_type", pa.string()),
            ("ranked_type", pa.string()),
            ("public_game_type", pa.string()),
            ("player_teams", pa.string()),
            ("nations", pa.string()),
            ("bots", pa.int32()),
            ("max_players", pa.int32()),
            ("spawn_immunity_duration_ticks", pa.int32()),
            *[(c, pa.bool_()) for c in CONFIG_BOOL.values()],
            ("config_json", pa.string()),
            ("winner_kind", pa.string()),
            ("winner_id", pa.string()),
            ("winner_members", pa.list_(pa.string())),
            ("stats_agreed", pa.bool_()),
            ("num_players", pa.int32()),
            ("num_tribes", pa.int32()),
            ("has_turns", pa.bool_()),
            ("num_intents", pa.int32()),
            ("in_scope", pa.bool_()),
        ]
    ),
    "participants": pa.schema(
        [
            ("game_id", pa.string()),
            ("client_id", pa.string()),
            ("public_id", pa.string()),
            ("username", pa.string()),
            ("clan_tag", pa.string()),
            ("flag", pa.string()),
            ("is_lobby_creator", pa.bool_()),
            ("team_index", pa.int32()),
            ("friends", pa.list_(pa.string())),
            ("won", pa.bool_()),
            ("has_stats", pa.bool_()),
            *[(c, pa.int64()) for c in STAT_COLUMNS],
            (
                "kills",
                pa.list_(pa.struct([("victim", pa.string()), ("tick", pa.int64())])),
            ),
        ]
    ),
    "intents": pa.schema(
        [
            ("game_id", pa.string()),
            ("tick", pa.int32()),
            ("seq", pa.int32()),
            ("client_id", pa.string()),
            ("type", pa.string()),
            ("other_id", pa.string()),
            ("tile", pa.int64()),
            ("troops", pa.float64()),
            ("gold", pa.int64()),
            ("unit", pa.string()),
            ("unit_id", pa.int64()),
            ("amount", pa.int32()),
            ("action", pa.string()),
            ("params", pa.string()),
        ]
    ),
}


# --------------------------------------------------------------------------------------
# Écriture
# --------------------------------------------------------------------------------------


def _writer(writers: dict, out: Path, table: str, month: str) -> pq.ParquetWriter:
    key = (table, month)
    writer = writers.get(key)
    if writer is None:
        directory = out / table / f"month={month}"
        directory.mkdir(parents=True, exist_ok=True)
        writer = writers[key] = pq.ParquetWriter(
            directory / "part.parquet", SCHEMAS[table], compression="zstd"
        )
    return writer


def _flush(writers: dict, out: Path, buffers: dict) -> None:
    """Un groupe de lignes par lot : la mémoire reste bornée quelle que soit la taille du brut."""
    for (table, month), rows in buffers.items():
        if rows:
            table_data = pa.Table.from_pylist(rows, schema=SCHEMAS[table])
            _writer(writers, out, table, month).write_table(table_data)
    buffers.clear()


def normalize(raw_dirs: list[Path], out: Path, chunk: int = 2000) -> dict:
    """Reconstruit les trois tables depuis le brut. Retourne un rapport chiffré.

    Les répertoires sont parcourus dans l'ordre donné et une partie n'est normalisée qu'une
    fois : passer `full` avant `meta` fait gagner les intents quand le record complet est là.
    """
    report: dict = {
        "records": 0,
        "by_dir": {},
        "rows": dict.fromkeys(TABLES, 0),
        "months": Counter(),
        "unknown_stats": Counter(),
        "without_start": 0,
    }
    writers: dict = {}
    buffers: dict = defaultdict(list)
    seen: set[str] = set()
    unknown: Counter = report["unknown_stats"]
    try:
        for base in raw_dirs:
            count = 0
            if not base.exists():
                report["by_dir"][base.name] = 0
                continue
            for game_id in raw.iter_game_ids(base):
                if game_id in seen:
                    continue
                seen.add(game_id)
                count += 1
                record = json.loads(raw.read(base, game_id))
                month = month_of(record["info"])
                if month == "unknown":
                    report["without_start"] += 1
                report["months"][month] += 1
                rows = {
                    "games": [game_row(record)],
                    "participants": participant_rows(record, unknown),
                    "intents": intent_rows(record),
                }
                for table, table_rows in rows.items():
                    buffers[(table, month)] += table_rows
                    report["rows"][table] += len(table_rows)
                report["records"] += 1
                if report["records"] % chunk == 0:
                    _flush(writers, out, buffers)
            report["by_dir"][base.name] = report["by_dir"].get(base.name, 0) + count
        _flush(writers, out, buffers)
    finally:
        for writer in writers.values():
            writer.close()
    report["files"] = sorted(str(p.relative_to(out)) for p in out.glob("*/*/*.parquet"))
    report["bytes"] = sum(p.stat().st_size for p in out.glob("*/*/*.parquet"))
    report["months"] = dict(sorted(report["months"].items()))
    report["unknown_stats"] = dict(unknown.most_common())
    return report


def rebuild(raw_dirs: list[Path], out: Path, chunk: int = 2000) -> dict:
    """Écrit à côté puis échange : un kill en cours de route ne laisse pas de demi-état."""
    tmp = out.with_name(out.name + ".tmp")
    old = out.with_name(out.name + ".old")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    tmp.mkdir(parents=True)
    report = normalize(raw_dirs, tmp, chunk=chunk)
    if out.exists():
        out.rename(old)
    tmp.rename(out)
    shutil.rmtree(old, ignore_errors=True)
    return report
