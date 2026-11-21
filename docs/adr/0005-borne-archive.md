# ADR-0005 — L'archive exploitable commence le 2025-09-12

- Date : 2026-11-21
- Statut : accepté (J0)

## Contexte

L'archive publique démarre le **2025-06-04** (id 1, vérifié par `afterId=0`). La question posée était
de couper « les deux premiers mois, qui semblent peu utiles ». Plutôt que de choisir une durée à
l'estime, on a cherché la date à partir de laquelle les records sont **exploitables**.

## Mesure

Sondage d'une partie publique tous les 30 jours sur toute l'archive, puis dichotomie, puis
confirmation sur 3 parties par date (26 requêtes au total) :

| date | `publicID` utilisables |
|---|---|
| 2025-06-04 → 2025-09-02 | **0 %** (0/133, 0/40, 0/81, 0/54) |
| 2025-09-08 | 0 % (0/44, 0/46, 0/34) |
| **2025-09-10** | **0 %** (0/41, 0/34, 0/52) |
| **2025-09-12** | **100 %** (69/69, 34/34, 50/50) |
| 2025-09-14 → 2026-11-21 | 100 % |

Autres champs, apparus progressivement : `lobbyCreatedAt` et `lobbyFillTime` entre novembre et
décembre 2025 ; `tribes` entre juillet et août 2026 ; `publicGameType` entre août et septembre 2026 ;
**`statsAgreed` seulement entre le 2026-10-12 et le 2026-10-19**.

## Choix

**L'ingestion commence au 2025-09-12.** Les 100 jours précédents (2025-06-04 → 2025-09-11) sont
abandonnés.

## Raison

Sans `publicID`, une ligne joueur n'est **attribuable à aucun compte**. Or tout le projet repose sur
l'identité du joueur : le rating chronologique (J4), la comparaison d'un joueur avec lui-même exigée
par la section « Rigueur », le bootstrap qui tire des joueurs entiers, et l'analyse de mes propres
parties (J7). Ces 100 jours ne pourraient servir qu'à des agrégats anonymes par map — et encore,
sur une version du jeu de plus d'un an, donc hors de toute comparaison valable d'équilibrage.

La borne est donc plus large que les deux mois envisagés (100 jours au lieu de 61), et elle est fixée
par une propriété des données, pas par une intuition.

## Conséquence sur le dimensionnement

Période exploitable : **435 jours** (2025-09-12 → 2026-11-21). L'activité a plus que doublé sur la
période (4 036 parties publiques/jour le 2025-10-02 contre 9 791 le 2026-11-01), donc le volume est
intégré à partir de 6 mesures réelles, et non extrapolé du débit actuel :

| | valeur |
|---|---|
| Parties publiques sur la période | **2 979 145** |
| Part dans le périmètre (exclusions qui se recoupent : union 76/300, somme 83) | **74,7 %** |
| **Parties à ingérer** | **2 224 428** |
| `meta` (`?turns=false`, 5,3 Ko) | **11,2 Go** |
| `full` pour 100 k parties re-simulées | 4,3 Go |
| Sortie de re-sim pour 100 k parties | 17,1 Go |
| Téléchargement à 2 req/s | **12,9 jours** |

## Attention : `statsAgreed` n'est pas un filtre utilisable

Il n'existe que depuis le ~2026-10-15, soit **5 semaines d'archive**. Filtrer l'analyse sur
`statsAgreed = true` jetterait ~97 % des données. Il reste un indicateur de qualité sur les parties
récentes, et le critère d'acceptation du J3 (« 500 parties `statsAgreed` ») est satisfiable
(~238 000 parties concernées), mais **la validité d'une re-sim se prouve par les hashs**, présents
sur tout l'historique (voir ADR-0003).

## Comment vérifier

```bash
python3 -I scratch/frontiere.py            # sonde mensuelle sur toute l'archive
```
La borne doit être revue si l'API se met à peupler `publicID` rétroactivement.
