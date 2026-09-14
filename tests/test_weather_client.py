"""Combinarea ansamblului meteo — inclusiv regresia pentru cea mai gravă
eroare tăcută găsită la audit (ponderi nerenormalizate)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.weather_client import (acoperire_per_model, asigura_inchidere_radiatie,
                                      combina_ansamblu)

IDX = pd.date_range("2026-06-01 00:00", periods=6, freq="h", tz="UTC")


def _model(valoare, index=IDX, coloana="shortwave_radiation") -> pd.DataFrame:
    return pd.DataFrame({coloana: pd.Series(valoare, index=index, dtype="float64")}, index=index)


def test_media_ponderata_simpla():
    pe_model = {"a": _model(100.0), "b": _model(200.0)}
    medie, _ = combina_ansamblu(pe_model)
    assert (medie["shortwave_radiation"] == 150.0).all()


def test_ponderi_configurate_sunt_respectate():
    pe_model = {"a": _model(100.0), "b": _model(200.0)}
    medie, _ = combina_ansamblu(pe_model, {"a": 3.0, "b": 1.0})
    assert medie["shortwave_radiation"].iloc[0] == pytest.approx(125.0)


def test_REGRESIE_model_lipsa_pe_o_ora_nu_scade_media():
    """BUG ISTORIC: când un model nu avea valoare la o oră, media ponderată
    era calculată ca sumă a contribuțiilor disponibile, dar cu ponderile
    inițiale — adică împărțită implicit la 1, nu la suma ponderilor prezente.
    Cu 3 modele egale și unul lipsă, rezultatul era cu ~33% prea mic.

    Cazul e frecvent în operare: modelele au orizonturi diferite (ICON ~7,5
    zile, GFS 16), deci la orizonturi lungi valorile lipsesc sistematic —
    iar eroarea nu apărea nicăieri ca eroare, doar ca producție subestimată.
    """
    a, b, c = _model(300.0), _model(300.0), _model(300.0)
    c.iloc[3:, 0] = np.nan          # al treilea model "se termină" la jumătate
    medie, _ = combina_ansamblu({"a": a, "b": b, "c": c})

    assert medie["shortwave_radiation"].iloc[0] == pytest.approx(300.0)
    assert medie["shortwave_radiation"].iloc[4] == pytest.approx(300.0), \
        "media trebuie să rămână 300 acolo unde modelele disponibile spun 300"


def test_dispersia_cu_un_singur_model_este_zero_nu_nan():
    """Cu un singur model nu există dezacord de măsurat. NaN s-ar propaga în
    intrările modelului ML; 0 e valoarea corectă semantic."""
    _, dispersie = combina_ansamblu({"a": _model(100.0)})
    assert (dispersie["shortwave_radiation"] == 0.0).all()


def test_dispersia_reflecta_dezacordul_dintre_modele():
    _, dispersie = combina_ansamblu({"a": _model(100.0), "b": _model(200.0)})
    assert dispersie["shortwave_radiation"].iloc[0] == pytest.approx(np.std([100, 200], ddof=1))


def test_indici_diferiti_se_reunesc_fara_pierdere_de_ore():
    """Modelele pot acoperi intervale diferite; reuniunea indexurilor nu
    trebuie să piardă ore, iar orele acoperite de un singur model rămân
    valide (cu ponderea renormalizată la acel model)."""
    scurt = _model(100.0, IDX[:3])
    lung = _model(200.0, IDX)
    medie, _ = combina_ansamblu({"scurt": scurt, "lung": lung})
    assert len(medie) == len(IDX)
    assert medie["shortwave_radiation"].iloc[0] == pytest.approx(150.0)
    assert medie["shortwave_radiation"].iloc[5] == pytest.approx(200.0)


def test_acoperire_per_model():
    a = pd.DataFrame({v: 1.0 for v in ["shortwave_radiation", "direct_normal_irradiance",
                                        "diffuse_radiation", "temperature_2m", "wind_speed_10m",
                                        "wind_speed_100m", "surface_pressure"]}, index=IDX)
    b = a.copy()
    b.iloc[3:] = np.nan
    acoperire = acoperire_per_model({"complet": a, "partial": b})
    assert acoperire["complet"] == 1.0
    assert acoperire["partial"] == pytest.approx(0.5)


def test_inchiderea_radiatiei_impune_consistenta_fizica():
    """GHI = DHI + DNI·cos(θz). Sursele NWP nu închid exact relația, pentru că
    cele trei câmpuri vin din post-procesări diferite."""
    idx = pd.date_range("2026-06-21 09:00", periods=3, freq="h", tz="UTC")
    meteo = pd.DataFrame({"ghi": [600.0, 700.0, 800.0], "dhi": [100.0, 120.0, 150.0],
                           "dni": [999.0, 999.0, 999.0]}, index=idx)
    cos_z = pd.Series([0.5, 0.6, 0.7], index=idx)
    rezultat = asigura_inchidere_radiatie(meteo, cos_z)
    reconstituit = rezultat["dhi"] + rezultat["dni"] * cos_z
    assert np.allclose(reconstituit.values, meteo["ghi"].values, rtol=1e-6)


def test_inchiderea_nu_produce_valori_negative_sau_infinite():
    idx = pd.date_range("2026-06-21 03:00", periods=3, freq="h", tz="UTC")
    meteo = pd.DataFrame({"ghi": [0.0, 5.0, 10.0], "dhi": [50.0, 5.0, 2.0],
                           "dni": [100.0, 100.0, 100.0]}, index=idx)
    cos_z = pd.Series([0.0, 0.01, 0.2], index=idx)   # soare la orizont
    rezultat = asigura_inchidere_radiatie(meteo, cos_z)
    assert (rezultat["dni"] >= 0).all() and np.isfinite(rezultat["dni"]).all()
    assert (rezultat["dhi"] <= meteo["ghi"]).all(), "difuza nu poate depăși globala"
