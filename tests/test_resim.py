"""Tests du J3 : le critere de validite (ADR-0003) et la conversion NDJSON -> Parquet.

Le critere est la piece la plus chere a se tromper du jalon : s'il est trop laxiste, le J4 et
le J5 travaillent sur des parties mal rejouees sans que rien ne le dise.
"""

import json
import os
from pathlib import Path

import pytest

from ofa import engine, resim

CLONE = Path(__file__).resolve().parent.parent / "data" / "openfront"


def record(**stats_par_joueur) -> dict:
    return {
        "info": {
            "winner": ["player", "cid-a"],
            "players": [
                {"clientID": cid, "stats": stats} for cid, stats in stats_par_joueur.items()
            ],
        }
    }


def sortie(winner=("player", "cid-a"), **stats_par_joueur) -> dict:
    return {
        "winner": list(winner),
        "allPlayersStats": dict(stats_par_joueur),
        "hashes": {"compared": 100, "matches": 100, "firstMismatch": None},
    }


# --------------------------------------------------------------------------------------
# ADR-0003 : ce qui fait qu'une re-sim est valide
# --------------------------------------------------------------------------------------


def test_diff_vide_quand_la_resim_est_fidele():
    rec = record(**{"cid-a": {"gold": ["1", "2"], "peakTroops": "3"}})
    out = sortie(**{"cid-a": {"gold": ["1", "2"], "peakTroops": "3"}})
    assert resim.diff_stats(rec, out) == []
    assert resim.classify(rec, out) == ("ok", [])


def test_bigint_compares_en_entiers_jamais_en_flottants():
    """22 202 217 393 fait 35 bits : en float64 l'egalite tiendrait encore, mais pas a 2^53."""
    enorme = str(2**60 + 1)
    rec = record(**{"cid-a": {"gold": [enorme]}})
    assert resim.diff_stats(rec, sortie(**{"cid-a": {"gold": [enorme]}})) == []
    presque = str(2**60 + 2)
    problemes = resim.diff_stats(rec, sortie(**{"cid-a": {"gold": [presque]}}))
    assert len(problemes) == 1 and "cid-a.gold" in problemes[0]


def test_champs_jamais_archives_exclus_seulement_s_ils_manquent_au_record():
    """ADR-0003 pt 5 : ce que la source ne contient pas ne peut pas diverger."""
    rec = record(**{"cid-a": {"gold": ["1"]}})
    out = sortie(**{"cid-a": {"gold": ["1"], "spawnTile": 42, "killedBy": "x", "deathPosition": 3}})
    assert resim.diff_stats(rec, out) == []
    # Mais si le record les porte un jour, ils redeviennent comparables.
    rec2 = record(**{"cid-a": {"gold": ["1"], "spawnTile": 7}})
    assert resim.diff_stats(rec2, out) != []


def test_joueur_sans_stats_des_deux_cotes_concorde():
    """ADR-0003 pt 4 : 5 cas sur la partie a 95 joueurs, absence des deux cotes = accord."""
    rec = record(**{"cid-a": {}, "cid-b": None})
    assert resim.diff_stats(rec, sortie()) == []


def test_joueur_avec_stats_absent_de_la_resim_est_un_ecart():
    rec = record(**{"cid-a": {"gold": ["1"]}})
    assert resim.diff_stats(rec, sortie()) != []


def test_gagnant_different_est_un_ecart():
    rec = record(**{"cid-a": {}})
    problemes = resim.diff_stats(rec, sortie(winner=("player", "cid-b")))
    assert problemes and problemes[0].startswith("GAGNANT")


def test_hash_divergent_prime_sur_les_stats():
    """Le pt 1 est la vraie garantie : elle ne depend pas de nous, mais de clients independants."""
    rec = record(**{"cid-a": {"gold": ["1"]}})
    out = sortie(**{"cid-a": {"gold": ["1"]}})
    out["hashes"] = {"compared": 100, "matches": 99, "firstMismatch": 450}
    status, detail = resim.classify(rec, out)
    assert status == "hash_divergent" and "450" in detail[0]


