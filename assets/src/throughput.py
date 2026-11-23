"""The measurement that deleted a milestone: re-simulation throughput vs worker count (docs/PERF.md)."""
from svgkit import BLUE, GRID, LINE, MUTED, RED, AQUA, TXT, TXT2, svg, text

DATA = [(1, 162), (4, 612), (6, 746), (8, 900), (12, 954), (16, 1150), (20, 1160)]
W, H = 1200, 640
L, R, T, B = 110, 1120, 196, 500
b = [f'<rect width="{W}" height="{H}" rx="22" fill="#0E1111"/>']
b.append(text("The measurement that deleted a milestone", 64, 74, 30, TXT, "display-semi"))
b.append(text("Re-simulation throughput against worker count: 16 real games, the same list on every pass.", 64, 106, 16, TXT2))
b.append(text("ticks / second, whole machine", 64, 158, 13.5, MUTED))


def X(w):
    return L + (w - 1) / 19 * (R - L)


def Y(v):
    return B - v / 1200 * (B - T)


for v in range(0, 1201, 300):
    b.append(f'<path d="M{L} {Y(v):.1f}H{R}" stroke="{GRID if v else "#3A4646"}" stroke-width="1"/>')
    b.append(text(f"{v:,}".replace(",", " "), L - 14, Y(v) + 4.5, 13, MUTED, anchor="end"))
for w, _ in DATA:
    b.append(text(str(w), X(w), B + 26, 13, MUTED, anchor="middle"))
b.append(text("workers", R, B + 52, 13.5, MUTED, anchor="end"))

# the assumption
x6 = X(6)
b.append(f'<path d="M{x6:.1f} {T - 6}V{B}" stroke="{RED}" stroke-width="1.5" stroke-dasharray="4 5"/>')
b.append(text("the milestone assumed", x6 + 14, Y(330), 14, TXT2))
b.append(text("saturation started here", x6 + 14, Y(330) + 20, 14, TXT2))

# line + markers
pts = " ".join(f"{X(w):.1f},{Y(v):.1f}" for w, v in DATA)
b.append(f'<polyline points="{pts}" fill="none" stroke="{BLUE}" stroke-width="2.5" stroke-linejoin="round"/>')
for w, v in DATA:
    r = 7 if w == 8 else 5.5
    b.append(f'<circle cx="{X(w):.1f}" cy="{Y(v):.1f}" r="{r}" fill="{BLUE}" stroke="#0E1111" stroke-width="2.5"/>')
# selective labels
b.append(text("162", X(1) + 14, Y(162) + 5, 14, TXT, "semi"))
b.append(text("900", X(8), Y(900) - 18, 14, TXT, "semi", anchor="middle"))
b.append(text("the server runs 8", X(8), Y(900) - 40, 13.5, MUTED, anchor="middle"))
b.append(text("1 150", X(16), Y(1150) - 18, 14, TXT, "semi", anchor="middle"))
b.append(text("1 160", X(20), Y(1160) - 18, 14, TXT, "semi", anchor="end"))
# plateau bracket
yb = Y(1160) - 46
b.append(f'<path d="M{X(16):.1f} {yb + 8}V{yb}H{X(20):.1f}V{yb + 8}" stroke="{AQUA}" stroke-width="1.8" fill="none"/>')
b.append(text("the real plateau: +0.9%", (X(16) + X(20)) / 2, yb - 10, 14, TXT2, anchor="middle"))

b.append(f'<rect x="64" y="{H - 66}" width="{W - 128}" height="1" fill="{LINE}"/>')
b.append(text("The slowdown blamed on the workers came from a feature shipped in the same run. The milestone closed with no code change kept.",
              64, H - 36, 14, MUTED))
open("assets/throughput.svg", "w").write(svg(W, H, "".join(b),
     "Re-simulation throughput against worker count: 162 ticks per second at 1 worker, 612 at 4, 746 at 6, 900 at 8, "
     "954 at 12, 1150 at 16, 1160 at 20. The milestone had assumed saturation at 6 workers; the real plateau is between 16 and 20.", 900))
