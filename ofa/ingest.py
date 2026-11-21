"""Parcours de l'archive par `afterId`, reprenable et idempotent.

Le mode incrémental n'est pas un mode : relancer la commande reprend au curseur, et le
curseur suit l'archive. Une partie déjà sur disque ne coûte aucune requête de détail.

Sûreté à la reprise (critère d'acceptation du J1) :
  - le curseur n'avance qu'une fois la page entièrement écrite sur disque ;
  - à la reprise on repart `MARGIN` ids sous le curseur, parce qu'une partie archivée en
    retard peut recevoir un id inférieur à un id déjà dépassé (documenté par l'API) ;
  - la déduplication est l'existence du fichier, donc relire une marge ne coûte que des
    requêtes de liste.
"""

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from . import api, index, raw
from .client import Client
from .config import ARCHIVE_START, Config

MARGIN = 2000  # ids relus sous le curseur à chaque reprise


@dataclass
class Stats:
    pages: int = 0
    listed: int = 0
    skipped_before_start: int = 0
    skipped_present: int = 0
    skipped_failed: int = 0
    written: int = 0
    bytes_written: int = 0
    errors: dict[int, int] = field(default_factory=dict)
    total_annonce: int | None = None

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["errors"] = {str(k): v for k, v in self.errors.items()}
        return d


def _a_telecharger(cfg, row, stats, failed, archive_start) -> bool:
    """Trie une ligne de liste : hors periode, deja la, ou en echec definitif -> on passe."""
    if row["start"][:10] < archive_start:
        stats.skipped_before_start += 1
        return False
    if raw.exists(cfg.raw_meta, row["game"]):
        stats.skipped_present += 1
        return False
    if row["game"] in failed:
        stats.skipped_failed += 1
        return False
    return True


def _stocker(cfg, conn, game_id, resp, stats, failed) -> None:
    """Ecrit le record et l'index. Toujours sur le thread principal : sqlite n'est pas
    thread-safe, et de toute facon seul le reseau gagne a etre parallelise."""
    if resp.status != 200:
        stats.errors[resp.status] = stats.errors.get(resp.status, 0) + 1
        index.mark_failed(conn, game_id, resp.status, datetime.now(UTC).isoformat())
        failed.add(game_id)
        return
    stats.bytes_written += raw.write(cfg.raw_meta, game_id, resp.body)
    stats.written += 1
    players = resp.json().get("info", {}).get("players", [])
    index.add_participants(conn, game_id, [p.get("publicID") for p in players])


def _fetch_page(cfg, client, conn, rows, stats, failed, archive_start, workers: int) -> None:
    """Telecharge les details d'une page, en parallele si workers > 1.

    Mesure du J1 : en sequentiel, le temps de reponse (~0,07 s) s'ajoute a l'espacement
    du limiteur (0,25 s a 4 req/s), d'ou un debit effectif de 3,11 req/s au lieu de 4.
    Paralleliser les requetes laisse le limiteur imposer seul la cadence.
    """
    cibles = [r["game"] for r in rows if _a_telecharger(cfg, r, stats, failed, archive_start)]
    if not cibles:
        return
    if workers <= 1:
        for game_id in cibles:
            _stocker(cfg, conn, game_id, api.get_game(client, game_id, turns=False), stats, failed)
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        reponses = pool.map(lambda g: api.get_game(client, g, turns=False), cibles)
        # pool.map preserve l'ordre : on ecrit dans l'ordre de la page.
        for game_id, resp in zip(cibles, reponses, strict=True):
            _stocker(cfg, conn, game_id, resp, stats, failed)


def cursor_path(cfg: Config, ranked_type: str):
    return cfg.state_dir / f"cursor-{ranked_type}.json"


def load_cursor(cfg: Config, ranked_type: str) -> int:
    p = cursor_path(cfg, ranked_type)
    if not p.exists():
        return 0
    return int(json.loads(p.read_text())["after_id"])


def save_cursor(cfg: Config, ranked_type: str, after_id: int) -> None:
    p = cursor_path(cfg, ranked_type)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(json.dumps({"after_id": after_id}))
    os.replace(tmp, p)


def ingest(
    cfg: Config,
    client: Client,
    conn: sqlite3.Connection,
    ranked_type: str,
    *,
    limit: int = 1000,
    max_pages: int | None = None,
    archive_start: str = ARCHIVE_START,
) -> Stats:
    stats = Stats()
    cursor = load_cursor(cfg, ranked_type)
    after = max(0, cursor - MARGIN)
    failed = index.failed_ids(conn)

    while True:
        rows = api.list_games(client, ranked_type=ranked_type, after_id=after, limit=limit)
        if not rows:
            break
        stats.pages += 1
        stats.listed += len(rows)
        _fetch_page(cfg, client, conn, rows, stats, failed, archive_start, cfg.concurrency)
        conn.commit()
        # `id` est une chaîne décimale : comparer en entier, jamais en lexicographique.
        after = max(int(r["id"]) for r in rows)
        save_cursor(cfg, ranked_type, after)
        if max_pages is not None and stats.pages >= max_pages:
            break
    return stats


def day_marker(cfg: Config, ranked_type: str, day: str):
    return cfg.state_dir / "days" / ranked_type / f"{day}.done"


def ingest_day(
    cfg: Config,
    client: Client,
    conn: sqlite3.Connection,
    ranked_type: str,
    day: str,
    *,
    limit: int = 1000,
    max_pages: int | None = None,
    archive_start: str = ARCHIVE_START,
) -> Stats:
    """Une journee, paginee par curseur `after`. Marquee terminee seulement si elle est
    close depuis plus d'un jour : une partie peut encore etre archivee en retard."""
    stats = Stats()
    marker = day_marker(cfg, ranked_type, day)
    if marker.exists():
        return stats
    after: str | None = None
    while True:
        rows, total = api.list_window(
            client,
            ranked_type=ranked_type,
            start=f"{day}T00:00:00Z",
            end=f"{day}T23:59:59.999Z",
            limit=limit,
            after=after,
        )
        if not rows:
            break
        stats.pages += 1
        stats.listed += len(rows)
        if total is not None:
            stats.total_annonce = total
        _fetch_page(
            cfg, client, conn, rows, stats, index.failed_ids(conn), archive_start, cfg.concurrency
        )
        conn.commit()
        last = rows[-1]
        after = f"{last['start']},{last['game']}"
        if len(rows) < limit:
            break
        if max_pages is not None and stats.pages >= max_pages:
            return stats
    veille = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    if day < veille:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(stats.as_dict()))
    return stats


def ingest_days(
    cfg: Config,
    client: Client,
    conn: sqlite3.Connection,
    ranked_type: str,
    days: int,
    **kwargs,
) -> Stats:
    """Les `days` derniers jours, du plus recent vers le plus ancien."""
    total = Stats()
    today = datetime.now(UTC).date()
    for n in range(days):
        day = (today - timedelta(days=n)).isoformat()
        s = ingest_day(cfg, client, conn, ranked_type, day, **kwargs)
        total.pages += s.pages
        total.listed += s.listed
        total.written += s.written
        total.bytes_written += s.bytes_written
        total.skipped_present += s.skipped_present
        total.skipped_before_start += s.skipped_before_start
        total.skipped_failed += s.skipped_failed
        for k, v in s.errors.items():
            total.errors[k] = total.errors.get(k, 0) + v
    return total
