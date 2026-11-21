"""Tests de cohérence du J2, tous sur les 27 vrais records de tests/fixtures/.

La référence n'est jamais une valeur écrite à la main : c'est le JSON brut, recompté
indépendamment par ces tests. Les fixtures couvrent juin 2025 (info réduit, publicID null,
`stats` null, `disableNPCs`, intents `troop_ratio` et `allianceRequestReply`) et novembre 2026.
"""

import json
import re
from collections import Counter

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from conftest import FIXTURE_DIR, fixture_ids

from ofa import ingest, normalize, raw


def records() -> dict[str, dict]:
    return {gid: json.loads(raw.read(FIXTURE_DIR, gid)) for gid in fixture_ids()}


@pytest.fixture(scope="module")
def tables(tmp_path_factory) -> dict:
    """Normalise les 27 fixtures une fois pour tout le module, puis relit les Parquet."""
    out = tmp_path_factory.mktemp("parquet")
    report = normalize.normalize([FIXTURE_DIR], out, chunk=5)
    return {
        "report": report,
        "out": out,
        **{t: pq.read_table(out / t).to_pylist() for t in normalize.TABLES},
    }


def test_intents_par_type_identiques_au_brut(tables):
    """Critère d'acceptation de la spec : aucun intent perdu, aucun inventé."""
    attendu = Counter()
    for record in records().values():
        for turn in record.get("turns") or []:
            for intent in turn.get("intents") or []:
                attendu[intent["type"]] += 1
    obtenu = Counter(row["type"] for row in tables["intents"])
    assert obtenu == attendu
    assert sum(attendu.values()) == 91784  # ancre : si le jeu de fixtures change, ça se voit
    # Les types disparus du moteur actuel sont bien là : `type` est du texte libre.
    assert attendu["troop_ratio"] > 0
    assert attendu["allianceRequestReply"] > 0


def test_intents_ordre_et_tick(tables):
    """`tick` est le turnNumber, `seq` porte l'ordre d'exécution dans le tick."""
    recs = records()
    vus = set()
    for row in tables["intents"]:
        cle = (row["game_id"], row["tick"], row["seq"])
        assert cle not in vus
        vus.add(cle)
    for game_id, record in recs.items():
        ticks = [row["tick"] for row in tables["intents"] if row["game_id"] == game_id]
        if ticks:
            assert max(ticks) < record["info"]["num_turns"]
    # L'ordre interne d'un tick est celui du record.
    game_id, record = next(iter(recs.items()))
    turn = next(t for t in record["turns"] if len(t.get("intents") or []) > 1)
    lignes = sorted(
        (
            r
            for r in tables["intents"]
            if r["game_id"] == game_id and r["tick"] == turn["turnNumber"]
        ),
        key=lambda r: r["seq"],
    )
    assert [r["type"] for r in lignes] == [i["type"] for i in turn["intents"]]


def test_integrite_referentielle(tables):
    parties = {row["game_id"] for row in tables["games"]}
    assert len(parties) == len(tables["games"]) == len(fixture_ids())
    assert {row["game_id"] for row in tables["participants"]} <= parties
    assert {row["game_id"] for row in tables["intents"]} <= parties
    couples = [(row["game_id"], row["client_id"]) for row in tables["participants"]]
    assert len(set(couples)) == len(couples)
    # num_intents de games == le compte réel dans intents
    compte = Counter(row["game_id"] for row in tables["intents"])
    for row in tables["games"]:
        assert (row["num_intents"] or 0) == compte[row["game_id"]]


def test_references_joueur_expliquees(tables):
    """56 % des références joueur d'un intent sont des bots ou des nations : mesuré, pas supposé."""
    participants = {(r["game_id"], r["client_id"]) for r in tables["participants"]}
    hors_participants = 0
    emetteurs_inconnus = 0
    for row in tables["intents"]:
        if (row["game_id"], row["client_id"]) not in participants:
            emetteurs_inconnus += 1
        other = row["other_id"]
        connue = other is None or other == "AllPlayers"
        if not connue and (row["game_id"], other) not in participants:
            hors_participants += 1
            # Un bot ou une nation a un id de meme forme qu'un clientID : c'est la seule
            # chose verifiable. Attrape un other_id rempli avec un pseudo, un numero de
            # tuile ou un nom d'equipe.
            assert re.fullmatch(r"[0-9A-Za-z]{8}", other), other
    assert hors_participants == 51304  # bots et nations, absents de info.players
    assert emetteurs_inconnus == 297  # clients partis avant la photo finale


