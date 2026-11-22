"""J4b, experience 1 : le debit de la machine selon le nombre de workers.

Les 16 parties de docs/perf-sample.txt, choisies a cout MESURE voisin (123 s de CPU chacune au
J3, pas a ticks egaux : a 8 500 ticks le cout reel va de 16 a 241 s selon la map et les joueurs).
A cout egal, une passe tient en vagues pleines et la queue de fin de passe ne fausse pas le
debit machine. Une passe complete = 33 min de CPU, donc ~18 min de mur pour les six points.

Tout se passe dans un OFA_DATA_DIR jetable qui pointe par liens vers les vraies donnees :
data/resim et data/state/index.sqlite de production ne sont jamais touches, et le journal
remis a zero avant chaque passe fait rejouer les memes parties (sinon elles sont « deja faites »).

    uv run python scratch/bench_workers.py 1 4 6 8 12 16

Sortie : une ligne par passe, dans scratch/bench_workers.tsv.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = Path(os.environ.get("TMPDIR", "/tmp")) / "j4b"
SAMPLE = [
    ligne.strip()
    for ligne in (ROOT / "docs" / "perf-sample.txt").read_text().splitlines()
    if ligne.strip() and not ligne.startswith("#")
]
OUT = ROOT / "scratch" / "bench_workers.tsv"


def prepare() -> None:
    """Les liens vers les vraies donnees, et des repertoires de sortie vides."""
    for d in ("state", "resim", "logs"):
        (BENCH / d).mkdir(parents=True, exist_ok=True)
    for d in ("openfront", "engines", "parquet", "raw", "maps", "node_modules"):
        link = BENCH / d
        if not link.exists():
            link.symlink_to(ROOT / "data" / d)
    # Le cache de pages chaud avant la premiere passe : 29 Mo de records, sinon la passe 1
    # paie seule les lectures disque et porte le biais.
    subprocess.run(f"cat {ROOT}/data/raw/full/*/*.zst > /dev/null", shell=True, check=False)


def reset() -> None:
    (BENCH / "state" / "index.sqlite").unlink(missing_ok=True)
    shutil.rmtree(BENCH / "resim", ignore_errors=True)
    (BENCH / "resim").mkdir()


def journal() -> tuple[int, float, int, int]:
    conn = sqlite3.connect(BENCH / "state" / "index.sqlite")
    try:
        row = conn.execute(
            "select coalesce(sum(ticks),0), coalesce(sum(wall_s),0), coalesce(max(rss_kb),0),"
            " sum(status='ok') from resim"
        ).fetchone()
    finally:
        conn.close()
    return row


def run(workers: int) -> dict:
    # A 1 worker, une passe des 16 parties couterait 33 min pour une seule mesure : la
    # reference sans concurrence se lit sur une partie, les 16 ayant le meme cout.
    games = SAMPLE if workers > 1 else SAMPLE[:1]
    reset()
    t0 = time.monotonic()
    proc = subprocess.run(
        ["uv", "run", "ofa", "resim", "--workers", str(workers), "--batch", "1", "--game", *games],
        cwd=ROOT,
        env={**os.environ, "OFA_DATA_DIR": str(BENCH)},
        capture_output=True,
        text=True,
    )
    wall = time.monotonic() - t0
    if proc.returncode != 0:
        sys.exit(f"workers={workers} a echoue :\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    ticks, cpu_s, rss_kb, ok = journal()
    return {
        "workers": workers,
        "ok": ok,
        "selected": report["selected"],
        "ticks": ticks,
        "wall_s": round(wall, 1),
        "cpu_s": round(cpu_s, 1),
        # Le debit de la MACHINE : c'est lui qui decide du nombre de workers.
        "machine_ticks_s": round(ticks / wall, 1),
        # Le debit par partie, insensible a la queue de fin de passe : sa chute quand les
        # workers augmentent est la saturation elle-meme, sans le biais d'ordonnancement.
        "core_ticks_s": round(ticks / cpu_s, 1) if cpu_s else None,
        "busy": round(cpu_s / (wall * workers), 3),
        "rss_max_mb": rss_kb // 1024,
    }


def main(argv: list[str]) -> int:
    counts = [int(a) for a in argv[1:]] or [1, 4, 6, 8, 12, 16]
    prepare()
    with OUT.open("a") as f:
        for w in counts:
            r = run(w)
            print(json.dumps(r, ensure_ascii=False), flush=True)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
