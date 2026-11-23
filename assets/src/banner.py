"""Hero banner: title on the left, a territory replay on the right (animated, with a static fallback)."""

import numpy as np

from svgkit import (GlyphSet, AQUA, BG, BLUE, LINE, MUTED, ORANGE, TXT, TXT2, svg, text, width)

W, H = 1200, 420
import sys
SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 3
THRESH = float(sys.argv[2]) if len(sys.argv) > 2 else 0.34
rng = np.random.default_rng(SEED + 100)
SPAWNS = [(14, 18), (16, 68), (38, 40)]
STRENGTH = [1.0, 0.95, 0.9]
WAR_FROM = 15
WAR = {(0, 2): 0.16, (2, 0): 0.05, (1, 2): 0.06, (2, 1): 0.03, (0, 1): 0.02, (1, 0): 0.02}

# ---- map: land mask from smooth value noise ---------------------------------------------
COLS, ROWS, CELL = 88, 50, 6.0
MX, MY = 628, 40


def value_noise(cols, rows, scale, seed):
    r = np.random.default_rng(seed)
    gx, gy = int(cols // scale) + 2, int(rows // scale) + 2
    g = r.random((gy, gx))
    ys, xs = np.mgrid[0:rows, 0:cols] / scale
    x0, y0 = xs.astype(int), ys.astype(int)
    fx, fy = xs - x0, ys - y0
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a, b = g[y0, x0], g[y0, x0 + 1]
    c, d = g[y0 + 1, x0], g[y0 + 1, x0 + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


n = 0.55 * value_noise(COLS, ROWS, 14, SEED) + 0.3 * value_noise(COLS, ROWS, 6, SEED + 1) + 0.15 * value_noise(COLS, ROWS, 2.5, SEED + 2)
ys, xs = np.mgrid[0:ROWS, 0:COLS]
edge = np.minimum.reduce([xs, COLS - 1 - xs, ys * 1.4, (ROWS - 1 - ys) * 1.4]) / 10
n = n * np.clip(edge, 0, 1) ** 0.4
land = n > THRESH

# keep only the largest connected landmass plus big islands
from collections import deque
lab = np.full((ROWS, COLS), -1); sizes = []
for r in range(ROWS):
    for c in range(COLS):
        if land[r, c] and lab[r, c] < 0:
            q = deque([(r, c)]); lab[r, c] = len(sizes); k = 0
            while q:
                a, b = q.popleft(); k += 1
                for dr, dc in ((1,0),(-1,0),(0,1),(0,-1)):
                    aa, bb = a+dr, b+dc
                    if 0 <= aa < ROWS and 0 <= bb < COLS and land[aa, bb] and lab[aa, bb] < 0:
                        lab[aa, bb] = lab[r, c]; q.append((aa, bb))
            sizes.append(k)
keep = {i for i, k in enumerate(sizes) if k >= 40}
land = np.isin(lab, list(keep))

# ---- simulate three players: expansion, then a war ---------------------------------------
owner = np.full((ROWS, COLS), -1)
landcells = np.argwhere(land)
for p, (r, c) in enumerate(SPAWNS):
    lr, lc = landcells[np.argmin(((landcells - [r, c]) ** 2).sum(1))]
    SPAWNS[p] = (int(lr), int(lc))
    owner[lr, lc] = p

NB = [(-1, 0), (1, 0), (0, -1), (0, 1)]
FRAMES = 30
states = [owner.copy()]
for f in range(1, FRAMES):
    for _ in range(2):
        new = owner.copy()
        rnd = rng.random((ROWS, COLS, 4))
        for r in range(ROWS):
            for c in range(COLS):
                if not land[r, c]:
                    continue
                cur = owner[r, c]
                for i, (dr, dc) in enumerate(NB):
                    rr, cc = r + dr, c + dc
                    if not (0 <= rr < ROWS and 0 <= cc < COLS):
                        continue
                    p = owner[rr, cc]
                    if p < 0 or p == cur:
                        continue
                    if cur == -1:
                        if rnd[r, c, i] < 0.30 * STRENGTH[p]:
                            new[r, c] = p
                    elif f >= WAR_FROM:
                        pr = WAR.get((p, cur), 0.0)
                        if rnd[r, c, i] < pr:
                            new[r, c] = p
        owner = new
    states.append(owner.copy())

COLORS = [BLUE, AQUA, ORANGE]


def mix(hexc, a):
    h = hexc.lstrip("#"); b = BG.lstrip("#")
    rgb = [int(h[i:i+2], 16) for i in (0, 2, 4)]; bg = [int(b[i:i+2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(bg[i] + (rgb[i]-bg[i]) * a):02x}" for i in range(3))


FILL = [mix(c, 0.42) for c in COLORS]
DUR = 11.0
T_END_FRAMES = 0.74
HOLD_END = 0.95


def code(st):
    """0 = unowned, 1 + 2p = interior of p, 2 + 2p = border of p."""
    out = np.zeros((ROWS, COLS), int)
    for r in range(ROWS):
        for c in range(COLS):
            p = st[r, c]
            if p < 0:
                continue
            border = False
            for dr, dc in NB:
                rr, cc = r + dr, c + dc
                if not (0 <= rr < ROWS and 0 <= cc < COLS) or st[rr, cc] != p:
                    border = True
                    break
            out[r, c] = 2 + 2 * p if border else 1 + 2 * p
    return out


def runs_path(mask):
    """Horizontal runs as stroked line segments, relative moves: much smaller than filled rects."""
    parts, cx, cy = [], None, None
    for r in range(ROWS):
        c = 0
        while c < COLS:
            if mask[r, c]:
                c0 = c
                while c < COLS and mask[r, c]:
                    c += 1
                x, y = MX + c0 * CELL, MY + r * CELL + CELL / 2
                if cx is None:
                    parts.append(f"M{x:g} {y:g}")
                else:
                    parts.append(f"m{x - cx:g} {y - cy:g}")
                parts.append(f"h{(c - c0) * CELL:g}")
                cx, cy = MX + c * CELL, y
            else:
                c += 1
    return "".join(parts)


def color_of(k):
    p = (k - 1) // 2
    return COLORS[p] if k % 2 == 0 else FILL[p]


body = [f'<rect width="{W}" height="{H}" rx="22" fill="{BG}"/>']
body.append(f'<g fill="none"><path stroke="#182121" stroke-width="{CELL}" d="{runs_path(land)}"/>')

anim, static = [], []
codes = [code(s) for s in states]
prev = np.zeros((ROWS, COLS), int)
for f, cd in enumerate(codes):
    t = f / (FRAMES - 1) * T_END_FRAMES
    for k in range(1, 7):
        mask = (cd == k) & (prev != k)
        if not mask.any():
            continue
        if f == 0:
            vals, kt = "0.95;0.95;0", f"0;{HOLD_END};1"
        else:
            vals, kt = "0;0;1;1;0", f"0;{t:.4f};{min(t + 0.008, 0.99):.4f};{HOLD_END};1"
        anim.append(
            f'<path stroke="{color_of(k)}" stroke-width="{CELL}" opacity="0" d="{runs_path(mask)}">'
            f'<animate attributeName="opacity" dur="{DUR}s" repeatCount="indefinite" '
            f'values="{vals}" keyTimes="{kt}"/></path>'
        )
    prev = cd
for k in range(1, 7):
    mask = codes[-1] == k
    if mask.any():
        static.append(f'<path stroke="{color_of(k)}" stroke-width="{CELL}" d="{runs_path(mask)}"/>')

# pixel grid overlay: thin surface-coloured lines give the map its tile texture
grid = "".join(f"M{MX + c * CELL:g} {MY}v{ROWS * CELL:g}" for c in range(1, COLS)) + "".join(
    f"M{MX} {MY + r * CELL:g}h{COLS * CELL:g}" for r in range(1, ROWS))
body_grid = f'<path d="{grid}" stroke="{BG}" stroke-width="0.9" opacity="0.55" fill="none"/>'

for r, c in SPAWNS:
    cx, cy = MX + c * CELL + CELL / 2, MY + r * CELL + CELL / 2
    m = f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5.5" fill="{BG}" stroke="{TXT}" stroke-width="2"/>'
    anim.append(m)
    static.append(m)

TG = GlyphSet("t", 16, "medium")
ticks = [round(i * 600 / (FRAMES - 1)) * 10 for i in range(FRAMES)]
CX = MX + COLS * CELL
CY = 378
for f, tk in enumerate(ticks):
    t0 = f / (FRAMES - 1) * T_END_FRAMES
    t1 = (f + 1) / (FRAMES - 1) * T_END_FRAMES if f < FRAMES - 1 else 1.0
    label = f"tick {tk:,}".replace(",", " ")
    if f == 0:
        vals, kts = "1;0;0", f"0;{t1:.4f};1"
    elif f == FRAMES - 1:
        vals, kts = "0;1;1", f"0;{t0:.4f};1"
    else:
        vals, kts = "0;1;0;0", f"0;{t0:.4f};{t1:.4f};1"
    anim.append(
        f'<g opacity="{1 if f == 0 else 0}">{TG.text(label, CX, CY, TXT, "end")}'
        f'<animate attributeName="opacity" dur="{DUR}s" repeatCount="indefinite" calcMode="discrete" '
        f'values="{vals}" keyTimes="{kts}"/></g>'
    )
static.append(text(f"tick {ticks[-1]:,}".replace(",", " "), CX, CY, 16, TXT, "medium", "end"))

body.append(f'<g class="anim">{"".join(anim[:-(FRAMES + len(SPAWNS))])}</g>')
body.append(f'<g class="static">{"".join(static[:-(len(SPAWNS) + 1)])}</g>')
body.append("</g>")
body.append(body_grid)
body.append(f'<g class="anim">{"".join(anim[-(FRAMES + len(SPAWNS)):])}</g>')
body.append(f'<g class="static">{"".join(static[-(len(SPAWNS) + 1):])}</g>')
body.append(text("Every tick re-simulated in the real game engine", MX, CY, 14, MUTED, "regular"))

# ---- left column ---------------------------------------------------------------------------
LX = 64
body.append(text("OFA", LX - 6, 178, 132, TXT, "display-black", tracking=-0.02))
body.append(text("OpenFront Analyzer", LX, 222, 30, TXT, "display-semi"))
body.append(text("Replays real OpenFront games in the real engine,", LX, 262, 19, TXT2, "regular"))
body.append(text("then measures what actually wins.", LX, 288, 19, TXT2, "regular"))

stats = [("513 / 514", "replays bit-exact"), ("74.3 M", "rows of trajectory"), ("258 k", "players rated")]
x = LX
for big, small in stats:
    body.append(text(big, x, 352, 26, TXT, "display-bold"))
    body.append(text(small, x, 374, 13.5, MUTED, "regular"))
    x += max(width(big, 26, "display-bold"), width(small, 13.5)) + 34
# thin separator above stats
body.append(f'<rect x="{LX}" y="314" width="{x - LX - 34:.0f}" height="1" fill="{LINE}"/>')

body.insert(1, f"<defs>{TG.render_defs()}</defs>")
STYLE = ".static{display:none}@media (prefers-reduced-motion: reduce){.anim{display:none}.static{display:inline}}"
out = svg(W, H, "".join(body), "OFA, OpenFront Analyzer: replays real OpenFront games in the real engine, then measures what actually wins", 900, STYLE)
open("assets/banner.svg", "w").write(out)
print("banner", len(out) // 1024, "KB")
