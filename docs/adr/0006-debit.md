# ADR-0006 — Débit : 8 req/s pour le rattrapage, 1 req/s en régime permanent

- Date : 2026-11-21
- Statut : accepté (J1)

## Contexte

La spec fixait 1 req/s par défaut, « configurable ». Le propriétaire du projet a explicitement
autorisé un débit plus élevé. Ce qu'on sait, mesuré au J0 et au J1 :

- `docs/API.md` ne documente **aucune** limite de débit, et aucune authentification n'est requise ;
- **aucun en-tête** `RateLimit-*`, `Retry-After` ou `Cache-Control` n'est renvoyé sur `/public/games` ;
- `server: cloudflare` est en frontal, donc un 429 ou un 403 peut tomber **sans préavis** ;
- **plus de 450 requêtes** ont été envoyées à 1 req/s pendant le J0 **sans un seul 429 ni 403** ;
- la documentation décrit elle-même le parcours de **toute** l'archive par `afterId` : des clients
  en masse sont prévus par la conception de l'API ;
- **`api.openfront.io/robots.txt` renvoie 404**, et `openfront.io/robots.txt` dit
  `User-agent: * / Allow: /` : **aucun `Crawl-delay`**, aucune zone interdite ;
- **le serveur de jeu d'OpenFront limite lui-même HTTP à 20 requêtes par IP et par seconde**
  (`src/server/Master.ts` : `rateLimit({ windowMs: 1000, max: 20 })`). Ce n'est pas la limite du
  Worker `/public/*`, qui est un service distinct et closed-source, mais c'est le seuil que les
  auteurs du jeu considèrent eux-mêmes comme abusif, écrit dans leur code ;
- le seul client tiers public de cette API ([openfrontio-api-client](https://github.com/tidwell/openfrontio-api-client))
  n'implémente **aucune** limitation de débit et ne donne aucune consigne de politesse ;
- sur la vraie ingestion du J1, **41 404 requêtes** à 3,5 req/s effectifs : **aucun 429, aucun 403**.

Volume à collecter : **2 654 420 parties** (tout le public sauf le ranked 1v1, filtré au niveau de
la liste), sur la période exploitable 2025-09-12 → 2026-11-21.

## Options

| débit | durée du rattrapage | commentaire |
|---|---|---|
| 1 req/s (défaut de la spec) | **30,7 jours** | aucun risque, mais l'historique arrive tard |
| 2 req/s | 15,4 jours | |
| 4 req/s | 7,7 jours | premier choix du J1, avant de trouver le seuil de 20 req/s |
| **8 req/s** | **3,8 jours** | **retenu** : 40 % du seuil que les auteurs appliquent eux-mêmes par IP |
| 20 req/s | 1,5 jour | c'est leur seuil d'abus : appliquer à un service la limite d'un autre, sans marge |

## Choix

- **Rattrapage historique : 8 req/s**, 4 connexions simultanées (`OFA_RATE=8`, `OFA_CONCURRENCY=4`).
  Il faut ~4 requêtes en vol pour tenir 8 req/s avec une latence mesurée de 0,4 s par record.
- **Incrémental quotidien : 1 req/s** (`OFA_RATE=1`). Il ne traite que ~8 500 parties/jour, soit
  moins de 2 h 30 à ce débit : le débit élevé est une **pointe unique**, pas une charge permanente.
- Limiteur à **intervalle minimum** (1/R entre deux départs), donc aucune rafale possible.
- Backoff croissant sur 429, **403**, 408, 425 et 5xx, en respectant `Retry-After` quand il est en secondes.
- **Coupe-circuit** : 3 erreurs d'affilée arrêtent la collecte avec un code de sortie non nul
  (le timer systemd réessaiera plus tard), au lieu d'insister.
- **Ralentissement définitif sur 429 et 403** : le débit est divisé par deux à chaque occurrence,
  avec un plancher à 1 req/s, et **il n'est jamais remonté**. Un 5xx ou une coupure réseau ne
  ralentissent pas : ce ne sont pas des signaux de débit.
- **On ne cherche jamais la limite** en augmentant jusqu'à l'erreur. Pour aller au-delà de 8 req/s,
  on demande aux mainteneurs ; le User-Agent porte déjà une adresse de contact.

## Raison

Rien n'est documenté, donc le seul repère chiffré disponible est **celui que les auteurs
s'appliquent à eux-mêmes : 20 req/s par IP**. 8 req/s en représente 40 %, ce qui laisse une marge
de 2,5× et se défend en une phrase si on nous le demande. Monter à 20 reviendrait à transposer à un
service la limite d'un autre, sans marge : le gain (2,3 jours) ne vaut pas ce raisonnement-là.

8 req/s représente ~690 000 requêtes par jour pendant 4 jours, puis **moins de 9 000 par jour** :
la pointe est courte et le régime permanent est négligeable. Et la re-simulation reste le vrai
goulot — 7 jours pour 100 000 parties sur 8 cœurs — donc au-delà de 8 req/s on n'accélère plus le
projet, on augmente seulement le risque.

## Comment vérifier

```bash
uv run ofa rate-check        # intervalle minimum observe vs 1/R, debit moyen
```

Le critère est l'**intervalle minimum** entre deux départs de requête, pas un comptage sur fenêtre
glissante : avec un espacement exactement égal à 1/R, une gigue de 0,1 ms fait apparaître R+1
requêtes dans une fenêtre d'une seconde sans que le débit soit dépassé. Ce faux positif a été
rencontré à la première vraie exécution et il est couvert par un test de non-régression.

Mesuré sur la vraie ingestion du J1, **41 404 requêtes** à `OFA_RATE=4` :
avance maximale **+0,000 requête**, débit moyen 3,53 req/s, **aucun 429 ni 403**.

Le ralentissement adaptatif se vérifie par test (`test_un_429_ralentit_definitivement`,
`test_un_5xx_ne_ralentit_pas`, `test_le_ralentissement_a_un_plancher`) : en production il
apparaîtra dans les logs sous la forme
`ATTENTION: HTTP 429 -> debit ramene a N req/s pour le reste de l'execution`.
