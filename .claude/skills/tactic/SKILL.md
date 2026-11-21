---
name: tactic
description: Protocole obligatoire pour tester une hypothèse tactique et produire une fiche. À utiliser dès qu'on veut affirmer qu'un comportement aide ou nuit à gagner.
argument-hint: [hypothèse en une phrase]
---
Hypothèse : $ARGUMENTS

1. Écris findings/prereg/<id>.yaml : population (mode, map, niveau), définition du comportement, fenêtre [t0, t1], mesure du résultat, variables contrôlées, sens attendu, seuils. Commit AVANT toute mesure sur l'échantillon de confirmation.
2. Calcule sur l'échantillon d'exploration (`uv run ofa analyze explore <id>`). Effet faible ou échantillon insuffisant → arrête-toi et note-le dans la pré-inscription.
3. Confirme une seule fois (`uv run ofa analyze confirm <id>`, la lecture est loggée).
4. Robustesse : une autre définition raisonnable et une autre fenêtre. Changement de sens → REJETÉE.
5. Génère findings/cards/<id>.json (schéma validé), avec 5 exemples tirés au hasard et des contre-exemples.
6. Délègue la relecture à l'agent stats-reviewer, puis applique son verdict (corriger, rétrograder ou rejeter).
7. Résume : énoncé, effet + IC, n, niveau de preuve, verdict du relecteur.
