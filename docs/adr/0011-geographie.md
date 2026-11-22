# ADR-0011 — La géographie, et l'inventaire de ce que le moteur expose

- Date : 2026-11-22
- Statut : accepté (extension du J3)

## Contexte

Les tables du J3 disent *combien* un joueur possède, *quand* il attaque et *qui* il conquiert.
Elles ne disent rien de *où*. Or une bonne partie des questions tactiques sont géographiques :
a-t-il attaqué son voisin le plus faible ? était-il enclavé ou adossé à une côte ? son front avec
le futur gagnant faisait-il 10 tuiles ou 400 ? Sans ça, « cette ouverture fait gagner » reste une
association sans mécanisme.

Aucune de ces données n'est archivée par l'API : `games` connaît le nom de la map, rien de plus.
La géographie n'existe que par re-simulation, donc elle appartient au runner du J3.

## Ce que le moteur expose (lu dans le clone de référence)

| | où | note |
| --- | --- | --- |
| `player.borderTiles()` | `PlayerImpl.ts:598` | un `TileSet` **tenu à jour en continu** par le moteur : rien à recalculer |
| `player.nearby()` | `Game.ts:438`, `PlayerImpl.ts:608` | **la notion de voisin du moteur lui-même**, mémoïsée sur `territoryVersion` |
| `player.spawnTile()` | `Game.ts:364` | la tuile du premier carré |
| `map.x/y/width/height/numLandTiles` | `GameMap.ts` | la grille |
| `map.ownerID(tile)`, `isWater`, `isImpassable`, `hasFallout`, `isOceanShore` | idem | l'état d'une tuile |

**La découverte qui a changé le dessin** : `nearby()` ne se limite pas à l'adjacence terrestre. Il
ajoute les joueurs séparés de moi par **une rivière de moins de 5 tuiles** (échantillonnage d'une
tuile de rive sur 10, `shoreReachableNeighbors`), parce qu'on peut les attaquer en bateau. Une
réimplémentation de l'adjacence à la main les aurait manqués — et silencieusement. C'est la leçon
du J3, piège n° 3, et la règle du projet : on prend ce que le moteur donne, on ne le recalcule pas.

Deux autres règles de terrain, reprises telles quelles de `computeNearby` : une **montagne** ne se
conquiert jamais (`conquer` lève dessus), et de la **terre libre irradiée** n'est pas franchissable
tant que la retombée dure. Ni l'une ni l'autre n'est une frontière.

## Options et choix

### 1. Quelle résolution pour le graphe de voisinage ?

C'est le seul arbitrage qui coûte du disque. Trois cadences étaient sur la table pour la table de
paires : toutes les 100 ticks (+27 % de sortie), toutes les 10 ticks (+130 %), ou pas de table du
tout (+15 %, mais on perd *lesquels* et donc toute question du genre « a-t-il choisi le plus
faible ? »).

→ **Toutes les 10 ticks**, la même cadence que les trajectoires : décidé explicitement, en
connaissance du coût. La géographie est centrale pour l'analyse visée, et une résolution plus
grossière sur le graphe que sur les trajectoires aurait obligé à interpoler l'un sur l'autre.

### 2. L'ensemble des voisins, ou la longueur des fronts ?

**Les deux, de deux sources différentes** : l'ensemble vient de `nearby()` (autoritaire, rivières
comprises), la longueur d'une passe sur `borderTiles()`. Un voisin que `nearby()` signale et avec
qui je ne partage aucune tuile — celui d'en face d'une rivière — sort avec `tiles = 0`. Ce 0 est
donc porteur de sens : « atteignable, mais pas par la terre ».

La longueur est **asymétrique** et c'est voulu : « mes tuiles face à lui » n'est pas « ses tuiles
face à moi », parce qu'une de mes tuiles peut toucher deux des siennes. Ce qu'on mesure est mon
exposition à lui, donc une partie humain-humain produit **deux** lignes, une par point de vue.

### 3. Où ranger quoi

- **`borders`, une 4e table de `data/resim/`** : une ligne par (observateur humain, voisin,
  échantillon). L'observateur est toujours un humain, comme `player_ticks` — un bot n'est pas un
  sujet d'analyse. Le voisin, lui, peut être n'importe qui, y compris `0` pour de la terre libre.
- **5 agrégats sur `player_ticks`**, qui ne coûtent aucune ligne : `border` (le périmètre),
  `neighbours`, `border_front` (la part face à un joueur), `border_sea` (la part côtière),
  `border_longest` (le plus long front). Ils répondent aux questions d'exposition sans toucher à
  la table de paires.
