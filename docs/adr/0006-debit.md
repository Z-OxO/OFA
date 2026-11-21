# ADR-0006 — Débit : 4 req/s pour le rattrapage, 1 req/s en régime permanent

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
  en masse sont prévus par la conception de l'API.

Volume à collecter : **2 654 420 parties** (tout le public sauf le ranked 1v1, filtré au niveau de
la liste), sur la période exploitable 2025-09-12 → 2026-11-21.

## Options

| débit | durée du rattrapage | commentaire |
|---|---|---|
| 1 req/s (défaut de la spec) | **30,7 jours** | aucun risque, mais l'historique arrive tard |
| 2 req/s | 15,4 jours | |
| **4 req/s** | **7,7 jours** | retenu |
| 8 req/s | 3,8 jours | gain de 4 jours, risque doublé, et aucune justification à opposer à l'opérateur |

## Choix

- **Rattrapage historique : 4 req/s**, 2 connexions simultanées (`OFA_RATE=4`, `OFA_CONCURRENCY=2`).
- **Incrémental quotidien : 1 req/s** (`OFA_RATE=1`). Il ne traite que ~8 500 parties/jour, soit
  moins de 2 h 30 à ce débit : le débit élevé est une **pointe unique**, pas une charge permanente.
- Limiteur à **intervalle minimum** (1/R entre deux départs), donc aucune rafale possible.
- Backoff croissant sur 429, **403**, 408, 425 et 5xx, en respectant `Retry-After` quand il est en secondes.
- **Coupe-circuit** : 3 erreurs d'affilée arrêtent la collecte avec un code de sortie non nul
  (le timer systemd réessaiera plus tard), au lieu d'insister.
- **On ne cherche jamais la limite** en augmentant jusqu'à l'erreur. Pour aller au-delà de 4 req/s,
  on demande aux mainteneurs ; le User-Agent porte déjà une adresse de contact.

## Raison

Le gain réel est faible et le risque mal connu. La re-simulation reste le vrai goulot — 7 jours
pour 100 000 parties sur 8 cœurs — donc gagner 4 jours de téléchargement ne raccourcit pas le
projet d'autant. En face, un blocage Cloudflare coûterait bien plus que ces 4 jours, et nous
n'aurions aucun argument à présenter : rien ne documente ce qui est acceptable.

4 req/s représente ~345 000 requêtes par jour pendant 8 jours, puis moins de 9 000 par jour. C'est
un ordre de grandeur qu'un Worker Cloudflare servant un jeu à ~10 000 parties quotidiennes absorbe
sans difficulté, tout en restant assez bas pour rester défendable si on nous le demande.

## Comment vérifier

```bash
uv run ofa rate-check        # intervalle minimum observe vs 1/R, debit moyen
```

Le critère est l'**intervalle minimum** entre deux départs de requête, pas un comptage sur fenêtre
glissante : avec un espacement exactement égal à 1/R, une gigue de 0,1 ms fait apparaître R+1
requêtes dans une fenêtre d'une seconde sans que le débit soit dépassé. Ce faux positif a été
rencontré à la première vraie exécution et il est couvert par un test de non-régression.

Mesuré sur une vraie exécution de 36 requêtes à `OFA_RATE=4` : intervalle minimum **0,2499 s**
pour 0,2500 s requis, débit moyen 3,85 req/s, aucun 429 ni 403.
