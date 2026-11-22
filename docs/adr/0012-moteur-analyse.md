# ADR-0012 — Le moteur d'analyse : cribler, puis confirmer

- Date : 2026-11-22
- Statut : accepté (J5), après une revue de design qui a trouvé un trou et quatre pièges

## Contexte

Le J5 doit produire des fiches tactiques vraies. Les données disponibles pour un comportement dans
une fenêtre de temps sont les **513 parties re-simulées** (19 038 séries joueur×partie), pas les
48 100 du périmètre : c'est `ofa fetch-full` qui décide de ce volume (ADR-0004). La campagne en
cours porte l'échantillon vers ~4 500 parties.

Mesuré avant d'écrire une ligne, sur les 513 :

| fait | conséquence |
| --- | --- |
| `spawn_phase_end` vaut **202** sur les 513 parties | un repère constant : des fenêtres en ticks absolus sont comparables d'une partie à l'autre |
| durée des parties : 4 922 → 17 218 ticks (D1/D9) | la durée **dépend du résultat** ; une phase définie en % de durée ferait fuir le futur |
| avant le tick 1800 : **97,8 %** ont attaqué un humain, **98,7 %** la terre neutre | « attaquer tôt » n'est pas un comportement discriminant : c'est le jeu |
| avant 1800 : **76,3 %** ont bâti une ville, **50,3 %** ont demandé une alliance | ces deux-là coupent la population ; l'alliance est le contraste idéal |
| au tick 1800 (D1→D9) : attaques 12→48, tuiles 8 727→49 187, troupes/tuile 11→59, voisins 2→8 | la **dose** varie d'un facteur 4 à 6 : c'est là que vivent les tactiques, pas dans des booléens |
| 20 465 humains : 14 240 avec `death_position`, 5 701 vivants à la fin | les quatre mesures de résultat de la SPEC sont disponibles |

## Le trou : un protocole pré-inscrit ne découvre rien

La SPEC impose la pré-inscription avant toute mesure de confirmation. C'est une machine à **juger**,
pas à trouver : si les hypothèses écrites à la main sont tièdes, le jalon livre douze fiches tièdes
et aucune tactique.

**Choix : un étage de criblage, `ofa analyze screen`.** Une passe large (N comportements candidats ×
4 mesures de résultat) sur l'**échantillon d'exploration seul**, avec le même estimateur, classée par
taille d'effet. Elle ne produit aucune fiche, aucune p-valeur publiable, et n'ouvre **jamais**
l'échantillon de confirmation. Son unique produit : la liste des hypothèses qui méritent une
pré-inscription. C'est l'étage qui fait émerger ; les suivants ne font que valider.

## Les quatre pièges, et le choix pour chacun

1. **Une phase en fraction de durée fait fuir le résultat dans la définition du comportement.**
   Qui gagne vite joue court. → **Fenêtres en ticks absolus**, ancrées sur le 202 mesuré :
   ouverture 202–1800, milieu 1800–6000, fin 6000+. Aucune fenêtre relative à la durée, ni à la
   mort du joueur.
2. **Le sur-contrôle annule les vraies tactiques.** « À état égal à t1 » est juste pour un **choix à
   position égale**, et faux pour une **dose** : si le comportement est « s'étendre vite dans
   [t0,t1] », les tuiles à t1 *sont* son effet, et stratifier dessus le ramène à zéro
   (conditionnement sur le médiateur). → La pré-inscription **déclare son point de contrôle** :
   `t0` pour une dose, `t1` pour un choix. Le criblage sort les deux versions côte à côte.
3. **Sans le voisinage, une fiche décrit un quartier, pas un joueur.** 2 à 8 voisins entre D1 et D9.
   → Contrôles de contexte obligatoires, pris à t0 : `neighbours`, `border_front`, **frontière avec
   TerraNullius** (la terre libre, seule la table `borders` à `b = 0` la donne), force des voisins
   humains, `spawn_shore`, map, taille de partie.
4. **L'équilibrage bouge dans l'échantillon** (6 `git_commit` en 7 jours). → `git_commit` dans les
   strates, plage de commits écrite sur la fiche.

Et deux gardes :

