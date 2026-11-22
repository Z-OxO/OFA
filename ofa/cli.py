"""CLI de ofa. argparse suffit : pas de dépendance pour trois sous-commandes."""

import argparse
import json
import sys
import time
from pathlib import Path

from . import config, engine, fixtures, index, ingest, normalize, purge, rating, resim
from .client import CircuitOpen, Client, rate_report


def prepare(cfg) -> None:
    """Cree les repertoires et echoue TOT si un chemin est pris par un fichier.

    Vecu : le sandbox avait laisse un fichier vide a la place de data/raw, et un travail
    de 4 heures serait mort en cours de route. Mieux vaut refuser de demarrer.
    """
    for path in (cfg.raw_meta, cfg.raw_full, cfg.state_dir, cfg.log_dir):
        bloquant = next((p for p in [path, *path.parents] if p.exists() and not p.is_dir()), None)
        if bloquant is not None:
            raise SystemExit(
                f"{bloquant} existe et n'est pas un repertoire : ofa ne peut pas ecrire dedans.\n"
                f"Supprime ce fichier, ou pointe OFA_DATA_DIR ailleurs."
            )
        path.mkdir(parents=True, exist_ok=True)


def _client_and_index(cfg):
    prepare(cfg)
    return Client(cfg), index.connect(cfg.index_db)


