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

## Installation sur le serveur (Debian/Ubuntu natif, 8 cœurs, 100 Go)

Le serveur porte tout : collecte continue, normalisation, re-simulation, site (ADR-0009). Le
portable ne lui envoie que du code, par ce dépôt — à une exception près, l'amorçage des données.

```bash
# 1. le projet
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/Z-OxO/OFA.git ~/ofa
cd ~/ofa && bash setup-wsl.sh        # node 24, npm 12, uv, python 3.12, duckdb, uv sync

# 2. le contact du User-Agent (hors git, obligatoire : aucun appel API sans lui)
echo 'ton-adresse@exemple.fr' > ~/ofa/.ofa-contact

# 3. le clone de référence du moteur, 2,1 Go (indispensable pour re-simuler)
git clone https://github.com/openfrontio/OpenFrontIO.git ~/ofa/data/openfront

# 4. amorçage des données DEPUIS LE PORTABLE, ~650 Mo, collecte arrêtée des deux côtés :
#    évite de re-télécharger 55 952 records déjà payés à l'API
rsync -a --info=progress2 ~/ofa/data/raw/   serveur:~/ofa/data/raw/
rsync -a                  ~/ofa/data/state/ serveur:~/ofa/data/state/

# 5. tout le reste se reconstruit sur place (rien de calculable ne se transporte)
cd ~/ofa
uv run ofa normalize     # 3 min 27 pour 55 952 records
uv run ofa rating        # 14 s
uv run ofa fixtures      # les fixtures sont hors git : à retélécharger pour pouvoir tester
uv run pytest -q -m "not slow and not network"
```

Ensuite, et seulement dans `tmux` ou un timer systemd (J9) :

| Tâche | Commande | Débit |
| --- | --- | --- |
| collecte incrémentale | `OFA_RATE=1 uv run ofa ingest` | 1 req/s (ADR-0006) |
| purge quotidienne | `uv run ofa purge-deleted` | |
| records complets | `uv run ofa fetch-full --sample N --seed 20261121` | 1 req/s, graine fixe = sur-ensemble |
| moteurs | `uv run ofa engines` | un worktree + `npm ci` par commit |
| re-simulation | `uv run ofa resim --evict` | ~150 s CPU/partie ; `--evict` obligatoire (51 Go) |
| suivi | `uv run ofa resim --watch` | dans un autre shell |

**Un seul `ofa` qui appelle l'API à la fois** : le limiteur de débit vit dans le processus, deux
commandes en parallèle doublent le débit sans aucune alerte. Arrête la collecte avant un
`fetch-full`.
