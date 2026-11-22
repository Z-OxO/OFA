"""Le niveau : un rating chronologique (J4, ADR-0010).

Le niveau n'est pas une fin : c'est le controle que la SPEC impose pour qu'une fiche tactique
ne confonde pas « ce comportement fait gagner » avec « les bons joueurs ont ce comportement ».
Ce module livre donc une colonne, le rating d'AVANT-partie de chaque (partie, joueur), et rien
de plus.

Modele : Plackett-Luce en ligne. Une partie est une liste ordonnee de groupes ex aequo
(FFA : surtout des singletons ; Team : deux groupes, les vainqueurs et le reste). Le gradient
se calcule en O(n) par sommes suffixes, la ou l'Elo par paires couterait O(n^2) -- 1,4 milliard
de paires sur l'archive projetee du serveur (ADR-0010).
"""

import datetime as dt
import math
import shutil
from collections import defaultdict
from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from .config import CONFIRMATION_START, RATING_HALFLIFE, RATING_LR0, RATING_SCALE, RATING_START
from .resim import _encoding  # meme encodage que les tables du J3 : mesure, pas gout

# publicID absent ou expurge : le joueur joue, mais il ne capitalise rien (ADR-0010).
ANON = None
REDACTED = "REDACTED"

SCHEMA = pa.schema(
    [
        ("game_id", pa.string()),
        ("public_id", pa.string()),
        ("rating_before", pa.float32()),
        ("rating_after", pa.float32()),
        ("games_before", pa.int32()),
        ("rank", pa.int32()),  # rang de competition : 1 + le nombre de joueurs strictement devant
        ("field_size", pa.int32()),  # participants classes, anonymes compris
    ]
)

# Les seuils de parties anterieures du rapport : un rating a 0 partie vaut l'a priori, et le
# dire est la moitie du resultat (7 jours d'archive, mediane 1 partie par joueur).
THRESHOLDS = (0, 1, 5, 10)


def elo(theta: float) -> float:
    """theta (log-force) -> echelle Elo, pour que le J6 affiche un nombre que tout le monde lit."""
    return RATING_START + RATING_SCALE * theta


def theta_of(rating: float) -> float:
    return (rating - RATING_START) / RATING_SCALE


# --------------------------------------------------------------------------------------
# Le classement d'une partie
# --------------------------------------------------------------------------------------


def outcome_groups(mode: str, rows: list[tuple]) -> list[list[int]] | None:
    """Groupes d'ex aequo, meilleur d'abord, en indices de `rows`. None = pas de classement.

    `rows` : (cle_joueur, killed_at, final_tiles, won), deja filtre sur « a un resultat ».

    FFA : `final_tiles` n'est rempli que pour les vivants a la fin et `killed_at` que pour les
    morts (mesure : les deux etats sont exclusifs sur 1 212 726 lignes). Les vivants passent
    donc devant, par territoire final decroissant, puis les morts par mort decroissante. Le
    vainqueur est l'argmax de `final_tiles` sur 34 380 FFA sur 34 380 : `won` n'est pas une
    entree, c'est une contre-epreuve (tests/test_rating.py).

    Team : les equipes des perdants sont inconnues sans re-sim (`team_index` : 0,2 % des
    lignes). On ne sait donc qu'une chose, et on ne dit que celle-la : l'equipe de
    `winner_members` a battu tout le reste.
    """
    if mode == "Team":
        winners = [i for i, r in enumerate(rows) if r[3]]
        losers = [i for i, r in enumerate(rows) if not r[3]]
        return [winners, losers] if winners and losers else None
    order = sorted(range(len(rows)), key=lambda i: _ffa_key(rows[i]))
    groups: list[list[int]] = []
    previous = None
    for i in order:
        key = _ffa_key(rows[i])
        if key != previous:
            groups.append([])
            previous = key
        groups[-1].append(i)
    return groups if len(groups) > 1 else None


def _ffa_key(row: tuple) -> tuple[int, int]:
    _, killed_at, final_tiles, _ = row
    return (0, -final_tiles) if final_tiles is not None else (1, -killed_at)


