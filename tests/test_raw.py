"""Stockage brut : aller-retour sur de vrais records, et écriture atomique."""

import json

import pytest
from conftest import FIXTURE_DIR, fixture_ids

from ofa import raw


def test_aller_retour_sur_trois_vrais_records(tmp_path):
    for game_id in fixture_ids()[:3]:
        body = raw.read(FIXTURE_DIR, game_id)
        raw.write(tmp_path, game_id, body)
        assert raw.read(tmp_path, game_id) == body
        assert json.loads(body)["info"]["gameID"] == game_id


@pytest.mark.slow
def test_aller_retour_sur_tous_les_vrais_records(tmp_path):
    """zstd -19 sur les 27 records prend une trentaine de secondes : hors suite rapide."""
    for game_id in fixture_ids():
        body = raw.read(FIXTURE_DIR, game_id)
        raw.write(tmp_path, game_id, body)
        assert raw.read(tmp_path, game_id) == body
        assert json.loads(body)["info"]["gameID"] == game_id


def test_arborescence_par_prefixe(tmp_path):
    raw.write(tmp_path, "cPaLHdRvC2", b"{}")
    assert (tmp_path / "cp" / "cPaLHdRvC2.json.zst").exists()
    assert raw.exists(tmp_path, "cPaLHdRvC2")


def test_aucun_fichier_temporaire_ne_survit(tmp_path):
    raw.write(tmp_path, "cPaLHdRvC2", b'{"a":1}')
    assert list(tmp_path.glob("**/*.tmp*")) == []


def test_un_temporaire_abandonne_nest_jamais_pris_pour_une_partie(tmp_path):
    """Scénario kill -9 pendant une écriture : le .tmp traîne mais reste invisible."""
    raw.write(tmp_path, "cPaLHdRvC2", b'{"a":1}')
    partiel = tmp_path / "cp" / "cPnBjX152J.json.zst.tmp999"
    partiel.write_bytes(b"\x00 moitie ecrit")
    assert not raw.exists(tmp_path, "cPnBjX152J")
    assert list(raw.iter_game_ids(tmp_path)) == ["cPaLHdRvC2"]


def test_lecture_dun_fichier_absent_echoue_clairement(tmp_path):
    with pytest.raises(FileNotFoundError):
        raw.read(tmp_path, "inexistant")


def test_niveau_de_production(tmp_path, monkeypatch):
    """Le niveau 19 reellement utilise en production doit marcher et compresser mieux."""
    monkeypatch.setattr(raw, "LEVEL", raw_production_level())
    body = raw.read(FIXTURE_DIR, "cPaLHdRvC2")
    taille19 = raw.write(tmp_path, "cPaLHdRvC2", body)
    monkeypatch.setattr(raw, "LEVEL", 3)
    taille3 = raw.write(tmp_path / "bas", "cPaLHdRvC2", body)
    assert taille19 < taille3
    assert raw.read(tmp_path, "cPaLHdRvC2") == body


def raw_production_level() -> int:
    """Lit la constante du module source, pas la valeur monkeypatchee par la fixture."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(raw))
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.targets[0].id == "LEVEL":
            return node.value.value
    raise AssertionError("LEVEL introuvable dans ofa/raw.py")
