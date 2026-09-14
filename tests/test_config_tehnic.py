"""Configurarea completă a parcului din zona tehnică (`/tehnic/parcuri/{id}`,
R-305) — editare rapidă a înclinării/azimutului și toleranța la virgulă
zecimală, cauza cea mai probabilă a raportului „nu mai pot modifica
înclinarea și azimutul” (un `float()` strict respingea „30,5”, iar salvarea
eșua în întregime, silențios din perspectiva operatorului)."""
from __future__ import annotations

import pytest

from app.web.routes_tehnic import (_num, _parseaza_subansambluri,
                                    _serializeaza_subansambluri)
from app.models import Subansamblu, TipAsezarePV
from tests.helpers import parc_pv


def _form_minim(parc, **overrides) -> dict:
    """Corpul minim necesar pentru un POST valid pe `/tehnic/parcuri/{id}` —
    câmpurile omise cad pe valoarea curentă a parcului (`f()`/`fl()` din
    routes_tehnic.py), deci testele pot trimite doar ce le interesează."""
    de_baza = {
        "cod": parc.cod, "nume": parc.nume, "client_id": parc.client_id,
        "latitudine": str(parc.latitudine), "longitudine": str(parc.longitudine),
        "fus_orar": parc.fus_orar, "activ": "on",
        "putere_dc_kwp": str(parc.config_pv.putere_dc_kwp),
        "putere_ac_kw": str(parc.config_pv.putere_ac_kw),
        "subansambluri_text": _serializeaza_subansambluri(parc.config_pv.subansambluri),
    }
    de_baza.update(overrides)
    return de_baza


def test_num_accepta_virgula_zecimala():
    assert _num("30,5") == pytest.approx(30.5)
    assert _num(" 180,25 ") == pytest.approx(180.25)
    assert _num("12.5") == pytest.approx(12.5)


def test_serializare_subansambluri_pastreaza_inclinarea_zero():
    """Bug latent: `s.inclinare_grade or ''` trata 0° (montaj perfect plat,
    valoare fizic validă) ca „gol” din cauza felului în care Python
    evaluează 0 ca falsy — la fiecare re-afișare/salvare, un parc plat își
    pierdea silențios înclinarea (devenea None)."""
    subs = [Subansamblu(nume="Plat", putere_dc_kwp=100.0, inclinare_grade=0.0,
                         azimut_grade=0.0, tip_asezare=TipAsezarePV.FIX)]
    text = _serializeaza_subansambluri(subs)
    reparsate = _parseaza_subansambluri(text)
    assert reparsate[0].inclinare_grade == 0.0
    assert reparsate[0].azimut_grade == 0.0


def test_parseaza_subansambluri_accepta_virgula_zecimala():
    rezultat = _parseaza_subansambluri("Principal;500;30,5;180,25;fixed")
    assert rezultat[0].inclinare_grade == pytest.approx(30.5)
    assert rezultat[0].azimut_grade == pytest.approx(180.25)


def test_ruta_salveaza_accepta_virgula_zecimala_la_inclinare_azimut(client, repo):
    """Reproduce exact scenariul raportat: operatorul scrie „30,5” cu virgulă
    (tastatură românească) — înainte de fix, acest POST ar fi eșuat cu
    ValueError, prins de try/except, și nimic nu s-ar fi salvat."""
    parc = parc_pv(cod="CFG-1")
    repo.salveaza_parc(parc)

    r = client.post(f"/tehnic/parcuri/{parc.id}",
                     data=_form_minim(parc, inclinare_grade="30,5", azimut_grade="195,25"),
                     follow_redirects=False)
    assert r.status_code == 303
    assert "eroare" not in r.headers.get("location", "")

    reincarcat = repo.get_parc(parc.id)
    assert reincarcat.config_pv.subansambluri[0].inclinare_grade == pytest.approx(30.5)
    assert reincarcat.config_pv.subansambluri[0].azimut_grade == pytest.approx(195.25)


def test_ruta_salveaza_actualizeaza_inclinarea_azimutul_prin_campurile_rapide(client, repo):
    parc = parc_pv(cod="CFG-2")
    repo.salveaza_parc(parc)
    assert parc.config_pv.subansambluri[0].inclinare_grade == 30.0

    r = client.post(f"/tehnic/parcuri/{parc.id}",
                     data=_form_minim(parc, inclinare_grade="12", azimut_grade="210"),
                     follow_redirects=False)
    assert r.status_code == 303

    reincarcat = repo.get_parc(parc.id)
    assert reincarcat.config_pv.subansambluri[0].inclinare_grade == 12.0
    assert reincarcat.config_pv.subansambluri[0].azimut_grade == 210.0
    # celelalte câmpuri ale subansamblului (nume, putere) rămân neatinse
    assert reincarcat.config_pv.subansambluri[0].nume == "Principal"


def test_pagina_config_afiseaza_campurile_rapide_doar_pentru_un_singur_subansamblu(client, repo):
    parc_simplu = parc_pv(cod="CFG-3")
    repo.salveaza_parc(parc_simplu)
    r = client.get(f"/tehnic/parcuri/{parc_simplu.id}")
    assert r.status_code == 200
    assert 'name="inclinare_grade"' in r.text
    assert 'name="azimut_grade"' in r.text

    parc_multiplu = parc_pv(cod="CFG-4")
    parc_multiplu.config_pv.subansambluri = [
        Subansamblu(nume="Est", putere_dc_kwp=50.0, inclinare_grade=20.0, azimut_grade=90.0,
                    tip_asezare=TipAsezarePV.EST_VEST),
        Subansamblu(nume="Vest", putere_dc_kwp=50.0, inclinare_grade=20.0, azimut_grade=270.0,
                    tip_asezare=TipAsezarePV.EST_VEST),
    ]
    repo.salveaza_parc(parc_multiplu)
    r2 = client.get(f"/tehnic/parcuri/{parc_multiplu.id}")
    assert r2.status_code == 200
    assert 'name="inclinare_grade"' not in r2.text
    assert 'name="azimut_grade"' not in r2.text
