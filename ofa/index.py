"""Index opérationnel (publicID -> parties), reconstructible à partir de data/raw/.

Sa seule raison d'être est la purge quotidienne : sans index, retrouver les parties d'un
compte supprimé imposerait de relire les 13 Go de `meta/` chaque jour. Ce n'est pas une
source de vérité — les Parquet du J2 le sont.

Table sans rowid, clé primaire composite : pas d'index supplémentaire à payer.
"""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS participants (
  public_id TEXT NOT NULL,
  game_id   TEXT NOT NULL,
  PRIMARY KEY (public_id, game_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS purged (
  public_id  TEXT PRIMARY KEY,
  deleted_at TEXT NOT NULL,
  purged_at  TEXT NOT NULL,
  games      INTEGER NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")  # survit à un kill -9
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


def add_participants(conn: sqlite3.Connection, game_id: str, public_ids: list[str]) -> None:
    """Idempotent : une reprise qui repasse sur la même partie ne crée pas de doublon."""
    conn.executemany(
        "INSERT OR IGNORE INTO participants (public_id, game_id) VALUES (?, ?)",
        [(pid, game_id) for pid in public_ids if pid and pid != "REDACTED"],
    )


def games_of(conn: sqlite3.Connection, public_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT game_id FROM participants WHERE public_id = ?", (public_id,)
    ).fetchall()
    return [r[0] for r in rows]


def forget(conn: sqlite3.Connection, public_id: str) -> None:
    conn.execute("DELETE FROM participants WHERE public_id = ?", (public_id,))


def mark_purged(
    conn: sqlite3.Connection, public_id: str, deleted_at: str, purged_at: str, games: int
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO purged (public_id, deleted_at, purged_at, games) "
        "VALUES (?, ?, ?, ?)",
        (public_id, deleted_at, purged_at, games),
    )


FAILED_SCHEMA = """
CREATE TABLE IF NOT EXISTS failed (
  game_id TEXT PRIMARY KEY,
  status  INTEGER NOT NULL,
  at      TEXT NOT NULL
);
"""


def mark_failed(conn: sqlite3.Connection, game_id: str, status: int, at: str) -> None:
    """Un 404 ou 400 sur une partie est définitif : sans ça, chaque reprise le rejouerait."""
    conn.executescript(FAILED_SCHEMA)
    conn.execute(
        "INSERT OR REPLACE INTO failed (game_id, status, at) VALUES (?, ?, ?)",
        (game_id, status, at),
    )


def failed_ids(conn: sqlite3.Connection) -> set[str]:
    conn.executescript(FAILED_SCHEMA)
    return {r[0] for r in conn.execute("SELECT game_id FROM failed")}


# Le statut d'une re-sim est un etat MUTABLE : c'est lui qui permet la reprise d'un run de
# plusieurs heures et qui produit le decompte par cause du J3. Il n'a donc pas sa place en
# Parquet, et il se reconstruit en relancant la re-sim.
RESIM_SCHEMA = """
CREATE TABLE IF NOT EXISTS resim (
  game_id         TEXT PRIMARY KEY,
  status          TEXT NOT NULL,
  detail          TEXT,
  git_commit      TEXT,
  month           TEXT,
  ticks           INTEGER,
  wall_s          REAL,
  rss_kb          INTEGER,
  hashes_compared INTEGER,
  hashes_matched  INTEGER,
  rows_written    INTEGER,
  at              TEXT NOT NULL
);
"""


def resim_done(conn: sqlite3.Connection) -> set[str]:
    """Les parties deja valides : une reprise ne les rejoue pas (1 min par partie economisee)."""
    conn.executescript(RESIM_SCHEMA)
    return {r[0] for r in conn.execute("SELECT game_id FROM resim WHERE status = 'ok'")}


def resim_record(conn: sqlite3.Connection, summary: dict) -> None:
    conn.executescript(RESIM_SCHEMA)
    conn.execute(
        "INSERT OR REPLACE INTO resim (game_id, status, detail, git_commit, month, ticks, "
        "wall_s, rss_kb, hashes_compared, hashes_matched, rows_written, at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            summary["game_id"],
            summary["status"],
            "\n".join(summary.get("detail") or [])[:2000] or None,
            summary.get("git_commit"),
            summary.get("month"),
            summary.get("ticks"),
            summary.get("wall_s"),
            summary.get("rss_kb"),
            summary.get("hashes_compared"),
            summary.get("hashes_matched"),
            summary.get("rows"),
        ),
    )


def resim_counts(conn: sqlite3.Connection) -> list[tuple]:
    """(statut, parties, ticks, secondes de mur, pic RSS) : la matiere de PERF.md."""
    conn.executescript(RESIM_SCHEMA)
    return conn.execute(
        "SELECT status, count(*), coalesce(sum(ticks), 0), coalesce(sum(wall_s), 0), "
        "coalesce(max(rss_kb), 0) FROM resim GROUP BY status ORDER BY count(*) DESC"
    ).fetchall()