- **6 colonnes statiques sur `players`** : `spawn_x`, `spawn_y`, `spawn_shore`, et `map_width`,
  `map_height`, `map_land`. Les trois dernières sont des constantes de la partie recopiées par
  joueur : il n'y a pas de table « partie » du côté de la re-sim, et sans elles une position de
  départ ne veut rien dire. Trois petits entiers delta-encodés sur 188 000 lignes ne se discutent
  pas.
- **Rien de dérivable n'est stocké** : la distance au centre, la position normalisée ou la
  compacité se calculent en SQL depuis ces colonnes.

## L'inventaire : ce qu'on ajoute, ce qu'on écarte, ce qu'on laisse

Une passe de re-sim coûte 9 h : oublier un champ se paie deux fois. D'où un inventaire
**exhaustif** des interfaces `Player`, `Game`, `Unit`, `Attack` et de l'énumération
`GameUpdateType`, croisé avec ce que les tables portent déjà. La méthode du J2 : inventorier
avant de dessiner le schéma.

### Le cadrage qui a évité le double emploi

**`intents` (J2) porte déjà les décisions**, et pour exactement la même population que la re-sim
(les records complets). Donc : `intents.tile` donne **où** un joueur construit (`build_unit`, et
la cible d'une bombe), où il se pose (`spawn`), où il débarque (`boat.dst`) ; `cancel_attack`
donne les **retraites demandées** ; `quick_chat` et `emoji` donnent la communication ;
`troop_ratio` donne le curseur troupes/ouvriers. Rien de tout cela n'avait à être ajouté — je
m'étais trompé sur les retraites et sur les positions de construction avant de vérifier.

Et un pont, trouvé en lisant `GameMapImpl` : `x = ref % width`, `y = (ref / width) | 0`. La
colonne `map_width` ajoutée ici **débloque donc les coordonnées de tous les `TileRef` déjà
stockés**, les 91 784 lignes d'`intents` comprises.

Règle qui en sort : **`intents` = les décisions, `data/resim/` = les conséquences et l'état**.

### Ajouté (une seule passe)

| | où | pourquoi ça n'était pas dérivable |
| --- | --- | --- |
| le graphe de voisinage + longueur de front | table `borders` | ni dans les intents ni ailleurs |
| `border`, `neighbours`, `border_front`, `border_sea`, `border_longest` | `player_ticks` | exposition, sans coûter de ligne |
| `box_min_x/y`, `box_max_x/y` (`largestClusterBoundingBox`) | `player_ticks` | **la seule mesure de forme** que le moteur publie : étendue, allongement, centre approché |
| `decaying` (`isDecaying`) | `player_ticks` | le territoire qui pourrit quand le joueur n'est plus là |
| `last_tile_change` | `player_ticks` | le dernier changement territorial au tick **exact**, donc la stagnation sans l'arrondi à 10 |
| `spawn_x`, `spawn_y`, `spawn_shore`, `map_width`, `map_height`, `map_land` | `players` | la position de départ et le cadre où la lire |
| `disconnected_at` | `players` | le tick exact du décrochage |
| `tile` (position de l'unité, tuile de départ d'une attaque) | `events` | une perte ou une capture d'unité n'a **aucun intent en face** : sans ça, on ne sait pas où le front a cédé |
| `front` (largeur du front d'attaque, au départ puis maximale) | `events` | c'est le moteur qui choisit les tuiles, l'intent ne les porte pas |
| `flag` sur `attack_end` (retraite effective) | `events` | l'intent dit qu'une retraite est **demandée**, pas qu'elle a eu lieu |

### Écarté après mesure, pas par oubli

| | mesure | verdict |
| --- | --- | --- |
| `tradingPartners()` | **102 sur 104 joueurs** : `canTrade` ne teste que l'absence d'embargo | faux ami. Le vrai commerce est `gold_trade`, déjà là |
| `doomsdayClockTicks()` | `doomsday` est faux sur **10 518 824** lignes du J3 : l'état n'arrive jamais | un compteur pour un état jamais atteint, c'est le piège du J2 |
| `relation()` / `playerProfile()` | le score d'hostilité du moteur | **dérivable** : il est piloté par les attaques, dons, nukes et alliances, tous déjà dans `events` et `intents`. Et c'est en O(n²) |
| `unitsConstructed(type)` | cumul par type | dérivable : somme fenêtrée sur `events` (c'est déjà la contre-épreuve n° 1 du J3) |
| `isImmune()`, `inSpawnPhase()` | — | dérivables du tick et de `games.spawn_immunity_duration_ticks` |

### Laissé dehors, en connaissance de cause

- **`GameUpdateType.Tile` / `packedTileUpdates`** : l'historique territorial **tuile par tuile**.
  C'est la seule chose qui donnerait la forme exacte d'un territoire, et c'est deux ordres de
  grandeur au-dessus de tout le reste.
- **Les trajectoires d'unités** (un navire qui patrouille, un convoi qui traverse) : une table
  unités × échantillons. On a la position à la construction, à la capture et à la perte.
- **Trains et obus** : déjà écartés au J3 (2 464 événements sur 5 090, aucune stat en face).
- **`DisplayEvent` / `DisplayChatEvent` / `Emoji` / `UnitIncoming`** : du texte d'interface, et la
  communication est déjà dans `intents` (`quick_chat`, `emoji`).
- **`GamePaused`** : sans objet en re-jeu.

Reste donc **4 des 24 `GameUpdateType` non traités** pour une raison autre qu'une décision
documentée : `Tile` et `Player` (tous deux compactés, et le moteur n'expose pas de
décompacteur — le runner lit les objets vivants à la place), `GamePaused` et `UnitIncoming`.

## Le coût, mesuré

Même protocole que le J3 : la pire partie de l'archive (`cGYPygKBu8`, 28 601 ticks, 205 038 lignes),
rejouée avant et après. Avant (J3) : **374,3 s, 925 Mo de pic RSS**.

| | J3 | avec la géographie | écart |
| --- | --- | --- | --- |
| temps | 374,3 s | **401,8 s** | **+7,4 %** |
| pic RSS | 925 Mo | 946 Mo | +2,3 % |
| lignes écrites | 205 038 | **514 423** | **×2,51** |
| hashs concordants | 2 860 / 2 860 | **2 860 / 2 860** | le déterminisme tient |

Le surcoût CPU est dix fois plus petit que craint, pour une raison lisible dans le moteur :
`borderTiles()` est un ensemble **déjà tenu à jour** à chaque conquête, et `nearby()` est mémoïsé.
On ne recalcule rien, on lit. Le coût est donc dans l'écriture des lignes, pas dans le scan.

Répartition sur cette partie : `borders` 309 385 lignes, `events` 119 366, `player_ticks` 85 066,
`players` 606. La table de paires est donc **3,6 fois** `player_ticks` — c'est elle, le ×2,5.

Projection sur les 513 parties : **~9,1 h de CPU** (contre 8,44 h), soit ~1,5 h à 6 workers, et
une sortie d'environ **400 Mo** au lieu de 161 Mo.

## Conséquence : il faut tout re-simuler

Les 513 parties du J3 n'ont pas ces colonnes. `ofa resim` est incrémental et **sans `--force`**
(ADR-0003) : refaire une partie déjà valide dupliquerait ses lignes. Donc, à chaque changement de
schéma du runner, on vide et on recommence :

```bash
rm -rf data/resim
uv run python -c "from ofa import index; c = index.connect('data/state/index.sqlite'); \
  c.execute('delete from resim'); c.commit()"
uv run ofa resim                 # 8,44 h de CPU, ~1,4 h a 6 workers
uv run ofa resim --report
```

C'est le prix d'un schéma qui change, et il est payé en entier : jamais de table où la moitié des
parties aurait la géographie et l'autre non.

## Comment vérifier

```bash
# un voisin par l'eau : tiles = 0, et il doit exister
duckdb -c "select sum((tiles=0)::int) par_eau, sum((tiles>0)::int) par_terre
           from 'data/resim/borders/**/*.parquet' where b <> 0"

# l'ensemble des voisins de `borders` recoupe l'agregat de `player_ticks`
duckdb -c "with b as (select game_id, tick, a, count(*) filter (where b <> 0) n
                      from 'data/resim/borders/**/*.parquet' group by 1,2,3)
           select count(*) lignes, sum((b.n <> t.neighbours)::int) desaccords
           from b join 'data/resim/player_ticks/**/*.parquet' t
             on t.game_id=b.game_id and t.tick=b.tick and t.small_id=b.a"

# une position de depart est dans la carte
duckdb -c "select sum((spawn_x >= map_width or spawn_y >= map_height)::int) hors_carte
           from 'data/resim/players/**/*.parquet' where spawn_x is not null"
```

## Limites assumées

- **Pas de forme de territoire** : on a le périmètre et l'aire, donc la compacité, mais pas la
  géométrie (ni les tuiles possédées, ni le centre de masse). Stocker les tuiles d'un joueur à
  chaque échantillon serait un autre ordre de grandeur, et aucune question tactique posée à ce
  jour ne le demande.
- **`nearby()` échantillonne une tuile de rive sur 10** pour les voisins d'outre-rivière : c'est
  le moteur qui le fait, le jeu lui-même raisonne donc sur cette approximation. On la reprend
  telle quelle plutôt que d'être plus précis que le jeu.
- La géographie ne couvre que les parties re-simulées, comme le reste de `data/resim/` : 513 à ce
  jour, sur 48 100 parties du périmètre.