def test_record_sans_aucun_hash_ne_passe_pas_en_silence():
    rec = record(**{"cid-a": {}})
    out = sortie()
    out["hashes"] = {"compared": 0, "matches": 0, "firstMismatch": None}
    assert resim.classify(rec, out)[0] == "hash_absent"


def test_erreur_fatale_du_moteur_est_un_echec_de_runner():
    rec = record(**{"cid-a": {}})
    out = sortie()
    out["fatalError"] = "boom"
    assert resim.classify(rec, out)[0] == "runner_echec"


# --------------------------------------------------------------------------------------
# NDJSON du runner -> tables Arrow
# --------------------------------------------------------------------------------------


def ndjson(tmp_path: Path, rows: list[dict]) -> Path:
    p = tmp_path / "state.ndjson"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def etat(tick: int, small_id: int, **kw) -> dict:
    base = {
        "k": "s",
        "t": tick,
        "p": small_id,
        "tiles": 10,
        "gold": "1",
        "gold_earned": "1",
        "gold_trade": "0",
        "gold_train": "0",
        "gold_piracy": "0",
        "troops": "1",
        "allies": 0,
        "betrayals": 0,
        "traitor": False,
        "disconnected": False,
        "doomsday": False,
        "atk_out": 0,
        "atk_out_troops": "0",
        "atk_in": 0,
        "atk_in_troops": "0",
    }
    base.update({f"u_{k}": 0 for k in resim.UNIT_KEYS})
    base.update(kw)
    return base


def test_tables_depuis_le_flux(tmp_path):
    """Le tri par (small_id, tick) n'est pas cosmetique : mesure J0, il ramene la ligne de
    13,6 o a 5,00 o en Parquet zstd."""
    enorme = 2**40 + 7
    path = ndjson(
        tmp_path,
        [
            etat(10, 2),
            etat(10, 1, gold=str(enorme)),
            etat(20, 2),
            etat(20, 1),
            {"k": "e", "t": 15, "ev": "conquest", "a": 1, "b": 2, "gold": "500"},
            # Geographie des actions : la tuile et la largeur de front d'une attaque.
            {
                "k": "e",
                "t": 18,
                "ev": "attack_start",
                "a": 1,
                "b": 2,
                "troops": "400",
                "front": 12,
                "tile": 7351,
            },
            {
                "k": "e",
                "t": 12,
                "ev": "unit_built",
                "a": 1,
                "b": None,
                "ut": "City",
                "ui": 9,
                "lv": 2,
            },
            # Geographie (ADR-0011) : une ligne par (observateur, voisin). b = 0 est de la
            # terre libre, pas un joueur.
            {"k": "b", "t": 20, "a": 1, "b": 2, "n": 14},
            {"k": "b", "t": 10, "a": 1, "b": 0, "n": 31},
            {"k": "b", "t": 10, "a": 2, "b": 1, "n": 9},
            {
                "k": "p",
                "players": [
                    {
                        "small_id": 1,
                        "client_id": "cid-a",
                        "player_type": "FAKEHUMAN",
                        "username": "a",
                        "team": None,
                        "spawn_tile": 123,
                        "killed_by": None,
                        "death_position": None,
                        "final_tiles": 10,
                        "alive_at_end": True,
                        "spawn_x": 40,
                        "spawn_y": 12,
                        "spawn_shore": True,
                        "map_width": 200,
                        "map_height": 100,
                        "map_land": 9000,
                    }
                ],
            },
        ],
    )
    tables = resim.tables_from_stream(path, "gid", "2026-11")

    ticks = tables["player_ticks"]
    assert ticks.schema == resim.PLAYER_TICK_SCHEMA
    assert ticks.column("small_id").to_pylist() == [1, 1, 2, 2]
    assert ticks.column("tick").to_pylist() == [10, 20, 10, 20]
    # Un bigint au-dela de 2^32 traverse en int64 sans perte, et jamais en flottant.
    assert ticks.column("gold").to_pylist()[0] == enorme
    assert ticks.column("game_id").to_pylist() == ["gid"] * 4
    assert ticks.column("month").to_pylist() == ["2026-11"] * 4

    events = tables["events"]
    assert events.schema == resim.EVENT_SCHEMA
    assert events.column("tick").to_pylist() == [12, 15, 18]
    assert events.column("kind").to_pylist() == ["unit_built", "conquest", "attack_start"]
    assert events.column("unit_type").to_pylist() == ["City", None, None]
    assert events.column("gold").to_pylist() == [None, 500, None]
    # Un TileRef reste un entier, jamais un flottant : il sert d'indice (x = tile % width).
    assert events.column("tile").to_pylist() == [None, None, 7351]
    assert events.column("front").to_pylist() == [None, None, 12]
    # Catch-all (ADR-0007) : `lv` n'a pas de colonne promue, il n'est pas perdu pour autant.
    assert json.loads(events.column("params").to_pylist()[0]) == {"lv": 2}
    assert events.column("params").to_pylist()[1] is None
    assert events.column("params").to_pylist()[2] is None

    players = tables["players"]
    assert players.schema == resim.PLAYER_SCHEMA
    assert players.column("spawn_tile").to_pylist() == [123]
    assert players.column("team").to_pylist() == [None]
    assert players.column("spawn_x").to_pylist() == [40]
    assert players.column("spawn_shore").to_pylist() == [True]
    assert players.column("map_land").to_pylist() == [9000]
    assert players.column("disconnected_at").to_pylist() == [None]

    borders = tables["borders"]
    assert borders.schema == resim.BORDER_SCHEMA
    # Trie par (a, tick, b), pour la meme raison que player_ticks : des series monotones.
    assert list(
        zip(
            borders.column("a").to_pylist(),
            borders.column("tick").to_pylist(),
            borders.column("b").to_pylist(),
            borders.column("tiles").to_pylist(),
            strict=True,
        )
    ) == [(1, 10, 0, 31), (1, 20, 2, 14), (2, 10, 1, 9)]


