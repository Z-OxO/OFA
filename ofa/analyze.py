"""Moteur d'analyse (J5, ADR-0012) : cribler sur l'exploration, confirmer une fois, publier.

Trois etages, et un seul estimateur pour les trois :

1. `screen` passe en revue des comportements candidats sur l'echantillon d'EXPLORATION seul.
   Il ne publie rien : il propose des hypotheses a pre-inscrire.
2. `explore` mesure une hypothese pre-inscrite, toujours sur l'exploration, et dit si elle est
   assez puissante pour meriter la lecture unique de confirmation.
3. `confirm` ouvre l'echantillon de confirmation UNE fois par hypothese, et le journalise.

Rien n'est stocke : la table de sujets se reconstruit a la demande depuis les Parquet (quelques
secondes), parametree par la fenetre [t0, t1] de l'hypothese.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

# duckdb n'est importe QUE dans `connect()`. Ses wheels exigent l'AVX, que les `QEMU Virtual CPU`
# du serveur n'ont pas : un `import duckdb` en tete tuait TOUTE commande `ofa` la-bas par SIGILL,
# `ingest` et `resim` comprises, puisque cli.py importe ce module. Ici, seul `ofa analyze` echoue.
if TYPE_CHECKING:
    import duckdb

from .config import CONFIRMATION_START, Config

# `spawn_phase_end` vaut 202 sur les 513 parties re-simulees : un repere constant, anterieur a
# tout. Les fenetres sont donc en ticks ABSOLUS, jamais en fraction de duree (ADR-0012, piege 1).
SPAWN_PHASE_END = 202
# 210 et non 202 : l'etat n'est echantillonne qu'aux multiples de 10 ticks, donc une fenetre qui
# part de 202 ne joint rien (teste : 0 sujet). 210 est le premier etat apres la phase de spawn.
PHASES = {"ouverture": (210, 1800), "milieu": (1800, 6000), "fin": (6000, 12000)}

VIEWS = {
    "games": "parquet/games",
    "participants": "parquet/participants",
    "rating": "rating",
    "player_ticks": "resim/player_ticks",
    "events": "resim/events",
    "players": "resim/players",
    "borders": "resim/borders",
}

# Les quatre mesures de resultat de la SPEC. Le signe dit dans quel sens "mieux" va : en FFA la
# victoire est trop rare pour servir seule (1 gagnant sur N).
OUTCOMES = {
    "rank_pct": (-1, "rang normalise parmi les humains (0 = premier)"),
    "won": (+1, "part de victoires"),
    "survived_after": (+1, "ticks survecus apres t1"),
    "peak_share_after": (+1, "pic de territoire apres t1, en part de la map"),
}

# Les comportements candidats du criblage. Chacun est une expression SQL booleenne sur la table de
# sujets, et porte son point de controle : `t0` pour une DOSE (stratifier sur l'etat a t1
# annulerait l'effet, ADR-0012 piege 2), `t1` pour un CHOIX a position egale.
# Les seuils sont des medianes calculees sur la population analysee, jamais des constantes.
CANDIDATES: dict[str, tuple[str, str]] = {
    "beaucoup_attaques_humains": ("atk_human > median(atk_human) over ()", "t0"),
    "attaque_un_humain_tot": (
        "first_atk_human_tick is not null"
        " and first_atk_human_tick <= median(first_atk_human_tick) over ()",
        "t0",
    ),
    "engage_ses_troupes": (
        "atk_troops * 1.0 / nullif(troops0, 0) >"
        " median(atk_troops * 1.0 / nullif(troops0, 0)) over ()",
        "t0",
    ),
    "expansion_rapide": (
        "(tiles1 - tiles0) * 1.0 / nullif(tiles0, 0) >"
        " median((tiles1 - tiles0) * 1.0 / nullif(tiles0, 0)) over ()",
        "t0",
    ),
    "beaucoup_de_villes": ("built_city > median(built_city) over ()", "t0"),
    "ville_tot": (
        "first_city_tick is not null and first_city_tick <= median(first_city_tick) over ()",
        "t0",
    ),
    "un_port": ("built_port >= 1", "t0"),
    "une_usine": ("built_factory >= 1", "t0"),
    "des_defenses": ("built_defense > median(built_defense) over ()", "t0"),
    "demande_une_alliance": ("ally_req >= 1", "t0"),
    "obtient_une_alliance": ("ally_ok >= 1", "t0"),
    "trahit": ("betrayals >= 1", "t0"),
    "un_navire": ("built_warship >= 1", "t0"),
    "des_transports": ("built_transport > median(built_transport) over ()", "t0"),
    "une_bombe": ("built_bomb >= 1", "t0"),
    "garde_son_or": ("gold1 > median(gold1) over ()", "t1"),
    "troupes_denses": (
        "troops1 * 1.0 / nullif(tiles1, 0) > median(troops1 * 1.0 / nullif(tiles1, 0)) over ()",
        "t1",
    ),
    "beaucoup_de_conquetes": ("conquests > median(conquests) over ()", "t0"),
}

# Le controle negatif permanent (ADR-0012), en SIX tirages et non un seul : avec une centaine de
# grappes, un placebo unique sort de son IC une fois sur vingt par pur hasard (mesure : la graine
# `placebo` donnait +0,042 sur `won` en Team, les cinq autres zero). Ce qu'on verifie, c'est donc
# la DISTRIBUTION des placebos, pas un tirage.
PLACEBOS = 6
for _i in range(1, PLACEBOS + 1):
    CANDIDATES[f"controle_negatif_{_i}"] = (
        f"hash(game_id || small_id::varchar || 'placebo{_i}') % 2 = 0",
        "t0",
    )

STRATES_DEFAUT = ("mode", "tier_rating", "tier_size", "git_commit")

SUBJECTS_SQL = """
create or replace table subjects as
with pop as (
    select game_id, mode, map, num_players, git_commit, start
    from games
    where has_turns and stats_agreed and num_turns >= {t1} and ({population}) and {sample}
),
s0 as (select * from player_ticks where tick = {t0}),
s1 as (select * from player_ticks where tick = {t1}),
-- Une seule passe sur `borders` (46 M de lignes) pour les deux controles de contexte : la terre
-- libre (b = 0, TerraNullius : de la place ou s'etendre sans combattre) et la force des voisins.
-- Elle ne connait que les voisins HUMAINS : `player_ticks` n'a pas les nations ni les bots.
b0 as (
    select b.game_id, b.a as small_id,
        coalesce(sum(b.tiles) filter (where b.b = 0), 0) as free_border,
        coalesce(sum(n.tiles) filter (where b.b <> 0), 0) as neighbour_tiles
    from borders b
    left join s0 n on n.game_id = b.game_id and n.small_id = b.b
    where b.tick = {t0}
    group by 1, 2
),
win as (
    select game_id, a as small_id,
        count(*) filter (where kind = 'attack_start' and b <> 0) as atk_human,
        count(*) filter (where kind = 'attack_start' and b = 0) as atk_neutral,
        coalesce(sum(troops) filter (where kind = 'attack_start'), 0) as atk_troops,
        count(*) filter (where kind = 'conquest') as conquests,
        count(*) filter (where kind = 'unit_built' and unit_type = 'City') as built_city,
        count(*) filter (where kind = 'unit_built' and unit_type = 'Port') as built_port,
        count(*) filter (where kind = 'unit_built' and unit_type = 'Factory') as built_factory,
        count(*) filter (where kind = 'unit_built' and unit_type = 'Defense Post') as built_defense,
        count(*) filter (where kind = 'unit_built' and unit_type = 'Warship') as built_warship,
        count(*) filter (where kind = 'unit_built' and unit_type = 'Transport') as built_transport,
        count(*) filter (where kind = 'unit_built'
            and unit_type in ('Atom Bomb', 'Hydrogen Bomb', 'MIRV')) as built_bomb,
        count(*) filter (where kind = 'unit_lost') as units_lost,
        count(*) filter (where kind = 'alliance_request') as ally_req,
        count(*) filter (where kind = 'alliance_reply' and flag) as ally_ok,
        count(*) filter (where kind = 'alliance_broken') as betrayals,
        count(*) filter (where kind = 'donate') as donations,
        min(tick) filter (where kind = 'attack_start' and b <> 0) as first_atk_human_tick,
        min(tick) filter (where kind = 'unit_built' and unit_type = 'City') as first_city_tick,
        min(tick) filter (where kind = 'alliance_request') as first_ally_tick
    from events
    where tick > {t0} and tick <= {t1}
    group by 1, 2
),
-- Les attaques SUBIES pendant la fenetre. Sans elles, un comportement qui est une REACTION
-- (demander une alliance parce qu'on se fait attaquer) est confondu avec son declencheur : c'est
-- la causalite inverse de la SPEC, et le controle a t0 ne la voit pas, parce que le declencheur
-- arrive APRES t0.
inc as (
    select game_id, b as small_id,
        count(*) filter (where kind = 'attack_start') as atk_in,
        coalesce(sum(troops) filter (where kind = 'attack_start'), 0) as atk_in_troops,
        min(tick) filter (where kind = 'attack_start') as first_atk_in_tick
    from events
    where tick > {t0} and tick <= {t1} and b <> 0
    group by 1, 2
),
-- `death_position` compte les nations (GameImpl.recordDeathPosition) : le rang est donc recalcule
-- parmi les HUMAINS seuls. Plus petit = mieux : les vivants d'abord, puis les morts tardifs.
-- `rank()` et non `row_number()` : deux joueurs elimines au meme tick PARTAGENT leur position
-- (docs/DATA.md), et les departager dans l'ordre de lecture des Parquet rendait la mesure non
-- reproductible — mesure a l'appui, -0,05504 / -0,05298 / -0,05285 pour la meme commande.
fin as (
    select h.game_id, h.small_id, h.client_id, h.death_position, h.alive_at_end, h.final_tiles,
        h.spawn_shore, h.map_land, h.team,
        1.0 * (rank() over (partition by h.game_id order by h.alive_at_end desc,
                coalesce(h.death_position, 0) asc, h.final_tiles desc) - 1)
            / nullif(count(*) over (partition by h.game_id) - 1, 0) as rank_pct
    from players h
    where h.player_type = 'HUMAN'
),
apres as (
    select game_id, small_id, max(tick) as last_tick,
        max(tiles) filter (where tick > {t1}) as peak_after
    from player_ticks
    group by 1, 2
)
select
    p.game_id, s1.small_id, pa.public_id, p.mode, p.map, p.num_players, p.git_commit, p.start,
    r.rating_before, r.games_before, f.team, f.spawn_shore,
    s0.tiles as tiles0, s0.gold as gold0, s0.troops as troops0, s0.gold_earned as earned0,
    s0.neighbours as neighbours0, s0.border as border0, s0.border_front as front0,
    b0.free_border as free0, b0.neighbour_tiles as nb_tiles0,
    s1.tiles as tiles1, s1.gold as gold1, s1.troops as troops1, s1.gold_earned as earned1,
    s1.neighbours as neighbours1, s1.border as border1,
    coalesce(w.atk_human, 0) as atk_human, coalesce(w.atk_neutral, 0) as atk_neutral,
    coalesce(w.atk_troops, 0) as atk_troops, coalesce(w.conquests, 0) as conquests,
    coalesce(w.built_city, 0) as built_city, coalesce(w.built_port, 0) as built_port,
    coalesce(w.built_factory, 0) as built_factory, coalesce(w.built_defense, 0) as built_defense,
    coalesce(w.built_warship, 0) as built_warship,
    coalesce(w.built_transport, 0) as built_transport,
    coalesce(w.built_bomb, 0) as built_bomb, coalesce(w.units_lost, 0) as units_lost,
    coalesce(w.ally_req, 0) as ally_req, coalesce(w.ally_ok, 0) as ally_ok,
    coalesce(w.betrayals, 0) as betrayals, coalesce(w.donations, 0) as donations,
    w.first_atk_human_tick, w.first_city_tick, w.first_ally_tick,
    coalesce(i.atk_in, 0) as atk_in, coalesce(i.atk_in_troops, 0) as atk_in_troops,
    i.first_atk_in_tick,
    f.rank_pct, coalesce(pa.won, false) as won,
    greatest(coalesce(ap.last_tick, {t1}) - {t1}, 0) as survived_after,
    1.0 * coalesce(ap.peak_after, s1.tiles) / nullif(f.map_land, 0) as peak_share_after,
    1.0 * f.final_tiles / nullif(f.map_land, 0) as final_share,
    case when r.rating_before is null then 0 else
        ntile(4) over (partition by (r.rating_before is null)
            order by r.rating_before, p.game_id, s1.small_id) end as tier_rating,
    ntile(4) over (partition by p.mode order by s0.tiles, p.game_id, s1.small_id) as tier_state0,
    ntile(4) over (partition by p.mode order by s1.tiles, p.game_id, s1.small_id) as tier_state1,
    case when p.num_players <= 8 then 'S' when p.num_players <= 24 then 'M' else 'L' end
        as tier_size,
    case
        when coalesce(i.atk_in, 0) = 0 then 0
        when i.atk_in <= median(i.atk_in) over () then 1
        else 2
    end as tier_pression
from pop p
join s1 on s1.game_id = p.game_id
join s0 on s0.game_id = p.game_id and s0.small_id = s1.small_id
join fin f on f.game_id = p.game_id and f.small_id = s1.small_id
left join participants pa on pa.game_id = p.game_id and pa.client_id = f.client_id
left join rating r on r.game_id = p.game_id and r.public_id = pa.public_id
left join win w on w.game_id = p.game_id and w.small_id = s1.small_id
left join b0 on b0.game_id = p.game_id and b0.small_id = s1.small_id
left join inc i on i.game_id = p.game_id and i.small_id = s1.small_id
left join apres ap on ap.game_id = p.game_id and ap.small_id = s1.small_id
"""


@dataclass(frozen=True)
class Spec:
    """Une hypothese pre-inscrite (findings/prereg/<id>.yaml)."""

    id: str
    enonce: str
    comportement: str
    resultat: str
    sens_attendu: str  # "+" ou "-", applique a la colonne de resultat telle quelle
    t0: int
    t1: int
    controle: str  # "t0" (dose) ou "t1" (choix a position egale)
    population: str  # SQL sur `games`
    strates: tuple[str, ...] = STRATES_DEFAUT
    grappe: str = "public_id"
    comportement_alt: str = ""
    t0_alt: int = 0
    t1_alt: int = 0
    n_min: int = 300
    joueurs_min: int = 100
    # L'instant a montrer dans les exemples : une expression SQL (ex. `first_ally_tick`). Par
    # defaut la fin de la fenetre, qui est toujours vraie mais moins parlante.
    tick_exemple: str = ""
    sha: str = ""

    @property
    def strates_completes(self) -> tuple[str, ...]:
        """Les strates declarees, plus l'etat au point de controle choisi."""
        return (*self.strates, f"tier_state{'0' if self.controle == 't0' else '1'}")


def prereg_path(root: Path, hid: str) -> Path:
    return root / "findings" / "prereg" / f"{hid}.yaml"


def load_spec(root: Path, hid: str) -> Spec:
    path = prereg_path(root, hid)
    if not path.exists():
        raise SystemExit(f"pas de pre-inscription : {path}")
    raw = path.read_bytes()
    d = yaml.safe_load(raw)
    d["sha"] = hashlib.sha256(raw).hexdigest()[:16]
    d["strates"] = tuple(d.get("strates") or STRATES_DEFAUT)
    if d["resultat"] not in OUTCOMES:
        raise SystemExit(f"resultat inconnu : {d['resultat']} (voir OUTCOMES)")
    if d["controle"] not in ("t0", "t1"):
        raise SystemExit("controle doit valoir t0 (dose) ou t1 (choix)")
    return Spec(**d)


def connect(cfg: Config) -> duckdb.DuckDBPyConnection:
    import duckdb

    con = duckdb.connect()
    for name, rel in VIEWS.items():
        glob = f"{cfg.data_dir / rel}/**/*.parquet"
        con.execute(f"create view {name} as select * from read_parquet('{glob}')")
    return con


def build_subjects(
    con: duckdb.DuckDBPyConnection, *, t0: int, t1: int, population: str, sample: str
) -> int:
    """Construit la table de sujets (partie, joueur) pour la fenetre ]t0, t1].

    `sample` vaut exploration, confirmation ou tout. La coupure est celle du J4
    (`CONFIRMATION_START`), figee : la deplacer invaliderait toute confirmation passee.
    """
    if t0 % 10 or t1 % 10:
        raise SystemExit(f"t0 et t1 doivent etre des multiples de 10 ticks (recu {t0}, {t1})")
    cut = f"'{CONFIRMATION_START}'::timestamptz"
    where = {
        "exploration": f"start < {cut}",
        "confirmation": f"start >= {cut}",
        "tout": "true",
    }[sample]
    con.execute(SUBJECTS_SQL.format(t0=t0, t1=t1, population=population, sample=where))
    return con.execute("select count(*) from subjects").fetchone()[0]


def cells(
    con: duckdb.DuckDBPyConnection,
    *,
    comportement: str,
    resultat: str,
    grappe: str,
    strates,
    sous_groupe: str = "true",
) -> None:
    """Reduit les sujets aux quatre colonnes de l'estimateur : grappe, strate, bras, resultat.

    Le comportement est evalue dans un SELECT interne, parce qu'il contient des fenetres
    (`median(...) over ()`) qu'un WHERE refuse.
    """
    stratum = " || '|' || ".join(f"coalesce(cast({s} as varchar), '?')" for s in strates)
    con.execute(f"""
        create or replace table cells as
        with t as (
            select {grappe} as cluster, {stratum} as stratum, game_id, public_id as pid,
                   ({comportement}) as arm, cast({resultat} as double) as y
            from subjects where {sous_groupe}
        )
        select * from t where cluster is not null and arm is not null and y is not null
    """)


def effect(con: duckdb.DuckDBPyConnection, *, draws: int = 1000, seed: int = 20261122) -> dict:
    """Standardisation directe sur strates, IC et p par bootstrap de grappes (ADR-0012).

    L'effet est la moyenne des ecarts intra-strate, ponderee par les effectifs du bras
    "comportement present" (poids ATT) : "qu'auraient obtenu ces memes joueurs sans ce
    comportement". Seules les strates ou les deux bras existent comptent, et le bootstrap
    rejoue exactement ce jeu de strates.
    """
    con.execute("""
        create or replace table cs as
        select cluster, stratum, arm, sum(y) as sy, count(*) as n from cells group by 1, 2, 3
    """)
    con.execute("""
        create or replace table ok as
        select stratum from cs group by stratum
        having sum(n) filter (where arm) > 0 and sum(n) filter (where not arm) > 0
    """)
    point = con.execute("""
        with m as (
            select c.stratum,
                sum(sy) filter (where arm) / sum(n) filter (where arm) as m1,
                sum(sy) filter (where not arm) / sum(n) filter (where not arm) as m0,
                sum(n) filter (where arm) as w
            from cs c join ok using (stratum) group by 1
        )
        select sum(w * (m1 - m0)) / nullif(sum(w), 0) from m
    """).fetchone()[0]

    # `joueurs` et `grappes` ne sont pas la meme chose des que la grappe est la partie (Team) :
    # la SPEC exige 100 joueurs DISTINCTS, et c'est le nombre de grappes qui fait la largeur de
    # l'intervalle. Confondre les deux ferait passer 102 parties pour 102 joueurs.
    n, n1, clusters, joueurs, strata_used = con.execute("""
        select (select sum(n) from cs join ok using (stratum)),
               (select sum(n) from cs join ok using (stratum) where arm),
               (select count(distinct cluster) from cs join ok using (stratum)),
               (select count(distinct c.pid) from cells c join ok using (stratum)),
               (select count(*) from ok)
    """).fetchone()
    out = {
        "effet": point,
        "n": int(n or 0),
        "n_comportement": int(n1 or 0),
        "joueurs": int(joueurs or 0),
        "grappes": int(clusters or 0),
        "strates": int(strata_used or 0),
        "parties": con.execute("select count(distinct game_id) from cells").fetchone()[0],
        "tirages": draws,
    }
    if point is None or not clusters:
        return out | {"ic95": [None, None], "p": None, "ecart_type": None, "mde": None}

    # Bootstrap : on retire `clusters` grappes avec remise, `draws` fois, et on recalcule l'effet
    # a chaque tirage. Tout en SQL : 34 M de lignes de reechantillonnage passent en secondes.
    # Les grappes du tirage sont celles qui vivent dans une strate utilisable, pas toutes.
    con.execute("""
        create or replace table cl as
        -- `order by cluster` n'est pas decoratif : sans lui, la numerotation des grappes varie
        -- d'une execution a l'autre et le bootstrap tire d'autres grappes pour la meme graine.
        select row_number() over (order by cluster) - 1 as i, cluster
        from (select distinct cluster from cs join ok using (stratum))
    """)
    k = con.execute("select count(*) from cl").fetchone()[0]
    rows = con.execute(f"""
        with picks as (
            select d.d, cast(hash(d.d * 1000003 + u.j + {seed}) % {k} as bigint) as i
            from range(0, {draws}) d(d), range(0, {k}) u(j)
        ),
        bm as (
            select p.d, c.stratum,
                sum(c.sy) filter (where c.arm) as s1, sum(c.n) filter (where c.arm) as n1,
                sum(c.sy) filter (where not c.arm) as s0, sum(c.n) filter (where not c.arm) as n0
            from picks p
            join cl on cl.i = p.i
            join cs c on c.cluster = cl.cluster
            join ok on ok.stratum = c.stratum
            group by 1, 2
        )
        select sum(n1 * (s1 / n1 - s0 / n0)) / sum(n1) as est
        from bm where n1 > 0 and n0 > 0 group by d order by est
    """).fetchall()
    ests = [r[0] for r in rows if r[0] is not None]
    if len(ests) < draws // 2:
        return out | {"ic95": [None, None], "p": None, "ecart_type": None, "mde": None}
    lo = ests[int(0.025 * (len(ests) - 1))]
    hi = ests[int(0.975 * (len(ests) - 1))]
    mean = sum(ests) / len(ests)
    sd = math.sqrt(sum((e - mean) ** 2 for e in ests) / max(len(ests) - 1, 1))
    neg = sum(1 for e in ests if e <= 0) / len(ests)
    pos = sum(1 for e in ests if e >= 0) / len(ests)
    p = max(2 * min(neg, pos), 1.0 / len(ests))
    # Effet minimal detectable, ~80 % de puissance a 5 % bilateral : 2,8 ecarts-types.
    return out | {"ic95": [lo, hi], "p": p, "ecart_type": sd, "mde": 2.8 * sd}


def measure(
    con: duckdb.DuckDBPyConnection,
    *,
    comportement: str,
    resultat: str,
    grappe: str,
    strates,
    draws: int,
    seed: int = 20261122,
    sous_groupe: str = "true",
) -> dict:
    cells(
        con,
        comportement=comportement,
        resultat=resultat,
        grappe=grappe,
        strates=strates,
        sous_groupe=sous_groupe,
    )
    return effect(con, draws=draws, seed=seed)


def bh(ps: list[float]) -> list[float]:
    """Benjamini-Hochberg : p-valeurs ajustees, dans l'ordre d'entree."""
    m = len(ps)
    order = sorted(range(m), key=lambda i: ps[i])
    adj = [0.0] * m
    prev = 1.0
    for rank, i in reversed(list(enumerate(order, start=1))):
        prev = min(prev, ps[i] * m / rank)
        adj[i] = min(prev, 1.0)
    return adj


def screen(
    cfg: Config, *, phase: str, draws: int, population: str, grappe: str = "public_id"
) -> list[dict]:
    """Criblage : tous les candidats x toutes les mesures, sur l'EXPLORATION seule.

    Aucune fiche, aucune p-valeur publiable : le produit est une liste d'hypotheses a
    pre-inscrire. L'echantillon de confirmation n'est jamais ouvert ici.
    """
    t0, t1 = PHASES[phase]
    con = connect(cfg)
    n = build_subjects(con, t0=t0, t1=t1, population=population, sample="exploration")
    if not n:
        raise SystemExit("aucun sujet : verifie la population et que la re-sim a tourne")
    out = []
    for name, (sql, controle) in CANDIDATES.items():
        strates = (*STRATES_DEFAUT, f"tier_state{'0' if controle == 't0' else '1'}")
        for resultat in OUTCOMES:
            r = measure(
                con,
                comportement=sql,
                resultat=resultat,
                grappe=grappe,
                strates=strates,
                draws=draws,
            )
            signe = (
                "nul"
                if r["ic95"][0] is None or r["ic95"][0] <= 0 <= r["ic95"][1]
                else ("+" if r["effet"] > 0 else "-")
            )
            out.append(
                {"candidat": name, "controle": controle, "resultat": resultat, "signe": signe} | r
            )
    con.close()
    return out


def _reads_path(root: Path) -> Path:
    return root / "findings" / "reads.jsonl"


def reads(root: Path) -> list[dict]:
    path = _reads_path(root)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run(
    cfg: Config,
    spec: Spec,
    *,
    sample: str,
    draws: int,
    alt_def: bool = False,
    alt_win: bool = False,
) -> dict:
    """Une mesure complete d'une hypothese, sur l'echantillon demande.

    Les deux leviers de robustesse sont SEPARES : une autre definition et une autre fenetre sont
    deux tests, pas un. Les changer ensemble ne dirait pas lequel des deux a bouge.
    """
    t0 = spec.t0_alt if alt_win and spec.t0_alt else spec.t0
    t1 = spec.t1_alt if alt_win and spec.t1_alt else spec.t1
    comportement = spec.comportement_alt if alt_def and spec.comportement_alt else spec.comportement
    con = connect(cfg)
    n = build_subjects(con, t0=t0, t1=t1, population=spec.population, sample=sample)
    if not n:
        con.close()
        raise SystemExit(f"aucun sujet dans l'echantillon {sample}")
    r = measure(
        con,
        comportement=comportement,
        resultat=spec.resultat,
        grappe=spec.grappe,
        strates=spec.strates_completes,
        draws=draws,
    )
    con.close()
    suffisant = r["n"] >= spec.n_min and r["joueurs"] >= spec.joueurs_min
    return r | {
        "hypothese": spec.id,
        "sha_prereg": spec.sha,
        "echantillon": sample,
        "fenetre": [t0, t1],
        "definition": comportement,
        "resultat_mesure": spec.resultat,
        "echantillon_suffisant": suffisant,
        "verdict": _verdict(spec, r, suffisant),
    }


def _verdict(spec: Spec, r: dict, suffisant: bool) -> str:
    if not suffisant:
        return "echantillon insuffisant"
    if r["ic95"][0] is None:
        return "non estimable"
    if r["ic95"][0] <= 0 <= r["ic95"][1]:
        if r["mde"] and abs(r["effet"]) < r["mde"]:
            return "sous-puissante"
        return "effet nul"
    attendu = 1 if spec.sens_attendu == "+" else -1
    return "confirme le sens attendu" if r["effet"] * attendu > 0 else "sens OPPOSE a l'attendu"


def confirm(cfg: Config, root: Path, spec: Spec, *, draws: int) -> dict:
    """Ouvre l'echantillon de confirmation, UNE fois par hypothese, et le journalise.

    Deuxieme lecture refusee, et SANS echappatoire : c'est la regle de la SPEC, et elle est dans
    le code, pas dans la bonne volonte. `--force` de la CLI ne leve que le garde de puissance.
    Pour re-tester, il faut une nouvelle hypothese, donc un nouvel id.
    """
    deja = [x for x in reads(root) if x["hypothese"] == spec.id]
    if deja:
        raise SystemExit(
            f"REFUSE : l'echantillon de confirmation a deja ete lu pour {spec.id} "
            f"le {deja[0]['quand']} (prereg {deja[0]['sha_prereg']}). "
            "Une hypothese = une lecture. Cree un nouvel id pour re-tester."
        )
    r = run(cfg, spec, sample="confirmation", draws=draws)
    with _reads_path(root).open("a") as f:
        f.write(
            json.dumps(
                {
                    "hypothese": spec.id,
                    "quand": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "sha_prereg": spec.sha,
                    "n": r["n"],
                    "joueurs": r["joueurs"],
                    "grappes": r["grappes"],
                    "parties": r["parties"],
                    "effet": r["effet"],
                    "ic95": r["ic95"],
                    "p": r["p"],
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    return r


def _runs_dir(root: Path) -> Path:
    d = root / "findings" / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_run(root: Path, r: dict) -> Path:
    path = _runs_dir(root) / f"{r['hypothese']}-{r['echantillon']}.json"
    path.write_text(json.dumps(r, ensure_ascii=False, indent=1))
    return path


def load_run(root: Path, hid: str, sample: str) -> dict | None:
    path = _runs_dir(root) / f"{hid}-{sample}.json"
    return json.loads(path.read_text()) if path.exists() else None


def subgroups(cfg: Config, spec: Spec, *, by: str, draws: int) -> list[dict]:
    """Le meme effet, recalcule groupe par groupe (tranche de niveau, map...).

    Sert au niveau de preuve A, qui exige la coherence par tranche et par map. Un groupe sous les
    seuils n'est pas montre : il serait du bruit presente comme un desaccord.
    """
    con = connect(cfg)
    build_subjects(con, t0=spec.t0, t1=spec.t1, population=spec.population, sample="tout")
    valeurs = [
        r[0]
        for r in con.execute(
            f"select {by} from subjects where {by} is not null group by 1 having count(*) >= 300"
        ).fetchall()
    ]
    out = []
    for v in valeurs:
        lit = f"'{v}'" if isinstance(v, str) else str(v)
        r = measure(
            con,
            comportement=spec.comportement,
            resultat=spec.resultat,
            grappe=spec.grappe,
            strates=spec.strates_completes,
            draws=draws,
            sous_groupe=f"{by} = {lit}",
        )
        if r["n"] >= spec.n_min and r["joueurs"] >= spec.joueurs_min:
            out.append({"groupe": f"{by}={v}", **r})
    con.close()
    return out


def within_player(cfg: Config, spec: Spec, *, draws: int) -> dict:
    """Le joueur compare a lui-meme : la strate EST le joueur.

    C'est l'exigence la plus dure du niveau A, et elle n'existe que pour les joueurs assidus :
    un joueur n'y entre que s'il a joue des parties avec ET sans le comportement.
    """
    con = connect(cfg)
    build_subjects(con, t0=spec.t0, t1=spec.t1, population=spec.population, sample="tout")
    r = measure(
        con,
        comportement=spec.comportement,
        resultat=spec.resultat,
        grappe=spec.grappe,
        strates=("public_id",),
        draws=draws,
    )
    con.close()
    return r


def _niveau(conf: dict | None, rob: list[dict], tranches, maps, intra) -> tuple[str, str]:
    """Le niveau de preuve de la SPEC, et la raison, calcules et jamais choisis a la main."""
    if conf is None or not conf["echantillon_suffisant"]:
        return "C", "pas de lecture de confirmation valide : association descriptive seulement"
    if conf["ic95"][0] is None or conf["ic95"][0] <= 0 <= conf["ic95"][1]:
        return "C", "effet non distinguable de zero sur l'echantillon de confirmation"
    signe = 1 if conf["effet"] > 0 else -1
    incoherent = [
        r["definition"] if "definition" in r else r.get("groupe", "?")
        for r in rob
        if r["effet"] is None or (r["effet"] > 0) != (signe > 0)
    ]
    if incoherent:
        return "C", f"la robustesse change le sens de l'effet : {', '.join(incoherent)}"

    def coherent(xs) -> bool:
        return bool(xs) and all((x["effet"] > 0) == (signe > 0) for x in xs)

    intra_ok = (
        intra is not None and intra["ic95"][0] is not None and (intra["effet"] > 0) == (signe > 0)
    )
    if coherent(tranches) and coherent(maps) and intra_ok:
        return "A", "confirme, coherent par tranche de niveau, par map, et joueur contre lui-meme"
    return "B", "confirme et ajuste sur le niveau, l'etat et le contexte (strates)"


def examples(cfg: Config, spec: Spec, *, n: int = 5, seed: int = 20261122) -> dict:
    """Exemples et contre-exemples, tires au hasard a graine fixe, jamais choisis a la main.

    Le viewer n'accepte pas de tick de depart (RECON fait 8) : le lien ouvre la partie, le tick et
    le mm:ss disent ou chercher.
    """
    con = connect(cfg)
    build_subjects(con, t0=spec.t0, t1=spec.t1, population=spec.population, sample="tout")
    tick = spec.tick_exemple or str(spec.t1)
    # Le contre-exemple, c'est le comportement present et le resultat quand meme du mauvais cote
    # de la mediane : il montre que la fiche parle d'une moyenne, pas d'une loi.
    rate = "y > median(y) over ()" if spec.sens_attendu == "-" else "y < median(y) over ()"
    con.execute(f"""
        create or replace table ex as
        with t as (
            select s.game_id, s.small_id, p.username, s.mode, s.map, s.rank_pct, s.won,
                   {tick} as tick, ({spec.comportement}) as arm,
                   cast({spec.resultat} as double) as y
            from subjects s
            left join players p on p.game_id = s.game_id and p.small_id = s.small_id
        )
        select *, {rate} as rate from t where arm
    """)
    rows = {}
    for cle, filtre in (("exemples", "true"), ("contre_exemples", "rate")):
        rows[cle] = con.execute(f"""
            select game_id, small_id, username, mode, map, tick,
                   round(rank_pct, 3), won, round(y, 4)
            from ex where {filtre}
            using sample reservoir({n} rows) repeatable ({seed})
        """).fetchall()
    con.close()
    out = {}
    for cle, rs in rows.items():
        out[cle] = [
            {
                "game_id": r[0],
                "joueur": r[2],
                "small_id": r[1],
                "mode": r[3],
                "map": r[4],
                "tick": int(r[5]) if r[5] is not None else None,
                "mmss": f"{int(r[5]) // 600:02d}:{int(r[5]) % 600 // 10:02d}"
                if r[5] is not None
                else None,
                "rang_normalise": r[6],
                "gagnant": r[7],
                "replay": f"https://openfront.io/#replay-viewer={r[0]}",
            }
            for r in rs
        ]
    return out


def card(cfg: Config, root: Path, spec: Spec, *, draws: int) -> dict:
    """Assemble la fiche : ce qui a ete mesure, a quel niveau de preuve, et ou le verifier."""
    expl = load_run(root, spec.id, "exploration")
    conf = load_run(root, spec.id, "confirmation")
    if expl is None:
        raise SystemExit(f"lance d'abord `ofa analyze explore {spec.id}`")
    rob = []
    if spec.comportement_alt:
        r = run(cfg, spec, sample="exploration", draws=draws, alt_def=True)
        rob.append({"test": "autre definition", "definition": r["definition"], **r})
    if spec.t0_alt or spec.t1_alt:
        r = run(cfg, spec, sample="exploration", draws=draws, alt_win=True)
        rob.append({"test": "autre fenetre", "fenetre": r["fenetre"], **r})
    tranches = subgroups(cfg, spec, by="tier_rating", draws=draws) if conf else []
    maps = subgroups(cfg, spec, by="map", draws=draws) if conf else []
    intra = within_player(cfg, spec, draws=draws) if conf else None
    niveau, raison = _niveau(conf, rob, tranches, maps, intra)
    base = conf or expl
    ex = examples(cfg, spec)
    return {
        "id": spec.id,
        "enonce": spec.enonce,
        "population": spec.population,
        "mode_fenetre": {"t0": spec.t0, "t1": spec.t1, "controle": spec.controle},
        "definition": spec.comportement,
        "mesure_resultat": {
            "colonne": spec.resultat,
            "sens_attendu": spec.sens_attendu,
            "unite": OUTCOMES[spec.resultat][1],
        },
        "effet": base["effet"],
        "ic95": base["ic95"],
        "p": base["p"],
        "n_observations": base["n"],
        "n_joueurs": base["joueurs"],
        "n_parties": base["parties"],
        "niveau_preuve": niveau,
        "raison_niveau": raison,
        "n_grappes": base.get("grappes"),
        "exploration": {
            k: expl[k] for k in ("effet", "ic95", "p", "n", "joueurs", "grappes", "parties")
        },
        "confirmation": {
            k: conf[k] for k in ("effet", "ic95", "p", "n", "joueurs", "grappes", "parties")
        }
        if conf
        else None,
        "robustesse": [
            {"test": r["test"], "effet": r["effet"], "ic95": r["ic95"], "n": r["n"]} for r in rob
        ],
        "par_tranche_de_niveau": [
            {"groupe": g["groupe"], "effet": g["effet"], "ic95": g["ic95"]} for g in tranches
        ],
        "par_map": [{"groupe": g["groupe"], "effet": g["effet"], "ic95": g["ic95"]} for g in maps],
        "joueur_contre_lui_meme": {
            "effet": intra["effet"],
            "ic95": intra["ic95"],
            "n": intra["n"],
            "joueurs": intra["joueurs"],
        }
        if intra
        else None,
        "limites": limites(spec, base),
        "exemples": ex["exemples"],
        "contre_exemples": ex["contre_exemples"],
        "sha_prereg": spec.sha,
        "genere_le": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def limites(spec: Spec, base: dict) -> list[str]:
    """Les limites connues, ecrites par le code : elles ne doivent pas dependre de ma memoire."""
    out = [
        f"trajectoires disponibles sur {base['parties']} parties seulement : c'est `ofa fetch-full`"
        " qui decide de ce volume, pas la re-simulation",
        f"population restreinte aux joueurs VIVANTS a t1 = {spec.t1} et aux parties d'au moins"
        f" {spec.t1} ticks : les parties plus courtes sont hors fiche",
        "le voisinage n'est connu que pour les voisins humains (`player_ticks` exclut nations"
        " et bots)",
        f"controle pris a {spec.controle} : "
        + (
            "l'etat a t1 n'est PAS controle, parce qu'il est l'effet du comportement"
            if spec.controle == "t0"
            else "l'etat a t1 est controle, donc l'effet est celui d'un choix a position egale"
        ),
    ]
    if base["joueurs"] and base["n"]:
        obs_par_joueur = base["n"] / base["joueurs"]
        if obs_par_joueur < 1.2:
            out.append(
                f"{obs_par_joueur:.2f} observation par joueur : la comparaison d'un joueur avec"
                " lui-meme est hors de portee sur cet echantillon"
            )
    return out
