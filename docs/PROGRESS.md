# Avancement

Spec : [docs/SPEC.md](SPEC.md). Décisions : [docs/adr/](adr/). Mesures de perf : [docs/PERF.md](PERF.md).

| Jalon | État | Date |
| --- | --- | --- |
| J0 Reconnaissance et mesures | en cours | 2026-11-21 |
| J1 Ingestion | à faire | |
| J2 Normalisation | à faire | |
| J3 Re-simulation | à faire | |
| J4 Niveau | à faire | |
| J5 Moteur d'analyse | à faire | |
| J6 Site web | à faire | |
| J7 Mes parties | à faire | |
| J8 Couche IA | à faire | |
| J9 Production | à faire | |

## J0 — Reconnaissance et mesures (en cours)

Livrable : `docs/RECON.md`. Aucun code de production ; scripts jetables dans `scratch/`.

### Fait

- `docs/SPEC.md` : copie bit-à-bit de `PROMPT.md` (`cmp` silencieux, 195 lignes).
- Clone de référence d'OpenFront dans `data/openfront/` (lecture seule).
- Exploration du repo et de l'API par 3 subagents : inventaire des intents, des `GameUpdateType`,
  des getters de `Player`, des schémas de record et de stats, des endpoints publics.
  12 requêtes réelles à 1 req/s pour les premières mesures.

### Environnement mesuré

| Mesure | Valeur | Commande |
| --- | --- | --- |
| Cœurs / RAM / disque libre | 20 / 7 Go / 927 Go | `nproc`, `free -g`, `df -h /home/zoxos` |
| Node conforme à `engines` | `/usr/bin/node` v24.22.3, npm 12.2.3 | `/usr/bin/node --version` |
| Node par défaut du PATH (non conforme) | nvm v22.22.3 | `which -a node` |

### Décisions prises

- `NODE_BIN=/usr/bin/node` explicite pour toute la re-sim : nvm masque le Node 24 exigé par
  `engines` + `engine-strict=true`.
- Les worktrees de moteur vont dans `data/engines/<sha>/` ; `data/openfront/` n'est jamais modifié.

### Limites connues à ce stade

- `afterId` n'a pas encore été exercé en live (seulement documenté).
- Le re-jeu depuis Node n'a pas encore été prouvé sur une vraie partie.
- La projection de volume repose sur un seul chiffre réel (2 741 parties publiques Team le 18/11).

### Bloqué sur (toi)

- Patch des domaines autorisés dans `.claude/settings.json` (fichier en deny pour moi).
- publicID, alias e-mail pour le User-Agent, specs du serveur Debian.