def test_flux_vide_donne_des_tables_vides_au_bon_schema(tmp_path):
    """Un schema stable meme sans ligne : c'est le piege du J2, un lot vide typé Null."""
    path = tmp_path / "vide.ndjson"
    path.write_text("")
    tables = resim.tables_from_stream(path, "gid", "2026-11")
    assert set(tables) == set(resim.SCHEMAS)
    assert all(t.num_rows == 0 for t in tables.values())
    assert all(tables[name].schema == schema for name, schema in resim.SCHEMAS.items())


def test_la_barre_de_suivi_compte_les_valides():
    """Seules les parties valides avancent la barre : les echecs seront rejoues."""
    vide = resim.progress_line(0, 514, 514, 0.0, None)
    assert vide.startswith("[" + "-" * 24 + "] 0/514")
    assert "echecs 514" in vide and "reste -" in vide

    moitie = resim.progress_line(257, 514, 1, 600.0, 257 / 600)
    assert moitie.startswith("[" + "#" * 12 + "-" * 12 + "] 257/514")
    assert " 50.0%" in moitie
    assert "reste ~10 min" in moitie  # 257 parties restantes au meme debit

    fin = resim.progress_line(514, 514, 1, 1200.0, 0.4)
    assert fin.startswith("[" + "#" * 24 + "] 514/514")
    assert "100.0%" in fin and "reste -" in fin


def test_la_barre_ne_divise_pas_par_zero():
    """Aucune partie selectionnee : le suivi doit afficher, pas planter."""
    assert "0/0" in resim.progress_line(0, 0, 0, 0.0, None)


# --------------------------------------------------------------------------------------
# Preparation du moteur
# --------------------------------------------------------------------------------------


@pytest.mark.skipif(not CLONE.exists(), reason="clone de reference absent")
def test_nom_de_dossier_de_map():
    """La cle de l'enum GameMapType en minuscules, pas le nom affiche."""
    assert engine.map_dir_name(CLONE, "Ottoman Empire") == "ottomanempire"
    assert engine.map_dir_name(CLONE, "World") == "world"
    with pytest.raises(RuntimeError, match="map inconnue"):
        engine.map_dir_name(CLONE, "Atlantide")


