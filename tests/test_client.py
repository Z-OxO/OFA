"""Le client est le seul chemin vers l'API : son débit et son backoff sont testés ici."""

import json

import pytest
import requests

from ofa.client import CircuitOpen, Client, RateLimiter, rate_report


def test_rate_limiter_espace_les_requetes(clock):
    limiter = RateLimiter(4.0, now=clock.now, sleep=clock.sleep)
    for _ in range(5):
        limiter.acquire()
    # La première part tout de suite, les suivantes sont espacées de 1/4 s.
    assert clock.sleeps == [pytest.approx(0.25)] * 4


def test_rate_limiter_ne_rattrape_pas_le_temps_perdu(clock):
    """Après une pause, on ne doit pas envoyer une rafale pour compenser."""
    limiter = RateLimiter(2.0, now=clock.now, sleep=clock.sleep)
    limiter.acquire()
    clock.t += 10.0  # long travail entre deux requêtes
    limiter.acquire()
    limiter.acquire()
    assert clock.sleeps == [pytest.approx(0.5)]  # une seule attente, pas de rafale


class FakeSession:
    """Rejoue une liste de réponses préparées."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.headers = {}
        self.asked = 0

    def get(self, url, timeout=None):
        self.asked += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply

        class R:
            status_code = reply[0]
            headers = reply[1]
            content = reply[2]

        return R()


def _client(cfg, clock, replies):
    session = FakeSession(replies)
    return Client(cfg, now=clock.now, sleep=clock.sleep, session_factory=lambda: session), session


def test_429_est_reessaye_en_respectant_retry_after(cfg, clock):
    client, session = _client(
        cfg, clock, [(429, {"Retry-After": "7"}, b""), (200, {}, b'{"ok":1}')]
    )
    resp = client.get("/games?x=1")
    assert resp.status == 200
    assert session.asked == 2
    assert 7.0 in clock.sleeps  # l'en-tête a été respecté, pas l'exponentielle


def test_403_est_reessaye_car_cloudflare_le_renvoie_sans_avertissement(cfg, clock):
    client, session = _client(cfg, clock, [(403, {}, b""), (200, {}, b"{}")])
    assert client.get("/games?x=1").status == 200
    assert session.asked == 2


def test_coupe_circuit_apres_erreurs_consecutives(cfg, clock):
    client, _ = _client(cfg, clock, [(503, {}, b"")] * 5)
    with pytest.raises(CircuitOpen):
        client.get("/games?x=1")


def test_404_est_definitif_et_ne_declenche_pas_de_reessai(cfg, clock):
    client, session = _client(cfg, clock, [(404, {}, b"not found")])
    resp = client.get("/game/inconnu")
    assert resp.status == 404
    assert session.asked == 1


def test_erreur_reseau_est_reessayee(cfg, clock):
    client, session = _client(cfg, clock, [requests.ConnectionError("coupure"), (200, {}, b"{}")])
    assert client.get("/games?x=1").status == 200
    assert session.asked == 2


def test_chaque_requete_est_journalisee(cfg, clock):
    client, _ = _client(cfg, clock, [(200, {}, b'{"a":1}')])
    client.get("/games?x=1")
    lines = (cfg.log_dir / "requests.jsonl").read_text().strip().splitlines()
    entry = json.loads(lines[0])
    assert entry["status"] == 200
    assert entry["bytes"] == 7
    assert "url" in entry and "seconds" in entry


def test_rate_report_detecte_une_vraie_rafale(tmp_path):
    """5 requetes en 40 ms a 4 req/s : l'avance sur l'echeancier doit accuser."""
    log = tmp_path / "requests.jsonl"
    stamps = [100.0, 100.01, 100.02, 100.03, 100.04, 200.0]
    log.write_text("".join(json.dumps({"ts": t}) + "\n" for t in stamps))
    r = rate_report(log, 4.0)
    assert r["max_advance"] > 3.0
    assert r["max_in_window"] == 5


def test_rate_report_tolere_la_gigue_de_lordonnanceur(tmp_path):
    """Cas reellement observe sur 21 545 requetes d'une vraie ingestion.

    6,6 % des intervalles descendent sous 1/R parce que l'horodatage est pris apres le
    reveil, le pire a -5 %. L'avance sur l'echeancier, elle, reste nulle : aucune rafale.
    Ni l'intervalle minimum ni le comptage sur fenetre ne savent faire cette difference.
    """
    import random

    rng = random.Random(7)
    log = tmp_path / "requests.jsonl"
    # Echeancier exact a 4 req/s, plus une gigue positive de 0 a 13 ms sur l'horodatage.
    stamps = [i * 0.25 + rng.uniform(0, 0.013) for i in range(2000)]
    log.write_text("".join(json.dumps({"ts": t}) + "\n" for t in stamps))
    r = rate_report(log, 4.0)
    assert r["min_interval"] < 0.25  # l'intervalle minimum accuse a tort
    assert r["max_in_window"] == 5  # la fenetre glissante aussi
    assert r["max_advance"] <= 1.0  # l'avance, non : c'est elle qui compte
