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
    log = tmp_path / "requests.jsonl"
    stamps = [100.0, 100.01, 100.02, 100.03, 100.04, 200.0]
    log.write_text("".join(json.dumps({"ts": t}) + "\n" for t in stamps))
    r = rate_report(log)
    assert r["requests"] == 6
    assert r["max_in_window"] == 5
    assert r["min_interval"] == pytest.approx(0.01)  # c'est ca qui accuse, pas la fenetre


def test_rate_report_ne_signale_pas_un_faux_depassement(tmp_path):
    """Espacement parfait a 4 req/s : l'intervalle minimum doit valoir 0,25 s.

    Piege trouve lors de la premiere vraie execution : avec un espacement exactement
    egal a 1/R, une gigue de 0,1 ms fait compter R+1 requetes dans une fenetre d'une
    seconde. La garantie reelle du limiteur est l'intervalle minimum, pas ce comptage.
    """
    log = tmp_path / "requests.jsonl"
    stamps = [i * 0.25 - (0.0001 if i % 3 else 0) for i in range(40)]
    log.write_text("".join(json.dumps({"ts": t}) + "\n" for t in stamps))
    r = rate_report(log)
    assert r["min_interval"] >= 0.2498
    assert r["mean_rate"] == pytest.approx(4.0, abs=0.01)