@pytest.mark.skipif(not CLONE.exists(), reason="clone de reference absent")
def test_commit_absent_ou_bidon_est_refuse():
    data_dir = CLONE.parent
    assert not engine.commit_exists(data_dir, "")
    assert not engine.commit_exists(data_dir, "DEV")
    assert not engine.commit_exists(data_dir, "0" * 40)
    assert engine.commit_exists(data_dir, "8b5d071eed95f07c701b32e4c64e95b03f8577c1")


def test_eviction_garde_un_lockfile_encore_partage():
    """5 des 6 commits de l'archive partagent le meme lockfile : l'evincer au premier commit
    fini coûterait un `npm ci` de 15 s pour chacun des suivants."""
    lockfiles = {"aaa": "lock1", "bbb": "lock1", "ccc": "lock2"}
    # Le lot de `aaa` est fini, `bbb` et `ccc` attendent encore.
    finis, utiles = resim.evictions({"aaa": 0, "bbb": 3, "ccc": 1}, lockfiles)
    assert finis == ["aaa"]
    assert utiles == {"lock1", "lock2"}  # lock1 sert encore a bbb : on n'y touche pas
    # `bbb` fini a son tour : plus personne ne partage lock1.
    finis, utiles = resim.evictions({"bbb": 0, "ccc": 1}, lockfiles)
    assert finis == ["bbb"] and utiles == {"lock2"}
    # Plus rien en attente : aucun lockfile a garder.
    assert resim.evictions({"ccc": 0}, lockfiles) == (["ccc"], set())
    # Rien de fini : rien a evincer.
    assert resim.evictions({"aaa": 2}, lockfiles) == ([], {"lock1"})


def test_eviction_d_un_moteur_absent_ne_fait_rien():
    freed = engine.evict(Path("/inexistant"), "0" * 40, set())
    assert freed["worktree_mb"] == 0 and freed["erreur"] is None


@pytest.mark.slow
@pytest.mark.skipif(not CLONE.exists(), reason="clone de reference absent")
def test_une_vraie_partie_rejouee_de_bout_en_bout(tmp_path):
    """Le seul test qui exerce le moteur. Pas de mock : une vraie partie, au bon commit.

    Il attrape ce qu'aucun test rapide ne peut voir : un changement d'interface du moteur
    dans le clone, une map qui ne se materialise plus, un runner qui ne compile plus.
    """
    data_dir = CLONE.parent
    # Une vraie partie, mais la plus courte qui exerce quand meme les trajectoires, les
    # unites et la diplomatie : une partie de 211 ticks a 4 joueurs ne prouverait rien.
    jouables = [
        g
        for g in resim.candidates(data_dir / "parquet")
        if engine.is_ready(data_dir, g["git_commit"])
        and g["num_turns"] >= 3000
        and g["num_players"] >= 10
    ]
    if not jouables:
        pytest.skip("aucun moteur provisionne : lance `uv run ofa engines`")
    partie = min(jouables, key=lambda g: (g["num_turns"], g["game_id"]))
    summary = resim.run_one(
        {
            "data_dir": str(data_dir),
            "tmp": str(tmp_path),
            "game_id": partie["game_id"],
            "git_commit": partie["git_commit"],
            "num_turns": partie["num_turns"],
            "month": partie["month"],
        }
    )
    assert summary["status"] == "ok", summary["detail"]
    assert summary["hashes_compared"] == summary["hashes_matched"] > 0
    assert summary["rows"] > 1000
    tables = summary["tables"]
    assert tables["player_ticks"].num_rows > 0
    assert tables["events"].num_rows > 0
    assert tables["players"].num_rows >= partie["num_players"]


def test_workers_borne_par_la_ram():
    """0,8 Go par worker : sur le local c'est la RAM qui plafonne, pas les 20 coeurs."""
    assert 1 <= resim.workers_default() <= (os.cpu_count() or 1)
