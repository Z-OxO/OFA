"""Jeu de fixtures : de vrais records, choisis au J0 pour couvrir les cas qui cassent.

Pas de données synthétiques ici : la spec interdit de mocker le moteur, et un record
réel contient les pièges qu'on n'inventerait pas (stats lacunaires, publicID absent,
compte REDACTED, tableaux de stats plus courts sur les vieilles versions).
`tests/fixtures/*.zst` est hors git : ces records contiennent de vrais pseudonymes.
"""

from pathlib import Path

from . import api, raw
from .client import Client

# gameID -> pourquoi cette partie est dans le jeu de tests.
FIXTURES: dict[str, str] = {
    "00TxMvVK": "2025-06-04, 82 joueurs : publicID null partout, info reduit, pas de statsAgreed",
    "004nOUnr": "2025-06-04, 36 joueurs : idem, autre taille",
    "00oCT3v4": "2025-06-01, 37 joueurs : id superieur a une partie plus recente",
    "cMEfTCyCL1": "FFA 100 joueurs contenant un compte REDACTED",
    "dsVWDaAjbe": "FFA 18 joueurs, petite",
    "dHmvqYKWTg": "FFA 50 joueurs",
    "dYgPC1HdrG": "FFA 70 joueurs",
    "cR4EhwxE2N": "FFA 10 joueurs",
    "dZBzzHMcB9": "Team 52 joueurs",
    "c72aLwLruP": "Team 22 joueurs, courte",
    "dmm3dkeCbi": "Team 68 joueurs",
    "cAAuuWWMDH": "Team 20 joueurs",
    "dieBwPMtBE": "ranked 2v2",
    "cTojJtaVu3": "ranked 2v2",
    "dd2oWu1Wkn": "ranked 1v1 (hors perimetre : doit etre exclu, pas plante)",
    "cxWsnmjHq1": "ranked 1v1",
    "dKpQC4RCzq": "Humans Vs Nations (hors perimetre d'analyse)",
    "cyLNY6T6B7": "Humans Vs Nations",
    "cTbsbCRoAN": "publicGameType special (hors perimetre d'analyse)",
    "cTf3G8KqWf": "publicGameType special",
    "ddcUz3DS7w": "Team 4 joueurs, 211 ticks : la plus courte",
    "cPnBjX152J": "FFA 2 joueurs, 211 ticks",
    "cDkWi2N1AT": "FFA 95 joueurs, 29342 ticks : la plus grosse re-simulee au J0",
    "dghYVfZ6y6": "FFA 125 joueurs, 28574 ticks : le plus de joueurs",
    "ddDHAx1Tt4": "statsAgreed=false",
    "cPaLHdRvC2": "FFA 25 joueurs, re-simulee au J0 (diff vide)",
    "dtQhXS8owK": "Team 24 joueurs, re-simulee au J0 (diff vide)",
}

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def download(client: Client, dest: Path = FIXTURE_DIR) -> dict:
    """Télécharge les fixtures manquantes. Une partie déjà présente n'est pas redemandée."""
    summary = {"present": 0, "downloaded": 0, "failed": {}}
    for game_id in FIXTURES:
        if raw.exists(dest, game_id):
            summary["present"] += 1
            continue
        resp = api.get_game(client, game_id, turns=True)
        if resp.status != 200:
            summary["failed"][game_id] = resp.status
            continue
        raw.write(dest, game_id, resp.body)
        summary["downloaded"] += 1
    return summary
