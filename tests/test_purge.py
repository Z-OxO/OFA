"""Purge des comptes supprimés, sur de vrais records.

C'est la seule exception à l'immuabilité de data/raw, donc elle est testée de près :
le bon joueur et lui seul est expurgé, et l'index oublie le compte.
"""

import json

from conftest import FakeClient, fake_archive

from ofa import index, ingest, purge, raw
from ofa.client import Response


def test_redact_ne_touche_que_le_joueur_cible():
    from conftest import FIXTURE_DIR

    body = raw.read(FIXTURE_DIR, "cPaLHdRvC2")
    avant = json.loads(body)
    cible = avant["info"]["players"][3]["publicID"]
    autres = {
        p["clientID"]: p["username"] for p in avant["info"]["players"] if p["publicID"] != cible
    }

    nouveau, touches = purge.redact(body, cible)
    apres = json.loads(nouveau)
    assert touches == 1
    expurges = [p for p in apres["info"]["players"] if p["publicID"] == "REDACTED"]
    assert len(expurges) == 1
    assert expurges[0]["username"] == "REDACTED"
    # Tous les autres joueurs sont intacts : on n'efface pas la partie des 24 autres.
    assert {
        p["clientID"]: p["username"]
        for p in apres["info"]["players"]
        if p["publicID"] != "REDACTED"
    } == autres
    # Le reste du record est inchangé.
    assert apres["gitCommit"] == avant["gitCommit"]
    assert apres["info"]["winner"] == avant["info"]["winner"]
    assert apres["info"]["num_turns"] == avant["info"]["num_turns"]


def test_redact_sur_un_compte_absent_ne_change_rien():
    from conftest import FIXTURE_DIR

    body = raw.read(FIXTURE_DIR, "cPaLHdRvC2")
    nouveau, touches = purge.redact(body, "ZZZZZZZZ")
    assert touches == 0
    assert nouveau is body  # pas de réécriture inutile


class FakePurgeClient(FakeClient):
    def __init__(self, rows, records, deleted):
        super().__init__(rows, records)
        self.deleted = deleted

    def get(self, path, tries=5):
        if path.startswith("/players/recently-deleted"):
            self.calls.append(path)
            return Response(200, {}, json.dumps(self.deleted).encode())
        return super().get(path, tries)


def test_purge_de_bout_en_bout_sur_fixture(cfg):
    """Critère d'acceptation du J1 : test de purge sur fixture."""
    rows, records = fake_archive()
    conn = index.connect(cfg.index_db)
    ingest.ingest(cfg, FakeClient(rows, records), conn, "unranked", archive_start="2025-09-12")

    # On choisit un compte réellement présent dans plusieurs records ingérés.
    victime, nb_parties = conn.execute(
        "SELECT public_id, count(*) FROM participants GROUP BY public_id "
        "ORDER BY count(*) DESC LIMIT 1"
    ).fetchone()
    assert nb_parties >= 1
    parties = index.games_of(conn, victime)

    client = FakePurgeClient(
        rows, records, [{"publicId": victime, "deletedAt": "2026-11-20T10:00:00.000Z"}]
    )
    resume = purge.purge(cfg, client, conn)

    assert resume == {
        "deleted_listed": 1,
        "accounts_purged": 1,
        "records_redacted": len(parties),
    }
    # Le compte n'apparaît plus nulle part dans data/raw.
    for game_id in parties:
        rec = json.loads(raw.read(cfg.raw_meta, game_id))
        assert victime not in [p.get("publicID") for p in rec["info"]["players"]]
        assert any(p.get("publicID") == "REDACTED" for p in rec["info"]["players"])
    # L'index l'a oublié, et la purge est tracée.
    assert index.games_of(conn, victime) == []
    trace = conn.execute(
        "SELECT deleted_at, games FROM purged WHERE public_id = ?", (victime,)
    ).fetchone()
    assert trace == ("2026-11-20T10:00:00.000Z", len(parties))
    # Le curseur de purge est mémorisé pour le lendemain.
    assert purge.load_since(cfg) == "2026-11-20T10:00:00.000Z"


def test_purge_sans_suppression_ne_fait_rien(cfg):
    rows, records = fake_archive()
    conn = index.connect(cfg.index_db)
    client = FakePurgeClient(rows, records, [])
    resume = purge.purge(cfg, client, conn)
    assert resume == {"deleted_listed": 0, "accounts_purged": 0, "records_redacted": 0}
    assert purge.load_since(cfg) is None