def has_outcome(killed_at, final_tiles) -> bool:
    """Sans `killed_at` ni `final_tiles`, un participant n'a pas de resultat.

    7,2 % des lignes FFA : `has_stats` est faux, le joueur a quitte avant que le moteur
    n'ecrive quoi que ce soit. Le placer dernier inventerait une defaite.
    """
    return killed_at is not None or final_tiles is not None


# --------------------------------------------------------------------------------------
# Le modele
# --------------------------------------------------------------------------------------


def gradients(groups: list[list[int]], theta: list[float]) -> list[float]:
    """Gradient de la log-vraisemblance de Plackett-Luce, ex aequo compris (Breslow).

        LL = somme_{g < m} [ somme_{k dans G_g} theta_k - |G_g| log S_g ]
        grad_k = [k hors du dernier groupe] - w_k * somme_{g <= min(h, m-1)} |G_g| / S_g

    avec w = exp(theta) et S_g la somme suffixe des w. Le facteur du DERNIER groupe est retire :
    « les derniers n'ont battu personne » n'informe de rien, et le garder tirerait les perdants
    d'une partie Team les uns vers les autres. Consequence utile : le gradient somme a zero,
    donc le rating est a somme nulle, comme l'Elo -- et sur deux joueurs c'est exactement l'Elo
    par paire.
    """
    m = len(groups)
    shift = max(theta)  # exp(theta) deborde sans recentrage
    w = [math.exp(t - shift) for t in theta]
    suffix = [0.0] * m
    total = 0.0
    for g in range(m - 1, -1, -1):
        total += sum(w[k] for k in groups[g])
        suffix[g] = total
    grad = [0.0] * len(theta)
    cumulative = 0.0
    for h, group in enumerate(groups):
        if h < m - 1:
            cumulative += len(group) / suffix[h]
        won = 1.0 if h < m - 1 else 0.0
        for k in group:
            grad[k] = won - w[k] * cumulative
    return grad


def ranks_of(groups: list[list[int]], n: int) -> list[int]:
    rank = [0] * n
    ahead = 0
    for group in groups:
        for k in group:
            rank[k] = ahead + 1
        ahead += len(group)
    return rank


# --------------------------------------------------------------------------------------
# Lecture
# --------------------------------------------------------------------------------------


def _as_utc(value) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def games_by_month(parquet_dir: Path) -> dict[str, list[tuple]]:
    """Les parties du perimetre, par mois, chacune triee par (end, game_id).

    `in_scope` (ADR-0007) : Humans Vs Nations, special et le ranked 1v1 ne sont pas le meme jeu.
    """
    table = ds.dataset(parquet_dir / "games", format="parquet", partitioning="hive").to_table(
        columns=["game_id", "end", "mode", "month"],
        filter=(ds.field("in_scope") == True),  # noqa: E712 - pyarrow veut l'egalite explicite
    )
    out: dict[str, list[tuple]] = defaultdict(list)
    for game_id, end, mode, month in zip(
        table["game_id"].to_pylist(),
        table["end"].to_pylist(),
        table["mode"].to_pylist(),
        table["month"].to_pylist(),
        strict=True,
    ):
        if end is None:  # sans date de fin, pas de place dans la chronologie
            continue
        out[month].append((_as_utc(end), game_id, mode))
    for month in out:
        out[month].sort(key=lambda row: (row[0], row[1]))
    return dict(out)


def participants_of_month(parquet_dir: Path, month: str) -> dict[str, list[tuple]]:
    """(cle_joueur, killed_at, final_tiles, won) par partie, pour un mois.

    Un mois a la fois : l'archive du serveur ne tiendra pas en memoire d'un coup, une de ses
    partitions oui -- 1,36 Go de pic pour les 48 100 parties de novembre (docs/PERF.md).

    shortcut: un mois complet du serveur (~206 000 parties, 7,8 M de participations) projette
    ~5 Go de pic ; si ca ne passe pas, decouper par jour (filtre `game_id.isin(...)` sur la
    meme partition, relue une fois par jour) plutot que de changer le modele.
    """
    table = ds.dataset(
        parquet_dir / "participants", format="parquet", partitioning="hive"
    ).to_table(
        columns=["game_id", "public_id", "killed_at", "final_tiles", "won"],
        filter=(ds.field("month") == month),
    )
    rows: dict[str, list[tuple]] = defaultdict(list)
    for game_id, public_id, killed_at, final_tiles, won in zip(
        table["game_id"].to_pylist(),
        table["public_id"].to_pylist(),
        table["killed_at"].to_pylist(),
        table["final_tiles"].to_pylist(),
        table["won"].to_pylist(),
        strict=True,
    ):
        if has_outcome(killed_at, final_tiles):
            key = ANON if public_id in (None, REDACTED) else public_id
            rows[game_id].append((key, killed_at, final_tiles, won))
    return dict(rows)


