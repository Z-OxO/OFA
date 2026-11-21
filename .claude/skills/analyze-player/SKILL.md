---
name: analyze-player
description: Analyse en profondeur les parties d'un joueur OpenFront et produit des recommandations prouvées.
argument-hint: [publicID ou pseudo.1234]
disable-model-invocation: true
---
Joueur : $ARGUMENTS

1. Résous le publicID si on t'a donné un pseudo (via ofa/ingest/client.py, jamais d'appel direct à l'API).
2. `uv run ofa ingest --player <id>` puis `uv run ofa resim --player <id>` (priorité haute).
3. `uv run ofa me <id>` : courbes comparées aux gagnants de même niveau, sur la même map, aux mêmes instants.
4. Garde 3 à 5 écarts. Chaque recommandation doit citer une fiche tactique (niveau de preuve affiché) ET au moins 2 moments du joueur (gameID, mm:ss, lien replay). Sans les deux, ne la fais pas.
5. Signale à part les écarts sans fiche correspondante : ce sont des hypothèses à passer par /tactic.
