"""Configuration par variables d'environnement (le même code tourne en local et sur le serveur)."""

import os
from dataclasses import dataclass
from pathlib import Path

API_BASE = "https://api.openfront.io/public"

# L'archive exploitable commence quand publicID devient non nul (ADR-0005).
ARCHIVE_START = "2025-09-12"

# Périmètre d'ingestion : les rankedType partitionnent exactement les parties publiques
# (mesuré : 8348 unranked + 1038 1v1 + 179 2v2 + 0 + 0 = 9565 = total), donc on évite
# le ranked 1v1 sans jamais le télécharger. `special` et Humans Vs Nations ne sont pas
# filtrables depuis la liste (publicGameType en est absent) : ils sont ingérés puis
# exclus à l'analyse.
RANKED_TYPES = ("unranked", "2v2")


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class Config:
    data_dir: Path
    rate: float  # requêtes par seconde, en moyenne lissée
    concurrency: int  # requêtes simultanées
    contact: str  # adresse de contact placée dans le User-Agent
    timeout: float
    max_consecutive_errors: int

    @property
    def user_agent(self) -> str:
        return f"ofa/0.1 (OpenFront Analyzer; contact: {self.contact})"

    @property
    def raw_meta(self) -> Path:
        return self.data_dir / "raw" / "meta"

    @property
    def raw_full(self) -> Path:
        return self.data_dir / "raw" / "full"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def index_db(self) -> Path:
        return self.data_dir / "state" / "index.sqlite"


def contact_from_environment(root: Path) -> str:
    """OFA_CONTACT, sinon le fichier .ofa-contact (hors git) : jamais écrit dans le code."""
    contact = os.environ.get("OFA_CONTACT", "").strip()
    if contact:
        return contact
    f = root / ".ofa-contact"
    if f.exists():
        return f.read_text().strip()
    raise SystemExit(
        "Contact manquant. Crée .ofa-contact à la racine du projet (une ligne : "
        "l'adresse de contact à mettre dans le User-Agent), ou exporte OFA_CONTACT."
    )


def load(root: Path | None = None) -> Config:
    root = root or Path(__file__).resolve().parent.parent
    data_dir = Path(os.environ.get("OFA_DATA_DIR", root / "data"))
    return Config(
        data_dir=data_dir,
        # 4 req/s pour le rattrapage historique, 1 req/s pour l'incrémental quotidien
        # (OFA_RATE=1) : voir docs/adr/0006-debit.md.
        rate=_env_float("OFA_RATE", 4.0),
        concurrency=_env_int("OFA_CONCURRENCY", 2),
        contact=contact_from_environment(root),
        timeout=_env_float("OFA_TIMEOUT", 120.0),
        max_consecutive_errors=_env_int("OFA_MAX_CONSECUTIVE_ERRORS", 3),
    )
