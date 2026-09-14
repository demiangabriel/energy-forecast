"""Ajustarea pierderilor din pagina de calibrare.

Cerința operatorului, explicit: factorul de scalare NU trebuie să se dubleze
cu pierderile deja configurate. Soluția aleasă nu e o distribuire automată, ci
un dialog în care operatorul ajustează fiecare pierdere și vede totalul cumulat
în timp real, până ajunge la factorul țintă. Platforma nu scrie singură nimic —
deci nimic nu se poate dubla pe ascuns.
"""
from __future__ import annotations

import pytest

from tests.helpers import config_pv_simplu, parc_pv


def test_pierderile_se_compun_multiplicativ_nu_aditiv():
    """5% + 5% = 9,75% pierdere, nu 10%. Totalul afișat în dialog folosește
    aceeași formulă ca modelul fizic — altfel operatorul ar ținti greșit."""
    config = config_pv_simplu(pierdere_murdarire_pct=5.0, pierdere_neuniformitate_pct=5.0,
                               pierdere_cablaj_conexiuni_pct=0.0, pierdere_lid_pct=0.0,
                               pierdere_placuta_pct=0.0, pierdere_indisponibilitate_pct=0.0)
    assert config.factor_pierderi_sistem() == pytest.approx(0.9025)


def test_salvarea_scrie_exact_ce_a_introdus_operatorul(client, repo):
    parc = parc_pv(cod="PIER-1", config_pv=config_pv_simplu())
    repo.salveaza_parc(parc)

    r = client.post(f"/parcuri/{parc.id}/calibrare/pierderi", data={
        "pierdere_murdarire_pct": "3.5", "pierdere_indisponibilitate_pct": "2",
        "pierdere_nemodelata_pct": "4.5",
    }, follow_redirects=False)
    assert r.status_code == 303

    c = repo.get_parc(parc.id).config_pv
    assert c.pierdere_murdarire_pct == 3.5
    assert c.pierdere_indisponibilitate_pct == 2.0
    assert c.pierdere_nemodelata_pct == 4.5


def test_nimic_nu_se_scrie_automat_din_factorul_de_scalare(client, repo):
    """Garanția cerută: simpla vizitare a paginii de calibrare nu modifică
    nicio pierdere. Distribuirea rămâne o decizie a operatorului."""
    parc = parc_pv(cod="PIER-2", config_pv=config_pv_simplu(pierdere_murdarire_pct=2.0))
    repo.salveaza_parc(parc)
    inainte = repo.get_parc(parc.id).config_pv.factor_pierderi_sistem()

    client.get(f"/parcuri/{parc.id}/calibrare")

    assert repo.get_parc(parc.id).config_pv.factor_pierderi_sistem() == inainte


def test_campurile_omise_raman_neschimbate(client, repo):
    parc = parc_pv(cod="PIER-3", config_pv=config_pv_simplu(pierdere_lid_pct=1.25))
    repo.salveaza_parc(parc)

    client.post(f"/parcuri/{parc.id}/calibrare/pierderi",
                 data={"pierdere_murdarire_pct": "3"}, follow_redirects=False)

    assert repo.get_parc(parc.id).config_pv.pierdere_lid_pct == 1.25


def test_virgula_zecimala_e_acceptata(client, repo):
    parc = parc_pv(cod="PIER-4", config_pv=config_pv_simplu())
    repo.salveaza_parc(parc)

    client.post(f"/parcuri/{parc.id}/calibrare/pierderi",
                 data={"pierdere_murdarire_pct": "3,5"}, follow_redirects=False)

    assert repo.get_parc(parc.id).config_pv.pierdere_murdarire_pct == 3.5


def test_procent_imposibil_e_respins_fara_sa_strice_configurarea(client, repo):
    parc = parc_pv(cod="PIER-5", config_pv=config_pv_simplu(pierdere_murdarire_pct=2.0))
    repo.salveaza_parc(parc)

    r = client.post(f"/parcuri/{parc.id}/calibrare/pierderi",
                     data={"pierdere_murdarire_pct": "150"}, follow_redirects=False)
    assert r.status_code == 303
    assert "nu%20s-au%20putut" in r.headers["location"].lower() or \
           "nu s-au putut" in r.headers["location"].lower()
    assert repo.get_parc(parc.id).config_pv.pierdere_murdarire_pct == 2.0


def test_mesajul_de_confirmare_arata_totalul_cumulat(client, repo):
    parc = parc_pv(cod="PIER-6", config_pv=config_pv_simplu())
    repo.salveaza_parc(parc)

    r = client.post(f"/parcuri/{parc.id}/calibrare/pierderi",
                     data={"pierdere_murdarire_pct": "10"}, follow_redirects=False)
    assert "total%20cumulat" in r.headers["location"] or "total cumulat" in r.headers["location"]


def test_pierderea_ajustata_chiar_schimba_productia_calculata():
    """Un câmp salvat dar neaplicat în fizică ar fi mai rău decât inutil."""
    from app.core.pv_model import calculeaza_productie_pv
    from tests.helpers import meteo_pv_cer_senin

    meteo = meteo_pv_cer_senin("2026-06-21 00:00", 24, 44.43, 26.10, 80.0)
    fara = calculeaza_productie_pv(config_pv_simplu(), 44.43, 26.10, 80.0, meteo)
    cu = calculeaza_productie_pv(config_pv_simplu(pierdere_nemodelata_pct=10.0),
                                  44.43, 26.10, 80.0, meteo)
    assert cu["putere_ac_fizic_kw"].sum() == pytest.approx(
        fara["putere_ac_fizic_kw"].sum() * 0.9, rel=0.01)