- **la lecture unique de confirmation ne se gaspille pas** : `explore` rapporte l'**effet minimal
  détectable** (2,8 × l'écart-type bootstrap, ~80 % de puissance). Sous ce seuil, l'hypothèse est
  classée « sous-puissante » et `confirm` refuse d'ouvrir l'échantillon ;
- **un contrôle négatif permanent, en SIX tirages** : un comportement factice à graine fixe
  traverse tout le pipeline à chaque lot et doit rendre un effet nul. Les tests synthétiques de la
  SPEC disent que la méthode *peut* voir ; le contrôle négatif dit qu'elle ne *fabrique* pas.
  **Six tirages et non un seul**, et c'est une correction imposée par la mesure : la première
  version en tirait un, qui a « trouvé » +4,2 points de victoire d'équipe à p = 0,003. Les cinq
  autres graines rendent zéro. Avec une centaine de grappes, un placebo unique sort de son
  intervalle une fois sur vingt par pur hasard — ce qu'on vérifie est donc la *distribution* des
  placebos, et l'alerte ne sonne qu'au-delà d'un tirage non nul pour une même mesure (P(≥2) = 3 %).

## L'estimateur

**Standardisation directe sur strates** (appariement exact sur des strates grossières), poids ATT :
l'effet est la moyenne, pondérée par les effectifs du bras « comportement présent », des écarts
intra-strate. Seules les strates où les deux bras existent comptent, et le même jeu de strates sert
au bootstrap. **IC à 95 % et p-valeur par bootstrap de grappes sur `public_id`** (percentile), parce
qu'un joueur apparaît dans plusieurs parties et que ses observations ne sont pas indépendantes
(SPEC § Rigueur). En Team, la grappe est la partie : les équipiers partagent le résultat.

**Tout en SQL DuckDB**, bootstrap compris (34 M de lignes de rééchantillonnage pour 2 000 tirages,
quelques secondes), donc **sans numpy ni pandas**. Trois dépendances ajoutées : `duckdb` (le vrai
usage de lecture annoncé au J4), `pyyaml` (les pré-inscriptions), `jsonschema` (les fiches).

**Pas de table de features, pas de nouvelle racine de données** : un seul gabarit SQL, paramétré par
`t0` et `t1`, construit à la demande la table de sujets `(partie, joueur)` — identité, `rating_before`,
état à t0 **et** à t1, agrégats de la fenêtre, contexte de voisinage, et les quatre résultats. Rien
à invalider, rien à reconstruire, rien à transporter.

## Reproductibilité : trois sources éliminées, et la quatrième est irréductible

Un chiffre qui change d'une exécution à l'autre n'est pas vérifiable, donc sans valeur. La fiche de
rodage a rendu deux valeurs différentes pour un calcul identique, et la **même commande** a rendu
−0,05504, puis −0,05298, puis −0,05285. Trois causes, toutes corrigées :

1. **`ntile` tranchait ses ex æquo dans l'ordre de lecture des Parquet** — et des milliers de
   joueurs sont exactement à 1500, l'a priori du rating. Les trois fenêtres de tranche portent
   désormais un départage explicite (`order by valeur, game_id, small_id`).
2. **Le rang final utilisait `row_number()` sur un classement à ex æquo**, alors que deux joueurs
   éliminés au même tick *partagent* leur position (docs/DATA.md). C'est `rank()` : à la fois plus
   juste et stable.
3. **Le bootstrap tirait avec `random()` dans une requête parallélisée**, qui ne consomme pas sa
   graine dans un ordre stable, et numérotait ses grappes avec un `row_number() over ()` sans
   `ORDER BY`. Le tirage vient maintenant d'un `hash(tirage, rang, graine)`, déterministe et
   insensible au parallélisme.

Après correction : trois exécutions, checksums de la table de sujets identiques, effet identique à
**1e-9** près. Le résidu est à la 16e décimale — l'addition flottante en parallèle n'est pas
associative — et le test de non-régression l'écrit au lieu d'exiger une égalité au bit près.

**Et une distinction qui n'en est pas une seule** : `joueurs` comptait les grappes. En Team, où la
grappe est la partie, 102 parties passaient pour 102 joueurs face à un seuil de la SPEC qui en
exige 100. Les deux sont comptés séparément : la mesure Team porte 3 337 joueurs et **102 grappes**,
et c'est le second chiffre qui fait la largeur de l'intervalle.

## Ce qui ne pourra jamais émerger de ces données

- **Où** l'on attaque et la forme du territoire : rien au-delà de la boîte englobante du plus gros
  bloc, de la tuile de départ d'une attaque et de la largeur du front (ADR-0011). Pas de
  « encercler », pas de « couper un isthme ».
- Tout ce qui se joue à moins de 10 ticks d'état (les événements, eux, sont au tick exact).
- Trajectoires d'unités, routes de bateaux, trains et obus (exclus d'`events`).
- Le comportement des bots et des nations : aucune trajectoire (humains seulement).
- La diplomatie au-delà de ce que le moteur publie : alliances, embargos, cibles, dons. Pas de chat.
- Les **55 431 parties sans `turns`** : stats finales seulement. Elles servent le rating et le
  contexte descriptif, jamais une hypothèse à fenêtre.
- Le **niveau A** pour la plupart des fiches : 86 % des joueurs n'apparaissent qu'une fois dans
  l'échantillon, et un tirage uniforme plus large n'y change presque rien. A restera réservé aux
  joueurs assidus ; le régime normal est B.

## Comment vérifier

```bash
# la methode voit un effet plante, et rejette un effet qui ne vient que du niveau
uv run pytest -q tests/test_analyze.py

# le criblage, sur l'exploration seule (aucune lecture de confirmation)
uv run ofa analyze screen --top 30

# une hypothese de bout en bout, puis la preuve que la confirmation n'a ete lue qu'une fois
uv run ofa analyze explore <id> && uv run ofa analyze confirm <id>
uv run ofa analyze confirm <id>        # doit REFUSER
cat findings/reads.jsonl
```