# --------------------------------------------------------------------------------------
# La passe chronologique
# --------------------------------------------------------------------------------------


def run(
    parquet_dir: Path, out: Path, lr0: float = RATING_LR0, half: float = RATING_HALFLIFE
) -> dict:
    """Parcourt toutes les parties du perimetre dans l'ordre du temps et ecrit la table.

    Le rating d'une partie est ecrit AVANT d'etre mis a jour par elle : c'est ce decalage qui
    fait qu'une analyse peut s'en servir sans circularite.
    """
    report = {
        "games": 0,
        "rated": 0,
        "rows": 0,
        "no_ranking": 0,
        "anonymous": 0,
        "players": 0,
        "by_month": {},
    }
    theta: dict[str, float] = defaultdict(float)
    played: dict[str, int] = defaultdict(int)
    months = games_by_month(parquet_dir)
    for month in sorted(months):
        rows_by_game = participants_of_month(parquet_dir, month)
        columns: dict[str, list] = {name: [] for name in SCHEMA.names}
        for _, game_id, mode in months[month]:
            report["games"] += 1
            rows = rows_by_game.get(game_id)
            if not rows:
                report["no_ranking"] += 1
                continue
            groups = outcome_groups(mode, rows)
            if groups is None:
                report["no_ranking"] += 1
                continue
            report["rated"] += 1
            keys = [r[0] for r in rows]
            before = [theta[k] if k is not ANON else 0.0 for k in keys]
            grad = gradients(groups, before)
            rank = ranks_of(groups, len(rows))
            for i, key in enumerate(keys):
                if key is ANON:
                    report["anonymous"] += 1
                    continue
                n = played[key]
                after = before[i] + lr0 / (1 + n / half) * grad[i]
                theta[key] = after
                played[key] = n + 1
                columns["game_id"].append(game_id)
                columns["public_id"].append(key)
                columns["rating_before"].append(elo(before[i]))
                columns["rating_after"].append(elo(after))
                columns["games_before"].append(n)
                columns["rank"].append(rank[i])
                columns["field_size"].append(len(rows))
        _write_month(out, month, columns)
        report["by_month"][month] = len(columns["game_id"])
        report["rows"] += len(columns["game_id"])
    report["players"] = len(played)
    report["bytes"] = sum(p.stat().st_size for p in out.glob("*/*.parquet"))
    return report


def _write_month(out: Path, month: str, columns: dict[str, list]) -> None:
    if not columns["game_id"]:
        return
    directory = out / f"month={month}"
    directory.mkdir(parents=True, exist_ok=True)
    table = pa.table(columns, schema=SCHEMA)
    pq.write_table(table, directory / "part.parquet", **_encoding(SCHEMA))


def rebuild(parquet_dir: Path, out: Path, **kwargs) -> dict:
    """Ecrit a cote puis echange, comme `normalize.rebuild` : pas de demi-etat sur un kill.

    Reconstruction totale, jamais d'incremental : la passe coute des secondes, et une purge de
    compte doit pouvoir reecrire toute l'histoire sans trace de l'ancienne.
    """
    tmp = out.with_name(out.name + ".tmp")
    old = out.with_name(out.name + ".old")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    tmp.mkdir(parents=True)
    report = run(parquet_dir, tmp, **kwargs)
    if out.exists():
        out.rename(old)
    tmp.rename(out)
    shutil.rmtree(old, ignore_errors=True)
    return report


# --------------------------------------------------------------------------------------
# Acceptation : le rating d'avant-partie predit-il le classement ?
# --------------------------------------------------------------------------------------


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Correlation de rang, ex aequo moyennes. None quand elle n'a pas de sens."""
    n = len(xs)
    if n < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return num / (dx * dy) if dx and dy else None


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def _logloss(delta_theta: float, won: bool) -> float:
    p = 1 / (1 + math.exp(-max(-30.0, min(30.0, delta_theta))))
    p = min(max(p, 1e-9), 1 - 1e-9)
    return -math.log(p) if won else -math.log(1 - p)


