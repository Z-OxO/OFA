"""Stockage brut : les octets exactement tels que reçus de l'API, compressés en zstd.

Deux étages (ADR-0004) : `meta/` contient la réponse de `?turns=false` pour tout le
périmètre, `full/` le record complet des parties qu'on re-simule.

Écriture atomique : fichier temporaire dans le même répertoire puis `os.replace`, donc
un kill -9 ne laisse jamais de fichier partiel que la reprise prendrait pour valide.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import zstandard

LEVEL = 19  # mesuré au J0 : -19 vaut -9 + 15 % de gain pour 0,5 s sur 1,5 Mo, négligeable ici


def path_for(base: Path, game_id: str) -> Path:
    """data/raw/<etage>/<2 premiers caracteres>/<gameID>.json.zst"""
    return base / game_id[:2].lower() / f"{game_id}.json.zst"


def exists(base: Path, game_id: str) -> bool:
    return path_for(base, game_id).exists()


def write(base: Path, game_id: str, body: bytes) -> int:
    """Écrit le corps tel quel, compressé. Retourne la taille sur disque."""
    dest = path_for(base, game_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    blob = zstandard.ZstdCompressor(level=LEVEL).compress(body)
    tmp = dest.with_suffix(dest.suffix + f".tmp{os.getpid()}")
    tmp.write_bytes(blob)
    os.replace(tmp, dest)
    return len(blob)


def read(base: Path, game_id: str) -> bytes:
    """Lecture en flux : une trame ecrite par le CLI zstd ne porte pas sa taille."""
    with (
        path_for(base, game_id).open("rb") as f,
        zstandard.ZstdDecompressor().stream_reader(f) as r,
    ):
        return r.read()


def iter_game_ids(base: Path) -> Iterator[str]:
    for p in sorted(base.glob("*/*.json.zst")):
        yield p.name.removesuffix(".json.zst")
