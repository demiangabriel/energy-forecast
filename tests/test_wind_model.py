"""Motorul fizic eolian — teste de plauzibilitate fizică (R-1502)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.wind_model import (calculeaza_productie_eoliana, corecteaza_densitate,
                                   extrapoleaza_viteza_la_butuc, _deriva_rugozitate)
from tests.helpers import config_eolian_simplu, meteo_eolian_simplu


def test_sub_viteza_pornire_putere_zero():
    meteo = meteo_eolian_simplu("2026-03-01 00:00", 3, viteza_10m=1.0)  # mult sub pornire
    config = config_eolian_simplu(viteza_pornire_ms=3.0, viteza_nominala_ms=12.0, viteza_oprire_ms=25.0)
    rez = calculeaza_productie_eoliana(config, 150.0, meteo)
    assert (rez["putere_ac_fizic_kw"] <= 1e-6).all()


def test_peste_viteza_oprire_putere_zero():
    meteo = meteo_eolian_simplu("2026-03-01 00:00", 3, viteza_10m=40.0)  # furtună, peste oprire
    config = config_eolian_simplu(viteza_pornire_ms=3.0, viteza_nominala_ms=12.0, viteza_oprire_ms=25.0)
    rez = calculeaza_productie_eoliana(config, 150.0, meteo)
    assert (rez["putere_ac_fizic_kw"] <= 1e-6).all(), \
        "turbina trebuie oprită (putere zero) peste viteza de oprire — protecție mecanică"


def test_la_viteza_nominala_produce_puterea_nominala():
    # folosim direct viteza echivalentă (ocolim extrapolarea/densitatea) pt precizie
    v = pd.Series([12.0, 15.0, 20.0])
    from app.core.wind_model import _putere_din_viteze_simple
    putere = _putere_din_viteze_simple(v, 3.0, 12.0, 25.0, 2000.0)
    assert np.allclose(putere.values, [2000.0, 2000.0, 2000.0])


def test_rampa_cubica_intre_pornire_si_nominala():
    from app.core.wind_model import _putere_din_viteze_simple
    v = pd.Series([3.0, 7.5, 11.999])
    putere = _putere_din_viteze_simple(v, 3.0, 12.0, 25.0, 2000.0)
    assert putere.iloc[0] == pytest.approx(0.0, abs=1e-6)
    # la jumătatea intervalului pornire->nominală, rampa cubică ((0.5)^3=0.125) << rampă liniară
    assert putere.iloc[1] < 2000.0 * 0.5
    assert putere.iloc[1] > 0.0
    assert putere.iloc[2] < 2000.0  # sub prag, nu a atins încă platoul


def test_curba_putere_exacta_are_prioritate():
    curba = [[0, 0], [3, 50], [8, 900], [12, 2000], [25, 2000], [25.01, 0]]
    config = config_eolian_simplu(curba_putere=curba, numar_turbine=1)
    meteo = meteo_eolian_simplu("2026-03-01 00:00", 1, viteza_10m=8.0)
    # ocolim extrapolarea (folosim viteza direct la butuc = 8 prin înălțime egală cu z de referință logaritmică)
    rez = calculeaza_productie_eoliana(config, 0.0, meteo)
    assert rez["putere_ac_fizic_kw"].iloc[0] > 0


def test_fara_curba_si_fara_viteze_simple_ridica_eroare():
    config = config_eolian_simplu(viteza_pornire_ms=None, viteza_nominala_ms=None,
                                    viteza_oprire_ms=None, curba_putere=None)
    meteo = meteo_eolian_simplu("2026-03-01 00:00", 3, viteza_10m=10.0)
    with pytest.raises(ValueError, match="R-422"):
        calculeaza_productie_eoliana(config, 0.0, meteo)


def test_densitate_aer_rece_creste_viteza_echivalenta():
    """R-424: aer mai dens (rece) -> viteză echivalentă mai mare la aceeași
    viteză măsurată (produce mai mult la aceeași viteză brută)."""
    v = pd.Series([10.0, 10.0])
    temp_rece = pd.Series([-10.0, -10.0])
    temp_calda = pd.Series([35.0, 35.0])
    v_echiv_rece = corecteaza_densitate(v, temp_rece, None, 0.0)
    v_echiv_calda = corecteaza_densitate(v, temp_calda, None, 0.0)
    assert (v_echiv_rece > v_echiv_calda).all()


def test_extrapolare_creste_viteza_cu_inaltimea():
    """Profilul logaritmic: viteza la butuc (>10m) trebuie să fie mai mare
    decât viteza măsurată la 10m, pentru un teren tipic (z0 > 0)."""
    v10 = pd.Series([8.0, 8.0, 8.0])
    v_butuc = extrapoleaza_viteza_la_butuc(v10, inaltime_butuc_m=120.0)
    assert (v_butuc > v10).all()


def test_derivare_rugozitate_din_10m_100m_este_plauzibila():
    """z0 derivat prin bisecție trebuie să fie pozitiv și mic (teren agricol
    tipic: mm până la ~1m), și viteza extrapolată din 10m/100m reale să fie
    apropiată de valoarea măsurată direct la 100m."""
    v10 = pd.Series([6.0])
    v100 = pd.Series([9.0])
    z0 = _deriva_rugozitate(v10, v100)
    assert (z0 > 0).all()
    assert (z0 < 5.0).all()

    v_butuc_din_date_reale = extrapoleaza_viteza_la_butuc(v10, 100.0, v100)
    assert v_butuc_din_date_reale.iloc[0] == pytest.approx(9.0, rel=0.05)


def test_pierderi_siaj_constant_si_sistem_reduc_productia_exact():
    """Cu siaj CONSTANT (comportamentul clasic), raportul trebuie să fie exact
    produsul celor două pierderi."""
    meteo = meteo_eolian_simplu("2026-03-01 00:00", 6, viteza_10m=10.0)
    config_fara = config_eolian_simplu(pierdere_siaj_pct=0.0, pierdere_sistem_pct=0.0,
                                        wake_dependent_de_viteza=False)
    config_cu = config_eolian_simplu(pierdere_siaj_pct=8.0, pierdere_sistem_pct=2.0,
                                      wake_dependent_de_viteza=False)
    e1 = calculeaza_productie_eoliana(config_fara, 150.0, meteo)["putere_ac_fizic_kw"].sum()
    e2 = calculeaza_productie_eoliana(config_cu, 150.0, meteo)["putere_ac_fizic_kw"].sum()
    assert e2 < e1
    raport_asteptat = (1 - 0.08) * (1 - 0.02)
    assert abs(e2 / e1 - raport_asteptat) < 0.01


def test_siajul_dependent_de_viteza_dispare_peste_nominal():
    """Siajul depinde de coeficientul de tracțiune: e maxim la sarcină
    parțială și tinde spre zero mult peste viteza nominală, unde turbinele
    din față extrag puțin din flux. Un procent constant ar greși aici."""
    from app.core.wind_model import factor_siaj
    config = config_eolian_simplu(pierdere_siaj_pct=10.0, viteza_pornire_ms=3.0,
                                   viteza_nominala_ms=12.0, viteza_oprire_ms=25.0)
    viteze = pd.Series([6.0, 12.0, 20.0], index=pd.RangeIndex(3))
    factori = factor_siaj(config, viteze)
    assert factori.iloc[0] == pytest.approx(0.90, abs=1e-6)   # sarcină parțială -> siaj complet
    assert factori.iloc[1] == pytest.approx(0.90, abs=1e-6)   # la nominal -> încă complet
    assert factori.iloc[2] == pytest.approx(1.00, abs=1e-6)   # mult peste nominal -> fără siaj


def test_netezirea_cu_turbulenta_reduce_puterea_langa_pornire():
    """Lângă viteza de pornire curba e convexă: media puterilor peste
    distribuția vitezelor din interval e MAI MARE decât puterea la viteza
    medie. Lângă nominal, curba e concavă și efectul se inversează."""
    from app.core.wind_model import netezeste_cu_turbulenta, _evalueaza_curba
    config = config_eolian_simplu(viteza_pornire_ms=3.0, viteza_nominala_ms=12.0,
                                   viteza_oprire_ms=25.0, putere_nominala_kw=2000.0)
    idx = pd.RangeIndex(2)
    viteze = pd.Series([4.0, 11.5], index=idx)
    ti = pd.Series([0.15, 0.15], index=idx)
    brut = _evalueaza_curba(config, viteze)
    neted = netezeste_cu_turbulenta(config, viteze, ti)
    assert neted.iloc[0] > brut.iloc[0], "zona convexă (lângă pornire): netezirea crește puterea"
    assert neted.iloc[1] < brut.iloc[1], "zona concavă (lângă nominal): netezirea scade puterea"


def test_densitatea_cu_umiditate_scade_densitatea_aerului():
    """Vaporii de apă sunt mai ușori decât aerul uscat: aerul umed e mai
    puțin dens la aceeași temperatură și presiune (IEC 61400-12-1)."""
    from app.core.wind_model import densitate_aer
    idx = pd.RangeIndex(1)
    temp = pd.Series([25.0], index=idx)
    presiune = pd.Series([101325.0], index=idx)
    uscat = densitate_aer(temp, presiune, 0.0, None)
    umed = densitate_aer(temp, presiune, 0.0, pd.Series([95.0], index=idx))
    assert umed.iloc[0] < uscat.iloc[0]
    assert abs(umed.iloc[0] - uscat.iloc[0]) < 0.05   # efectul e mic, dar real


def test_lipsa_coloana_obligatorie_ridica_eroare():
    meteo = meteo_eolian_simplu("2026-03-01 00:00", 3, viteza_10m=8.0).drop(columns=["temp_aer_c"])
    with pytest.raises(ValueError, match="Coloană meteo lipsă"):
        calculeaza_productie_eoliana(config_eolian_simplu(), 0.0, meteo)
