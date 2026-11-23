"""'How I work with an AI agent': three roles, the loop, and the context the agent loads."""

from svgkit import AQUA, BLUE, CARD, CLAUDE, LINE, MUTED, TXT, TXT2, svg, text, width

W, H = 1200, 600
b = [f'<rect width="{W}" height="{H}" rx="22" fill="#0E1111"/>']
b.append(text("I steer, the agent builds, the harness checks", 64, 74, 30, TXT, "display-semi"))
b.append(text("Claude Code writes the code. I own the spec, the plans, and the checks that are allowed to say no.",
              64, 106, 16, TXT2))

COLS = [
    (BLUE, "ME", "I steer", [
        ("Write the spec", "scope, constraints, rigour rules"),
        ("Review every plan", "plan mode first: I cut, then it codes"),
        ("Decide in writing", "12 ADRs: options, measure, re-check"),
    ]),
    (CLAUDE, "AGENT", "Claude Code builds", [
        ("Writes the code", "Python, a TypeScript runner, SQL"),
        ("Tests on real games", "100 tests, the engine is never mocked"),
        ("Shows its measurements", "every number comes with its command"),
    ]),
    (AQUA, "HARNESS", "The harness checks", [
        ("A hook gates every turn", "fast tests red: the turn cannot end"),
        ("A subagent tries to refute", "stats-reviewer, before anything ships"),
        ("One look at held-out data", "the CLI refuses a second read"),
    ]),
]
CW, GAP, TOP, CH = 330, 41, 140, 268
for i, (col, tag, title, items) in enumerate(COLS):
    x = 64 + i * (CW + GAP)
    b.append(f'<rect x="{x}" y="{TOP}" width="{CW}" height="{CH}" rx="16" fill="{CARD}" stroke="{LINE}" stroke-width="1.5"/>')
    b.append(f'<rect x="{x + 24}" y="{TOP}" width="56" height="3" rx="1.5" fill="{col}"/>')
    tw = width(tag, 11, "mono", tracking=0.08)
    b.append(f'<rect x="{x + 24}" y="{TOP + 24}" width="{tw + 16:.0f}" height="22" rx="6" fill="{col}" opacity="0.16"/>')
    b.append(text(tag, x + 32, TOP + 39, 11, col, "mono", tracking=0.08))
    b.append(text(title, x + 24, TOP + 80, 22, TXT, "display-semi"))
    for j, (head, sub) in enumerate(items):
        y = TOP + 122 + j * 50
        b.append(f'<circle cx="{x + 30}" cy="{y - 5}" r="4" fill="{col}"/>')
        b.append(text(head, x + 44, y, 15.5, TXT, "semi"))
        b.append(text(sub, x + 44, y + 21, 13.5, MUTED))
    if i < 2:
        ax = x + CW + 8
        ay = TOP + CH / 2
        b.append(f'<path d="M{ax} {ay}h{GAP - 18}" stroke="{TXT2}" stroke-width="2" fill="none"/>'
                 f'<path d="M{ax + GAP - 22} {ay - 5}l6 5l-6 5" stroke="{TXT2}" stroke-width="2" fill="none" '
                 f'stroke-linecap="round" stroke-linejoin="round"/>')

# feedback loop: from the harness card back to "I steer"
x0 = 64 + CW / 2
x2 = 64 + 2 * (CW + GAP) + CW / 2
yb = TOP + CH
yl = yb + 46
b.append(f'<path d="M{x2} {yb + 6}V{yl - 14}q0 14 -14 14H{x0 + 14}q-14 0 -14 -14V{yb + 14}" '
         f'stroke="{MUTED}" stroke-width="1.8" fill="none" stroke-dasharray="5 6"/>')
b.append(f'<path d="M{x0 - 5} {yb + 14}l5 -7l5 7" stroke="{MUTED}" stroke-width="1.8" fill="none" '
         f'stroke-linecap="round" stroke-linejoin="round"/>')
lbl = "anything that turned out false goes in PROGRESS.md, and the next session starts from it"
lw = width(lbl, 13.5)
b.append(f'<rect x="{W / 2 - lw / 2 - 12:.0f}" y="{yl - 12}" width="{lw + 24:.0f}" height="24" fill="#0E1111"/>')
b.append(text(lbl, W / 2, yl + 5, 13.5, TXT2, anchor="middle"))

# context chips
cy = 512
b.append(f'<rect x="64" y="{cy - 20}" width="{W - 128}" height="1" fill="{LINE}"/>')
b.append(text("LOADED BY THE AGENT", 64, cy + 22, 11.5, MUTED, "semi", tracking=0.08))
chips = ["CLAUDE.md", ".claude/rules/", "/tactic", "/analyze-player", "stats-reviewer", "hooks/gate.sh",
         "docs/adr/"]
x = 64 + width("LOADED BY THE AGENT", 11.5, "semi", tracking=0.08) + 22
for c in chips:
    cw = width(c, 13, "mono") + 22
    b.append(f'<rect x="{x:.0f}" y="{cy + 2}" width="{cw:.0f}" height="30" rx="8" fill="{CARD}" stroke="{LINE}" stroke-width="1.2"/>')
    b.append(text(c, x + 11, cy + 22, 13, TXT2, "mono"))
    x += cw + 10
print("chips end at", round(x), "of", W - 64)

open("assets/workflow.svg", "w").write(svg(W, H, "".join(b),
     "How I work with an AI agent: I steer (spec, plan review, decision records), Claude Code builds "
     "(code, tests on real games, measurements), the harness checks (a hook gates every turn, a subagent "
     "tries to refute findings, one single read of held-out data). What turned out false is logged for the next session.",
     900))
