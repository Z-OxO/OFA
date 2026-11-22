# ADR-0010 — Le niveau : un rating chronologique de Plackett-Luce

- Date : 2026-11-22
- Statut : accepté (J4)

## Contexte

Le but du projet est de dire **quelle façon de jouer fait gagner**. Le niveau n'y est pas une fin :
c'est le contrôle que la SPEC impose (§ Rigueur, *Effet du niveau*) pour qu'une fiche tactique ne
confonde pas « ce comportement fait gagner » avec « les bons joueurs ont ce comportement ». Il faut
donc une colonne « niveau d'avant-partie » par (partie, joueur), et le J4 ne doit rien livrer de
plus.

Trois questions à trancher : avec quoi mesurer le résultat d'une partie, avec quel modèle, et où
ranger la sortie.

## Options et mesures

### 1. Le résultat d'une partie

En FFA la victoire est trop rare (1 sur 38 en moyenne), la SPEC demande donc le classement. Deux
candidats : `players.death_position` (le classement exact, mais seulement pour les 513 parties
re-simulées du J3) ou les stats du serveur déjà normalisées au J2 (48 100 parties).

Croisement exhaustif sur les 34 380 FFA à vainqueur joueur : un participant est dans **exactement
un** de ces états — vivant à la fin (`final_tiles` rempli, `killed_at` nul : 261 789), mort
(`killed_at` rempli, `final_tiles` nul : 950 937), ou sans aucune stat (94 386, 7,2 %). Et le
vainqueur est l'argmax de `final_tiles` **34 380 fois sur 34 380**.

→ **Classement tiré des tables du J2** : les vivants d'abord par territoire final décroissant, puis
les morts par mort décroissante. `won` n'est pas une entrée du calcul, c'est une contre-épreuve
(`test_le_vainqueur_ffa_est_toujours_premier`, et la vérification SQL ci-dessous). La re-sim serait
plus précise sur 1 % des parties et absente sur 99 %.

Les 7,2 % **sans stats sont hors classement** : sans `killed_at` ni `final_tiles`, il n'y a pas de
résultat. Les placer derniers inventerait une défaite à quelqu'un qui a surtout perdu sa connexion.

En **Team**, les équipes des perdants sont inconnues sans re-sim (`participants.team_index` :
0,2 % des lignes ; `players.team` du J3 : 147 parties). On ne sait qu'une chose, on ne dit que
celle-là : l'équipe de `games.winner_members` a battu tout le reste → **deux groupes**.

### 2. Le modèle

| option | coût sur l'archive projetée du serveur (~3 M de parties, 38 joueurs/partie) | dépendance |
| --- | --- | --- |
| Elo développé par paires | ~**1,4 milliard** de mises à jour (O(n²) par partie) | aucune |
| TrueSkill / openskill | idem ou pire, et un modèle qu'on ne contrôle pas | +1 paquet |
| **Plackett-Luce en ligne** | ~**113 millions** (O(n) par partie) | aucune |

Le gradient de la log-vraisemblance de Plackett-Luce avec ex æquo (approximation de Breslow) se
calcule en une passe par sommes suffixes :

```
LL     = Σ_{g < m} [ Σ_{k ∈ G_g} θ_k − |G_g| · log S_g ]        S_g = Σ_{j ∈ G_g..G_m} w_j
grad_k = [k ∉ dernier groupe] − w_k · Σ_{g ≤ min(h, m−1)} |G_g| / S_g        w = exp(θ)
```

