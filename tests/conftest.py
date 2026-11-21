"""Outillage des tests : config isolée, horloge contrôlée, faux client, archive factice.

L'archive factice est construite à partir des **vrais** records de tests/fixtures/ :
seuls les identifiants et l'ordre sont inventés, le contenu est authentique.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ofa import raw
from ofa.client import Response
from ofa.config import Config

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def compression_rapide(monkeypatch):
    """zstd -19 coute 0,5 s sur un gros record : inutile de le payer a chaque test.

    Le niveau de production reste teste explicitement par test_niveau_de_production.
    """
    monkeypatch.setattr(raw, "LEVEL", 3)


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(
        data_dir=tmp_path,
        rate=1000.0,  # les tests ne doivent pas attendre
        concurrency=2,
        contact="tests@example.invalid",
        timeout=5.0,
        max_consecutive_errors=3,
    )


class FakeClock:
    """Horloge et sommeil contrôlés : les tests de débit sont instantanés et déterministes."""

    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def fixture_ids() -> list[str]:
    return sorted(p.name.removesuffix(".json.zst") for p in FIXTURE_DIR.glob("*/*.json.zst"))


def fake_archive(game_ids: list[str] | None = None) -> tuple[list[dict], dict[str, bytes]]:
    """Construit (lignes de liste, records) depuis les vraies fixtures, triées par date."""
    ids = game_ids or fixture_ids()
    entries = []
    for game_id in ids:
        body = raw.read(FIXTURE_DIR, game_id)
        info = json.loads(body)["info"]
        started = datetime.fromtimestamp(info["start"] / 1000, UTC)
        entries.append((started, game_id, body))
    entries.sort()
    rows, records = [], {}
    for n, (started, game_id, body) in enumerate(entries, start=1):
        rows.append(
            {
                "id": str(n),  # l'API renvoie une chaîne décimale
                "game": game_id,
                "start": started.isoformat().replace("+00:00", "Z"),
                "type": "Public",
                "mode": "Free For All",
                "rankedType": "unranked",
            }
        )
        records[game_id] = body
    return rows, records


class FakeClient:
    """Même interface que Client.get, sans réseau. Compte et classe les appels."""

    def __init__(
        self, rows: list[dict], records: dict[str, bytes], fail: dict[str, int] | None = None
    ):
        self.rows = rows
        self.records = records
        self.fail = fail or {}
        self.calls: list[str] = []
        self.crash_after_details: int | None = None
        self._details = 0

    @property
    def detail_calls(self) -> list[str]:
        return [c for c in self.calls if c.startswith("/game/")]

    @property
    def list_calls(self) -> list[str]:
        return [c for c in self.calls if c.startswith("/games?")]

    def get(self, path: str, tries: int = 5) -> Response:
        self.calls.append(path)
        if path.startswith("/games?"):
            params = dict(p.split("=", 1) for p in path.split("?", 1)[1].split("&"))
            limit = int(params["limit"])
            if "afterId" in params:
                after = int(params["afterId"])
                page = [r for r in self.rows if int(r["id"]) > after][:limit]
                return Response(200, {}, json.dumps(page).encode())
            # Mode fenetre : tri chronologique, curseur `after=<start>,<game>`.
            day = params["start"][:10]
            window = sorted(
                (r for r in self.rows if r["start"][:10] == day),
                key=lambda r: (r["start"], r["game"]),
            )
            if "after" in params:
                start, game = params["after"].split(",", 1)
                window = [r for r in window if (r["start"], r["game"]) > (start, game)]
            page = window[:limit]
            headers = (
                {} if "after" in params else {"Content-Range": f"games 0-{len(page)}/{len(window)}"}
            )
            return Response(200, headers, json.dumps(page).encode())
        if path.startswith("/game/"):
            game_id = path.removeprefix("/game/").split("?", 1)[0]
            self._details += 1
            if self.crash_after_details is not None and self._details > self.crash_after_details:
                raise KeyboardInterrupt("kill -9 simule au milieu d'une page")
            if game_id in self.fail:
                return Response(self.fail[game_id], {}, b"{}")
            return Response(200, {}, self.records[game_id])
        raise AssertionError(f"chemin inattendu dans le faux client : {path}")
