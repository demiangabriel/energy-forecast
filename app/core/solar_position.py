"""
Poziția solară — un singur loc care aplică regula R-512:

    "Eticheta unui interval reprezintă ÎNCEPUTUL lui (ora 12:00 = intervalul
    12:00-13:00) — convenția piețelor de energie (PZU/OPCOM, ENTSO-E) și a
    contoarelor. Poziția solară trebuie evaluată la MIJLOCUL intervalului, nu
    la etichetă, altfel apare o eroare sistematică dimineața și seara, de semn
    opus."

Toate calculele interne sunt în UTC, fără excepție (R-511).
"""
from __future__ import annotations

import pandas as pd
from pvlib import solarposition, irradiance

PAS_IMPLICIT = pd.Timedelta(hours=1)


def pas_din_index(etichete_utc: pd.DatetimeIndex) -> pd.Timedelta:
    """Durata unui interval, dedusă din distanța dintre etichete.

    Se deduce în loc să fie presupusă o oră pentru că lanțul rulează și la 15
    minute: un mijloc de interval calculat cu jumătate de oră fix ar fi ieșit
    atunci complet în afara intervalului (cu 22,5 minute), deplasând sistematic
    geometria solară a fiecărui punct."""
    if len(etichete_utc) < 2:
        return PAS_IMPLICIT
    diferente = pd.Series(etichete_utc).diff().dropna()
    if diferente.empty:
        return PAS_IMPLICIT
    pas = diferente.median()
    return pas if pas > pd.Timedelta(0) else PAS_IMPLICIT


def mijloc_interval(etichete_utc: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Din eticheta de la ÎNCEPUTUL intervalului, întoarce momentul de la
    mijlocul lui (ex: eticheta 12:00 la pas orar -> 12:30), folosit strict
    pentru poziția solară."""
    return etichete_utc + pas_din_index(etichete_utc) / 2


def calculeaza_pozitie_solara(etichete_utc: pd.DatetimeIndex, latitudine: float,
                               longitudine: float, altitudine_m: float = 0.0) -> pd.DataFrame:
    """Poziție solară (zenit, azimut) la mijlocul fiecărui interval orar.
    Indexul rezultatului rămâne pe etichetele ORIGINALE (ora finală), ca să se
    alinieze direct cu restul seriei meteo — doar valorile sunt calculate la
    mijlocul intervalului."""
    momente = mijloc_interval(etichete_utc)
    poz = solarposition.get_solarposition(
        momente, latitude=latitudine, longitude=longitudine, altitude=altitudine_m,
    )
    poz.index = etichete_utc
    poz["dni_extra"] = irradiance.get_extra_radiation(momente).values
    return poz
