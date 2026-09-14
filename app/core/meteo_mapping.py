"""
Traduce coloanele brute Open-Meteo (vezi weather_client.VARIABILE_ORARE) în
coloanele așteptate de motoarele fizice (pv_model.py / wind_model.py).
Un singur loc pentru această corespondență — folosit atât la prognoza
curentă (forecast_engine.py), cât și la reconstrucția istorică
(weather_historical.py), ca cele două să rămână identice.

Regula de aur: variabilele SUPLIMENTARE se copiază doar dacă există în sursă.
Motoarele fizice tratează absența lor ca "funcționalitatea respectivă e
oprită", niciodată ca eroare — unele modele meteo și unele endpoint-uri
(arhivă, reanaliză) nu expun toate câmpurile.
"""
from __future__ import annotations

import pandas as pd

# (coloana Open-Meteo, coloana internă, factor de conversie)
SUPLIMENTARE_PV = [
    ("snowfall", "zapada_cm", 1.0),            # cm în intervalul orar
    ("snow_depth", "strat_zapada_m", 1.0),     # m
    ("precipitation", "precipitatii_mm", 1.0),  # mm în intervalul orar
    ("cloud_cover", "nori_total_pct", 1.0),
    ("cloud_cover_low", "nori_jos_pct", 1.0),
    ("cloud_cover_mid", "nori_mediu_pct", 1.0),
    ("cloud_cover_high", "nori_sus_pct", 1.0),
    ("relative_humidity_2m", "umiditate_pct", 1.0),
    ("surface_pressure", "presiune_pa", 100.0),  # Open-Meteo: hPa -> Pa
]

SUPLIMENTARE_EOLIAN = [
    ("surface_pressure", "presiune_pa", 100.0),  # Open-Meteo: hPa -> Pa (corecția de densitate)
    ("wind_direction_10m", "directie_vant_10m_grade", 1.0),
    ("wind_direction_100m", "directie_vant_100m_grade", 1.0),
    ("relative_humidity_2m", "umiditate_pct", 1.0),
    ("cloud_cover", "nori_total_pct", 1.0),
    ("precipitation", "precipitatii_mm", 1.0),
]


def _adauga_suplimentare(destinatie: dict, sursa: pd.DataFrame, perechi) -> None:
    for coloana_sursa, coloana_interna, factor in perechi:
        if coloana_sursa in sursa.columns:
            destinatie[coloana_interna] = sursa[coloana_sursa] * factor


def pregateste_meteo_pv(medie: pd.DataFrame) -> pd.DataFrame:
    date = {
        "ghi": medie["shortwave_radiation"],
        "dni": medie["direct_normal_irradiance"],
        "dhi": medie["diffuse_radiation"],
        "temp_aer_c": medie["temperature_2m"],
        "viteza_vant_ms": medie["wind_speed_10m"],
    }
    _adauga_suplimentare(date, medie, SUPLIMENTARE_PV)
    return pd.DataFrame(date, index=medie.index)


def pregateste_meteo_eolian(medie: pd.DataFrame) -> pd.DataFrame:
    date = {
        "viteza_vant_10m_ms": medie["wind_speed_10m"],
        "temp_aer_c": medie["temperature_2m"],
    }
    if "wind_speed_100m" in medie.columns:
        date["viteza_vant_100m_ms"] = medie["wind_speed_100m"]
    _adauga_suplimentare(date, medie, SUPLIMENTARE_EOLIAN)
    return pd.DataFrame(date, index=medie.index)
