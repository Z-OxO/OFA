---
paths:
  - "ofa/analysis/**"
  - "ofa/rating/**"
  - "findings/**"
---
# Rigueur statistique (détail : docs/SPEC.md, section « Rigueur »)
- Comportement mesuré dans [t0, t1], résultat mesuré après t1, à état égal à t1 et à niveau égal ; seulement des joueurs encore en vie à t1.
- Intervalles de confiance : bootstrap en tirant des joueurs entiers. En Team, résultat = celui de l'équipe.
- Échantillon de confirmation = parties les plus récentes, une seule lecture par hypothèse, lecture loggée.
- Correction de Benjamini-Hochberg ; taille d'effet + IC 95 % avant toute p-valeur.
- Moins de 300 observations ou de 100 joueurs → « échantillon insuffisant ».
- Toujours séparer map, mode, taille d'équipe, difficulté, période.
- Exemples de fiche tirés au hasard avec une graine fixe, jamais choisis à la main.
