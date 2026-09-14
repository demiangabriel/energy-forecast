"""Controale de calitate (R-1503) — exactitatea celor 5 verificări și
distincția blocant/avertisment."""
from __future__ import annotations

import pandas as pd
import pytest

from app.core.quality_gates import evalueaza_calitate
from app.models import Tehnologie

IDX = pd.date_range("2026-06-01 00:00", periods=6, freq="h", tz="UTC")


def _serie(*valori):
    assert len(valori) == len(IDX)
    return pd.Series(valori, index=IDX, dtype=float)


def test_putere_negativa_e_blocanta():
    putere = _serie(-5, 10, 20, 30, 40, 50)
    rez = evalueaza_calitate(putere_finala_kw=putere, putere_instalata_kw=1000, tehnologie=Tehnologie.EOLIAN)
    assert rez.blocat[IDX[0]] is True
    assert any("negativă" in m for m in rez.avertismente[IDX[0]])
    assert all(not rez.blocat[ts] for ts in IDX[1:])


def test_putere_peste_instalata_e_blocanta():
    putere = _serie(10, 10, 10, 10, 10, 2000)  # peste 1000 kW instalat, cu marjă de 2%
    rez = evalueaza_calitate(putere_finala_kw=putere, putere_instalata_kw=1000, tehnologie=Tehnologie.EOLIAN)
    assert rez.blocat[IDX[-1]] is True
    assert all(not rez.blocat[ts] for ts in IDX[:-1])


def test_marja_2pct_peste_instalata_nu_blocheaza():
    putere = _serie(10, 10, 10, 10, 10, 1015)  # 1015 < 1000*1.02 = 1020, zgomot numeric acceptat
    rez = evalueaza_calitate(putere_finala_kw=putere, putere_instalata_kw=1000, tehnologie=Tehnologie.EOLIAN)
    assert not any(rez.blocat.values())


def test_productie_semnificativa_noaptea_pv_e_blocanta():
    putere = _serie(50, 0, 0, 0, 0, 0)  # producție "diurnă" la un index marcat NOAPTE
    zenit = _serie(150, 10, 10, 10, 10, 10)  # >= 95 => noapte la primul timestamp
    rez = evalueaza_calitate(putere_finala_kw=putere, putere_instalata_kw=1000,
                              tehnologie=Tehnologie.FOTOVOLTAIC, solar_zenith_grade=zenit)
    assert rez.blocat[IDX[0]] is True


def test_productie_mica_noaptea_sub_prag_nu_blocheaza():
    putere = _serie(5, 0, 0, 0, 0, 0)  # 0.5% din 1000 kW, sub pragul implicit de 1%
    zenit = _serie(150, 10, 10, 10, 10, 10)
    rez = evalueaza_calitate(putere_finala_kw=putere, putere_instalata_kw=1000,
                              tehnologie=Tehnologie.FOTOVOLTAIC, solar_zenith_grade=zenit)
    assert rez.blocat[IDX[0]] is False


def test_noaptea_nu_se_verifica_la_eolian():
    """Verificarea de noapte se aplică DOAR fotovoltaicului (are sens doar
    acolo) — la eolian nu ar trebui aplicată deloc, indiferent de zenit."""
    putere = _serie(500, 0, 0, 0, 0, 0)
    zenit = _serie(150, 10, 10, 10, 10, 10)
    rez = evalueaza_calitate(putere_finala_kw=putere, putere_instalata_kw=1000,
                              tehnologie=Tehnologie.EOLIAN, solar_zenith_grade=zenit)
    assert rez.blocat[IDX[0]] is False


def test_serie_meteo_constanta_genereaza_avertisment_nu_blocant():
    putere = _serie(10, 20, 30, 40, 50, 60)
    serie_constanta = _serie(500, 500, 500, 500, 500, 500)  # 6 valori identice consecutiv
    rez = evalueaza_calitate(putere_finala_kw=putere, putere_instalata_kw=1000,
                              tehnologie=Tehnologie.EOLIAN, serie_meteo_pt_constanta=serie_constanta,
                              praguri={"prag_serie_constanta_ore": 6})
    assert not any(rez.blocat.values()), "seria constantă trebuie să fie doar avertisment, niciodată blocantă"
    assert any("constantă" in m for m in rez.avertismente[IDX[-1]])