def test_bigint_sans_perte(tables):
    """Les stats sont des bigint : int(chaîne), jamais float, et Int64 dans le Parquet."""
    schema = normalize.SCHEMAS["participants"]
    for column in normalize.STAT_COLUMNS:
        assert str(schema.field(column).type) == "int64"
    assert not any("float" in str(schema.field(i).type) for i in range(len(schema))), (
        "aucune colonne flottante dans participants"
    )

    lignes = {(r["game_id"], r["client_id"]): r for r in tables["participants"]}
    maximum = 0
    verifiees = 0
    for game_id, record in records().items():
        for player in record["info"]["players"]:
            brut = player.get("stats") or {}
            ligne = lignes[(game_id, player["clientID"])]
            for famille, noms in normalize.ARRAYS.items():
                valeurs = brut.get(famille)
                if valeurs is None:
                    continue
                if not isinstance(valeurs, list):
                    valeurs = [valeurs]
                for i, nom in enumerate(noms[: len(valeurs)]):
                    attendu = int(valeurs[i]) if valeurs[i] is not None else 0
                    assert ligne[f"{famille}_{nom}"] == attendu
                    maximum = max(maximum, attendu)
                    verifiees += 1
            if "peakTroops" in brut:
                assert ligne["peak_troops"] == int(brut["peakTroops"])
            if "kills" in brut:
                assert ligne["kills"] == [
                    {"victim": k["victim"], "tick": int(k["tick"])} for k in brut["kills"]
                ]
    assert verifiees > 10000
    assert maximum == 981904281  # la plus grande stat du jeu de fixtures (gold_trade)


def test_bigint_au_dela_de_32_bits(tmp_path):
    """La plus grande stat de l'archive ne tient pas sur 32 bits : Int64 n'est pas un luxe.

    `gold_trade = 22 202 217 393`, mesuré sur les 2 070 777 lignes joueur de data/raw/meta.
    Aucune fixture ne l'atteint, donc le cas est testé pour lui-même, bout en bout.
    """
    plat = normalize.flatten_stats({"gold": ["0", "0", "22202217393"]})
    assert plat["gold_trade"] == 22202217393

    schema = normalize.SCHEMAS["participants"]
    ligne = dict.fromkeys(schema.names)
    ligne.update({"game_id": "x", "client_id": "y", "has_stats": True, **plat})
    chemin = tmp_path / "part.parquet"
    pq.write_table(pa.Table.from_pylist([ligne], schema=schema), chemin)
    assert pq.read_table(chemin).to_pylist()[0]["gold_trade"] == 22202217393


def test_regles_de_remplissage(tables):
    """0 et « inconnu » ne sont pas la même chose : les trois règles, sur de vraies lignes."""
    sans_stats = [r for r in tables["participants"] if not r["has_stats"]]
    assert sans_stats
    for ligne in sans_stats:
        assert all(ligne[c] is None for c in normalize.STAT_COLUMNS)
        assert ligne["kills"] is None

    avec = [r for r in tables["participants"] if r["has_stats"]]
    # Regle 2 : famille absente -> 0 (le moteur n'ecrit une cle que quand il l'incremente).
    sans_bombes = [r for r in avec if r["bombs_abomb_launch"] == 0]
    assert sans_bombes
    # Regle 3 : killed_at / final_tiles absents -> NULL, jamais 0.
    assert any(r["killed_at"] is None for r in avec)
    assert any(r["final_tiles"] is None for r in avec)

    # Tableau tronque a droite : les vieux records n'ont que 3 des 4 compteurs d'attaques.
    ancien = json.loads(raw.read(FIXTURE_DIR, "00TxMvVK"))
    joueur = next(p for p in ancien["info"]["players"] if len(p["stats"]["attacks"]) == 3)
    plat = normalize.flatten_stats(joueur["stats"])
    assert plat["attacks_sent"] == int(joueur["stats"]["attacks"][0])
    assert plat["attacks_max_recv"] == 0  # index absent : 0 (et DATA.md dit « non suivi »)
    assert plat["units_city_upgrade"] == 0


def test_conquests_scalaire_est_accepte():
    """Forme antérieure à notre archive : `conquests` était un scalaire avant d'être un tableau."""
    plat = normalize.flatten_stats({"conquests": "7"})
    assert plat["conquests_human"] == 7
    assert plat["conquests_nation"] == 0


def test_cle_de_stat_inconnue_signalee(tmp_path):
    """Une stat ajoutée par le moteur ne doit pas disparaître en silence."""
    inconnues = Counter()
    normalize.flatten_stats({"deathPosition": 3, "gold": ["1"]}, inconnues)
    assert inconnues == {"deathPosition": 1}
    # Un tableau plus long que la dimension declaree est le meme signal.
    inconnues.clear()
    normalize.flatten_stats({"tiles": ["1", "2", "3", "4"]}, inconnues)
    assert inconnues == {"tiles[4]": 1}


def test_schema_stable_entre_lots(tmp_path):
    """Le piège du type Null : un lot sans valeur ne doit pas changer le type d'une colonne."""
    out = tmp_path / "parquet"
    out.mkdir()
    normalize.normalize([FIXTURE_DIR], out, chunk=1)
    for table in normalize.TABLES:
        fichiers = sorted(out.glob(f"{table}/**/*.parquet"))
        assert len(fichiers) >= 2  # juin 2025 et novembre 2026
        schemas = {pq.read_schema(f) for f in fichiers}
        assert len(schemas) == 1, f"{table} : {len(schemas)} schemas distincts"
        # et le glob entier se relit d'un coup
        assert pq.read_table(out / table).num_rows > 0


