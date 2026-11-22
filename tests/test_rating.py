"""Tests du J4. Le modèle se teste sur un effet planté, le classement sur les vrais records.

Le rating n'est pas une fin : ce qui est vérifié ici, c'est qu'il est utilisable comme contrôle
« à niveau égal » par le J5 — qu'il apprend vraiment (effet planté), qu'il lit le résultat des
parties tel qu'il est archivé (fixtures), et qu'un joueur sans publicID ne fabrique rien.
"""

import math
import random

import pyarrow.parquet as pq
import pytest
from conftest import FIXTURE_DIR

from ofa import normalize, rating

# --------------------------------------------------------------------------------------
# Le modèle, sans données
# --------------------------------------------------------------------------------------


def test_gradient_somme_a_zero():
    """Le rating est à somme nulle : ce qu'un joueur gagne, les autres le perdent."""
    groups = [[0], [1, 2], [3], [4, 5, 6]]
    theta = [0.9, 0.1, -0.4, 1.7, 0.0, -2.0, 0.3]
    assert abs(sum(rating.gradients(groups, theta))) < 1e-12


def test_gradient_decroit_avec_le_rang():
    """À force égale, mieux classé = gradient plus grand. Sinon le rating apprend à l'envers."""
    groups = [[0], [1], [2], [3], [4]]
    grad = rating.gradients(groups, [0.0] * 5)
    assert grad == sorted(grad, reverse=True)
    assert grad[0] > 0 > grad[-1]


def test_sur_deux_joueurs_c_est_l_elo_par_paire():
    """Le cas n = 2 doit redonner exactement 1 - p(A gagne), la formule de l'Elo."""
    theta = [0.6, -0.2]
    attendu = 1 / (1 + math.exp(-(theta[0] - theta[1])))
    grad = rating.gradients([[0], [1]], theta)
    assert grad[0] == pytest.approx(1 - attendu)
    assert grad[1] == pytest.approx(-(1 - attendu))


def test_gradient_ne_deborde_pas():
    """exp(50) déborde sans recentrage : un joueur très bien noté ne doit pas tuer la passe."""
    grad = rating.gradients([[0], [1], [2]], [50.0, -50.0, 0.0])
    assert all(math.isfinite(g) for g in grad)
    assert abs(sum(grad)) < 1e-9


def test_groupe_unique_na_pas_de_classement():
    assert rating.outcome_groups("Free For All", [(None, None, 5, False)] * 3) is None


def test_team_sans_vainqueur_na_pas_de_classement():
    rows = [("a", 10, None, False), ("b", 20, None, None)]
    assert rating.outcome_groups("Team", rows) is None


def test_effet_plante_le_rating_retrouve_les_forces():
    """SPEC § Test de la méthode : un effet planté doit être retrouvé.

    50 joueurs de forces connues, 400 parties de 8 joueurs tirées par le modèle lui-même.
    Le rating final doit retrouver l'ordre des forces.
    """
    rng = random.Random(20261122)
    truth = {f"p{i}": rng.gauss(0, 1) for i in range(50)}
    names = list(truth)
    theta = dict.fromkeys(names, 0.0)
    played = dict.fromkeys(names, 0)
    for _ in range(400):
        table = rng.sample(names, 8)
        # Tirage de Plackett-Luce : on choisit le premier proportionnellement à exp(force).
        order, pool = [], list(table)
        while pool:
            weights = [math.exp(truth[n]) for n in pool]
            pick = rng.choices(range(len(pool)), weights=weights)[0]
            order.append(pool.pop(pick))
        groups = [[i] for i in range(len(order))]
        grad = rating.gradients(groups, [theta[n] for n in order])
        for i, name in enumerate(order):
            theta[name] += rating.RATING_LR0 / (1 + played[name] / rating.RATING_HALFLIFE) * grad[i]
            played[name] += 1
    rho = rating.spearman([truth[n] for n in names], [theta[n] for n in names])
    assert rho > 0.8, f"le modèle n'apprend pas : Spearman {rho:.3f}"


