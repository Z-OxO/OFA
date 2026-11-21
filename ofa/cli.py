"""CLI de ofa. argparse suffit : pas de dépendance pour trois sous-commandes."""

import argparse
import json
import sys
from pathlib import Path

from . import config, fixtures, index, ingest, purge
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