def test_pas_de_persistent_id(tables):
    """On n'identifie les joueurs que par publicID : persistentID ne doit jamais sortir."""
    assert "persistent_id" not in normalize.SCHEMAS["participants"].names
    valeurs = {str(v) for row in tables["participants"] for v in row.values() if isinstance(v, str)}
    assert "persistentID" not in normalize.SCHEMAS["games"].names
    for record in records().values():
        for player in record["info"]["players"]:
            pid = player.get("persistentID")
            if pid:
                assert pid not in valeurs


def test_hors_perimetre_marque_pas_plante(tables):
    """1v1, special et Humans Vs Nations sont normalisés, puis exclus par une clause where."""
    par_id = {row["game_id"]: row for row in tables["games"]}
    assert par_id["dd2oWu1Wkn"]["in_scope"] is False  # ranked 1v1
    assert par_id["cTbsbCRoAN"]["in_scope"] is False  # publicGameType special
    assert par_id["dKpQC4RCzq"]["in_scope"] is False  # Humans Vs Nations
    assert par_id["cPaLHdRvC2"]["in_scope"] is True
    # Les records de juin 2025 : info reduit, publicID null, stats null, disableNPCs.
    ancien = par_id["00TxMvVK"]
    assert ancien["in_scope"] is True
    assert ancien["lobby_created_at"] is None
    assert ancien["stats_agreed"] is None
    assert ancien["map_size"] is None
    assert "disableNPCs" in ancien["config_json"]
    sans_public_id = [
        r for r in tables["participants"] if r["game_id"] == "00TxMvVK" and r["public_id"] is None
    ]
    assert len(sans_public_id) == ancien["num_players"]


def test_won_et_winner(tables):
    """`won` est déduit du tuple winner, et vaut NULL quand aucun vainqueur n'est archivé."""
    par_id = {row["game_id"]: row for row in tables["games"]}
    ffa = par_id["cPaLHdRvC2"]
    gagnants = [r for r in tables["participants"] if r["game_id"] == "cPaLHdRvC2" and r["won"]]
    assert [r["client_id"] for r in gagnants] == [ffa["winner_id"]]
    equipe = next(r for r in tables["games"] if r["winner_kind"] == "team")
    membres = set(equipe["winner_members"])
    gagnants_equipe = {
        r["client_id"]
        for r in tables["participants"]
        if r["game_id"] == equipe["game_id"] and r["won"]
    }
    assert gagnants_equipe == membres
    sans_vainqueur = [r for r in tables["games"] if r["winner_kind"] is None]
    for row in sans_vainqueur:
        assert all(
            r["won"] is None for r in tables["participants"] if r["game_id"] == row["game_id"]
        )


def test_relance_identique(tmp_path):
    """Idempotence : deux reconstructions de suite donnent le même rapport."""
    out = tmp_path / "parquet"
    premier = normalize.rebuild([FIXTURE_DIR], out, chunk=7)
    second = normalize.rebuild([FIXTURE_DIR], out, chunk=7)
    assert premier == second
    assert not (out.with_name(out.name + ".tmp")).exists()
    assert not (out.with_name(out.name + ".old")).exists()


def test_une_partie_normalisee_une_seule_fois(tmp_path):
    """Le même répertoire deux fois ne doit pas dupliquer les lignes."""
    out = tmp_path / "parquet"
    out.mkdir()
    report = normalize.normalize([FIXTURE_DIR, FIXTURE_DIR], out, chunk=5)
    assert report["records"] == len(fixture_ids())
    assert report["by_dir"]["fixtures"] == len(fixture_ids())


def test_fetch_full_echantillonne_dans_le_perimetre(cfg):
    """L'étage 2 de l'ADR-0004 : seules les parties du périmètre, et jamais deux fois."""
    from conftest import FakeClient, fake_archive

    rows, records = fake_archive()
    for game_id, body in records.items():
        raw.write(cfg.raw_meta, game_id, body)
    client = FakeClient(rows, records)

    premier = ingest.fetch_full(cfg, client, sample=5, seed=1)
    assert premier["downloaded"] == 5
    assert premier["present"] == 0
    assert premier["bytes"] > 0
    telecharges = set(raw.iter_game_ids(cfg.raw_full))
    assert len(telecharges) == 5
    # Aucune partie hors perimetre d'analyse n'a ete telechargee.
    for game_id in telecharges:
        info = json.loads(raw.read(cfg.raw_full, game_id))["info"]
        assert normalize.in_scope(info)

    # Relance a graine identique : meme tirage, zero requete.
    avant = len(client.detail_calls)
    second = ingest.fetch_full(cfg, client, sample=5, seed=1)
    assert second["present"] == 5
    assert second["downloaded"] == 0
    assert len(client.detail_calls) == avant
    assert set(raw.iter_game_ids(cfg.raw_full)) == telecharges

    # Et ces records portent bien des intents, contrairement a ceux de meta.
    out = cfg.data_dir / "parquet"
    rapport = normalize.rebuild([cfg.raw_full, cfg.raw_meta], out)
    assert rapport["rows"]["intents"] > 0
    assert rapport["by_dir"]["full"] == 5
