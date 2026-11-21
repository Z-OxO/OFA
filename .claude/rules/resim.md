---
paths:
  - "resim/**"
---
# Re-simulation
- Ne jamais modifier le code du moteur OpenFront ; on le compile tel quel, au commit du record.
- Un moteur compilé par sha, en cache dans data/engines/<sha>/ ; version de Node prise au commit (engines / .nvmrc s'il existe).
- Si l'interface du moteur change d'un commit à l'autre : un adaptateur par version d'interface détectée, pas un par commit.
- Une re-sim n'est valide que si les stats finales sont identiques au record. Sinon : erreur classée, jamais de données partielles silencieuses.
- Commenter le TS pour un débutant : expliquer les types et l'async, pas la logique évidente.
