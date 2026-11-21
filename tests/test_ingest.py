"""Parcours de l'archive : reprise sans trou ni doublon, et zéro requête inutile.

Ce fichier porte trois des critères d'acceptation du J1.
"""

import contextlib
import json

from conftest import FakeClient, fake_archive

from ofa import index, ingest, raw

START = "2025-09-12"  # ADR-0005


def _perimetre(rows):
    return [r["game"] for r in rows if r["start"][:10] >= START]


def test_parcours_complet_ecrit_toutes_les_parties_du_perimetre(cfg):
    rows, records = fake_archive()
    client = FakeClient(rows, records)
    conn = index.connect(cfg.index_db)
    stats = ingest.ingest(cfg, client, conn, "unranked", limit=10, archive_start=START)

    attendues = _perimetre(rows)
    assert stats.written == len(attendues)
    assert stats.skipped_before_start == len(rows) - len(attendues)
    assert sorted(raw.iter_game_ids(cfg.raw_meta)) == sorted(attendues)
    # Chaque record stocké est relisible et c'est bien le bon.
    for game_id in attendues:
        assert json.loads(raw.read(cfg.raw_meta, game_id))["info"]["gameID"] == game_id


def test_une_relance_ne_fait_aucune_requete_de_detail(cfg):
    """Critère d'acceptation : 0 requête pour les parties déjà présentes."""
    rows, records = fake_archive()
    conn = index.connect(cfg.index_db)

    first = FakeClient(rows, records)
    ingest.ingest(cfg, first, conn, "unranked", limit=10, archive_start=START)
    assert len(first.detail_calls) == len(_perimetre(rows))

    second = FakeClient(rows, records)
    stats = ingest.ingest(cfg, second, conn, "unranked", limit=10, archive_start=START)
    assert second.detail_calls == []
    assert stats.written == 0
    assert stats.skipped_present == len(_perimetre(rows))


def test_kill_au_milieu_puis_reprise_sans_trou_ni_doublon(cfg):
    """Critère d'acceptation : un kill -9 en plein milieu, puis une reprise.

    Le crash cible une partie nommée, pas un numéro d'appel : c'est déterministe même
    avec plusieurs requêtes en vol, donc le test vaut aussi pour le chemin parallèle.
    """
    rows, records = fake_archive()
    conn = index.connect(cfg.index_db)
    attendues = _perimetre(rows)
    victime = attendues[5]

    crashed = FakeClient(rows, records)
    crashed.crash_on_game = victime
    with contextlib.suppress(KeyboardInterrupt):
        ingest.ingest(cfg, crashed, conn, "unranked", limit=10, archive_start=START)

    ecrits_avant = set(raw.iter_game_ids(cfg.raw_meta))
    assert victime not in ecrits_avant  # la partie fautive n'est pas sur disque
    assert ecrits_avant  # mais le travail d'avant est conserve
    assert ingest.load_cursor(cfg, "unranked") == 0  # page inachevee : curseur immobile
    assert list(cfg.raw_meta.glob("**/*.tmp*")) == []

    resumed = FakeClient(rows, records)
    ingest.ingest(cfg, resumed, conn, "unranked", limit=10, archive_start=START)

    apres = sorted(raw.iter_game_ids(cfg.raw_meta))
    assert apres == sorted(attendues)  # aucun trou
    assert len(apres) == len(set(apres))  # aucune donnee dupliquee
    assert list(cfg.raw_meta.glob("**/*.tmp*")) == []

    # Chaque partie a ete demandee au moins une fois, et le surcout de la reprise est
    # borne par ce qui etait en vol au moment du crash.
    total = crashed.detail_calls + resumed.detail_calls
    assert set(total) == {f"/game/{g}?turns=false" for g in attendues}
    assert len(total) - len(set(total)) <= cfg.concurrency + 1


def test_le_parallelisme_ecrit_exactement_les_memes_parties(cfg):
    """Deux requetes en vol ne doivent rien changer au resultat, seulement au debit."""
    from dataclasses import replace

    rows, records = fake_archive()
    attendues = sorted(_perimetre(rows))

    sequentiel = replace(cfg, concurrency=1, data_dir=cfg.data_dir / "seq")
    conn1 = index.connect(sequentiel.index_db)
    s1 = ingest.ingest(
        sequentiel, FakeClient(rows, records), conn1, "unranked", limit=7, archive_start=START
    )

    parallele = replace(cfg, concurrency=2, data_dir=cfg.data_dir / "par")
    conn2 = index.connect(parallele.index_db)
    s2 = ingest.ingest(
        parallele, FakeClient(rows, records), conn2, "unranked", limit=7, archive_start=START
    )

    assert sorted(raw.iter_game_ids(sequentiel.raw_meta)) == attendues
    assert sorted(raw.iter_game_ids(parallele.raw_meta)) == attendues
    assert s1.written == s2.written == len(attendues)
    assert s1.bytes_written == s2.bytes_written


