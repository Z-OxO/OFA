"""Tests de la METHODE (J5), sur donnees synthetiques dont on connait la verite.

La SPEC l'exige : un effet plante doit etre retrouve, et un effet qui ne vient que du niveau des
joueurs doit etre rejete. Ces deux tests ne touchent aucune vraie partie : ils testent
l'estimateur, pas les donnees. Le pendant sur vraies donnees est le controle negatif du criblage.
"""

import json
import random
from pathlib import Path

import duckdb
import pytest

from ofa import analyze
from ofa.config import Config

CFG = Config(
    data_dir=Path("/nonexistent"),
    rate=1.0,
    concurrency=1,
    contact="x",
    timeout=1.0,
    max_consecutive_errors=1,
)


def _table(con, rows):
    """Une table de sujets synthetique : les 5 colonnes que l'estimateur regarde."""
    con.execute("""
        create table subjects (
            game_id varchar, public_id varchar, tier_rating integer, b integer, y double
        )
    """)
    con.executemany("insert into subjects values (?, ?, ?, ?, ?)", rows)


def _mesure(con, draws=400):
    return analyze.measure(
        con,
        comportement="b = 1",
        resultat="y",
        grappe="public_id",
        strates=("tier_rating",),
        draws=draws,
    )


def test_effet_plante_retrouve():
    """Effet vrai = +0,10, pose sur un bruit et sur un effet de niveau trois fois plus gros."""
    rng = random.Random(1)
    rows = []
    for j in range(1200):  # des joueurs, pas des lignes : 1200 joueurs x ~3 parties
        tier = 1 + j % 4
        for g in range(3):
            b = 1 if rng.random() < 0.5 else 0
            y = 0.3 * tier + 0.10 * b + rng.gauss(0, 0.2)
            rows.append((f"g{g}", f"p{j}", tier, b, y))
    con = duckdb.connect()
    _table(con, rows)
    r = _mesure(con)
    assert r["n"] == len(rows)
    assert r["joueurs"] == 1200  # la grappe est le joueur, pas la ligne
    assert r["ic95"][0] < 0.10 < r["ic95"][1], r
    assert r["ic95"][0] > 0, r  # et l'effet est bien detecte comme non nul
    assert abs(r["effet"] - 0.10) < 0.02, r


def test_effet_du_seul_niveau_rejete():
    """Le comportement n'agit PAS : seul le niveau agit, et il pousse aussi au comportement.

    C'est le piege central de la SPEC ("les bons joueurs font beaucoup de choses en meme temps").
    Sans stratification, l'ecart brut est enorme ; avec, l'estimateur doit rendre zero.
    """
    rng = random.Random(2)
    rows = []
    for j in range(1200):
        tier = 1 + j % 4
        for g in range(3):
            b = 1 if rng.random() < 0.15 + 0.25 * tier else 0  # les bons le font plus
            y = 0.5 * tier + rng.gauss(0, 0.2)  # mais y ne depend QUE du niveau
            rows.append((f"g{g}", f"p{j}", tier, b, y))
    con = duckdb.connect()
    _table(con, rows)

    brut = con.execute(
        "select avg(y) filter (where b = 1) - avg(y) filter (where b = 0) from subjects"
    ).fetchone()[0]
    assert brut > 0.4, brut  # le test a des dents : l'association naive est massive

    r = _mesure(con)
    assert r["ic95"][0] <= 0 <= r["ic95"][1], r  # ... et l'estimateur la rejette
    assert abs(r["effet"]) < 0.03, r


def test_grappe_sur_le_joueur_elargit_l_intervalle():
    """Un meme joueur dans 10 parties n'apporte pas 10 observations independantes.

    Le comportement est une HABITUDE (constante chez un joueur) et le joueur a son propre niveau :
    c'est la situation reelle, et celle ou compter des lignes mentirait. Memes donnees, meme
    effet : seule la grappe change, et l'IC doit s'elargir quand on tire des joueurs entiers.
    (Si le comportement variait au hasard A L'INTERIEUR de chaque joueur, l'effet du joueur
    s'annulerait dans la grappe et l'IC se resserrerait : mesure faite, direction inverse.)
    """
    rng = random.Random(3)
    rows = []
    for j in range(200):
        biais = rng.gauss(0, 0.5)  # l'effet propre du joueur, partage par ses 10 parties
        b = 1 if rng.random() < 0.5 else 0  # son habitude, la meme dans les 10
        for g in range(10):
            rows.append((f"g{g}", f"p{j}", 1, b, 0.1 * b + biais + rng.gauss(0, 0.1)))
    con = duckdb.connect()
    _table(con, rows)

    par_joueur = _mesure(con)
    con.execute("alter table subjects add column ligne varchar")
    con.execute("update subjects set ligne = public_id || game_id")
    par_ligne = analyze.measure(
        con, comportement="b = 1", resultat="y", grappe="ligne", strates=("tier_rating",), draws=400
    )
    largeur = lambda r: r["ic95"][1] - r["ic95"][0]  # noqa: E731
    assert largeur(par_joueur) > largeur(par_ligne), (par_joueur, par_ligne)