def cmd_ingest(args) -> int:
    """Deux parcours : par fenetre de jours (recent d'abord) ou par afterId (toute l'archive).

    Le mode incremental n'est pas un mode a part : relancer `--days 2` suffit, les
    journees deja terminees ne coutent aucune requete.
    """
    cfg = config.load()
    client, conn = _client_and_index(cfg)
    total = {}
    try:
        for ranked_type in args.ranked_types or config.RANKED_TYPES:
            if args.backfill:
                stats = ingest.ingest(
                    cfg, client, conn, ranked_type, limit=args.limit, max_pages=args.max_pages
                )
            else:
                stats = ingest.ingest_days(
                    cfg,
                    client,
                    conn,
                    ranked_type,
                    args.days,
                    limit=args.limit,
                    max_pages=args.max_pages,
                )
            total[ranked_type] = stats.as_dict()
            print(f"{ranked_type}: {json.dumps(stats.as_dict())}", flush=True)
    except CircuitOpen as exc:
        print(f"COLLECTE EN PAUSE : {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    print(f"requetes HTTP: {client.requests_made}")
    print(json.dumps(total))
    return 0


def cmd_purge(args) -> int:
    cfg = config.load()
    client, conn = _client_and_index(cfg)
    try:
        summary = purge.purge(cfg, client, conn, since=args.since)
    finally:
        conn.close()
    print(json.dumps(summary))
    return 0


def cmd_fixtures(args) -> int:
    cfg = config.load()
    client = Client(cfg)
    summary = fixtures.download(client)
    print(json.dumps(summary))
    return 1 if summary["failed"] else 0


def cmd_normalize(args) -> int:
    """Reconstruit les trois tables Parquet depuis data/raw. Toujours tout, jamais d'incremental.

    `full` avant `meta` : quand on a le record complet d'une partie, c'est lui qui est
    normalise, et c'est le seul qui porte les intents.
    """
    cfg = config.load()
    out = Path(args.out) if args.out else cfg.data_dir / "parquet"
    report = normalize.rebuild([cfg.raw_full, cfg.raw_meta], out, chunk=args.chunk)
    print(json.dumps({k: v for k, v in report.items() if k != "files"}, ensure_ascii=False))
    if report["unknown_stats"]:
        print(
            "CLES DE STATS INCONNUES (le moteur a change, il manque des colonnes) : "
            f"{report['unknown_stats']}",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_fetch_full(args) -> int:
    """Telecharge le record COMPLET (avec `turns`) d'un echantillon de parties deja ingerees."""
    cfg = config.load()
    prepare(cfg)
    summary = ingest.fetch_full(cfg, Client(cfg), args.sample, args.seed)
    print(json.dumps(summary))
    return 1 if summary["failed"] else 0


def cmd_rate_check(args) -> int:
    """Prouve, apres coup, que le debit configure n'a pas ete depasse.

    Critere : l'avance sur l'echeancier ideal. Le limiteur planifie la i-eme requete a
    t0 + i/R, donc i - R*(t_i - t_0) ne doit jamais depasser 1.
    """
    cfg = config.load()
    path = Path(args.log or cfg.log_dir / "requests.jsonl")
    rate = args.rate or cfg.rate
    r = rate_report(path, rate)
    print(f"requetes journalisees    : {r['requests']}")
    if r["min_interval"] is None:
        print("pas assez de requetes pour conclure")
        return 0
    print(f"avance max sur l'echeancier: {r['max_advance']:+.3f} requete  (doit rester <= 1)")
    print(f"debit moyen              : {r['mean_rate']:.2f} req/s  (reference {rate})")
    print(f"intervalle minimum       : {r['min_interval']:.4f} s  (indicatif, gigue de l'OS)")
    print(f"max sur 1 s              : {r['max_in_window']}  (indicatif)")
    ok = r["max_advance"] <= 1.0
    print("VERDICT :", "debit respecte" if ok else "DEBIT DEPASSE")
    return 0 if ok else 1


def cmd_engines(args) -> int:
    """Prepare un moteur par commit de la selection (ADR-0001), et chiffre ce que ca coute.

    Grouper par commit n'est pas un arbitrage, c'est la preparation : 6 worktrees au lieu de
    514. Ce qui s'arbitre, c'est la traine (ADR-0008).
    """
    cfg = config.load()
    selected = resim.candidates(cfg.data_dir / "parquet")
    if args.commit:
        selected = [g for g in selected if g["git_commit"] == args.commit]
    report = resim.engines_report(cfg, selected)
    for row in report["commits"]:
        print(
            f"{row['commit']}  {row['games']:>5} parties  "
            f"present={row['exists']}  pret={row['ready']}  "
            f"lockfile={row.get('lockfile', '?')} en_cache={row.get('lockfile_cached')}  "
            f"{row.get('mb') or 0} Mo"
        )
    if args.dry_run:
        return 0
    needed: list[str] = []
    for sha in report["to_prepare"]:
        try:
            wt = engine.prepare(cfg.data_dir, sha)
        except engine.NeedsShell as exc:
            print(f"{sha[:8]} : {exc}", file=sys.stderr)
            needed += exc.commands
            continue
        note = engine.node_version_note(cfg.data_dir, sha)
        print(f"{sha[:8]} pret dans {wt}" + (f"  ATTENTION {note}" if note else ""))
    if needed:
        print(
            "\nLe sandbox refuse ces commandes (.git en lecture seule). A lancer toi-meme :",
            file=sys.stderr,
        )
        for c in dict.fromkeys(needed):
            print(f"  ! {c}", file=sys.stderr)
        return 2
    return 0


def watch_resim(cfg, interval: float) -> int:
    """Barre de progression, a lancer dans un autre shell pendant que la re-sim tourne.

    Rien n'est instrumente dans la re-sim : la table `resim` est en WAL, donc lisible
    pendant qu'elle est ecrite. Le suivi ne fait que la relire (ADR-0003 : c'est elle qui
    porte la reprise, donc elle est de toute facon la verite sur l'avancement).
    """
    total = len(resim.candidates(cfg.data_dir / "parquet"))
    conn = index.connect(cfg.index_db)
    depart = None
    t0 = time.monotonic()
    fige = time.monotonic()
    precedent = None
    try:
        while True:
            rows = index.resim_counts(conn)
            # On compte les VALIDES, pas les lignes : seules celles-la ne seront pas
            # rejouees (`index.resim_done`). Sinon les echecs d'un run precedent feraient
            # afficher 514/514 d'entree.
            ok = next((r[1] for r in rows if r[0] == "ok"), 0)
            echecs = sum(r[1] for r in rows if r[0] != "ok")
            if depart is None:
                depart, t0 = ok, time.monotonic()
            if (ok, echecs) != precedent:
                precedent, fige = (ok, echecs), time.monotonic()
            watched = time.monotonic() - t0
            rate = (ok - depart) / watched if watched > 0 and ok > depart else None
            ligne = resim.progress_line(ok, total, echecs, watched, rate)
            immobile = time.monotonic() - fige
            if immobile > 180:
                ligne += f"  (rien de nouveau depuis {immobile / 60:.0f} min)"
            print("\r\033[K" + ligne, end="", flush=True)
            if ok >= total:
                print()
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        print()
        for status, n, _, _, _ in rows:
            print(f"  {status:<24} {n}")
        print("detail : uv run ofa resim --report")
        return 0
    finally:
        conn.close()


def cmd_resim(args) -> int:
    """Rejoue les parties selectionnees et ecrit les quatre tables de data/resim/."""
    cfg = config.load()
    if args.watch:
        return watch_resim(cfg, args.interval)
    if args.report:
        conn = index.connect(cfg.index_db)
        try:
            rows = index.resim_counts(conn)
        finally:
            conn.close()
        total = sum(r[1] for r in rows)
        if not total:
            print("aucune re-sim journalisee : lance `uv run ofa resim`")
            return 0
        ok = next((r for r in rows if r[0] == "ok"), None)
        for status, n, ticks, wall, rss in rows:
            debit = f"{ticks / wall:.0f} ticks/s" if wall else "-"
            print(
                f"{status:<24} {n:>5}  ({n / total:>6.1%})  {ticks:>10} ticks  "
                f"{wall / 3600:>6.2f} h  {debit}  pic {rss // 1024} Mo"
            )
        if ok is not None and ok[3]:
            # Un worker rejoue 3600 / (wall / parties) parties par heure.
            print(f"\nvalides : {ok[1]}/{total} = {ok[1] / total:.2%}")
            print(f"debit : {3600 / (ok[3] / ok[1]):.1f} parties/h/coeur")
        return 0
    report = resim.resim(
        cfg,
        limit=args.limit,
        commit=args.commit,
        games=args.game or None,
        workers=args.workers,
        batch=args.batch,
        dry_run=args.dry_run,
        evict=args.evict,
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


def cmd_rating(args) -> int:
    """Le niveau d'avant-partie de chaque (partie, joueur), dans l'ordre du temps.

    Ce n'est pas une fin : c'est le controle « a niveau egal » sans lequel une fiche tactique
    du J5 confondrait « ce comportement fait gagner » et « les bons joueurs le font ».
    """
    cfg = config.load()
    parquet = cfg.data_dir / "parquet"
    out = Path(args.out) if args.out else cfg.data_dir / "rating"
    if args.report:
        if not out.exists():
            print("aucun rating : lance `uv run ofa rating`", file=sys.stderr)
            return 1
        report = rating.metrics(parquet, out)
        print(f"echantillon de confirmation : depuis {report['since']}, {report['games']} parties")
        print(
            f"{'mode':<14}{'>=n':>4}{'parties':>9}{'joueurs':>9}{'paires':>10}"
            f"{'log-loss':>10}{'naif':>8}{'Spearman':>10}{'experience':>12}"
        )
        for key, c in report["cells"].items():
            mode, threshold = key.rsplit("|", 1)
            if c["pairs"] == 0:
                continue
            print(
                f"{mode:<14}{threshold:>4}{c['games']:>9}{c['players']:>9}{c['pairs']:>10}"
                f"{c['logloss']:>10.4f}{c['logloss_naive']:>8.4f}"
                f"{c['spearman']:>10.3f}{c['spearman_experience']:>12.3f}"
            )
        cell = report["cells"].get("tous|0")
        if cell is None or cell["pairs"] == 0:
            print("pas assez de paires pour conclure", file=sys.stderr)
            return 1
        ok = (
            cell["logloss"] < cell["logloss_naive"]
            and cell["spearman"] > cell["spearman_experience"]
        )
        print(
            "\nVERDICT :",
            "le rating bat les deux references" if ok else "REFERENCE NAIVE NON BATTUE",
        )
        return 0 if ok else 1
    report = rating.rebuild(parquet, out)
    print(json.dumps(report, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ofa", description="OpenFront Analyzer")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="collecte les parties (reprend ou elle s'est arretee)")
    p.add_argument(
        "--days", type=int, default=2, help="N derniers jours, du plus recent (defaut 2)"
    )
    p.add_argument(
        "--backfill",
        action="store_true",
        help="parcourt toute l'archive par afterId, du plus ancien au plus recent",
    )
    p.add_argument("--limit", type=int, default=1000, help="parties par page de liste (<=1000)")
    p.add_argument("--max-pages", type=int, default=None, help="s'arrete apres N pages")
    p.add_argument("--ranked-types", nargs="*", default=None, help="defaut: unranked 2v2")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("purge-deleted", help="expurge les comptes supprimes")
    p.add_argument("--since", default=None, help="ISO 8601, dans les 7 derniers jours")
    p.set_defaults(func=cmd_purge)

    p = sub.add_parser("fixtures", help="telecharge les vrais records de test")
    p.set_defaults(func=cmd_fixtures)

    p = sub.add_parser("normalize", help="reconstruit les tables Parquet depuis data/raw")
    p.add_argument("--out", default=None, help="repertoire de sortie (defaut: data/parquet)")
    p.add_argument("--chunk", type=int, default=2000, help="records par groupe de lignes")
    p.set_defaults(func=cmd_normalize)

    p = sub.add_parser("fetch-full", help="telecharge les records complets (avec turns)")
    p.add_argument("--sample", type=int, required=True, help="nombre de parties visees")
    p.add_argument("--seed", type=int, default=20261121, help="graine du tirage (reproductible)")
    p.set_defaults(func=cmd_fetch_full)

    p = sub.add_parser("engines", help="prepare un moteur par commit (worktree + node_modules)")
    p.add_argument("--commit", default=None, help="ne prepare que ce commit")
    p.add_argument("--dry-run", action="store_true", help="affiche le cout, ne prepare rien")
    p.set_defaults(func=cmd_engines)

    p = sub.add_parser("resim", help="rejoue les parties et ecrit data/resim/")
    p.add_argument("--limit", type=int, default=None, help="s'arrete apres N parties")
    p.add_argument("--commit", default=None, help="ne rejoue que les parties de ce commit")
    p.add_argument("--game", nargs="*", default=None, help="gameID precis")
    p.add_argument("--workers", type=int, default=None, help="defaut : min(coeurs, Go libres)")
    p.add_argument("--batch", type=int, default=8, help="parties par tache (defaut 8)")
    p.add_argument("--dry-run", action="store_true", help="affiche la selection, ne rejoue rien")
    p.add_argument("--report", action="store_true", help="relit le journal et sort l'acceptation")
    p.add_argument(
        "--watch",
        action="store_true",
        help="barre de progression, a lancer dans un autre shell pendant que la re-sim tourne",
    )
    p.add_argument("--interval", type=float, default=2.0, help="rafraichissement du suivi (s)")
    p.add_argument(
        "--evict",
        action="store_true",
        help="retire le moteur d'un commit des que son lot est fini (ADR-0008 : 51 Go sur "
        "l'archive complete). DETRUIT les worktrees, donc explicite et jamais par defaut.",
    )
    p.set_defaults(func=cmd_resim)

    p = sub.add_parser("rating", help="le niveau d'avant-partie, dans l'ordre du temps")
    p.add_argument("--out", default=None, help="repertoire de sortie (defaut: data/rating)")
    p.add_argument(
        "--report",
        action="store_true",
        help="relit data/rating et sort l'acceptation sur l'echantillon de confirmation",
    )
    p.set_defaults(func=cmd_rating)

    p = sub.add_parser("rate-check", help="verifie le debit dans le journal des requetes")
    p.add_argument("--log", default=None)
    p.add_argument(
        "--rate",
        type=float,
        default=None,
        help="debit de reference (defaut : OFA_RATE), utile pour verifier un vieux journal",
    )
    p.set_defaults(func=cmd_rate_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
