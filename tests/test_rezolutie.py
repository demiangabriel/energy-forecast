"""Rezoluția temporală: trecerea de la pasul orar al modelelor meteo la pasul
de 15 minute al decontării."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.rezolutie import _index_fin, energie_din_putere, reesantioneaza_meteo
from tests.helpers import parc_pv


def _meteo_orar(ore: int = 48) -> tuple[pd.DataFrame, pd.DataFrame]:
    index = pd.date_range("2026-06-21 00:00", periods=ore, freq="h", tz="UTC")
    from pvlib.location import Location
    cs = Location(44.43, 26.10, altitude=80, tz="UTC").get_clearsky(
        index + pd.Timedelta(minutes=30), model="ineichen")
    cs.index = index
    medie = pd.DataFrame({
        "shortwave_radiation": cs["ghi"] * 0.8,
        "direct_normal_irradiance": cs["dni"] * 0.7,
        "diffuse_radiation": cs["dhi"],
        "temperature_2m": np.linspace(10, 25, ore),
        "wind_speed_10m": 3.0, "wind_speed_100m": 5.0, "surface_pressure": 1013.0,
        "precipitation": 1.0,
    }, index=index)
    dispersie = pd.DataFrame({"shortwave_radiation": 20.0, "wind_speed_10m": 0.4}, index=index)
    return medie, dispersie


def test_indexul_fin_pastreaza_conventia_de_inceput_de_interval():
    """R-512: eticheta e ÎNCEPUTUL intervalului. O oră etichetată 12:00 acoperă
    12:00-13:00, deci la 15 minute devine 12:00, 12:15, 12:30, 12:45."""
    orar = pd.date_range("2026-06-21 12:00", periods=2, freq="h", tz="UTC")
    fin = _index_fin(orar, 15)
    assert str(fin[0]) == "2026-06-21 12:00:00+00:00"
    assert str(fin[3]) == "2026-06-21 12:45:00+00:00"
    assert str(fin[-1]) == "2026-06-21 13:45:00+00:00"
    assert len(fin) == 8


def test_reesantionarea_produce_patru_valori_pe_ora():
    parc = parc_pv()
    medie, dispersie = _meteo_orar(24)
    fina, disp_fina = reesantioneaza_meteo(medie, dispersie, parc, 15)
    assert len(fina) == 24 * 4
    assert len(disp_fina) == len(fina)


def test_radiatia_se_interpoleaza_prin_indicele_de_senin_nu_liniar():
    """Verificarea cheie: în jurul răsăritului, GHI-ul crește puternic
    neliniar. Interpolarea liniară pe valorile brute deplasează energia;
    interpolarea indicelui de senin urmează geometria solară reală.

    Testăm că valorile fine urmează forma cerului senin (raport aproape
    constant), nu o dreaptă între valorile orare."""
    parc = parc_pv()
    medie, dispersie = _meteo_orar(24)
    fina, _ = reesantioneaza_meteo(medie, dispersie, parc, 15)

    from app.core.clearsky import irradianta_cer_senin
    cs_fin = irradianta_cer_senin(fina.index, parc.latitudine, parc.longitudine, parc.altitudine_m)
    zi = cs_fin["ghi"] > 50
    raport = (fina.loc[zi, "shortwave_radiation"] / cs_fin.loc[zi, "ghi"])
    assert raport.std() < 0.05, "indicele de senin trebuie să rămână aproape constant (era 0,8)"
    assert raport.mean() == pytest.approx(0.8, abs=0.05)


def test_energia_totala_se_pastreaza_aproximativ():
    """Reeșantionarea nu trebuie să creeze sau să distrugă energie."""
    parc = parc_pv()
    medie, dispersie = _meteo_orar(48)
    fina, _ = reesantioneaza_meteo(medie, dispersie, parc, 15)
    energie_orara = medie["shortwave_radiation"].sum()          # Wh/m2 la pas orar
    energie_fina = fina["shortwave_radiation"].sum() / 4        # 4 pași pe oră
    assert energie_fina == pytest.approx(energie_orara, rel=0.03)


def test_variabilele_cumulative_se_impart_pe_subintervale():
    """Precipitațiile sunt o cantitate acumulată pe interval: 1 mm/oră
    înseamnă 0,25 mm pe fiecare sfert de oră, nu 1 mm pe fiecare."""
    parc = parc_pv()
    medie, dispersie = _meteo_orar(24)
    fina, _ = reesantioneaza_meteo(medie, dispersie, parc, 15)
    assert fina["precipitatii_mm"].sum() if "precipitatii_mm" in fina.columns else True
    assert fina["precipitation"].iloc[10] == pytest.approx(0.25)
    assert fina["precipitation"].sum() == pytest.approx(medie["precipitation"].sum(), rel=0.05)


def test_temperatura_se_interpoleaza_neted():
    parc = parc_pv()
    medie, dispersie = _meteo_orar(24)
    fina, _ = reesantioneaza_meteo(medie, dispersie, parc, 15)
    diferente = fina["temperature_2m"].diff().dropna()
    assert diferente.max() < medie["temperature_2m"].diff().dropna().max() + 1e-9


def test_pasul_orar_nu_schimba_nimic():
    parc = parc_pv()
    medie, dispersie = _meteo_orar(12)
    fina, disp = reesantioneaza_meteo(medie, dispersie, parc, 60)
    assert fina is medie and disp is dispersie


def test_conversia_putere_energie():
    """Distincția kW vs kWh e invizibilă la pas orar (sunt egale numeric) și
    devine o eroare de 4× la 15 minute."""
    putere = pd.Series([100.0, 100.0, 100.0, 100.0])
    assert energie_din_putere(putere, 60).sum() == pytest.approx(400.0)
    assert energie_din_putere(putere, 15).sum() == pytest.approx(100.0)