def test_spearman_connait_ses_cas_limites():
    assert rating.spearman([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)
    assert rating.spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert rating.spearman([1, 1, 1], [1, 2, 3]) is None  # aucune variance : pas de corrélation
    assert rating.spearman([1, 2], [2, 1]) is None  # trop peu de points


# --------------------------------------------------------------------------------------
# Le classement, sur les vrais records
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def monde(tmp_path_factory) -> dict:
    """Normalise les fixtures une fois, puis fait tourner la passe complète dessus."""
    data = tmp_path_factory.mktemp("data")
    parquet, out = data / "parquet", data / "rating"
    normalize.normalize([FIXTURE_DIR], parquet, chunk=5)
    report = rating.rebuild(parquet, out)
    rows = pq.read_table(out).to_pylist() if report["rows"] else []
    return {"parquet": parquet, "out": out, "report": report, "rows": rows}


def test_le_vainqueur_ffa_est_toujours_premier(monde):
    """Contre-épreuve : `won` n'entre pas dans le classement, il doit en sortir.

    Mesuré sur l'archive : le vainqueur est l'argmax de `final_tiles` 34 380 fois sur 34 380.
    Si cet invariant casse un jour, c'est ici qu'on l'apprend.
    """
    games = {g["game_id"]: g for g in pq.read_table(monde["parquet"] / "games").to_pylist()}
    parts = pq.read_table(monde["parquet"] / "participants").to_pylist()
    gagnants = {
        (p["game_id"], p["public_id"])
        for p in parts
        if p["won"]
        and games[p["game_id"]]["mode"] == "Free For All"
        and games[p["game_id"]]["in_scope"]  # le ranked 1v1 et `special` ne sont pas notes
        and p["public_id"] not in (None, rating.REDACTED)
    }
    premiers = {(r["game_id"], r["public_id"]) for r in monde["rows"] if r["rank"] == 1}
    assert gagnants, "aucune fixture FFA avec vainqueur : le test ne prouverait rien"
    assert gagnants <= premiers


def test_les_vivants_precedent_les_morts(monde):
    """Classement FFA : vivant à la fin devant mort, mort tardive devant mort précoce."""
    rows = [
        ("a", None, 400, False),
        ("b", 900, None, False),
        ("c", None, 900, False),
        ("d", 100, None, False),
    ]
    groups = rating.outcome_groups("Free For All", rows)
    assert [rows[g[0]][0] for g in groups] == ["c", "a", "b", "d"]


def test_les_ex_aequo_forment_un_seul_groupe():
    rows = [("a", 500, None, False), ("b", 500, None, False), ("c", 900, None, False)]
    groups = rating.outcome_groups("Free For All", rows)
    assert groups == [[2], [0, 1]]
    assert rating.ranks_of(groups, 3) == [2, 2, 1]  # rang de compétition


def test_une_partie_team_donne_deux_groupes(monde):
    games = pq.read_table(monde["parquet"] / "games").to_pylist()
    team = [
        g for g in games if g["mode"] == "Team" and g["winner_kind"] == "team" and g["in_scope"]
    ]
    assert team, "aucune fixture Team dans le périmètre"
    parts = pq.read_table(monde["parquet"] / "participants").to_pylist()
    for game in team[:3]:
        rows = [
            (p["public_id"], p["killed_at"], p["final_tiles"], p["won"])
            for p in parts
            if p["game_id"] == game["game_id"]
            and rating.has_outcome(p["killed_at"], p["final_tiles"])
        ]
        groups = rating.outcome_groups("Team", rows)
        assert groups is not None and len(groups) == 2
        assert all(rows[i][3] for i in groups[0])
        assert not any(rows[i][3] for i in groups[1])


# --------------------------------------------------------------------------------------
# Les joueurs sans publicID (critère d'acceptation de la spec)
# --------------------------------------------------------------------------------------


def test_une_partie_entierement_anonyme_necrit_rien(monde):
    """Les records de juin 2025 n'ont aucun publicID : ils ne doivent ni planter ni produire."""
    parts = pq.read_table(monde["parquet"] / "participants").to_pylist()
    anonymes = {
        g
        for g in {p["game_id"] for p in parts}
        if all(p["public_id"] is None for p in parts if p["game_id"] == g)
    }
    assert anonymes, "aucune fixture sans publicID : le critère ne serait pas testé"
    assert anonymes & {r["game_id"] for r in monde["rows"]} == set()
    assert monde["report"]["anonymous"] > 0


def test_redacted_ne_capitalise_pas_mais_compte_dans_le_terrain(monde):
    """Un compte purgé n’a pas de ligne, mais il occupe une place dans le terrain."""
    assert rating.REDACTED not in {r["public_id"] for r in monde["rows"]}
    parts = pq.read_table(monde["parquet"] / "participants").to_pylist()
    expurges = [p for p in parts if p["public_id"] == rating.REDACTED]
    assert expurges, "aucune fixture REDACTED : le critère ne serait pas testé"
    partie = expurges[0]["game_id"]
    lignes = [r for r in monde["rows"] if r["game_id"] == partie]
    assert lignes, "la partie du compte expurgé doit quand même être notée"
    classes = sum(
        1
        for p in parts
        if p["game_id"] == partie and rating.has_outcome(p["killed_at"], p["final_tiles"])
    )
    assert lignes[0]["field_size"] == classes > len(lignes)


def test_retirer_un_anonyme_change_le_gradient_des_autres():
    """« Ils comptent dans la normalisation » doit être vrai, pas décoratif."""
    avec = rating.gradients([[0], [1], [2]], [0.0, 0.0, 0.0])
    sans = rating.gradients([[0], [1]], [0.0, 0.0])
    assert avec[0] != pytest.approx(sans[0])


# --------------------------------------------------------------------------------------
# La table
# --------------------------------------------------------------------------------------


def test_la_passe_est_deterministe(monde):
    """Deux passes sur les mêmes Parquet doivent donner exactement les mêmes lignes."""
    second = monde["out"].with_name("rating2")
    rating.rebuild(monde["parquet"], second)
    assert pq.read_table(second).to_pylist() == monde["rows"]


def test_une_ligne_par_partie_et_par_joueur(monde):
    cles = [(r["game_id"], r["public_id"]) for r in monde["rows"]]
    assert len(cles) == len(set(cles))


def test_le_rating_se_chaine_dans_le_temps(monde):
    """`rating_after` d'une partie est le `rating_before` de la suivante, pour le même joueur.

    C'est la preuve que le rating écrit est bien celui d'AVANT la partie : sans ce décalage,
    une analyse qui s'en sert tournerait en rond.
    """
    games = {g["game_id"]: g for g in pq.read_table(monde["parquet"] / "games").to_pylist()}
    par_joueur: dict[str, list] = {}
    for row in monde["rows"]:
        par_joueur.setdefault(row["public_id"], []).append(row)
    chaines = 0
    for lignes in par_joueur.values():
        lignes.sort(key=lambda r: (games[r["game_id"]]["end"], r["game_id"]))
        for precedent, suivant in zip(lignes, lignes[1:], strict=False):
            assert suivant["rating_before"] == pytest.approx(precedent["rating_after"], abs=1e-3)
            assert suivant["games_before"] == precedent["games_before"] + 1
            chaines += 1
    assert chaines > 0, "aucun joueur avec deux parties : le test ne prouverait rien"


def test_le_premier_rating_est_lapriori(monde):
    debutants = [r for r in monde["rows"] if r["games_before"] == 0]
    assert debutants
    assert all(r["rating_before"] == pytest.approx(rating.RATING_START) for r in debutants)


def test_metrics_distingue_un_rating_informatif_dun_bruit(monde):
    """Le rapport doit dire « mieux que la référence naïve » quand c'est vrai, et pas sinon."""
    report = rating.metrics(monde["parquet"], monde["out"], since="2000-01-01T00:00:00+00:00")
    assert report["games"] > 0
    cellule = report["cells"]["tous|0"]
    assert cellule["pairs"] > 0
    assert cellule["logloss_naive"] == pytest.approx(math.log(2), abs=1e-9)
