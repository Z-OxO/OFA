# ADR-0004 — Ingestion en deux étages (`turns=false` partout, record complet à la demande)

- Date : 2026-11-21
- Statut : accepté (J0)

## Contexte

Le serveur dispose de **100 Go de SSD**. Le périmètre arrêté (FFA et Team classiques + ranked 2v2,
sans ranked 1v1, sans `publicGameType: "special"`, sans `Humans Vs Nations`) représente
**~8 500 parties/jour à ingérer** et **2 654 420 parties** sur la période exploitable (2025-09-12 → 2026-11-21, ADR-0005).

Mesures du J0 : un record complet pèse **~45 Ko** compressé en moyenne (médiane 39,2 Ko), le même
record **sans `turns`** pèse **5,3 Ko** (×6), et **le nombre de requêtes est identique dans les deux
cas** (1 par partie). La compression a été poussée à son terme : dictionnaire zstd entraîné +4,0 %,
archive solide +5,1 %, `xz -9e` +3,7 % — rien de matériel à gagner (`PERF.md`).

## Options

| | Volume | Verdict |
|---|---|---|
| **A** — records complets sur toute l'archive | 164 Go de brut, ~225 Go au total | **ne tient pas** dans 100 Go |
| **B** — `?turns=false` partout + record complet pour les parties re-simulées | 26 + 4 + 17 (re-sim) + 12 (Parquet) + 9 (clone et moteurs) + 2,5 (index) = **~71 Go** | retenu |
| **C** — records complets limités aux 6 derniers mois | 56 + 17 + 12 + 9 = **~94 Go** | tient, mais **aucune marge** et on perd l'historique 2025 |

## Choix

**Option B.** Deux arborescences distinctes sous `data/raw/`, chacune immuable et conservée telle que
reçue :

```
data/raw/meta/<2 premiers caracteres du gameID>/<gameID>.json.zst   # reponse de ?turns=false
data/raw/full/<2 premiers caracteres du gameID>/<gameID>.json.zst   # reponse complete
```

Cible : `meta` pour **tout** le public sauf le ranked 1v1 (**2 654 420 parties, 21 Go de données, ~26 Go sur disque** — chiffre révisé au J1 sur 55 405 parties réelles ; les 13,4 Go venaient de 27 fixtures biaisées vers les petites parties), `full` pour les **~100 000 parties
re-simulées** (4 Go), priorisées comme le demande la spec — mes parties, puis les maps
sous-représentées, puis les plus récentes.

## Raison

L'analyse de population (usage 1 de la spec) n'a besoin que des stats de fin de partie, qui sont
intégralement dans la réponse `?turns=false`. Les `turns` ne servent qu'à la re-simulation, et la
re-simulation est de toute façon **plafonnée par le CPU, pas par le disque** : 14 150 parties/jour
sur 8 cœurs, soit 7 jours pour 100 000 parties. Stocker les `turns` de 2,65 M parties qu'on ne
rejouera jamais coûterait 106 Go pour rien.

L'option C tiendrait, mais elle sacrifie l'historique 2025 **et** ne laisse aucune marge, alors que
l'option B garde l'archive entière pour l'analyse de population et laisse ~39 Go libres.

## Coût assumé

1. **Écart avec « une partie finie n'est jamais re-téléchargée ».** Une partie retenue pour la
   re-simulation est téléchargée une seconde fois, en version complète. L'écart porte sur ~100 000
   parties, soit **+2,8 % de requêtes**. La règle est conservée à l'intérieur de chaque étage : un
   fichier déjà présent dans `meta/` ou dans `full/` n'est jamais redemandé.
2. **Re-simuler une partie non prévue au départ exige un téléchargement.** Acceptable à 2 req/s.
3. Si le disque passe un jour à 400 Go ou plus, l'option A redevient préférable et cette ADR doit
   être remplacée : elle n'engage rien d'irréversible, `meta/` restant utile comme index.

## Comment vérifier

- `du -sh data/raw/meta data/raw/full` doit suivre la trajectoire 26 Go / 4 Go.
- Le compteur de requêtes des logs d'ingestion doit montrer **0 requête** pour une partie déjà
  présente dans l'étage demandé (critère d'acceptation du J1).
- Le ratio `full/meta` doit rester proche du nombre de parties effectivement re-simulées : s'il
  dérive, c'est que la priorisation télécharge des `full` qui ne sont jamais rejoués.
