# ofa — OpenFront Analyzer

Spec : docs/SPEC.md (lis seulement la section du jalon en cours). Avancement : docs/PROGRESS.md. Décisions : docs/adr/.

## Commandes
- `uv sync` : dépendances
- `uv run pytest -q -m "not slow and not network"` : tests rapides ; `-m slow` = re-sim de vraies parties
- `uv run ruff check . && uv run ruff format .`
- `uv run ofa --help` : CLI (fixtures, ingest, purge-deleted, normalize, resim, rate, analyze, me, serve)
- `cd resim && npm ci && npx tsc --noEmit`
- `duckdb -c "select count(*) from 'data/parquet/games/**/*.parquet'"` : requêtes ponctuelles

## Architecture (ne change pas sans ADR)
- data/raw/ ne se modifie jamais (records zstd tels que reçus) ; tout le reste se reconstruit à partir de lui.
- Parquet = source de vérité ; le site le lit en lecture seule.
- Tout appel à l'API OpenFront passe par ofa/ingest/client.py (débit limité). Aucun autre chemin.
- TypeScript uniquement dans resim/. data/openfront/ = clone de référence, lecture seule.

## Règles
- IMPORTANT : aucune conclusion d'analyse sans la skill /tactic et l'agent stats-reviewer.
- Tests sur de vrais records (tests/fixtures/, régénérés par `uv run ofa fixtures`). Pas de mock du moteur.
- Fonctions simples, par composition ; aucune abstraction sans second usage réel.
- Fin de jalon : PROGRESS.md à jour, commit, stop.
- Réponds en français.
- En compactant le contexte, garde : le jalon en cours, les fichiers modifiés, les commandes de test.

## Pièges
- Les stats sont des bigint sérialisés en chaînes : les convertir en int Python, jamais en float.
- publicID peut être null ou "REDACTED".
- 1 tick = 100 ms. Le moteur n'est déterministe qu'au gitCommit du record.
- api.openfront.io/game/<id> (sans /public) est bloqué par Cloudflare.
- Environnement : WSL2 Ubuntu, projet dans le système de fichiers Linux (~/ofa), jamais sous /mnt/c (lent).
