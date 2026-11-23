# ofa — OpenFront Analyzer

Spec : docs/SPEC.md (lis seulement la section du jalon en cours). Avancement : docs/PROGRESS.md. Décisions : docs/adr/.

## Commandes
- `uv sync` : dépendances
- `uv run pytest -q -m "not slow and not network"` : tests rapides ; `-m slow` = re-sim de vraies parties
- `uv run ruff check . && uv run ruff format .`
- `uv run ofa --help` : CLI (fixtures, ingest, purge-deleted, normalize, fetch-full, rate-check,
  engines, resim, rating, analyze, me, serve)
- type-check du runner : il n'a de sens que DANS un worktree (ses imports `@openfront/*` y
  resolvent). `resim/` ne porte donc pas de package.json :
  `cd data/engines/<sha> && ./node_modules/.bin/tsc --noEmit -p tsconfig.json`, et on ne lit que
  les lignes qui mentionnent `ofa_runner` (les 17 autres viennent du sparse-checkout).
- `uv run ofa resim --report` : le tableau d'acceptation du J3, par cause
- profil CPU d'une re-sim : `OFA_PROFILE=<répertoire> uv run ofa resim --workers 1 --game <id>` —
  pose un `.cpuprofile` (Chrome DevTools) et un `.profile.json` par partie (temps par classe
  d'Execution, temps propre par fonction, et nos trois compteurs). C'est l'instrument du moteur
  lui-même (`tests/perf/fullgame/Profiler.ts`), chargé dynamiquement, absent en production.
- `uv run ofa resim --watch` : barre de progression, dans un AUTRE shell pendant que la
  re-sim tourne (elle relit la table `resim`, en WAL ; rien n'est instrumenté dans la re-sim)
- `uv run ofa analyze screen --phase ouverture|milieu|fin [--population "<SQL>"] [--grappe game_id]` :
  le criblage, sur l'exploration SEULE. Sortie dans `findings/screen-<phase>-<population>.json`
- `uv run ofa analyze explore|confirm|card <id>` : une hypothèse pré-inscrite, puis sa fiche
- `uv run ofa analyze report` : le journal des lectures de confirmation
- `duckdb -c "select count(*) from 'data/parquet/games/**/*.parquet'"` : requêtes ponctuelles

## Architecture (ne change pas sans ADR)
- data/raw/ ne se modifie jamais (records zstd tels que reçus) ; tout le reste se reconstruit à partir de lui.
- Parquet = source de vérité ; le site le lit en lecture seule. data/parquet/ (J2) se
  reconstruit en entier ; data/resim/ (J3) est incrémental et ne doit JAMAIS vivre sous
  data/parquet/, que `normalize` échange en bloc ; data/rating/ (J4) non plus, même raison, mais
  lui se reconstruit en entier (14 s).
- Tout appel à l'API OpenFront passe par ofa/client.py (débit limité). Aucun autre chemin.
- TypeScript uniquement dans resim/. data/openfront/ = clone de référence, lecture seule.
- Deux machines (ADR-0009) : le SERVEUR porte tout (collecte continue, normalisation, re-sim,
  site) ; ce PORTABLE est une machine de développement. Ce qui fait tenir les 100 Go du serveur,
  c'est `ofa resim --evict` (51 Go : les worktrees ET les caches node_modules, 362 Mo par version
  de lockfile). Circulent : raw/full + parquet/games vers ici, le code par git vers là-bas. Les
  Parquet se reconstruisent en 2 h 45, ils ne se transportent pas.
- `df` sous WSL2 annonce la taille PROVISIONNÉE du disque virtuel (1007 Go) : le vrai espace libre
  est celui du disque Windows, `df -h /mnt/c` (118 Go). Ne jamais dimensionner sur le premier.

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
- On passe toujours par /public/ : c'est le chemin documenté et stable. (Le blocage Cloudflare de /game/<id> sans /public est RÉFUTÉ : HTTP 200 mesuré au J0, RECON.md fait 7.)
- Environnement : WSL2 Ubuntu, projet dans le système de fichiers Linux (~/ofa), jamais sous /mnt/c (lent).
- Sandbox : data/raw/ est en lecture seule (voulu, J1 : la collecte tourne hors session) et `git worktree add`/`npm ci` échouent sur .git. Ces commandes, c'est à l'utilisateur de les lancer : demande-les-lui avec `! <commande>`.
- Le hook rtk résume la sortie de pytest et affiche « No tests collected » même quand tout passe : pour la vraie sortie, `rtk proxy "uv run pytest -q -m 'not slow and not network'"`. Il tronque aussi à 50 lignes, donc tout comptage vient d'un `count`, jamais de `| wc -l`.
- multiprocessing : Python 3.14 démarre en forkserver, dont le socket est refusé par le sandbox. Passe `mp_context=multiprocessing.get_context("fork")`.
- Le CLI `tsx` plante dans le sandbox (listen EPERM sur son pipe IPC) : `node --import tsx`.
- Le limiteur de débit vit dans le processus : deux `ofa` qui appellent l'API en parallèle font
  deux fois le débit configuré, sans alerte. Un seul à la fois.
- Analyse (J5) : les fenêtres sont en **multiples de 10 ticks**, parce que l'état n'est
  échantillonné que là. L'ouverture part donc de **210** et non de 202 (`spawn_phase_end`) : une
  fenêtre à 202 ne joint RIEN, et une garde refuse maintenant un t0 ou t1 hors grille.
- Analyse : toute fenêtre SQL qui classe (`ntile`, `row_number`, `rank`) doit **départager ses ex
  æquo explicitement**, sinon l'ordre de lecture des Parquet change le résultat d'une exécution à
  l'autre (vécu : −0,05504 / −0,05298 / −0,05285 pour la même commande). Et le bootstrap tire par
  `hash(...)`, jamais par `random()`, qui ne consomme pas sa graine dans l'ordre sous parallélisme.
- Analyse : `confirm` n'ouvre l'échantillon réservé qu'**une fois par hypothèse**, et ce n'est pas
  contournable depuis la CLI. `--force` ne lève que le garde de puissance (effet sous le MDE).
  Pour re-tester, il faut un nouvel id.
- `fetch-full` plafonne à **1,4 req/s** quel que soit `OFA_RATE` : sa boucle est séquentielle, son
  débit est l'inverse de la latence. Ne jamais estimer sa durée en divisant par le débit configuré.
- `workers_default()` rend `min(cœurs, MemAvailable en Go)` — **MemAvailable au moment du
  lancement**. Sur le serveur, 8 workers en vol consomment ~6,4 Go et laissent 5 Go
  disponibles : relancer `resim` à ce moment-là donnerait **5 workers au lieu de 8** (−37 % de
  débit), sans un mot d'alerte. Pour une campagne, passer `--workers 8` explicitement.
- Le coût CPU d'une partie et son coût **sous concurrence** sont deux nombres différents : 42-50 s
  à un worker, 120-153 s à 6-8 workers (J4c). Un `wall_s` du journal n'est donc PAS un coût CPU, et
  la re-sim est limitée par la **mémoire** (le débit par cœur tombe de 200 à 120 ticks/s de 1 à
  14 workers). Pour mesurer un changement de code, lire le **CPU total** d'une passe du banc, pas
  son mur : avec 16 parties sur 14 workers, le mur est fixé par la queue de fin de passe.
- `ofa rate-check` lit tout le journal, sans fenêtre : pour vérifier une seule exécution, filtrer les lignes à la main (les requêtes de `fetch-full` sont les `/game/<id>` SANS `?turns=false`).