Le facteur du **dernier** groupe est retiré : « les derniers n'ont battu personne » n'informe de
rien, et le garder tirerait les perdants d'une partie Team les uns vers les autres. Conséquences
vérifiées par les tests : le gradient **somme à zéro** (rating à somme nulle, comme l'Elo) et sur
deux joueurs il **redonne exactement l'Elo par paire**. `exp(θ)` est recentré sur le maximum, sans
quoi un joueur très bien noté fait déborder la passe.

→ **Plackett-Luce**, et donc **aucune dépendance ajoutée** : pas de numpy, pas de polars. La passe
est séquentielle par nature (un rating chronologique ne se vectorise pas entre parties) et linéaire
en participations : **14,3 s pour 48 100 parties et 1,68 M de lignes**. Polars et DuckDB attendront
un vrai usage de lecture (J5/J6).

Pas d'apprentissage : `lr(n) = lr0 / (1 + n / demi-vie)`, réglé sur la **seule fenêtre
d'exploration** (entraînement du 15 au 17/11, évaluation le 18/11), log-loss / Spearman :

| lr0 | 0,01 | 0,03 | 0,08 | **0,2** | 0,4 | 0,8 |
| --- | --- | --- | --- | --- | --- | --- |
| | 0,683 / 0,299 | 0,668 / 0,302 | 0,648 / 0,307 | **0,636 / 0,306** | 0,648 / 0,283 | 0,744 / 0,225 |

Demi-vie (à lr0 = 0,4) : 5 → 0,636 ; 20 → 0,636 ; **∞ → 0,688**. La décroissance du pas compte, sa
valeur exacte non : le plateau est large. D'où `RATING_LR0 = 0.2` et `RATING_HALFLIFE = 20` figés
dans `config.py`, et **aucune commande de calibration** à maintenir : refaire cette table est un
script jetable.

### 3. Les joueurs sans publicID (critère d'acceptation)

`public_id` est à 100 % sur l'archive bornée par l'ADR-0005, avec 277 lignes `REDACTED` (comptes
purgés). Un joueur sans identité **joue mais ne capitalise pas** : il compte dans la normalisation
`S_g` — sinon la taille du terrain est fausse et tous les autres sont mal notés — reste à l'a priori
θ = 0, et n'a ni ligne ni mémoire. Une partie entièrement anonyme (les records de juin 2025 n'ont
aucun publicID) n'écrit rien et ne plante pas. Les trois cas sont testés sur de vraies fixtures.

### 4. Le rangement

- **`data/rating/`, sa propre racine.** Jamais sous `data/parquet/`, que `normalize` échange en bloc.
- **Reconstruction totale**, jamais d'incrémental (contrairement à `data/resim/`) : la passe coûte
  14 s, et la purge d'un compte doit pouvoir réécrire toute l'histoire sans trace de l'ancienne.
  Écriture à côté puis échange, comme `normalize.rebuild`.
- **Échelle Elo stockée** : `rating = 1500 + (400 / ln 10) · θ`. Le modèle travaille en θ, la
  conversion est à l'écriture, pour que le J6 affiche un nombre que tout le monde lit.
- **Un seul θ par joueur**, tous modes confondus. Le rapport donne les métriques par mode ; si Team
  décroche trop, c'est la clé du dictionnaire qui devient `(public_id, mode)` — une ligne. Mesuré :
  Team est plus faible mais bat les deux références, donc pas maintenant.

### 5. La coupure du protocole

`CONFIRMATION_START = 2026-11-20T00:00:00Z`, constante unique dans `config.py` : 36 397 parties du
périmètre avant, 11 703 après (76 / 24). Le pas d'apprentissage a été réglé en deçà de cette date ;
la confirmation n'a été lue qu'une fois, pour le rapport d'acceptation.
**Déplacer cette date invalide toute confirmation passée.**

## Comment vérifier

```bash
uv run ofa rating            # la passe : 48 100 parties, 1,68 M de lignes, 14,3 s
uv run ofa rating --report   # l'acceptation sur l'échantillon de confirmation

# contre-épreuve 1, sans le code de ofa : rank = 1 tombe-t-il sur le joueur `won` ?
duckdb -c "with r as (select * from 'data/rating/**/*.parquet'),
  p as (select game_id, public_id, won from 'data/parquet/participants/**/*.parquet'),
  g as (select game_id from 'data/parquet/games/**/*.parquet'
        where in_scope and mode='Free For All' and winner_kind='player')
select count(*), sum(p.won::int) from r join g using(game_id)
 join p using(game_id, public_id) where r.rank=1"
# -> 34 359 / 34 359

# contre-épreuve 2 : le chaînage temporel (rating_after = rating_before de la partie suivante)
# -> 1 426 259 transitions, 0 rupture de rating, 0 rupture de compteur
```

## Limites assumées

- En Team, le rating d'un joueur est celui de son équipe : un passager est surévalué. Et un
  équipier déconnecté absent de `winner_members` a `won = false` alors que son équipe a gagné.
  Non corrigeable sans re-simuler les 12 965 parties Team.
- Pas d'incertitude par joueur (σ à la TrueSkill) : `games_before` est le seul indicateur de
  fiabilité, et le rapport est ventilé par ce seuil.
- La purge d'un compte efface son `public_id` : la reconstruction suivante perd son historique.
  Cohérent avec « tout se reconstruit depuis `data/raw/` ».
