# Avancement

Spec : [docs/SPEC.md](SPEC.md). Décisions : [docs/adr/](adr/). Perf : [docs/PERF.md](PERF.md).

| Jalon | État | Date |
| --- | --- | --- |
| J0 Reconnaissance et mesures | **en cours** — bloqué sur 3 informations | 2026-11-21 |
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

Livrable : [docs/RECON.md](RECON.md). Aucun code de production ; POC jetable dans `scratch/resim_poc/`.

### Fait

- `docs/SPEC.md` : copie bit-à-bit de `PROMPT.md` (`cmp` silencieux, 195 lignes).
- Clone de référence d'OpenFront dans `data/openfront/` (2,1 Go), **SHA épinglé
  `1636e2049c3aee43384ac72521a142ef85314aba`**. Jamais modifié.
- Exploration déléguée à 3 subagents (code lu + 12 requêtes réelles à 1 req/s) : inventaire des
  **25 intents**, des **24 `GameUpdateType`**, des getters de `Player`, des schémas de record et de
  stats, des endpoints publics. Les deux inventaires clés ont été recontrôlés à la main au SHA épinglé.
- **7 des 8 faits** de « Ce qu'on sait déjà » tranchés (détail et preuves dans RECON.md), dont deux
  corrections : `createGameRunner` est dans `GameRunner.ts`, et le viewer de replay **n'accepte
  aucun tick de départ**.
- Chaîne de re-sim prouvée mécaniquement : worktree sparse + `npm ci` + `node --import tsx` rejoue
  1 500 ticks d'un vrai record en 3,4 s.
- Déterminisme par commit démontré **par l'expérience** : le même record rejoué au mauvais commit
  diverge dès le tour 210 (21 hashs corrects sur 150).

### Mesuré

| Mesure | Valeur |
| --- | --- |
| Machine | 20 cœurs, 7 Go RAM, 927 Go libres |
| Node conforme à `engines` | `/usr/bin/node` v24.22.3 (nvm expose un v22 non conforme) |
| Record complet / sans `turns` | 804 156 o / 48 314 o (×16,6) |
| Latence record / liste | 0,41 s / 1,52 s |
| Parties publiques Team, 18/11 (1 jour) | 2 741 (via `Content-Range`) |
| Commits OpenFront par an / versions de lockfile / versions de maps | 2 611 / 82 / 223 |
| `npm ci` | 15,4 s, 362 Mo |
| Re-sim | ≈ 440 ticks/s, pic RSS 243 Mo, démarrage 1,4 s |

### Décisions prises (ADR)

- **ADR-0001** : cache moteur = worktree sparse par sha + `node_modules` par hash de lockfile +
  maps par tree-sha. **Pas de bundle** : mesuré inutile (1,4 s de démarrage, < 5 %).
  C'est un écart assumé avec la lettre de la spec (« moteur compilé »).
- **ADR-0002** : le runner part de `tests/replay/ReplayGame.ts`, qui existe déjà et compare les hashs.
- `NODE_BIN=/usr/bin/node` partout dans la re-sim.

### Limites connues à ce stade

- `afterId` documenté mais **jamais exécuté en live**.
- Le critère d'acceptation du J0 (3 vraies parties rejouées, diff vide) **n'est pas atteint** : il
  faut de vrais records, donc l'API.
- Les 12 requêtes de mesure ne sont pas encore loggées en JSONL.
- Un `gitCommit` de record peut être **irrécupérable** depuis GitHub (`upload-pack: not our ref`
  constaté sur le record de démo) : part de records concernés inconnue.
- Projection de volume appuyée sur un seul chiffre réel (2 741 parties Team sur une journée).

### Bloqué sur (toi)

1. Patch des domaines autorisés dans `.claude/settings.json` (fichier en deny pour moi) :
   `raw.githubusercontent.com`, `api.github.com`, `objects.githubusercontent.com`, `openfront.io`.
2. L'**alias e-mail** du projet, pour le User-Agent de toute requête à l'API.
3. Mon **publicID**.
4. Les **specs du serveur Debian** (cœurs / RAM / disque), pour dimensionner la re-sim.
