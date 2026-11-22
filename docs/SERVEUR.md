# Le serveur : installation, et comment on se sert de `ofa`

Le serveur porte tout : collecte continue, normalisation, re-simulation, analyse, et le site du J6
(ADR-0009). Le portable est une machine de développement : il écrit le code, le pousse par git, et
valide sur de petits lots.

Tous les chiffres de ce document sont **mesurés**, pas estimés, sauf ceux marqués « projeté ».

---

## 1. Installation, une fois

```bash
# 1.1 le projet
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/Z-OxO/OFA.git ~/ofa
cd ~/ofa && bash setup-wsl.sh     # node 24, npm 12, uv, python 3.12, duckdb, uv sync
```

`setup-wsl.sh` marche sur Debian/Ubuntu natif (il détecte l'absence de WSL et continue). Il est
relançable sans risque : il n'écrase aucun fichier existant.

```bash
# 1.2 le contact du User-Agent : SANS LUI, AUCUN APPEL API NE PART (hors git, exprès)
echo 'ton-adresse@exemple.fr' > ~/ofa/.ofa-contact

# 1.3 le clone de référence du moteur OpenFront, 2,1 Go : indispensable pour re-simuler
git clone https://github.com/openfrontio/OpenFrontIO.git ~/ofa/data/openfront
```

Ce clone n'est **jamais modifié** : `ofa engines` en tire un worktree par version du moteur
(ADR-0001, ADR-0008). Ne travaille jamais dedans à la main.

```bash
# 1.4 vérification
cd ~/ofa
uv run ofa --help
uv run ofa fixtures                                  # ~20 vrais records pour les tests (réseau)
uv run pytest -q -m "not slow and not network"       # 97 tests, ~15 s
```

### Amorçage des données depuis le portable (optionnel mais recommandé)

650 Mo déjà payés à l'API. Les refaire télécharger au serveur coûterait 2 h de requêtes pour un
résultat identique. **Collecte arrêtée des deux côtés**, depuis le portable :

```bash
rsync -a --info=progress2 ~/ofa/data/raw/   serveur:~/ofa/data/raw/     # 566 Mo meta + 29 Mo full
rsync -a                  ~/ofa/data/state/ serveur:~/ofa/data/state/   # 54 Mo, le curseur
```

Tout le reste se reconstruit sur place et **ne se transporte jamais** : `parquet` (3 min 27),
`rating` (14 s), `resim` (des jours, mais incrémental), `maps`, `engines`.

---

## 2. Les commandes, par ordre d'utilité

`OFA_RATE` est le débit API en requêtes/seconde (défaut **8**, le débit de rattrapage de
l'ADR-0006 ; **1** pour l'incrémental quotidien). `OFA_DATA_DIR` déplace `data/`.

| Commande | Ce qu'elle fait | Coût mesuré |
| --- | --- | --- |
| `ofa ingest` | collecte les 2 derniers jours, reprend où elle s'est arrêtée | quelques minutes |
| `ofa ingest --backfill` | remonte toute l'archive (depuis le 2025-09-12) par `afterId` | **7,7 j** à 4 req/s, 26 Go, 2,65 M parties (projeté) |
| `ofa purge-deleted` | retire les comptes supprimés entre-temps | minutes |
| `ofa fetch-full --sample N --seed 20261121` | télécharge le record **complet** (avec `turns`) de N parties tirées au hasard. Même graine = **sur-ensemble** : les présentes ne coûtent rien | 57 Ko et 1 requête par partie |
| `ofa normalize` | reconstruit **en entier** `data/parquet/` (games, participants, intents) | 3 min 27 pour 55 952 records ; ~2 h 45 pour l'archive complète (projeté) |
| `ofa rating` | le niveau d'avant-partie de chaque (partie, joueur), reconstruit en entier | 14,3 s pour 48 100 parties, 1,33 Go de RSS |
| `ofa engines` | prépare un moteur par version du jeu (worktree + `npm ci`) | 37 Mo par worktree, 362 Mo par version de lockfile |
| `ofa resim --evict` | rejoue les parties et écrit `data/resim/`. **Incrémental** : reprend où il s'est arrêté | **153 s de CPU par partie** ; `--evict` récupère 51 Go |
| `ofa resim --watch` | barre de progression, **dans un autre shell** pendant que la re-sim tourne | — |
| `ofa resim --report` | le tableau d'acceptation : réussites et échecs par cause | secondes |
| `ofa analyze screen` | criblage : 24 comportements × 4 résultats, sur l'exploration seule | ~2 min |
| `ofa analyze explore <id>` | mesure une hypothèse pré-inscrite, autant de fois qu'on veut | secondes |
| `ofa analyze confirm <id>` | ouvre l'échantillon de confirmation, **une seule fois par hypothèse** | secondes |
| `ofa analyze card <id>` | écrit la fiche JSON validée au schéma | ~1 min |
| `ofa rate-check` | prouve après coup que le débit configuré n'a pas été dépassé | secondes |
| `ofa fixtures` | retélécharge les ~20 vrais records des tests (hors git) | minutes |

### Les quatre règles qui évitent les dégâts

1. **Un seul `ofa` qui parle à l'API à la fois.** Le limiteur de débit vit dans le processus : deux
   commandes en parallèle font deux fois le débit configuré, **sans aucune alerte**. `ingest` et
   `fetch-full` ne se chevauchent jamais. La re-simulation, elle, ne touche pas l'API : elle peut
   tourner en même temps que n'importe quelle collecte.
2. **`normalize` échange `data/parquet/` en bloc.** Ne le lance pas pendant une re-sim : c'est là
   que `resim` lit sa liste de candidats. Entre deux passes, jamais pendant.
3. **`--evict` n'est pas le défaut, et il le faut en production.** Il détruit les worktrees des
   moteurs au fur et à mesure : 51 Go sur l'archive complète, et c'est ce qui fait tenir le projet
   dans 100 Go (ADR-0009).
4. **`data/raw/` ne se modifie jamais.** Tout le reste se reconstruit à partir de lui.

---

## 3. Le déroulé d'une campagne

L'ordre compte, et les étapes 3 et 4 cohabitent (l'une est du réseau, l'autre du CPU).

```bash
cd ~/ofa
tmux new -s ofa          # les longues tâches ne vivent pas dans un ssh qui se coupe

# 1. densifier la fenêtre : les records complets, seuls sur l'API
OFA_RATE=8 uv run ofa fetch-full --sample 48100 --seed 20261121

# 2. mettre les tables à jour — c'est `normalize` qui met `has_turns` à jour, donc la sélection
#    de la re-sim, et qui reconstruit `intents`
uv run ofa normalize && uv run ofa rating

# 3. les moteurs, puis la re-simulation (CPU pur, des jours, reprenable)
uv run ofa engines
uv run ofa resim --evict

# 4. dans un AUTRE tmux : le rattrapage historique (réseau pur), pour que le rating soit juste
OFA_RATE=4 uv run ofa ingest --backfill

# suivi, dans un troisième shell
uv run ofa resim --watch
```

### Combien ça coûte, selon ce qu'on re-simule

| parties re-simulées | brut | `intents` | `resim` | CPU | mur sur 8 cœurs |
| --- | --- | --- | --- | --- | --- |
| 4 500 | 257 Mo | 185 Mo | 2,3 Go | 191 h | ~24 h |
| 20 000 | 1,1 Go | 820 Mo | 10,4 Go | 848 h | 4,4 jours |
| 48 100 (tout le périmètre d'aujourd'hui) | 2,7 Go | 2,0 Go | 25 Go | 2 040 h | 10,6 jours |

**Télécharger est gratuit, rejouer est cher** : la collecte d'un record coûte une requête, sa
re-simulation 153 secondes de CPU. C'est la re-sim qui dimensionne tout.

---

## 4. Vérifier que tout va bien

```bash
uv run ofa resim --report          # réussites et échecs par cause
uv run ofa rating --report         # le rating bat-il encore les deux références naïves ?
uv run ofa rate-check              # le débit API a-t-il été respecté ?
uv run ofa analyze report          # le journal des lectures de confirmation (une par hypothèse)

duckdb -c "select count(*) from 'data/parquet/games/**/*.parquet'"
duckdb -c "select count(distinct game_id) from 'data/resim/player_ticks/**/*.parquet'"

df -h /                                       # l'espace réel
du -sm --exclude=node_modules data/engines/*  # 35 à 37 Mo par moteur attendu
du -sh data/node_modules                      # TROMPEUR : liens durs, 362 Mo par lockfile en vrai
git -C data/openfront worktree list           # aucune ligne `prunable`
```

Si `git worktree add` échoue en laissant une inscription `prunable` :
`git -C data/openfront worktree prune`, puis relancer `ofa engines`.

---

## 5. Ce qui n'existe pas encore

Les timers systemd (ingestion, purge quotidienne, re-sim, analyse, page santé) sont le **J9**. En
attendant, tout se lance à la main dans `tmux`, et c'est voulu : on ne met en service que ce qui a
été mesuré.
