# Mission

Construire « ofa » (OpenFront Analyzer) : un outil qui collecte le maximum de parties publiques d'OpenFront (openfront.io, repo github.com/openfrontio/OpenFrontIO), les rejoue pour extraire leur déroulé détaillé, et en tire des tactiques concrètes, chacune prouvée par des chiffres et des moments vérifiables de vraies parties.
Deux usages :
1. Comprendre ce que font les gagnants ET les perdants dans toute la population, par map, mode et niveau.
2. Analyser mes propres parties en profondeur pour m'aider à progresser.
Les résultats se consultent dans un site web. Une conclusion fausse est pire que pas de conclusion : la fiabilité passe avant le nombre de tactiques.

# Comment travailler avec moi

- Je suis à l'aise en Python, C et Linux, débutant en TypeScript. Le TS est limité au module de re-simulation et doit être commenté pour que je puisse le lire.
- J'aime la composition et je déteste l'overengineering : des fonctions simples, aucune abstraction sans un second usage réel, pas de framework quand une bibliothèque suffit.
- Réponds-moi en français.

# Ce qu'on sait déjà (vérifié le 21/11/2026, à re-vérifier au J0)

1. API publique documentée dans docs/API.md du repo OpenFront :
   - `GET https://api.openfront.io/public/games` : fenêtre de 2 jours max, `limit` ≤ 1000, parcours de toute l'archive avec `afterId`, en relisant une marge sous le curseur (une partie archivée en retard peut recevoir un id plus bas). Cette liste ne donne pas la map.
   - `GET /public/game/:id` : le record complet (`?turns=false` sans le journal d'actions).
   - Aucune limite de débit n'est documentée.
2. Record : `GameRecordSchema` dans packages/shared/src/WireSchemas.ts. Il contient `gitCommit`, `info.config` (gameMap, gameMode, playerTeams, rankedType, difficulty, bots, nations…), `info.players[]` (clientID, username, clanTag, publicID, team, stats), winner, duration, num_turns, `statsAgreed`, et `turns[]` = journal des intents (union `Intent` dans packages/engine-api/src/Schemas.ts). publicID est null sur les vieilles parties et vaut "REDACTED" si le compte a été supprimé.
3. Stats de fin de partie : packages/engine-api/src/StatsSchemas.ts. Ce sont des tableaux indexés par les constantes `*_INDEX_*`, avec des bigint sérialisés en chaînes décimales.
4. 1 turn = 1 tick = 100 ms (une vraie partie : 4202 turns pour 420 s).
5. Le moteur TS n'est déterministe QUE sur le build qui a joué la partie (docs/Replays.md). packages/engine/src/WinnerReplay.ts rejoue un record en Node sans navigateur : `createGameRunner(gameStart, undefined, mapFiles, callback)` puis `addTurn` / `executeNextTick`. src/server/WinnerReplayChild.ts montre comment charger la map (`loadMapFiles` + un `GameMapLoader` qui lit resources/maps/<map>/). Le commit de prod dd85bcfb8b20e599fae15298bf56db1e92947f61 se récupère avec `git fetch --depth 1 origin <sha>`. Au 21/11/2026, le repo exige Node >=24.15 <25 et npm >=12.1 <13 (engine-strict=true dans .npmrc) ; ces versions peuvent varier selon le commit.
6. Obligation : purger chaque jour, dans toutes nos données, les joueurs listés par `/public/players/recently-deleted`.
7. `api.openfront.io/game/<id>` (sans /public) bloque les scripts (Cloudflare). Utiliser /public/.
8. Le replay s'ouvre via `https://openfront.io/#replay-viewer=<gameID>`. Vérifier s'il accepte un tick de départ.

# Périmètre

- Modes : parties publiques FFA et Team (toutes tailles d'équipe), ranked ffa et 2v2 inclus. Exclus : 1v1, solo, privées.
- Maps : toutes. Au moins 40 analysées séparément si l'échantillon le permet ; les autres regroupées, avec le seuil affiché.
- Niveau, deux dimensions :
  (a) Niveau du joueur : un rating multi-joueurs (OpenSkill par défaut ; justifie si tu changes) calculé dans l'ordre chronologique. On n'utilise que le rating d'AVANT la partie, pour qu'il ne contienne pas son résultat. On y ajoute l'elo ranked quand l'API le donne.
  (b) Difficulté des nations (`config.difficulty`), comme variable de contexte.
- Volume : le maximum utile. Toute l'archive des modes ciblés est collectée. La re-simulation se fait par priorité : mes parties, puis les maps sous-représentées, puis les plus récentes. Le volume cible est décidé au J0 à partir de mesures, pas deviné.
- Mes parties : publicID `<MON_PUBLIC_ID>`. À défaut, le retrouver via `/public/player/<pseudo.1234>`.

# Contraintes non négociables

- Respect de l'API :
  - un seul client HTTP partagé, avec un débit limité (par défaut 1 requête/s et 2 en parallèle, configurable) ;
  - sur 429 ou 5xx, attendre de plus en plus longtemps avant de réessayer, en respectant `Retry-After` ;
  - un User-Agent qui identifie le projet et donne un contact ;
  - si trop d'erreurs s'enchaînent, mettre la collecte en pause.
  Une partie finie ne change plus : elle n'est jamais re-téléchargée. Ne cherche jamais la limite de l'API en la bombardant de requêtes.
- Données brutes intouchables : `data/raw/<préfixe>/<gameID>.json.zst`, exactement tel que reçu. Tout le reste en est dérivé et doit pouvoir être reconstruit.
- La performance est une exigence. Mesure avant d'optimiser et consigne les chiffres dans docs/PERF.md. Concrètement :
  - re-sim en parallèle sur tous les cœurs (plusieurs processus Node) ;
  - moteur compilé une seule fois par commit, puis mis en cache ;
  - sorties en Parquet (compression zstd) ;
  - analyse en DuckDB/Polars, jamais de boucle Python ligne à ligne sur les gros volumes.
- Les fichiers Parquet sont la source de vérité. Le site les lit en lecture seule, pour ne jamais se battre avec le pipeline pour le verrou d'une base DuckDB.
- Stack : Python 3.12, uv, ruff, pytest, DuckDB, Polars. TypeScript/Node uniquement dans resim/ (version de Node exigée par OpenFront au commit concerné). Site : FastAPI + Jinja2 + HTMX + Observable Plot, sans SPA. IA : API Claude, clé dans .env.
- Le même code tourne en local (WSL2 Ubuntu) et sur un serveur Debian 12 toujours allumé (nginx déjà en place). Configuration par variables d'environnement (.env.example), tâches planifiées par des timers systemd.
- Données personnelles : on identifie les joueurs par publicID, on ne stocke jamais persistentID, on purge les comptes supprimés.

# Méthode de travail

1. Explore avant de décider. À chaque jalon, commence en plan mode : lis le code OpenFront concerné (clone de référence dans data/openfront/, jamais modifié), lis la doc, fais de vraies requêtes. Délègue l'exploration à des subagents pour garder ton contexte propre. Chaque hypothèse est marquée VÉRIFIÉE (avec le fichier ou la commande qui le prouve) ou NON VÉRIFIÉE.
2. Teste sur de vraies données. Les fixtures sont de vrais records téléchargés par `ofa fixtures` : environ 20 parties couvrant FFA, Team et ranked, petites et grosses, plusieurs maps, dont au moins un compte REDACTED et une partie sans publicID. Pas de mock du moteur. Les données synthétiques servent uniquement à tester la méthode statistique (un effet planté, dont on connaît la taille, doit être retrouvé).
3. Montre des preuves, pas des affirmations. En fin de jalon, donne les commandes lancées, leur sortie, les chiffres, et ce qui reste faux ou incertain.
4. Un jalon à la fois. À la fin de chacun : mets à jour docs/PROGRESS.md (ce qui est fait, chiffres, décisions, limites), commit, puis arrête-toi et attends mon feu vert.
5. Si la réalité contredit cette spec, arrête-toi et dis-le, au lieu de contourner.
6. Chaque décision structurante fait l'objet d'une ADR courte dans docs/adr/ : contexte, options, choix, raison, comment vérifier.
7. Les longs traitements (toute l'archive, re-sim massive) tournent hors de ta session (tmux ou systemd). Toi, tu valides sur de petits lots et tu lis les logs et les métriques.

# Rigueur de l'analyse (obligatoire)

Un résultat qui ne respecte pas ces règles est marqué « exploratoire », ou n'est pas montré.

- **Joueurs récurrents.** Un même joueur apparaît dans beaucoup de parties, donc ses observations ne sont pas indépendantes. Les intervalles de confiance se calculent par rééchantillonnage (bootstrap) en tirant des joueurs entiers, pas des lignes. En Team, le résultat qui compte est celui de l'équipe.
- **Mesure du résultat.** En FFA, la victoire est trop rare (1 gagnant sur N). On utilise aussi le classement final (deathPosition), le temps de survie et la part de territoire (finale et pic). Toujours dire quelle mesure est utilisée.
- **Causalité inverse et survie.** Les gagnants construisent plus parce qu'ils gagnent, pas forcément l'inverse. Donc : mesurer un comportement dans une fenêtre [t0, t1], mesurer le résultat après t1, et comparer à état égal à t1 (territoire, or, troupes, rang) et à niveau égal. Ne comparer que des joueurs encore en vie à t1.
- **Effet du niveau.** Les bons joueurs font beaucoup de choses en même temps. Comparer à rating égal (par tranches ou en ajustant), et privilégier la comparaison d'un joueur avec lui-même (avec/sans le comportement) dès que l'échantillon le permet.
- **Contexte.** Ne jamais mélanger sans le dire : map, mode, taille d'équipe, nombre de joueurs, difficulté des nations, période ou commit (l'équilibrage du jeu change dans le temps).
- **Tests multiples.** Plus on teste d'hypothèses, plus on trouve de faux positifs. Donc :
  - chaque hypothèse est écrite dans findings/prereg/*.yaml AVANT d'être confirmée ;
  - les parties sont coupées dans le temps : un échantillon d'exploration, et un échantillon de confirmation (les plus récentes) qui n'est jamais regardé pendant l'exploration et n'est lu qu'une fois par hypothèse (lecture loggée) ;
  - correction de Benjamini-Hochberg sur l'ensemble des hypothèses testées ensemble.
- **Taille d'effet avant p-valeur.** Exprimer l'effet en unités parlantes (places gagnées au classement, points de % de victoire) avec son intervalle de confiance à 95 %. Seuil par groupe analysé : au moins 300 observations et 100 joueurs distincts (justifie si tu changes). En dessous : « échantillon insuffisant ».
- **Corrélation ≠ causalité.** Chaque tactique porte un niveau de preuve, affiché sur le site :
  - A : confirmée sur l'échantillon de confirmation, par tranche de niveau et par map, et cohérente quand on compare un joueur avec lui-même ;
  - B : confirmée et ajustée sur le niveau et le contexte ;
  - C : simple association descriptive.
- **Robustesse.** Relancer chaque résultat avec une autre définition raisonnable du comportement et une autre fenêtre de temps. S'il change de sens, il est rejeté.
- **Test de la méthode elle-même.** Sur des données synthétiques, un effet planté doit être retrouvé, et un effet qui ne vient que du niveau des joueurs doit être rejeté ou rétrogradé.

# Fiche tactique (sortie de l'analyse)

Un fichier JSON par tactique, validé par un schéma, contenant :
- id ;
- un énoncé actionnable : « sur <map>, en <mode>, au niveau <x>, faire Y avant t » ;
- la population concernée ;
- la définition précise du comportement mesuré ;
- la mesure du résultat ;
- l'effet et son intervalle de confiance ;
- n observations, n joueurs, n parties ;
- le niveau de preuve ;
- les tests de robustesse et leurs résultats ;
- les limites connues ;
- au moins 5 exemples réels (gameID, joueur, tick, mm:ss, lien replay), tirés au hasard avec une graine fixe, jamais triés à la main ;
- des contre-exemples.

# L'IA dans le produit

L'API Claude rédige des explications et propose des hypothèses. Elle ne produit jamais un chiffre.
- Elle reçoit un dossier de preuves (fiches + agrégats) et doit citer les id des fiches.
- Un validateur rejette tout texte qui contient un nombre absent du dossier (à l'arrondi près) ou qui cite un id inexistant.
- Ses hypothèses suivent le même protocole que les autres (écrites à l'avance, confirmées sur l'échantillon réservé) et sont marquées « proposée par l'IA ».
- Les textes sont générés par lots hors ligne et mis en cache, jamais à chaque visite.

# Jalons

J0 — Reconnaissance et mesures. Pas de code produit : scripts jetables dans scratch/.
Livrable : docs/RECON.md, qui contient :
- chaque fait de « Ce qu'on sait », marqué VÉRIFIÉE ou NON VÉRIFIÉE ;
- l'inventaire des intents et de ce que chacun encode ;
- l'inventaire des GameUpdateType et des données par joueur lisibles pendant la simulation (tuiles, or, troupes, unités, alliances, attaques en cours) ;
- les paramètres acceptés par le viewer de replay ;
- les mesures à 1 req/s : latence, taille d'un record avec et sans turns par mode ;
- la répartition sur 7 jours : parties par mode, map et taille, nombre de gitCommit distincts, % statsAgreed, % publicID null ;
- un POC de re-sim sur 3 vraies parties (une FFA, une Team, une grosse partie) rejouées au bon commit ;
- une projection de stockage et de temps pour 1, 6 et 12 mois d'archive, le débit de re-sim par cœur, et le volume cible proposé, calcul à l'appui.
Acceptation : pour ces 3 parties, le gagnant et les stats de chaque joueur recalculées sont identiques au record (diff vide), et chaque chiffre de RECON.md est accompagné de la commande qui le produit.

J1 — Ingestion
Commandes `ofa ingest` (parcours de l'archive + mode incrémental) et `ofa purge-deleted` ; stockage brut ; curseur persistant.
Acceptation :
- 7 jours des modes ciblés ingérés ;
- une relance fait 0 requête pour les parties déjà présentes (compteur dans les logs) ;
- un kill -9 en plein milieu, puis une reprise, ne laisse ni trou ni doublon (test automatisé) ;
- test de purge sur fixture ;
- le débit configuré n'est jamais dépassé (vérifié dans les logs).

J2 — Normalisation, sans re-sim
Tables Parquet : games ; participants (stats aplaties en colonnes nommées) ; intents (une ligne par action : tick, joueur, type, cible, paramètres décodés).
Acceptation :
- docs/DATA.md documente les schémas ;
- tests de cohérence sur les fixtures : nombre d'intents par type identique au brut, tout joueur référencé existe, stats bigint sans perte ;
- je vérifie dans le viewer 2 moments d'une partie à partir de ce que tes tables indiquent.

J3 — Re-simulation à grande échelle
Dans resim/ :
- compilation du moteur pour chaque commit (git worktree + bundle mis en cache dans data/engines/<sha>/) ;
- un runner Node qui sort, toutes les 10 ticks, l'état de chaque joueur (tuiles, or, troupes, unités par type, alliances) ;
- et, au tick exact, les événements : conquêtes, éliminations, constructions et destructions, bombes, alliances formées ou rompues, résultats d'attaque si le moteur les expose ;
- plusieurs workers en parallèle, mémoire bornée, timeouts, erreurs classées par cause.
Acceptation :
- sur 500 parties statsAgreed, au moins 99 % rejouées avec des stats finales identiques au record ;
- les échecs sont listés par cause ;
- débit (parties/h/cœur) et pic mémoire consignés dans docs/PERF.md.

J4 — Niveau
Rating chronologique sur toutes les parties collectées.
Acceptation :
- sur l'échantillon de confirmation, le rating d'avant-partie prédit le classement mieux qu'une référence naïve (corrélation de rang et log-loss rapportées) ;
- les joueurs sans publicID sont traités explicitement.

J4b — Le nombre de workers (fait, et refermé : son prémisse ne tenait pas)
Motivation d'origine : 848 h de CPU projetées pour 20 000 parties. Mesuré depuis : c'est **4,4
jours de tâche de fond** sur les 8 cœurs du serveur, qui n'a rien d'autre à faire et devant lequel
personne n'attend. Le ×3 que ce jalon s'était fixé n'était rattaché à aucun besoin, seulement à un
chiffre qui fait peur écrit en heures de CPU.
Ce qui a été fait, la seule piste qui coûtait zéro ligne de code : **le nombre de workers**, le
même échantillon fixe de 16 parties rejoué à 1, 4, 6, 8, 12, 16 et 20 workers (docs/PERF.md § J4b).
Résultat : la saturation annoncée au J3 **n'existe pas** — 12 workers battent 6, 16 battent 12 —
et le « 1 008 → 615 ticks/s » venait de la géographie seule. `workers_default()` est déjà dans le
plateau sur les deux machines : aucun changement conservé, ce qui est le bon résultat d'une mesure.
Acceptation, atteinte :
- docs/PERF.md § J4b porte les huit passes, la commande qui les produit, et pourquoi l'échantillon
  est choisi à coût mesuré voisin et non à ticks égaux (à 8 500 ticks, le coût réel va de 16 à 241 s) ;
- le déterminisme est intact : 8 rejeux des mêmes 16 parties à des concurrences différentes,
  8 × 16 statuts `ok`, et 307 788 lignes de `player_ticks` identiques à celles du J3 ;
- la projection tient dans un budget écrit : 848 h de CPU = ~4,4 jours sur le serveur.
Abandonnées faute de besoin, et non faute de temps — zstd-19, l'aller-retour NDJSON, le relevé
d'état, le plancher mémoire par processus, le démarrage de Node : chacune est des jours de travail
pour raccourcir un calcul que personne ne regarde. À ne reprendre que si une mesure montre qu'elles
bloquent quelque chose de réel. La règle du jalon, elle, reste pour tout le projet : **aucune
modification conservée sans sa mesure avant/après sur le même échantillon fixe**.

J4c — Accélérer la re-simulation (le jalon en cours)
Motivation, chiffrée : les 48 100 records du périmètre sont collectés, mais les rejouer coûte
**153 s de CPU par partie** (65 ticks/s/cœur), soit **4 500 parties/jour** sur les 8 cœurs du
serveur et **10 jours** pour tout le périmètre. Les 10 000 parties dont le J5 a besoin tombent
mercredi après-midi. C'est trop long, et ça le restera à chaque nouvelle campagne : le flux
d'arrivée du périmètre est de ~6 900 parties/jour, donc le serveur ne tient déjà pas le rythme.
Objectif : **×3 au moins**, mesuré, sans rien changer aux données produites.

Non négociables (une seule violation invalide le jalon) :
- **Déterminisme** : le même échantillon fixe re-simulé avant et après la modification produit des
  tables IDENTIQUES — nombre de lignes et sommes de contrôle par colonne — et les stats finales
  concordent toujours avec le record (ADR-0003).
- **Mêmes données** : les quatre tables (`player_ticks`, `events`, `players`, `borders`) gardent
  leurs colonnes et leur sémantique. Un échantillonnage plus grossier n'est pas « mêmes données » :
  s'il est proposé, il l'est explicitement, avec ce que l'analyse y perd.
- **Aucune modification conservée sans sa mesure avant/après sur le même échantillon fixe**
  (la règle héritée du J4b). L'échantillon est celui de `docs/perf-sample.txt`.
- Le moteur OpenFront **peut** être modifié — l'utilisateur l'autorise explicitement, ce qui lève
  la règle de `.claude/rules/resim.md` — mais alors : un patch versionné, appliqué à la création du
  worktree donc reproductible par commit, un ADR, et la preuve de déterminisme ci-dessus.

Première étape, obligatoire avant toute optimisation : **un profil CPU** (`node --cpu-prof`) sur
2 ou 3 vraies parties de coûts différents, publié dans docs/PERF.md, qui répartit les 153 s entre :
la boucle du moteur, notre instrumentation par tick, la géographie, l'écriture Parquet, et le
démarrage du process. Sans ce profil, toutes les pistes ci-dessous sont des paris.

Pistes connues, à confirmer ou écarter par la mesure (ordre de gain attendu, pas d'exécution) :
0. **Sans aucun code** : rejouer d'abord les parties utiles au J5 (`--game` sur la fenêtre de
   confirmation), et répartir une campagne sur les deux machines (le portable a 20 cœurs, la
   re-sim est incrémentale et ses Parquet s'ajoutent). Gain immédiat, zéro risque.
1. **La géographie coûte ×2,58** (mesuré à l'ADR-0011) et produit 46 M de lignes pour 513 parties.
   Le J5 ne lit `borders` qu'**au tick t0 de sa fenêtre**. Échantillonner les frontières tous les
   100 ticks au lieu de 10, ou ne garder que les 5 agrégats de `player_ticks`, se discute donc avec
   un vrai usage en face. C'est la piste la plus grosse, et c'est une question de spec avant d'être
   une question de code.
2. **Le démarrage du process** : `node --import tsx` transpile le runner ET le moteur à chaque
   partie. Un bundle (esbuild) par commit, mis en cache comme le worktree, supprime ce coût fixe.
   Écartée au J4b faute de besoin ; le besoin existe maintenant.
3. **Notre instrumentation** : le runner diffe les objets vivants à **chaque** tick pour détecter
   éliminations et attaques, alors que l'état n'est échantillonné que tous les 10. À profiler.
4. **La boucle du moteur elle-même** : ce que le profil désignera. Les tours de jeu non pertinents
   (trains, obus) sont déjà exclus de `events`, pas forcément de la simulation.
5. **Node** : version, flags de GC, taille du semi-space. Gratuit à tester, à ne garder que mesuré.

Acceptation :
- docs/PERF.md § J4c porte le profil CPU, puis chaque piste avec sa mesure avant/après sur
  l'échantillon fixe, et les pistes écartées avec la raison ;
- **×3 au moins** sur le mur d'une passe complète de l'échantillon fixe, ou l'explication écrite de
  pourquoi c'est impossible ;
- le déterminisme est prouvé : ~100 parties déjà valides rejouées après modification rendent des
  tables identiques (lignes et sommes de contrôle), et leurs stats concordent toujours ;
- docs/PROGRESS.md à jour, un ADR par décision structurante, commit, stop.

J5 — Moteur d'analyse
Indicateurs par phase de partie (ouverture, milieu…), protocole « Rigueur », génération des fiches. Chaque fiche passe par la skill /tactic et l'agent stats-reviewer.
Acceptation :
- les tests de la méthode passent (effet planté retrouvé, effet dû au niveau rejeté) ;
- au moins 10 fiches réelles, toutes valides au schéma, relues par stats-reviewer sans objection bloquante ;
- le log montre que l'échantillon de confirmation n'a été lu qu'une fois par hypothèse.

J6 — Site web
Pages :
- vue d'ensemble, filtrable par mode, map et niveau ;
- fiches tactiques avec leurs preuves et des liens vers les replays ;
- page partie : courbes territoire/or/troupes par joueur et journal d'événements ;
- page joueur.
Acceptation :
- chaque chiffre affiché remonte à une fiche ou à une requête (lien « d'où vient ce chiffre ») ;
- tests de l'API ;
- 95 % des réponses en moins de 300 ms sur le jeu de données complet, en local ;
- je navigue et je valide.

J7 — Mes parties en profondeur
`ofa me` + une page dédiée : mes courbes comparées à celles des gagnants de même niveau, sur la même map, aux mêmes instants ; mes principaux écarts ; 3 à 5 recommandations, chacune liée à une fiche tactique ET à des moments précis de mes parties.
Acceptation : rapport sur mes N dernières parties réelles, et chaque recommandation a ses deux liens de preuve.

J8 — Couche IA
Résumés et explications générés hors ligne, et le validateur.
Acceptation : un nombre inventé injecté dans un texte est rejeté (test) ; 100 % des textes publiés passent le validateur ; le coût de chaque lot est loggé.

J9 — Production
Timers systemd (ingestion, purge quotidienne, re-sim, analyse, IA), service web derrière nginx, logs, page santé (retard de la collecte, taux d'erreurs, dernière exécution de chaque tâche).
Acceptation : 48 h sans intervention, la purge a tourné chaque jour, la page santé est au vert.

# Pour commencer

1. Enregistre ce prompt tel quel dans docs/SPEC.md et crée docs/PROGRESS.md.
2. Clone OpenFront dans data/openfront/.
3. Lance le J0 en plan mode. Avant d'écrire du code, présente-moi ton plan et tes questions bloquantes.
