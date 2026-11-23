"""Milestones J0 to J9, as a track."""
from svgkit import AQUA, CARD, LINE, MUTED, TXT, TXT2, YELLOW, svg, text

MS = [
    ("J0", "Recon", ["assumptions", "verified"], "done"),
    ("J1", "Ingestion", ["55 952 games"], "done"),
    ("J2", "Normalise", ["3 Parquet", "tables"], "done"),
    ("J3", "Re-simulation", ["513/514", "bit-exact"], "done"),
    ("J4", "Rating", ["258 354", "players"], "done"),
    ("J4b", "Workers", ["refuted,", "no code kept"], "done"),
    ("J5", "Analysis", ["12 hypotheses", "pre-registered"], "now"),
    ("J6", "Website", [], "next"),
    ("J7", "My games", [], "next"),
    ("J8", "AI layer", [], "next"),
    ("J9", "Production", [], "next"),
]
W, H = 1200, 262
b = [f'<rect width="{W}" height="{H}" rx="22" fill="#0E1111"/>']
b.append(text("Where it stands", 64, 70, 26, TXT, "display-semi"))
# legend
lx = W - 64
for lab, kind in [("next", "next"), ("in progress", "now"), ("done", "done")]:
    tw = len(lab) * 7.4
    b.append(text(lab, lx, 66, 13.5, MUTED, anchor="end"))
    from svgkit import width
    tw = width(lab, 13.5)
    cx = lx - tw - 12
    if kind == "done":
        b.append(f'<circle cx="{cx:.1f}" cy="61" r="5.5" fill="{AQUA}"/>')
    elif kind == "now":
        b.append(f'<circle cx="{cx:.1f}" cy="61" r="4.5" fill="#0E1111" stroke="{YELLOW}" stroke-width="2.5"/>')
    else:
        b.append(f'<circle cx="{cx:.1f}" cy="61" r="4.5" fill="#0E1111" stroke="#3A4646" stroke-width="2"/>')
    lx = cx - 26

L, R, Y = 110, 1090, 140
step = (R - L) / (len(MS) - 1)
xnow = L + 6 * step
b.append(f'<path d="M{L} {Y}H{xnow:.1f}" stroke="{AQUA}" stroke-width="3" stroke-linecap="round"/>')
b.append(f'<path d="M{xnow:.1f} {Y}H{R}" stroke="#2C3636" stroke-width="2" stroke-dasharray="2 7" stroke-linecap="round"/>')
for i, (jid, name, sub, st) in enumerate(MS):
    x = L + i * step
    col = {"done": AQUA, "now": YELLOW, "next": MUTED}[st]
    b.append(text(jid, x, Y - 24, 12.5, col if st != "next" else MUTED, "mono", anchor="middle"))
    if st == "done":
        b.append(f'<circle cx="{x:.1f}" cy="{Y}" r="9" fill="{AQUA}" stroke="#0E1111" stroke-width="3"/>')
        b.append(f'<path d="M{x - 4:.1f} {Y}l2.8 2.8l5 -5.4" stroke="#0E1111" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>')
    elif st == "now":
        b.append(f'<circle cx="{x:.1f}" cy="{Y}" r="9" fill="none" stroke="{YELLOW}" stroke-width="2" opacity="0.6">'
                 f'<animate attributeName="r" values="9;19" dur="2.2s" repeatCount="indefinite"/>'
                 f'<animate attributeName="opacity" values="0.6;0" dur="2.2s" repeatCount="indefinite"/></circle>')
        b.append(f'<circle cx="{x:.1f}" cy="{Y}" r="8" fill="#0E1111" stroke="{YELLOW}" stroke-width="3"/>')
        b.append(f'<circle cx="{x:.1f}" cy="{Y}" r="3.2" fill="{YELLOW}"/>')
    else:
        b.append(f'<circle cx="{x:.1f}" cy="{Y}" r="7" fill="#0E1111" stroke="#3A4646" stroke-width="2"/>')
    b.append(text(name, x, Y + 40, 14, TXT if st != "next" else TXT2, "semi", anchor="middle"))
    for k, line in enumerate(sub):
        b.append(text(line, x, Y + 62 + k * 18, 12.5, MUTED, anchor="middle"))
open("assets/roadmap.svg", "w").write(svg(W, H, "".join(b),
     "Milestones: J0 reconnaissance, J1 ingestion, J2 normalisation, J3 re-simulation, J4 rating and J4b worker count are done; "
     "J5 analysis engine is in progress; J6 website, J7 personal report, J8 AI layer and J9 production are next.", 900))
