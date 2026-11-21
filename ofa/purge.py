"""Purge quotidienne des comptes supprimés (obligation de la spec).

C'est la seule exception à l'immuabilité de data/raw, et elle est explicite : on
remplace `publicID` et `username` du compte supprimé par `"REDACTED"`, exactement ce que
fait l'API dans ses propres réponses. Tout le reste du record est conservé — supprimer la
partie entière effacerait les données de 30 à 50 autres joueurs. Chaque purge est tracée
dans la table `purged`.
"""

import json
import sqlite3
from datetime import UTC, datetime

from . import api, index, raw
from .client import Client
from .config import Config

SINCE_FILE = "purge-since.json"


def redact(body: bytes, public_id: str) -> tuple[bytes, int]:
    """Retourne (nouveau corps, nombre de lignes joueur modifiées)."""
    record = json.loads(body)
    touched = 0
    for player in record.get("info", {}).get("players", []):
        if player.get("publicID") == public_id:
            player["publicID"] = "REDACTED"
            player["username"] = "REDACTED"
            touched += 1
    if touched == 0:
        return body, 0
    return json.dumps(record, separators=(",", ":")).encode(), touched


def load_since(cfg: Config) -> str | None:
    p = cfg.state_dir / SINCE_FILE
    return json.loads(p.read_text())["since"] if p.exists() else None


def save_since(cfg: Config, since: str) -> None:
    p = cfg.state_dir / SINCE_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"since": since}))


def purge(cfg: Config, client: Client, conn: sqlite3.Connection, since: str | None = None) -> dict:
    """Récupère les comptes supprimés et les expurge de data/raw et de l'index."""
    if since is None:
        since = load_since(cfg)
    rows = api.recently_deleted(client, since)
    now = datetime.now(UTC).isoformat()
    summary = {"deleted_listed": len(rows), "accounts_purged": 0, "records_redacted": 0}
    for row in rows:
        public_id, deleted_at = row["publicId"], row["deletedAt"]
        touched = 0
        for game_id in index.games_of(conn, public_id):
            for base in (cfg.raw_meta, cfg.raw_full):
                if not raw.exists(base, game_id):
                    continue
                new_body, n = redact(raw.read(base, game_id), public_id)
                if n:
                    raw.write(base, game_id, new_body)
                    touched += 1
        index.forget(conn, public_id)
        index.mark_purged(conn, public_id, deleted_at, now, touched)
        summary["accounts_purged"] += 1
        summary["records_redacted"] += touched
    conn.commit()
    if rows:
        save_since(cfg, max(r["deletedAt"] for r in rows))
    return summary
