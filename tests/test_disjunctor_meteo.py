"""Disjunctorul sursei meteo (`weather_client.DISJUNCTOR`).

Motivația e măsurată, nu teoretică: cu 50 de parcuri în portofoliu și
Open-Meteo căzut, rularea zilnică încerca ~800 de cereri HTTP și aștepta
cumulat ~50 de minute înainte de a se opri — fiecare parc relua de la zero
scara de reîncercări pentru o sursă despre care se știa deja că nu răspunde.
"""
from __future__ import annotations

import httpx
import pytest

import app.core.weather_client as wc
from app.core.weather_client import (DISJUNCTOR, PRAG_ESECURI_CONSECUTIVE, EroareMeteo,
                                      EroareMeteoTemporara, obtine_prognoza_meteo)

MODELE = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"]


class _ClientCare(object):
    """Client HTTP fals care numără cererile și răspunde cum i se cere."""

    def __init__(self, contor: dict, raspuns=None, exceptie=None, status=None, corp=None):
        self._contor, self._raspuns = contor, raspuns
        self._exceptie, self._status, self._corp = exceptie, status, corp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None, timeout=None):
        self._contor["n"] += 1
        if self._exceptie is not None:
            raise self._exceptie
        return self._raspuns


@pytest.fixture
def fara_asteptare(monkeypatch):
    """Elimină pauzele reale dintre reîncercări, dar le contorizează."""
    total = {"s": 0.0}
    monkeypatch.setattr(wc.time, "sleep", lambda s: total.__setitem__("s", total["s"] + s))
    return total


def _forteaza_retea_picata(monkeypatch, contor):
    monkeypatch.setattr(wc.httpx, "Client",
                         lambda *a, **k: _ClientCare(contor, exceptie=httpx.ConnectError("picat")))


def test_dupa_prag_esecuri_cererile_urmatoare_nu_mai_ating_reteaua(monkeypatch, fara_asteptare):
    contor = {"n": 0}
    _forteaza_retea_picata(monkeypatch, contor)

    with pytest.raises(EroareMeteo):
        obtine_prognoza_meteo(44.0, 26.0, MODELE, 0, 3)
    cereri_primul_parc = contor["n"]
    assert cereri_primul_parc > 0
    assert DISJUNCTOR.e_deschis(), "disjunctorul trebuia să se deschidă după eșecuri repetate"

    # „următoarele parcuri din portofoliu": nicio cerere nouă, eșec imediat
    for _ in range(10):
        with pytest.raises(EroareMeteoTemporara):
            obtine_prognoza_meteo(45.0, 27.0, MODELE, 0, 3)
    assert contor["n"] == cereri_primul_parc, \
        "după deschiderea disjunctorului nu trebuie să mai plece nicio cerere HTTP"


def test_mesajul_explica_de_ce_nu_s_a_incercat(monkeypatch, fara_asteptare):
    contor = {"n": 0}
    _forteaza_retea_picata(monkeypatch, contor)
    with pytest.raises(EroareMeteo):
        obtine_prognoza_meteo(44.0, 26.0, MODELE, 0, 3)

    with pytest.raises(EroareMeteoTemporara) as exc:
        obtine_prognoza_meteo(44.0, 26.0, MODELE, 0, 3)
    text = str(exc.value)
    assert "indisponibilă" in text
    assert "se reia automat" in text, "operatorul trebuie să afle că nu e nevoie de intervenție manuală"


def test_un_succes_reseteaza_disjunctorul(monkeypatch, fara_asteptare):
    DISJUNCTOR.inregistreaza_esec("test")
    DISJUNCTOR.inregistreaza_esec("test")
    assert DISJUNCTOR.esecuri == PRAG_ESECURI_CONSECUTIVE - 1
    assert not DISJUNCTOR.e_deschis()

    DISJUNCTOR.inregistreaza_succes()
    assert DISJUNCTOR.esecuri == 0
    assert not DISJUNCTOR.e_deschis()


def test_racirea_expirata_permite_o_noua_incercare(monkeypatch):
    """După perioada de răcire, platforma trebuie să reîncerce singură — o
    pană de sursă nu poate opri definitiv prognozele până la repornire."""
    ceas = {"t": 1000.0}
    monkeypatch.setattr(wc.time, "monotonic", lambda: ceas["t"])
    for _ in range(PRAG_ESECURI_CONSECUTIVE):
        DISJUNCTOR.inregistreaza_esec("pană simulată")
    assert DISJUNCTOR.e_deschis()

    ceas["t"] += wc.RACIRE_DISJUNCTOR_SECUNDE + 1
    assert not DISJUNCTOR.e_deschis(), "răcirea expirată trebuie să redeschidă calea spre sursă"


def test_eroarea_permanenta_4xx_nu_declanseaza_disjunctorul(monkeypatch, fara_asteptare):
    """Un id de model greșit e o problemă de CONFIGURARE a unui parc anume —
    nu un semn că sursa e căzută. Dacă ar deschide disjunctorul, un singur parc
    prost configurat ar bloca prognozele întregului portofoliu."""
    contor = {"n": 0}

    class Raspuns400:
        status_code = 400

        @staticmethod
        def json():
            return {"reason": "Cannot initialize WeatherModel from invalid model id"}

    monkeypatch.setattr(wc.httpx, "Client",
                         lambda *a, **k: _ClientCare(contor, raspuns=Raspuns400()))

    for _ in range(5):
        with pytest.raises(EroareMeteo) as exc:
            obtine_prognoza_meteo(44.0, 26.0, ["model_inexistent"], 0, 3)
        assert not isinstance(exc.value, EroareMeteoTemporara)

    assert not DISJUNCTOR.e_deschis()
    assert contor["n"] == 5, "fiecare cerere permanentă trebuie chiar încercată, o singură dată"


def test_portofoliu_mare_cu_sursa_cazuta_se_opreste_repede(monkeypatch, fara_asteptare, repo):
    """Testul care justifică tot mecanismul: 50 de parcuri, sursă complet
    căzută. Fără disjunctor erau ~800 de cereri; cu el, sub câteva zeci."""
    from app.core.daily_run import ruleaza_portofoliu
    from tests.helpers import parc_pv

    contor = {"n": 0}
    _forteaza_retea_picata(monkeypatch, contor)

    for i in range(50):
        p = parc_pv(cod=f"DJ-{i:03d}", mod_umbra=False)
        p.surse_meteo = MODELE
        repo.salveaza_parc(p)

    rezultate = ruleaza_portofoliu(repo, tip="programata", trimite_email=False)
    assert len(rezultate) == 50
    assert all(r["stare"] == "esuata" for r in rezultate), "toate trebuie marcate clar ca eșuate"
    assert contor["n"] < 40, (
        f"prea multe cereri HTTP către o sursă căzută: {contor['n']} "
        f"(fără disjunctor ar fi ~800)")
    assert fara_asteptare["s"] < 120, (
        f"prea multă așteptare cumulată: {fara_asteptare['s']:.0f} s "
        f"(fără disjunctor ~3000 s)")