def test_serie_meteo_variabila_nu_genereaza_avertisment_de_constanta():
    putere = _serie(10, 20, 30, 40, 50, 60)
    serie_variabila = _serie(100, 200, 150, 300, 250, 400)
    rez = evalueaza_calitate(putere_finala_kw=putere, putere_instalata_kw=1000,
                              tehnologie=Tehnologie.EOLIAN, serie_meteo_pt_constanta=serie_variabila,
                              praguri={"prag_serie_constanta_ore": 6})
    assert all(not m for m in rez.avertismente.values())


def test_salt_brusc_fata_de_rularea_precedenta_e_avertisment():
    putere = _serie(100, 100, 100, 100, 100, 100)
    precedenta = _serie(20, 20, 20, 20, 20, 20)  # salt de 400% pe fiecare oră
    rez = evalueaza_calitate(putere_finala_kw=putere, putere_instalata_kw=1000, tehnologie=Tehnologie.EOLIAN,
                              putere_rulare_precedenta_kw=precedenta, praguri={"prag_salt_brusc_pct": 60.0})
    assert not any(rez.blocat.values())
    assert all(any("Salt" in m for m in rez.avertismente[ts]) for ts in IDX)


def test_salt_mic_sub_prag_nu_genereaza_avertisment():
    putere = _serie(100, 100, 100, 100, 100, 100)
    precedenta = _serie(90, 90, 90, 90, 90, 90)  # ~10% variație, sub pragul de 60%
    rez = evalueaza_calitate(putere_finala_kw=putere, putere_instalata_kw=1000, tehnologie=Tehnologie.EOLIAN,
                              putere_rulare_precedenta_kw=precedenta, praguri={"prag_salt_brusc_pct": 60.0})
    assert all(not m for m in rez.avertismente.values())


def test_salt_pe_valori_mici_ignorat_ca_zgomot():
    """Variații mari în TERMENI PROCENTUALI dar mici în valoare absolută
    (sub 3% din puterea instalată) trebuie ignorate — altfel apar
    avertismente false pe ore cu producție aproape nulă."""
    putere = _serie(2, 2, 2, 2, 2, 2)
    precedenta = _serie(0.1, 0.1, 0.1, 0.1, 0.1, 0.1)  # variație "infinită" procentual, dar 1.9 kW absolut
    rez = evalueaza_calitate(putere_finala_kw=putere, putere_instalata_kw=1000, tehnologie=Tehnologie.EOLIAN,
                              putere_rulare_precedenta_kw=precedenta)
    assert all(not m for m in rez.avertismente.values())


def test_energie_zilnica_implauzibila_pv_e_avertisment():
    """Prag CF zilnic PV implicit 32%: 1000 kW instalat * 24h * 32% = 7680
    kWh/zi plafon. Forțăm o zi întreagă (24h) cu o putere orară sub pragul
    de 'peste puterea instalată' (deci nu blocată de check-ul #1), dar cu
    energia zilnică totală peste plafonul de plauzibilitate."""
    idx_zi = pd.date_range("2026-06-01 00:00", periods=24, freq="h", tz="UTC")
    putere = pd.Series([500.0] * 24, index=idx_zi)  # 500 kW/h < 1020 kW (prag blocant) dar 12000 kWh/zi > 7680
    rez = evalueaza_calitate(putere_finala_kw=putere, putere_instalata_kw=1000,
                              tehnologie=Tehnologie.FOTOVOLTAIC, praguri={"prag_cf_zilnic_max_pv_pct": 32.0})
    assert not any(rez.blocat.values())
    assert all(any("implauzibilă" in m for m in rez.avertismente[ts]) for ts in idx_zi)


def test_energie_zilnica_sub_plafon_nu_genereaza_avertisment():
    idx_zi = pd.date_range("2026-06-01 00:00", periods=24, freq="h", tz="UTC")
    putere = pd.Series([100.0] * 24, index=idx_zi)  # 2400 kWh, mult sub plafon
    rez = evalueaza_calitate(putere_finala_kw=putere, putere_instalata_kw=1000,
                              tehnologie=Tehnologie.FOTOVOLTAIC, praguri={"prag_cf_zilnic_max_pv_pct": 32.0})
    assert all(not any("implauzibilă" in m for m in msgs) for msgs in rez.avertismente.values())


