"""Le seul chemin vers l'API OpenFront. Aucun autre module ne fait de requête HTTP.

Garanties :
  - débit moyen <= `rate` requêtes/s, quel que soit le nombre de threads ;
  - au plus `concurrency` requêtes simultanées ;
  - attente croissante sur 429/403/5xx et erreurs réseau, en respectant `Retry-After` ;
  - arrêt de la collecte après `max_consecutive_errors` échecs d'affilée ;
  - une ligne JSONL par requête, pour pouvoir recompter le débit après coup.
"""

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import API_BASE, Config

# Codes qu'on réessaie. 403 est inclus : Cloudflare est en frontal et peut le renvoyer
# sans en-tête d'avertissement (mesuré au J0 : aucun RateLimit-* n'est exposé).
RETRY_STATUS = frozenset({403, 408, 425, 429, 500, 502, 503, 504, 522, 524})


class CircuitOpen(RuntimeError):
    """Trop d'erreurs d'affilée : on arrête au lieu d'insister."""


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self):
        return json.loads(self.body)


class RateLimiter:
    """Espacement strict : deux requêtes consécutives sont séparées d'au moins 1/rate.

    Pas de seau à jetons, donc pas de rafale possible : c'est volontaire, le but est de
    ne jamais dépasser le débit configuré, pas de le lisser au mieux.
    """

    def __init__(
        self,
        rate: float,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if rate <= 0:
            raise ValueError("rate doit être > 0")
        self._interval = 1.0 / rate
        self._now = now
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_at = now()

    def acquire(self) -> None:
        with self._lock:
            now = self._now()
            slot = max(now, self._next_at)
            self._next_at = slot + self._interval
        wait = slot - now
        if wait > 0:
            self._sleep(wait)


def _retry_after_seconds(headers: dict[str, str], attempt: int) -> float:
    """Respecte Retry-After s'il est exprimé en secondes, sinon attente exponentielle."""
    value = headers.get("Retry-After", "")
    if value.strip().isdigit():
        return min(300.0, float(value.strip()))
    return min(300.0, 2.0**attempt)


class Client:
    def __init__(
        self,
        cfg: Config,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        session_factory: Callable[[], requests.Session] = requests.Session,
    ):
        self.cfg = cfg
        self._limiter = RateLimiter(cfg.rate, now, sleep)
        self._slots = threading.BoundedSemaphore(cfg.concurrency)
        self._sleep = sleep
        self._now = now
        self._session_factory = session_factory
        self._thread_state = threading.local()
        self._lock = threading.Lock()
        self._consecutive_errors = 0
        self.requests_made = 0
        self._log_path = cfg.log_dir / "requests.jsonl"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    # Une session par thread : requests.Session n'est pas thread-safe, mais elle
    # réutilise les connexions TCP, ce qui compte sur des millions de requêtes.
    def _session(self) -> requests.Session:
        s = getattr(self._thread_state, "session", None)
        if s is None:
            s = self._session_factory()
            s.headers.update({"User-Agent": self.cfg.user_agent, "Accept-Encoding": "identity"})
            self._thread_state.session = s
        return s

    def _log(self, entry: dict) -> None:
        line = json.dumps(entry) + "\n"
        with self._lock, self._log_path.open("a") as f:
            f.write(line)

    def get(self, path: str, tries: int = 5) -> Response:
        """Un GET, débit respecté et réessais inclus. Lève en cas d'échec définitif."""
        url = path if path.startswith("http") else API_BASE + path
        last: Response | None = None
        for attempt in range(tries):
            self._limiter.acquire()
            with self._slots:
                started = self._now()
                started_wall = time.time()  # ce que voit le serveur : le DEBUT de la requete
                try:
                    r = self._session().get(url, timeout=self.cfg.timeout)
                    resp = Response(r.status_code, dict(r.headers), r.content)
                except requests.RequestException as exc:
                    resp = Response(0, {}, str(exc).encode())
                elapsed = self._now() - started
            with self._lock:
                self.requests_made += 1
            self._log(
                {
                    "ts": started_wall,
                    "url": url,
                    "status": resp.status,
                    "seconds": round(elapsed, 3),
                    "bytes": len(resp.body),
                    "attempt": attempt,
                }
            )
            last = resp
            if resp.status == 200:
                with self._lock:
                    self._consecutive_errors = 0
                return resp
            if resp.status not in RETRY_STATUS and resp.status != 0:
                # 400, 404... : définitif, c'est à l'appelant de décider.
                with self._lock:
                    self._consecutive_errors = 0
                return resp
            with self._lock:
                self._consecutive_errors += 1
                too_many = self._consecutive_errors >= self.cfg.max_consecutive_errors
            if too_many:
                raise CircuitOpen(
                    f"{self._consecutive_errors} erreurs d'affilée, "
                    f"dernière {resp.status} sur {url}"
                )
            if attempt < tries - 1:
                self._sleep(_retry_after_seconds(resp.headers, attempt))
        assert last is not None
        return last


def rate_report(log_path: Path, rate: float) -> dict:
    """Ce que le journal dit du débit réellement appliqué.

    Le bon critère est l'**avance sur l'échéancier idéal** : le limiteur planifie la
    i-ème requête à `t0 + i/R`, donc `i - R*(t_i - t_0)` ne doit jamais dépasser 1
    (la première requête part sans attendre). Cette grandeur est insensible à la gigue
    de l'ordonnanceur et détecte toute rafale, même brève.

    Les deux autres mesures sont indicatives et trompeuses prises seules :
      - l'intervalle minimum descend de quelques millisecondes sous 1/R parce que
        l'horodatage est pris après le réveil, pas à l'instant planifié (mesuré :
        6,6 % des intervalles, le pire à -5 %, pour une avance réelle nulle) ;
      - le comptage sur fenêtre glissante d'une seconde vaut R+1 dès que l'espacement
        est exactement 1/R.
    """
    stamps = sorted(
        json.loads(line)["ts"] for line in log_path.read_text().splitlines() if line.strip()
    )
    if len(stamps) < 2:
        return {
            "requests": len(stamps),
            "max_advance": 0.0,
            "min_interval": None,
            "max_in_window": len(stamps),
            "mean_rate": None,
        }
    t0 = stamps[0]
    max_advance = max(i - rate * (t - t0) for i, t in enumerate(stamps))
    intervals = [b - a for a, b in zip(stamps[:-1], stamps[1:], strict=True)]
    worst, start = 0, 0
    for end in range(len(stamps)):
        while stamps[end] - stamps[start] >= 1.0:
            start += 1
        worst = max(worst, end - start + 1)
    return {
        "requests": len(stamps),
        "max_advance": max_advance,
        "min_interval": min(intervals),
        "max_in_window": worst,
        "mean_rate": (len(stamps) - 1) / (stamps[-1] - t0),
    }