def test_la_marge_de_relecture_est_appliquee_a_la_reprise(cfg):
    """Une partie archivée en retard peut recevoir un id sous le curseur."""
    rows, records = fake_archive()
    conn = index.connect(cfg.index_db)
    ingest.ingest(cfg, FakeClient(rows, records), conn, "unranked", limit=100, archive_start=START)
    curseur = ingest.load_cursor(cfg, "unranked")
    assert curseur == max(int(r["id"]) for r in rows)

    # Une partie arrive après coup avec un id déjà dépassé.
    retardataire = {
        "id": str(curseur - 3),
        "game": "ZZretard01",
        "start": "2026-11-20T00:00:00Z",
        "type": "Public",
        "mode": "Team",
        "rankedType": "unranked",
    }
    rows2 = sorted([*rows, retardataire], key=lambda r: int(r["id"]))
    records2 = {**records, "ZZretard01": records[rows[0]["game"]]}
    client = FakeClient(rows2, records2)
    stats = ingest.ingest(cfg, client, conn, "unranked", limit=100, archive_start=START)
    assert stats.written == 1
    assert raw.exists(cfg.raw_meta, "ZZretard01")


def test_un_404_est_memorise_et_nest_pas_rejoue(cfg):
    rows, records = fake_archive()
    cible = _perimetre(rows)[0]
    conn = index.connect(cfg.index_db)

    first = FakeClient(rows, records, fail={cible: 404})
    stats = ingest.ingest(cfg, first, conn, "unranked", limit=100, archive_start=START)
    assert stats.errors == {404: 1}
    assert not raw.exists(cfg.raw_meta, cible)

    second = FakeClient(rows, records, fail={cible: 404})
    stats2 = ingest.ingest(cfg, second, conn, "unranked", limit=100, archive_start=START)
    assert cible not in [c.removeprefix("/game/").split("?")[0] for c in second.detail_calls]
    assert stats2.skipped_failed == 1


def test_lindex_des_participants_est_rempli(cfg):
    rows, records = fake_archive()
    conn = index.connect(cfg.index_db)
    ingest.ingest(cfg, FakeClient(rows, records), conn, "unranked", limit=100, archive_start=START)
    total = conn.execute("SELECT count(*) FROM participants").fetchone()[0]
    assert total > 500  # les vraies fixtures portent des milliers de lignes joueur
    # Les publicID null des vieux records ne doivent pas y entrer.
    assert (
        conn.execute(
            "SELECT count(*) FROM participants WHERE public_id IS NULL OR public_id = 'REDACTED'"
        ).fetchone()[0]
        == 0
    )


def test_mode_fenetre_ingere_une_journee_et_la_marque_terminee(cfg):
    rows, records = fake_archive()
    conn = index.connect(cfg.index_db)
    # Une journée ancienne : elle est close depuis longtemps, donc marquable.
    jour = "2026-11-19"
    attendues = [r["game"] for r in rows if r["start"][:10] == jour]
    assert attendues, "la journee choisie doit contenir des fixtures"

    client = FakeClient(rows, records)
    stats = ingest.ingest_day(cfg, client, conn, "unranked", jour, limit=2, archive_start=START)
    assert stats.written == len(attendues)
    assert stats.total_annonce == len(attendues)  # lu dans Content-Range
    assert ingest.day_marker(cfg, "unranked", jour).exists()

    # Une relance ne coûte aucune requête : la journée est marquée terminée.
    encore = FakeClient(rows, records)
    stats2 = ingest.ingest_day(cfg, client, conn, "unranked", jour, limit=2, archive_start=START)
    assert encore.calls == []
    assert stats2.written == 0


def test_mode_fenetre_ne_marque_pas_une_journee_encore_ouverte(cfg):
    from datetime import UTC, datetime

    rows, records = fake_archive()
    conn = index.connect(cfg.index_db)
    aujourdhui = datetime.now(UTC).date().isoformat()
    ingest.ingest_day(
        cfg, FakeClient(rows, records), conn, "unranked", aujourdhui, archive_start=START
    )
    # Des parties peuvent encore être archivées en retard : on ne ferme pas la journée.
    assert not ingest.day_marker(cfg, "unranked", aujourdhui).exists()


def test_demarrage_refuse_si_un_chemin_est_pris_par_un_fichier(cfg):
    """Un fichier vide a la place de data/raw doit arreter ofa immediatement.

    Vecu pendant le J1 : le sandbox avait materialise data/raw en fichier vide.
    Sans ce garde-fou, l'ingestion de 7 jours mourait apres plusieurs heures.
    """
    import pytest

    from ofa.cli import prepare

    (cfg.data_dir / "raw").write_text("")  # ce qu'on a reellement trouve sur le disque
    with pytest.raises(SystemExit) as erreur:
        prepare(cfg)
    assert "n'est pas un repertoire" in str(erreur.value)


def test_demarrage_cree_les_repertoires_manquants(cfg):
    from ofa.cli import prepare

    prepare(cfg)
    for chemin in (cfg.raw_meta, cfg.raw_full, cfg.state_dir, cfg.log_dir):
        assert chemin.is_dir()
