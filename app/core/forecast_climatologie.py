"""
Estimare de producție DINCOLO de orizontul util al prognozei meteo numerice
(R-906, ~16 zile — `zile_orizont_maxim`, vezi `app/config.py`). Modelele
meteo (ECMWF IFS, GFS, ICON) nu emit prognoze utile mai departe — nu e o
limitare a aplicației, ci a fizicii prognozei meteo (vezi `docs/LIMITARI.md`,
§4). O „zonă de prognoză pentru următoarea lună" NU poate fi deci o
prognoză meteorologică propriu-zisă pentru toate zilele ei.

Ce oferă acest modul e o estimare CLIMATOLOGICĂ: media producției fizice
calculate din vremea REALĂ (reanaliza ERA5, aceeași sursă ca la R-701,
`weather_client.obtine_meteo_istoric_real_cache`) din ACELEAȘI zile
calendaristice ale anilor anteriori. E metoda standard „an meteorologic
tipic" (typical meteorological year), aplicată la nivel de zi — NU o
predicție a vremii viitoare, ci un tipar istoric. Incertitudinea e mult mai
mare decât a prognozei NWP pe termen scurt, de-asta rezultatul include un
interval (min/max pe anii folosiți), nu doar o singură cifră, și fiecare
rând spune explicit pe câți ani s-a bazat media.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from app.core.meteo_mapping import pregateste_meteo_eolian, pregateste_meteo_pv
from app.core.pv_model import calculeaza_productie_pv
from app.core.wind_model import calculeaza_productie_eoliana
from app.models import Parc, Tehnologie

ANI_ISTORIC_IMPLICIT = 3


def _data_analoaga(zi: date, ani_in_urma: int) -> date:
    """Aceeași zi calendaristică, `ani_in_urma` ani mai devreme — 29
    februarie cade pe 28 februarie într-un an care nu e bisect."""
    try:
        return zi.replace(year=zi.year - ani_in_urma)
    except ValueError:
        return zi.replace(year=zi.year - ani_in_urma, day=28)


def _energie_zilnica_kwh(putere_kw: pd.Series) -> pd.Series:
    """Fiecare valoare orară aproximează puterea medie pe acea oră (R-512) —
    suma pe ziua calendaristică UTC a etichetei aproximează energia zilei."""
    if putere_kw.empty:
        return putere_kw
    return putere_kw.groupby(putere_kw.index.date).sum()


def prognoza_climatologica(repo, parc: Parc, inceput: date, sfarsit: date,
                            ani_istoric: int = ANI_ISTORIC_IMPLICIT) -> dict:
    """Estimare zilnică de energie pentru [inceput, sfarsit] (capete incluse),
    pe baza climatologiei anilor anteriori. Întoarce:
      {"randuri": [{"data", "energie_estimata_kwh", "energie_min_kwh",
                     "energie_max_kwh", "numar_ani_folositi"}, ...],
       "total_estimat_kwh", "ani_istoric_ceruti", "zile_fara_date", "are_date"}
    O zi fără NICIUN an disponibil (reanaliză indisponibilă pentru toți anii
    încercați) apare cu valorile pe None, nu e omisă — ca lipsa să fie
    vizibilă, nu ascunsă."""
    from app.core.weather_client import obtine_meteo_istoric_real_cache   # import local (testabilitate — patch pe modul)

    zile_tinta: list[date] = []
    zi = inceput
    while zi <= sfarsit:
        zile_tinta.append(zi)
        zi += timedelta(days=1)

    energie_pe_zi_calendaristica: dict[tuple[int, int], list[float]] = {
        (z.month, z.day): [] for z in zile_tinta
    }

    for ani_in_urma in range(1, ani_istoric + 1):
        inceput_analog = _data_analoaga(inceput, ani_in_urma)
        sfarsit_analog = _data_analoaga(sfarsit, ani_in_urma)
        try:
            meteo = obtine_meteo_istoric_real_cache(
                repo, parc.id, parc.latitudine, parc.longitudine, inceput_analog, sfarsit_analog)
        except Exception:
            continue
        if meteo is None or meteo.empty:
            continue

        if parc.tehnologie == Tehnologie.FOTOVOLTAIC:
            meteo_pregatit = pregateste_meteo_pv(meteo)
            rezultat = calculeaza_productie_pv(
                parc.config_pv, parc.latitudine, parc.longitudine, parc.altitudine_m, meteo_pregatit)
        else:
            meteo_pregatit = pregateste_meteo_eolian(meteo)
            rezultat = calculeaza_productie_eoliana(parc.config_eolian, parc.altitudine_m, meteo_pregatit)

        energie_zi = _energie_zilnica_kwh(rezultat["putere_ac_fizic_kw"].dropna())
        for zi_tinta in zile_tinta:
            zi_analoaga = _data_analoaga(zi_tinta, ani_in_urma)
            if zi_analoaga in energie_zi.index:
                energie_pe_zi_calendaristica[(zi_tinta.month, zi_tinta.day)].append(
                    float(energie_zi.loc[zi_analoaga]))

    randuri = []
    for zi_tinta in zile_tinta:
        valori = energie_pe_zi_calendaristica[(zi_tinta.month, zi_tinta.day)]
        if valori:
            randuri.append({
                "data": zi_tinta.isoformat(),
                "energie_estimata_kwh": sum(valori) / len(valori),
                "energie_min_kwh": min(valori),
                "energie_max_kwh": max(valori),
                "numar_ani_folositi": len(valori),
            })
        else:
            randuri.append({
                "data": zi_tinta.isoformat(), "energie_estimata_kwh": None,
                "energie_min_kwh": None, "energie_max_kwh": None, "numar_ani_folositi": 0,
            })

    return {
        "randuri": randuri,
        "total_estimat_kwh": sum(r["energie_estimata_kwh"] for r in randuri
                                  if r["energie_estimata_kwh"] is not None),
        "ani_istoric_ceruti": ani_istoric,
        "zile_fara_date": sum(1 for r in randuri if r["numar_ani_folositi"] == 0),
        "are_date": any(r["numar_ani_folositi"] > 0 for r in randuri),
    }
