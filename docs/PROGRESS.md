# Avancement

Spec : [docs/SPEC.md](SPEC.md). Décisions : [docs/adr/](adr/). Perf : [docs/PERF.md](PERF.md).

| Jalon | État | Date |
| --- | --- | --- |
| J0 Reconnaissance et mesures | **livré** — critère d'acceptation atteint ; 2 arbitrages en attente | 2026-11-21 |
| J1 Ingestion | à faire | |
| J2 Normalisation | à faire | |
| J3 Re-simulation | à faire | |
| J4 Niveau | à faire | |
| J5 Moteur d'analyse | à faire | |
| J6 Site web | à faire | |
| J7 Mes parties | à faire | |
| J8 Couche IA | à faire | |
| J9 Production | à faire | |

## J0 — Reconnaissance et mesures (livré)

Livrable : [docs/RECON.md](RECON.md) (470 lignes). Aucun code de production ; scripts jetables dans
`scratch/`. 380 requêtes API à 1 req/s, **aucun 429 ni 403**, toutes journalisées dans
`scratch/requests.jsonl`.

### Critère d'acceptation : ATTEINT

3 vraies parties rejouées au commit exact du record, **diff vide** (gagnant + toutes les stats
archivées de tous les joueurs) et **hashs 100 % identiques** :

| gameID | map | mode | joueurs | ticks | hashs | débit |
|---|---|---|---|---|---|---|
| `cPaLHdRvC2` | Gateway to the Atlantic | FFA | 25 | 9 701 | 970/970 | 159 t/s |
| `dtQhXS8owK` | Strait of Hormuz | Team | 24 | 5 931 | 593/593 | 253 t/s |
| `cDkWi2N1AT` | Great Lakes | FFA | 95 | 29 342 | 2934/2934 | 172 t/s |

### Les 8 faits de la spec

7 vérifiés, 1 **réfuté**. Trois corrections : `createGameRunner` est dans `GameRunner.ts` ; le viewer
de replay **n'accepte aucun tick de départ** ; `api.openfront.io/game/<id>` sans `/public` **n'est pas
bloqué** par Cloudflare (HTTP 200 depuis un script).

### Découvertes qui changent la suite

- **L'API fournit déjà `ffaRating.before`** par partie publique FFA — exactement le « rating d'avant
  la partie » du J4. Pas d'équivalent en Team. Devient l'étalon de validation du J4.
- **`deathPosition` n'est jamais archivé** (0 sur 993 lignes joueur) : la mesure de résultat principale
  en FFA n'existe que par re-simulation. `killedAt` n'en est qu'un proxy imparfait (Spearman −0,81 / −0,96).
- **Le ranked 1v1 fait 10 % des parties publiques** et doit être filtré explicitement ;
  **`rankedType=ffa` est vide** aujourd'hui.
- **La re-simulation est le facteur limitant**, pas le téléchargement : 14 150 parties/jour sur
  8 cœurs, contre 86 400 téléchargements/jour à 1 req/s.

### Chiffres clés

| Mesure | Valeur |
| --- | --- |
| Parties publiques/jour | 9 320 (6 619 FFA + 2 700 Team + 172 ranked 2v2) |
| Périmètre ofa (hors 1v1) | **8 388/jour** |
| Archive disponible | depuis le **2025-06-04** (535 jours, ~4,5 M parties) |
| Record complet / sans `turns`, zstd | 39,2 Ko / **5,3 Ko** (médiane) |
| Débit de re-sim | 159 à 253 ticks/s/cœur ; 0,8 Go par worker |
| Coût moteur par commit distinct | 48 Mo (3 commits distincts seulement sur 300 parties de 7 jours) |
| `statsAgreed = true` | 99,0 % |
| `publicID` null / REDACTED (7 j récents) | 0 % / 0,03 % — mais **100 % null** en juin 2025 |
| Maps distinctes | 103 sur 300 parties (longue traîne : 83 maps = 49 %) |

### Décisions prises (ADR)

- **ADR-0001** : cache moteur = worktree sparse (37 Mo) + `node_modules` par hash de lockfile copié en
  **liens durs** + maps par tree-sha. **Pas de bundle** (mesuré inutile).
- **ADR-0002** : le runner part de `tests/replay/ReplayGame.ts`, qui compare déjà les hashs.
- **ADR-0003** : définition du critère de validité d'une re-sim, avec les trois champs jamais archivés.

### En attente de ton arbitrage

1. **Volume cible** (RECON § 10.3) : l'archive complète en records complets fait 192 Go, le SSD en a
   100. Proposition : ingestion en deux étages (tout en `?turns=false` = 22,7 Go, records complets
   seulement pour les parties re-simulées) + re-sim sur ~100 k parties (46 Go, 7 jours sur 8 cœurs).
2. **Périmètre** : inclut-on `publicGameType: "special"` (9,7 %) et
   `playerTeams: "Humans Vs Nations"` (8,0 %) ?

### À régler au J1

- `data/raw/` n'est pas inscriptible (règle `Edit(./data/raw/**)` + sandbox). Les 27 records du J0
  sont dans `scratch/records/`.
- Les fixtures sont déjà pré-choisies et téléchargées : 27 records couvrant FFA, Team, ranked 1v1 et
  2v2, Humans Vs Nations, `special`, 2 joueurs à 125 joueurs, `statsAgreed=false`, un compte
  **REDACTED**, et 3 parties de juin 2025 **sans publicID**.
