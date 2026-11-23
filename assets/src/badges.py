"""Pill badges in the Fly-in style: dark pill, coloured dot, one line of text."""

from svgkit import AQUA, BLUE, CLAUDE, MAGENTA, ORANGE, TXT, VIOLET, YELLOW, svg, text, width

BADGES = [
    ("python", "Python 3.12", BLUE, YELLOW),
    ("typescript", "TypeScript engine replay", BLUE, None),
    ("duckdb", "DuckDB + Parquet", YELLOW, None),
    ("exact", "513/514 replays bit-exact", AQUA, None),
    ("tests", "100 tests on real games", AQUA, None),
    ("prereg", "12 pre-registered hypotheses", VIOLET, None),
    ("adr", "12 decision records", MAGENTA, None),
    ("claude", "Built with Claude Code", CLAUDE, None),
]

H, SIZE, PAD = 34, 14.5, 16
for key, label, dot, ring in BADGES:
    tw = width(label, SIZE, "medium")
    w = round(PAD + 13 + 10 + tw + PAD)
    body = (
        f'<rect x="0.75" y="0.75" width="{w - 1.5}" height="{H - 1.5}" rx="{(H - 1.5) / 2}" '
        f'fill="#161B1B" stroke="#2A3232" stroke-width="1.5"/>'
        f'<circle cx="{PAD + 5}" cy="17" r="{5 if ring else 5.5}" fill="{dot}"'
        + (f' stroke="{ring}" stroke-width="2.5"' if ring else "")
        + "/>"
        + text(label, PAD + 13 + 8, 22, SIZE, TXT, "medium")
    )
    disp = round(w * 28 / H)
    open(f"assets/badges/{key}.svg", "w").write(svg(w, H, body, label, disp))
print("badges ok")