class _Bucket:
    """Accumulateur d'une cellule du rapport (un mode x un seuil de parties anterieures)."""

    def __init__(self):
        self.pairs = 0
        self.loss = 0.0
        self.naive = 0.0
        self.spearman: list[float] = []
        self.experience: list[float] = []
        self.games = 0
        self.players: set[str] = set()

    def absorb(self, other: "_Bucket") -> None:
        self.pairs += other.pairs
        self.loss += other.loss
        self.naive += other.naive
        self.spearman += other.spearman
        self.experience += other.experience
        self.games += other.games
        self.players |= other.players

    def as_dict(self) -> dict:
        return {
            "games": self.games,
            "players": len(self.players),
            "pairs": self.pairs,
            "logloss": self.loss / self.pairs if self.pairs else None,
            "logloss_naive": self.naive / self.pairs if self.pairs else None,
            "spearman": _mean(self.spearman),
            "spearman_experience": _mean(self.experience),
        }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def metrics(parquet_dir: Path, rating_dir: Path, since: str = CONFIRMATION_START) -> dict:
    """Le tableau d'acceptation, par mode et par seuil de parties anterieures.

    Deux references naives : « aucune information » (tous egaux, p = 0,5, log-loss ln 2) et
    « l'experience » (le nombre de parties deja jouees, en correlation de rang). Battre la
    seconde est le vrai test : sans elle, un rating qui ne mesurerait que l'anciennete
    passerait pour un rating.
    """
    cut = dt.datetime.fromisoformat(since)
    scope = {
        game_id: mode
        for month, rows in games_by_month(parquet_dir).items()
        if month >= cut.strftime("%Y-%m")
        for end, game_id, mode in rows
        if end >= cut
    }
    table = ds.dataset(rating_dir, format="parquet", partitioning="hive").to_table(
        columns=["game_id", "public_id", "rating_before", "games_before", "rank"],
        filter=(ds.field("month") >= cut.strftime("%Y-%m")),
    )
    by_game: dict[str, list[tuple]] = defaultdict(list)
    for game_id, public_id, rating, n, rank in zip(
        table["game_id"].to_pylist(),
        table["public_id"].to_pylist(),
        table["rating_before"].to_pylist(),
        table["games_before"].to_pylist(),
        table["rank"].to_pylist(),
        strict=True,
    ):
        if game_id in scope:
            by_game[game_id].append((public_id, theta_of(rating), n, rank))
    buckets: dict[tuple[str, int], _Bucket] = defaultdict(_Bucket)
    for game_id, players in by_game.items():
        mode = scope[game_id]
        for threshold in THRESHOLDS:
            kept = [p for p in players if p[2] >= threshold]
            if len(kept) < 3 or len({p[3] for p in kept}) < 2:
                continue
            _accumulate(buckets[(mode, threshold)], kept)
    for (mode, threshold), bucket in list(buckets.items()):  # « tous modes » se somme
        if mode != "tous":
            buckets[("tous", threshold)].absorb(bucket)
    return {
        "since": since,
        "games": len(scope),
        "cells": {f"{mode}|{t}": b.as_dict() for (mode, t), b in sorted(buckets.items())},
    }


def _accumulate(bucket: _Bucket, kept: list[tuple]) -> None:
    bucket.games += 1
    bucket.players.update(p[0] for p in kept)
    sp = spearman([-p[1] for p in kept], [p[3] for p in kept])
    if sp is not None:
        bucket.spearman.append(sp)
        xp = spearman([-p[2] for p in kept], [p[3] for p in kept])
        bucket.experience.append(xp if xp is not None else 0.0)
    for i in range(len(kept)):
        for j in range(i + 1, len(kept)):
            a, b = kept[i], kept[j]
            if a[3] == b[3]:
                continue
            won = a[3] < b[3]
            bucket.loss += _logloss(a[1] - b[1], won)
            bucket.naive += _logloss(0.0, won)
            bucket.pairs += 1
