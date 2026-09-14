"""
Referința de "cer senin" — baza pentru trei lucruri distincte:

1. **Indicele de senin** kt* = putere_măsurată / putere_cer_senin. E scara
   naturală a producției fotovoltaice: elimină variația deterministă (zi/noapte,
   anotimp, geometrie) și lasă exact ce nu știe fizica — norii. Corectorul ML
   învață mult mai eficient pe această scară decât pe kW bruți
   (docs/cercetare/03-literatura-stiintifica.md, §3).

2. **Persistența inteligentă** — prognoza de referință corectă pentru solar:
   se persistă kt*, NU puterea. "Mâine la 13:00 va fi cât a fost azi la 13:00"
   e o referință naivă proastă; "mâine cerul va fi la fel de senin ca azi" e
   referința serioasă față de care trebuie măsurat orice model (§5).

3. **Detectarea problemelor de date** — o producție măsurată care depășește
   constant cerul senin înseamnă o eroare de configurare sau de metering.

Modelul de cer senin folosit e Ineichen-Perez cu climatologia de turbiditate
Linke inclusă offline în pvlib — nu necesită rețea.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pvlib import clearsky, atmosphere

from app.core.solar_position import calculeaza_pozitie_solara
from app.models import ConfigPV, Parc, Tehnologie

# Sub acest prag (kW) puterea de cer senin e prea mică pentru ca împărțirea
# la ea să fie stabilă — orele respective sunt tratate ca "noapte" pentru
# indicele de senin.
PRAG_CLEARSKY_KW = 1e-6

# Fracția din puterea instalată sub care o oră nu e utilă pentru verificare
# (răsărit/apus): sub ~0,5% nu se poate distinge un model bun de unul prost.
PRAG_ORA_UTILA_FRACTIE = 0.005


def irradianta_cer_senin(etichete_utc: pd.DatetimeIndex, latitudine: float,
                          longitudine: float, altitudine_m: float = 0.0) -> pd.DataFrame:
    """GHI/DNI/DHI de cer senin (Ineichen-Perez), evaluate — ca tot restul
    lanțului — la MIJLOCUL intervalului (R-512: eticheta e începutul lui)."""
    from app.core.solar_position import mijloc_interval

    poz = calculeaza_pozitie_solara(etichete_utc, latitudine, longitudine, altitudine_m)
    presiune = atmosphere.alt2pres(altitudine_m)
    masa_aer_rel = atmosphere.get_relative_airmass(poz["apparent_zenith"])
    masa_aer_abs = atmosphere.get_absolute_airmass(masa_aer_rel, presiune)
    momente = mijloc_interval(etichete_utc)
    turbiditate = clearsky.lookup_linke_turbidity(momente, latitudine, longitudine)
    turbiditate.index = etichete_utc

    cs = clearsky.ineichen(
        apparent_zenith=poz["apparent_zenith"],
        airmass_absolute=masa_aer_abs,
        linke_turbidity=turbiditate,
        altitude=altitudine_m,
        dni_extra=poz["dni_extra"],
    )
    cs.index = etichete_utc
    return cs


def meteo_cer_senin_pv(etichete_utc: pd.DatetimeIndex, latitudine: float, longitudine: float,
                        altitudine_m: float, temp_aer_c: pd.Series | None = None,
                        viteza_vant_ms: pd.Series | None = None) -> pd.DataFrame:
    """Un "meteo" sintetic de cer senin, în formatul așteptat de pv_model —
    ca puterea de cer senin să treacă prin EXACT același lanț fizic ca
    prognoza reală (aceeași geometrie, aceleași pierderi, aceeași degradare).
    Temperatura și vântul reale sunt folosite dacă există; altfel valori
    neutre, pentru că efectul lor pe referință e de ordinul procentelor."""
    cs = irradianta_cer_senin(etichete_utc, latitudine, longitudine, altitudine_m)
    temp = (temp_aer_c.reindex(etichete_utc) if temp_aer_c is not None
            else pd.Series(20.0, index=etichete_utc))
    vant = (viteza_vant_ms.reindex(etichete_utc) if viteza_vant_ms is not None
            else pd.Series(2.0, index=etichete_utc))
    return pd.DataFrame({
        "ghi": cs["ghi"], "dni": cs["dni"], "dhi": cs["dhi"],
        "temp_aer_c": temp.fillna(20.0), "viteza_vant_ms": vant.fillna(2.0),
    }, index=etichete_utc)


def putere_cer_senin_pv(config: ConfigPV, latitudine: float, longitudine: float, altitudine_m: float,
                         etichete_utc: pd.DatetimeIndex, temp_aer_c: pd.Series | None = None,
                         viteza_vant_ms: pd.Series | None = None) -> pd.Series:
    """Puterea AC pe care ar produce-o parcul pe cer perfect senin (kW)."""
    from app.core.pv_model import calculeaza_productie_pv   # import local: evită ciclul
    meteo = meteo_cer_senin_pv(etichete_utc, latitudine, longitudine, altitudine_m,
                                temp_aer_c, viteza_vant_ms)
    rezultat = calculeaza_productie_pv(config, latitudine, longitudine, altitudine_m, meteo)
    return rezultat["putere_ac_fizic_kw"]


def putere_referinta(parc: Parc, etichete_utc: pd.DatetimeIndex,
                      temp_aer_c: pd.Series | None = None,
                      viteza_vant_ms: pd.Series | None = None) -> pd.Series:
    """Scara de normalizare, pe tehnologie:
      - fotovoltaic: puterea de cer senin, oră cu oră (variază natural);
      - eolian: puterea instalată (nu există un "cer senin" al vântului —
        scara naturală e capacitatea).
    """
    from app.core.forecast_engine import putere_instalata_kw   # import local: evită ciclul
    if parc.tehnologie == Tehnologie.FOTOVOLTAIC and parc.config_pv:
        return putere_cer_senin_pv(parc.config_pv, parc.latitudine, parc.longitudine,
                                    parc.altitudine_m, etichete_utc, temp_aer_c, viteza_vant_ms)
    return pd.Series(putere_instalata_kw(parc), index=etichete_utc, dtype="float64")


def indice_senin(putere_kw: pd.Series, putere_cer_senin_kw: pd.Series,
                  limita_superioara: float = 1.5) -> pd.Series:
    """kt* = putere / putere_cer_senin, definit doar acolo unde referința e
    semnificativ diferită de zero. Limita superioară acoperă suprairadierea
    reală (efect de margine de nor), fără să lase valori absurde din
    împărțiri la aproape-zero."""
    referinta = putere_cer_senin_kw.reindex(putere_kw.index)
    valabil = referinta > PRAG_CLEARSKY_KW
    with np.errstate(divide="ignore", invalid="ignore"):
        kt = putere_kw.divide(referinta.where(valabil))
    return kt.replace([np.inf, -np.inf], np.nan).clip(lower=0.0, upper=limita_superioara)


def masca_ore_utile(parc: Parc, referinta_kw: pd.Series, putere_instalata_kw_: float) -> pd.Series:
    """Orele pe care ARE SENS să se calculeze indicatori de eroare.

    Pentru fotovoltaic: orele de zi, definite ca acelea în care referința de
    cer senin depășește un prag mic din puterea instalată (echivalentul
    practic al filtrului θz < 85° recomandat de Yang et al. 2020 — dar
    exprimat în puterea efectivă a acestui parc, deci ține cont și de
    orientare și de umbrirea de orizont).

    Pentru eolian: toate orele (producția nu are ciclu zi/noapte).
    """
    if parc.tehnologie != Tehnologie.FOTOVOLTAIC:
        return pd.Series(True, index=referinta_kw.index)
    prag = max(PRAG_CLEARSKY_KW, putere_instalata_kw_ * PRAG_ORA_UTILA_FRACTIE)
    return referinta_kw > prag
