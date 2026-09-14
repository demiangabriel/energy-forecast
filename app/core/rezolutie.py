"""
Rezoluția temporală a prognozei — trecerea de la pasul orar al modelelor
meteo la pasul de decontare al pieței.

De ce contează: piața din România decontează la 15 minute, iar cuplarea
pieței pentru ziua următoare (SDAC) a trecut la un interval de tranzacționare
de 15 minute din 30 septembrie 2025 (96 de intervale pe zi). O prognoză livrată
doar orar e cu un pas în urma pieței.

Cum se face corect: NU se interpolează liniar radiația. GHI are o formă
puternic neliniară în interiorul orei (mai ales la răsărit/apus), iar
interpolarea liniară pe valorile brute deplasează energia. Se interpolează
INDICELE DE SENIN (raportul dintre radiația prognozată și cea de cer senin),
care e o mărime lentă și aproape liniară, apoi se reconstruiește radiația
înmulțind cu radiația de cer senin calculată direct la pasul fin — unde
geometria solară e exactă prin construcție.

Restul variabilelor (temperatură, vânt, presiune, nori) se interpolează
liniar: sunt mărimi netede, iar eroarea de interpolare e neglijabilă față de
eroarea modelului meteo însuși.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.models import Parc

# Variabilele radiative, tratate prin indicele de senin.
VARIABILE_RADIATIVE = ["shortwave_radiation", "direct_normal_irradiance", "diffuse_radiation",
                        "direct_radiation"]
# Variabile cumulative pe interval (se împart la numărul de sub-intervale).
VARIABILE_CUMULATIVE = ["precipitation", "snowfall"]


def _index_fin(index_orar: pd.DatetimeIndex, pas_minute: int) -> pd.DatetimeIndex:
    """Etichetele fine, păstrând convenția "eticheta = ÎNCEPUTUL intervalului"
    (R-512): o oră etichetată 12:00 acoperă 12:00-13:00, deci la 15 minute
    devine 12:00, 12:15, 12:30, 12:45."""
    if len(index_orar) == 0:
        return index_orar
    pas = pd.Timedelta(minutes=pas_minute)
    sfarsit = index_orar.max() + pd.Timedelta(hours=1) - pas
    return pd.date_range(index_orar.min(), sfarsit, freq=pas, tz=index_orar.tz)


def reesantioneaza_meteo(medie: pd.DataFrame, dispersie: pd.DataFrame, parc: Parc,
                          pas_minute: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Trece seria meteo combinată de la pas orar la `pas_minute`."""
    if pas_minute >= 60 or medie.empty:
        return medie, dispersie

    from app.core.clearsky import irradianta_cer_senin

    index_fin = _index_fin(medie.index, pas_minute)
    sub_intervale = 60 // pas_minute

    cs_orar = irradianta_cer_senin(medie.index, parc.latitudine, parc.longitudine, parc.altitudine_m)
    cs_fin = irradianta_cer_senin(index_fin, parc.latitudine, parc.longitudine, parc.altitudine_m)

    coloane_fine = {}
    for coloana in medie.columns:
        serie = medie[coloana]
        if coloana in VARIABILE_RADIATIVE:
            referinta_orara = _referinta_radiativa(cs_orar, coloana)
            referinta_fina = _referinta_radiativa(cs_fin, coloana)
            with np.errstate(divide="ignore", invalid="ignore"):
                indice = serie / referinta_orara.where(referinta_orara > 1.0)
            indice = indice.replace([np.inf, -np.inf], np.nan).clip(0.0, 1.5)
            # indicele de senin e o mărime lentă: interpolarea lui e sigură
            indice_fin = (indice.reindex(indice.index.union(index_fin))
                                .interpolate(method="time", limit_direction="both")
                                .reindex(index_fin))
            # acolo unde nu există indice (noapte), radiația e oricum ~0
            coloane_fine[coloana] = (indice_fin.fillna(0.0) * referinta_fina).clip(lower=0.0)
        elif coloana in VARIABILE_CUMULATIVE:
            # cantitatea acumulată pe oră se repartizează egal pe sub-intervale
            serie_fina = (serie.reindex(serie.index.union(index_fin))
                               .ffill()
                               .reindex(index_fin))
            coloane_fine[coloana] = serie_fina / sub_intervale
        else:
            coloane_fine[coloana] = (serie.reindex(serie.index.union(index_fin))
                                           .interpolate(method="time", limit_direction="both")
                                           .reindex(index_fin))

    medie_fina = pd.DataFrame(coloane_fine, index=index_fin)

    dispersie_fina = pd.DataFrame(index=index_fin)
    for coloana in dispersie.columns:
        dispersie_fina[coloana] = (dispersie[coloana]
                                    .reindex(dispersie.index.union(index_fin))
                                    .interpolate(method="time", limit_direction="both")
                                    .reindex(index_fin))

    return medie_fina, dispersie_fina


def _referinta_radiativa(cs: pd.DataFrame, coloana: str) -> pd.Series:
    if coloana == "direct_normal_irradiance":
        return cs["dni"]
    if coloana == "diffuse_radiation":
        return cs["dhi"]
    return cs["ghi"]


def energie_din_putere(putere_kw: pd.Series, pas_minute: int) -> pd.Series:
    """kW -> kWh pe interval. La pas orar sunt egale numeric (de-asta nu s-a
    observat niciodată distincția); la 15 minute NU sunt, iar confuzia ar da
    o eroare de 4× în rapoarte."""
    return putere_kw * (pas_minute / 60.0)
