"""Forest plot, three small multiples (one axis per measure): the 12 pre-registered hypotheses, exploration half."""
import json

from svgkit import BLUE, CARD, GRID, LINE, MUTED, RED, TXT, TXT2, svg, text, width

RUNS = "findings/runs/"


def run(h):
    d = json.load(open(f"{RUNS}{h}-exploration.json"))
    return d["effet"], d["ic95"], d["n"]


# (id, label, fragile)
RANK = [
    ("navire-ouverture-ffa", "Builds a warship in the opening", False),
    ("ville-tot-ffa", "First city earlier than the median", False),
    ("alliance-milieu-ffa", "Asks for an alliance, mid-game", False),
    ("port-ouverture-ffa", "Builds a port in the opening", False),
    ("transports-milieu-ffa", "More transport attacks, mid-game", False),
    ("alliance-ouverture-ffa", "Asks for an alliance in the opening", False),
    ("or-garde-milieu-ffa", "More gold banked at tick 6 000", False),
    ("trahison-milieu-ffa", "Breaks an alliance, mid-game", True),
    ("trahison-ouverture-ffa", "Breaks an alliance in the opening", True),
]
TEAM = [("alliance-ouverture-team", "Asks for an alliance in the opening", False)]
SURV = [
    ("defenses-ouverture-ffa", "More defense posts in the opening", False),
    ("attaque-humain-tot-ffa", "Attacks a human earlier than the median", True),
]
# each panel: title, rows, sign flip (so that right = good for the player), scale, axis max, tick step, unit fmt
PANELS = [
    ("FFA · final rank", "percentile points, right = finishes higher", RANK, -100, 15, 5, "{:+.0f}", "{:+.1f}"),
    ("Team mode · team win rate", "points, right = team wins more", TEAM, 100, 20, 10, "{:+.0f}", "{:+.1f}"),
    ("FFA · survival after tick 1 800", "ticks (1 tick = 100 ms), right = survives longer", SURV, 1, 1000, 500, "{:+,.0f}", "{:+,.0f}"),
]

W = 1200


def num(s):
    return s.replace(",", "\u2009").replace("-", "\u2212")


LX, PX0, PX1 = 64, 470, 1000
ROW = 32
b = []
y = 74
b.append(text("12 pre-registered hypotheses, first look", LX, y, 30, TXT, "display-semi"))
b.append(text("Exploration half only. Effect at equal rating, starting state and neighbourhood, with a 95% cluster-bootstrap CI.",
              LX, y + 32, 16, TXT2))
b.append(text("None of these is a finding yet: the held-out half is read once, when the sample is dense enough.",
              LX, y + 56, 16, TXT2))
y += 100

for title, unit, rows, flip, amax, step, fmt, vfmt in PANELS:
    y += 34
    b.append(text(title, LX, y, 17, TXT, "semi"))
    b.append(text(unit, LX + width(title, 17, "semi") + 14, y, 14, MUTED))
    y += 18
    top = y
    h = len(rows) * ROW + 8
    b.append(f'<rect x="{LX - 16}" y="{top}" width="{W - 2 * LX + 32}" height="{h + 34}" rx="12" fill="{CARD}"/>')

    def X(v):
        return PX0 + (v + amax) / (2 * amax) * (PX1 - PX0)

    # grid + ticks
    t = -amax
    while t <= amax + 1e-9:
        gx = X(t)
        col = "#3A4646" if t == 0 else GRID
        b.append(f'<path d="M{gx:.1f} {top + 6}V{top + h}" stroke="{col}" stroke-width="{1.5 if t == 0 else 1}"/>')
        lab = "0" if t == 0 else num(fmt.format(t))
        b.append(text(lab, gx, top + h + 22, 12.5, MUTED, anchor="middle"))
        t += step
    for i, (hid, label, fragile) in enumerate(rows):
        e, (lo, hi), n = run(hid)
        e, lo, hi = e * flip, lo * flip, hi * flip
        lo, hi = min(lo, hi), max(lo, hi)
        cy = top + 4 + i * ROW + ROW / 2
        col = BLUE if e > 0 else RED
        b.append(text(label, LX, cy + 5, 14.5, TXT))
        if fragile:
            b.append(text("fragile", LX + width(label, 14.5) + 10, cy + 5, 12.5, MUTED, "semi"))
        b.append(f'<path d="M{X(lo):.1f} {cy}H{X(hi):.1f}" stroke="{col}" stroke-width="2.5" stroke-linecap="round"/>')
        if fragile:
            b.append(f'<circle cx="{X(e):.1f}" cy="{cy}" r="5.5" fill="{CARD}" stroke="{col}" stroke-width="2.5"/>')
        else:
            b.append(f'<circle cx="{X(e):.1f}" cy="{cy}" r="6" fill="{col}" stroke="{CARD}" stroke-width="2"/>')
        b.append(text(num(vfmt.format(e)), W - LX, cy + 5, 14, TXT, "semi", anchor="end"))
    y = top + h + 34

y += 34
b.append(f'<rect x="{LX}" y="{y - 22}" width="{W - 2 * LX}" height="1" fill="{LINE}"/>')
b.append(f'<circle cx="{LX + 6}" cy="{y - 4}" r="5.5" fill="none" stroke="{TXT2}" stroke-width="2.5"/>')
b.append(text("fragile: effect under 1.7× the minimum detectable effect, flagged in its pre-registration before the read.",
              LX + 22, y + 1, 13.5, MUTED))
y += 30
H = y
body = f'<rect width="{W}" height="{H}" rx="22" fill="#0E1111"/>' + "".join(b)
open("assets/findings.svg", "w").write(svg(W, H, body,
     "Forest plot of the 12 pre-registered hypotheses on the exploration half, each with a 95% cluster-bootstrap "
     "confidence interval. None is a finding until the held-out half is read.", 900))
print("findings H", H)
