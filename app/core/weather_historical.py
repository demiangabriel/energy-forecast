"""
Reconstrucția istoricului de prognoze (R-610-612) — folosită de stratul ML
(antrenare, secțiunea 6.2) și de calibrarea geometriei (secțiunea 7).

Folosește arhiva de "rulări precedente" a Open-Meteo (ce a prognozat fiecare
model, la momentul respectiv, pentru fiecare zi din trecut) — NU arhiva
obișnuită de vreme reconstruită după fapt, care ar da rezultate nerealist de
bune (R-610). Reconstrucția NU atinge niciodată tabelul de producție reală
(R-611) — doar recalculează partea de prognoză, folosind exclusiv modelul
fizic (fără corector ML, care oricum nu există încă în acest punct).

Cadrul întors conține, pe lângă rezultatul fizic, și contextul meteo și
referința de cer senin — exact aceleași coloane pe care le pregătește
forecast_engine la prognoza curentă. Antrenarea și predicția trebuie să vadă
identic aceleași intrări; altfel modelul "merge la antrenare și nu merge în
producție".
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from app.core.meteo_mapping import pregateste_meteo_eolian, pregateste_meteo_pv
from app.core.pv_model import calculeaza_productie_pv
from app.core.weather_client import combina_ansamblu, obtine_meteo_istoric_reconstruit
from app.core.wind_model import calculeaza_productie_eoliana
from app.models import Parc, Tehnologie


def reconstruieste_prognoza_fizica(parc: Parc, inceput: date, sfarsit: date) -> pd.DataFrame:
    """Întoarce un DataFrame indexat UTC cu, cel puțin, coloana
    `putere_ac_fizic_kw` (+ `fractie_incarcare`, `solar_zenith` pt. PV),
    calculată EXCLUSIV cu modelul fizic, pe baza arhivei de rulări
    precedente, pentru intervalul [inceput, sfarsit]."""
    from app.core.clearsky import putere_referinta
    from app.core.ml_corrector import pregateste_cadru

    pe_model = obtine_meteo_istoric_reconstruit(
        parc.latitudine, parc.longitudine, parc.surse_meteo, inceput, sfarsit
    )
    medie, dispersie = combina_ansamblu(pe_model, parc.ponderi_meteo)

    if parc.tehnologie == Tehnologie.FOTOVOLTAIC:
        meteo_pv = pregateste_meteo_pv(medie)
        rezultat = calculeaza_productie_pv(
            parc.config_pv, parc.latitudine, parc.longitudine, parc.altitudine_m, meteo_pv
        )
    else:
        meteo_eolian = pregateste_meteo_eolian(medie)
        rezultat = calculeaza_productie_eoliana(parc.config_eolian, parc.altitudine_m, meteo_eolian)

    referinta_cs = putere_referinta(
        parc, rezultat.index,
        medie["temperature_2m"] if "temperature_2m" in medie.columns else None,
        medie["wind_speed_10m"] if "wind_speed_10m" in medie.columns else None,
    )
    return pregateste_cadru(rezultat, medie, dispersie, referinta_cs)