def test_plafon_cf_diferit_pentru_eolian():
    """Plafonul de 95% pt. eolian e mult mai permisiv decât cel PV (32%) —
    aceeași încărcare care ar declanșa avertisment la un parc PV trebuie să
    treacă nesemnalată la un parc eolian echivalent ca mărime."""
    putere = pd.Series([500.0] * 6, index=IDX)  # 3000 kWh/zi din 1000 kW instalat -> 12.5% CF pe fereastra de 6h
    rez_eolian = evalueaza_calitate(putere_finala_kw=putere, putere_instalata_kw=1000,
                                     tehnologie=Tehnologie.EOLIAN,
                                     praguri={"prag_cf_zilnic_max_eolian_pct": 95.0})
    assert all(not any("implauzibilă" in m for m in msgs) for msgs in rez_eolian.avertismente.values())


def test_serie_constanta_noaptea_pv_nu_genereaza_avertisment_fals():
    """Bug real, găsit prin raportarea operatorului: GHI=0 constant pe multe
    ore consecutive de NOAPTE e fizică normală, nu o sursă meteo blocată —
    fără excluderea nopții (zenit >= 95°, ca la verificarea #2), avertismentul
    de „serie constantă” se declanșa practic în fiecare noapte, la orice
    prognoză PV."""
    idx = pd.date_range("2026-06-01 00:00", periods=24, freq="h", tz="UTC")
    # noapte (zenit mare) pe primele 8 și ultimele 6 ore, zi la mijloc
    zenit = pd.Series([110.0] * 8 + [30.0] * 10 + [110.0] * 6, index=idx)
    # GHI = 0 constant toată noaptea (16 ore consecutive de 0, mult peste
    # pragul implicit de 6) — variabil ziua, ca să nu declanșeze din altă cauză
    ghi = pd.Series([0.0] * 8 + [100, 250, 400, 550, 600, 550, 400, 250, 100, 0.0] + [0.0] * 6, index=idx)
    putere = pd.Series([0.0] * 8 + [10, 40, 90, 150, 180, 150, 90, 40, 10, 0.0] + [0.0] * 6, index=idx)
    rez = evalueaza_calitate(putere_finala_kw=putere, putere_instalata_kw=1000,
                              tehnologie=Tehnologie.FOTOVOLTAIC, solar_zenith_grade=zenit,
                              serie_meteo_pt_constanta=ghi, praguri={"prag_serie_constanta_ore": 6})
    assert not any("constantă" in m for msgs in rez.avertismente.values() for m in msgs), \
        f"constanța nopții (GHI=0) nu ar trebui semnalată: {rez.avertismente}"


def test_serie_constanta_ziua_pv_tot_genereaza_avertisment():
    """Regresie pentru fix-ul de mai sus: o serie CU ADEVĂRAT blocată în
    timpul ZILEI (zenit mic) trebuie să rămână semnalată — excluderea se
    aplică STRICT orelor de noapte, nu suprimă verificarea în general."""
    idx = pd.date_range("2026-06-01 06:00", periods=8, freq="h", tz="UTC")
    zenit = pd.Series([30.0] * 8, index=idx)  # toate orele sunt zi
    ghi = pd.Series([500.0] * 8, index=idx)   # blocat la aceeași valoare 8 ore
    putere = pd.Series([10, 20, 30, 40, 50, 60, 70, 80], index=idx, dtype=float)
    rez = evalueaza_calitate(putere_finala_kw=putere, putere_instalata_kw=1000,
                              tehnologie=Tehnologie.FOTOVOLTAIC, solar_zenith_grade=zenit,
                              serie_meteo_pt_constanta=ghi, praguri={"prag_serie_constanta_ore": 6})
    assert any("constantă" in m for msgs in rez.avertismente.values() for m in msgs)


def test_bloc_nu_suprima_avertismentele_pe_alte_ore():
    """O oră blocantă nu trebuie să afecteze evaluarea celorlalte ore — fiecare
    e independentă (esențial pt. logica de 'succes parțial', R-1503/R-1505)."""
    putere = _serie(-1, 100, 100, 100, 100, 100)
    precedenta = _serie(-1, 20, 20, 20, 20, 20)
    rez = evalueaza_calitate(putere_finala_kw=putere, putere_instalata_kw=1000, tehnologie=Tehnologie.EOLIAN,
                              putere_rulare_precedenta_kw=precedenta, praguri={"prag_salt_brusc_pct": 60.0})
    assert rez.blocat[IDX[0]] is True
    assert all(rez.blocat[ts] is False for ts in IDX[1:])
    assert all(any("Salt" in m for m in rez.avertismente[ts]) for ts in IDX[1:])
