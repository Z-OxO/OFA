"""Enveloppes minces autour des endpoints publics. Tout passe par Client."""

from .client import Client, Response


def list_games(
    client: Client,
    *,
    ranked_type: str,
    after_id: int,
    limit: int = 1000,
    game_type: str = "Public",
) -> list[dict]:
    """Parcours de l'archive par `afterId`, trié par id croissant.

    Piège mesuré au J0 : `id` est une chaîne décimale, à comparer en entier.
    `Content-Range` est absent dans ce mode, c'est normal.
    """
    path = f"/games?afterId={after_id}&limit={limit}&type={game_type}&rankedType={ranked_type}"
    resp = client.get(path)
    if resp.status != 200:
        raise RuntimeError(f"HTTP {resp.status} sur {path}: {resp.body[:200]!r}")
    return resp.json()


def get_game(client: Client, game_id: str, *, turns: bool) -> Response:
    """Le record. On rend la Response pour pouvoir stocker les octets reçus tels quels."""
    suffix = "" if turns else "?turns=false"
    return client.get(f"/game/{game_id}{suffix}")


def recently_deleted(client: Client, since: str | None = None) -> list[dict]:
    """Comptes supprimés dans les 7 derniers jours. `since` au-delà de 7 jours -> 400."""
    path = "/players/recently-deleted" + (f"?since={since}" if since else "")
    resp = client.get(path)
    if resp.status != 200:
        raise RuntimeError(f"HTTP {resp.status} sur {path}: {resp.body[:200]!r}")
    return resp.json()


def list_window(
    client: Client,
    *,
    ranked_type: str,
    start: str,
    end: str,
    limit: int = 1000,
    after: str | None = None,
    game_type: str = "Public",
) -> tuple[list[dict], int | None]:
    """Fenêtre temporelle (2 jours maximum), paginée par curseur `after`.

    `start` et `end` sont OBLIGATOIRES dans ce mode (400 sinon) : la spec l'omettait.
    Retourne (lignes, total) ; le total n'est donné qu'en première page, via Content-Range.
    Vérifié sur l'API : pas de chevauchement entre pages, ordre chronologique croissant.
    """
    path = f"/games?start={start}&end={end}&limit={limit}&type={game_type}&rankedType={ranked_type}"
    if after:
        path += f"&after={after}"
    resp = client.get(path)
    if resp.status != 200:
        raise RuntimeError(f"HTTP {resp.status} sur {path}: {resp.body[:200]!r}")
    total = None
    content_range = resp.headers.get("Content-Range", "")
    if "/" in content_range:
        tail = content_range.rsplit("/", 1)[-1]
        total = int(tail) if tail.isdigit() else None
    return resp.json(), total
