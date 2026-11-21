# ofa — OpenFront Analyzer (config Claude Code)

Kit de démarrage : config Claude Code + prompt du projet. Le code sera écrit par Claude Code, jalon par jalon.

## Installation (Windows + WSL2)

1. WSL2 + Ubuntu, si ce n'est pas déjà fait, dans PowerShell **administrateur** :
   `wsl --install -d Ubuntu-24.04`, redémarre, ouvre Ubuntu et crée ton utilisateur.
2. Dans Ubuntu, dézippe puis lance le script (il copie le projet dans `~/ofa`) :
   ```bash
   cd /mnt/c/Users/<toi>/Downloads   # là où est le zip
   unzip ofa.zip && bash ofa/setup-wsl.sh
   ```
3. Suis les étapes affichées à la fin du script.

## Contenu

| Fichier | Rôle |
| --- | --- |
| `PROMPT.md` | Prompt à coller dans Claude Code (Claude le sauvegarde ensuite dans `docs/SPEC.md`) |
| `CLAUDE.md` | Contexte chargé à chaque session : commandes, architecture, pièges |
| `.claude/settings.json` | Permissions, sandbox (domaines réseau autorisés), hooks |
| `.claude/hooks/format.sh` | ruff format + check après chaque écriture de `.py` |
| `.claude/hooks/gate.sh` | Bloque la fin d'un tour si les tests rapides échouent |
| `.claude/rules/*.md` | Règles chargées seulement quand Claude touche l'analyse ou la re-sim |
| `.claude/agents/stats-reviewer.md` | Relecteur statistique indépendant |
| `.claude/skills/tactic/` | `/tactic` : protocole pour toute hypothèse tactique |
| `.claude/skills/analyze-player/` | `/analyze-player <id>` : analyse de tes parties |
| `setup-wsl.sh` | Installe tout (Node 24, npm 12, uv, Python 3.12, DuckDB, Claude Code, sandbox) |

## Workflow

- Un jalon par session : `/clear` ou nouvelle session, « Lis docs/SPEC.md section Jx et docs/PROGRESS.md, plan mode, puis implémente ».
- Fin de jalon : `/code-review`, l'agent stats-reviewer pour l'analyse, tu valides le critère d'acceptation, commit.
- Les longs traitements (toute l'archive, re-sim massive) tournent dans tmux ou systemd, pas dans la session Claude.