def test_strate_sans_les_deux_bras_est_ecartee():
    """Une strate ou personne n'a le comportement n'apporte aucune comparaison."""
    rows = [("g", f"p{i}", 1, i % 2, 1.0 + (i % 2)) for i in range(400)]
    rows += [("g", f"q{i}", 2, 0, 50.0) for i in range(400)]  # tier 2 : un seul bras
    con = duckdb.connect()
    _table(con, rows)
    r = _mesure(con)
    assert r["strates"] == 1, r
    assert abs(r["effet"] - 1.0) < 0.001, r  # la strate muette ne tire pas l'effet vers 50


def test_fenetre_hors_multiple_de_dix_refusee():
    """L'etat n'est echantillonne qu'aux multiples de 10 ticks : le reste joint le vide."""
    with pytest.raises(SystemExit):
        analyze.build_subjects(None, t0=202, t1=1800, population="true", sample="tout")


def test_deuxieme_lecture_de_confirmation_refusee(tmp_path):
    """Une hypothese = une lecture. La regle est dans le code, pas dans la bonne volonte."""
    (tmp_path / "findings").mkdir()
    (tmp_path / "findings" / "reads.jsonl").write_text(
        json.dumps({"hypothese": "h1", "quand": "2026-11-22T10:00:00+0100", "sha_prereg": "abc"})
        + "\n"
    )
    spec = analyze.Spec(
        id="h1",
        enonce="peu importe",
        comportement="b = 1",
        resultat="rank_pct",
        sens_attendu="-",
        t0=210,
        t1=1800,
        controle="t0",
        population="true",
    )
    with pytest.raises(SystemExit, match="REFUSE"):
        analyze.confirm(CFG, tmp_path, spec, draws=10)
    assert len(analyze.reads(tmp_path)) == 1  # et rien n'a ete ajoute au journal


def test_benjamini_hochberg():
    # Cas classique : 4 p-valeurs, m/rang applique puis rendu monotone.
    assert [round(x, 4) for x in analyze.bh([0.01, 0.02, 0.03, 0.04])] == [0.04, 0.04, 0.04, 0.04]
    assert [round(x, 4) for x in analyze.bh([0.001, 0.5])] == [0.002, 0.5]
    assert analyze.bh([0.6]) == [0.6]


def test_deux_mesures_identiques_rendent_le_meme_chiffre():
    """La meme commande doit rendre le meme nombre. Deux fois, ca n'a pas ete le cas.

    1. `ntile` et le rang final tranchaient leurs ex aequo dans l'ordre de lecture des Parquet ;
    2. `random()` dans une requete parallelisee ne consomme pas sa graine dans un ordre stable.
    Resultat mesure avant correction : -0,05504 / -0,05298 / -0,05285 pour la meme commande.
    """
    rng = random.Random(7)
    rows = []
    for j in range(600):
        tier = 1 + j % 4
        for g in range(2):
            b = 1 if rng.random() < 0.5 else 0
            # Beaucoup d'ex aequo exacts, comme le rating a 1500 et les rangs partages.
            rows.append((f"g{g}", f"p{j}", tier, b, float(rng.randint(0, 3))))
    con = duckdb.connect()
    _table(con, rows)
    a = _mesure(con)
    b = _mesure(con)
    # A 1e-9 pres, et pas au bit pres : DuckDB somme les doubles en parallele, donc l'ordre des
    # additions varie et le dernier chiffre de la mantisse avec lui (mesure : 1e-16 d'ecart). Ce
    # qu'on exige, c'est qu'aucun CHOIX du code ne depende d'un ordre de lecture.
    assert abs(a["effet"] - b["effet"]) < 1e-9, (a["effet"], b["effet"])
    assert abs(a["ic95"][0] - b["ic95"][0]) < 1e-9, (a["ic95"], b["ic95"])
    assert abs(a["ic95"][1] - b["ic95"][1]) < 1e-9, (a["ic95"], b["ic95"])
    assert a["p"] == b["p"]
    assert a["strates"] == b["strates"] and a["joueurs"] == b["joueurs"]
