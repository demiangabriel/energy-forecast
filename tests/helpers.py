"""Utilitare comune pentru teste — generare de vreme sintetică, fără rețea.

Folosim modelul de cer senin al pvlib (date de climatologie turbiditate
incluse offline în bibliotecă) — nu interoghează Open-Meteo, deci testele
rulează rapid, determinist, fără conexiune la internet.
"""
from __future__ import annotations

import pandas as pd
from pvlib.location import Location

from app.models import (Client, ConfigEolian, ConfigPV, DirectieUmbrire, ModelTermicMontaj,
                         Parc, Subansamblu, Tehnologie, TehnologiePanou, TipAsezarePV, new_id)


def meteo_pv_cer_senin(inceput: str, ore: int, latitudine: float, longitudine: float,
                        altitudine_m: float = 0.0, freq: str = "h") -> pd.DataFrame:
    """DataFrame meteo PV (coloanele așteptate de pv_model.py), cu radiație de
    cer senin reală (poziție solară + model Ineichen), indexat pe eticheta de la
    ÎNCEPUTUL intervalului (convenția R-512), temp/vânt constante rezonabile."""
    etichete = pd.date_range(inceput, periods=ore, freq=freq, tz="UTC")
    loc = Location(latitudine, longitudine, altitude=altitudine_m, tz="UTC")
    # cer senin evaluat la mijlocul intervalului, ca să fie consistent cu
    # convenția R-512 aplicată intern de pv_model prin calculeaza_pozitie_solara
    momente = etichete + pd.Timedelta(minutes=30)
    cs = loc.get_clearsky(momente, model="ineichen")
    cs.index = etichete
    return pd.DataFrame({
        "ghi": cs["ghi"], "dni": cs["dni"], "dhi": cs["dhi"],
        "temp_aer_c": 20.0, "viteza_vant_ms": 2.0,
    }, index=etichete)


def meteo_eolian_simplu(inceput: str, ore: int, viteza_10m: float = 8.0,
                         viteza_100m: float | None = None, temp_c: float = 15.0,
                         presiune_pa: float | None = None, freq: str = "h") -> pd.DataFrame:
    etichete = pd.date_range(inceput, periods=ore, freq=freq, tz="UTC")
    date = {
        "viteza_vant_10m_ms": pd.Series(viteza_10m, index=etichete),
        "temp_aer_c": pd.Series(temp_c, index=etichete),
    }
    if viteza_100m is not None:
        date["viteza_vant_100m_ms"] = pd.Series(viteza_100m, index=etichete)
    if presiune_pa is not None:
        date["presiune_pa"] = pd.Series(presiune_pa, index=etichete)
    return pd.DataFrame(date, index=etichete)


def config_pv_simplu(**overrides) -> ConfigPV:
    date = dict(
        putere_dc_kwp=1000.0, putere_ac_kw=850.0, randament_invertor=0.98,
        subansambluri=[Subansamblu(nume="Principal", putere_dc_kwp=1000.0,
                                    inclinare_grade=30.0, azimut_grade=180.0,
                                    tip_asezare=TipAsezarePV.FIX)],
        tehnologie_panou=TehnologiePanou.MONOCRYSTALLINE,
        pierdere_murdarire_pct=0.0, pierdere_neuniformitate_pct=0.0,
        pierdere_cablaj_conexiuni_pct=0.0, pierdere_lid_pct=0.0,
        pierdere_placuta_pct=0.0, pierdere_indisponibilitate_pct=0.0,
        degradare_anuala_pct=0.6,
    )
    date.update(overrides)
    return ConfigPV(**date)


def config_eolian_simplu(**overrides) -> ConfigEolian:
    date = dict(
        numar_turbine=10, putere_nominala_kw=2000.0, inaltime_butuc_m=100.0,
        viteza_pornire_ms=3.0, viteza_nominala_ms=12.0, viteza_oprire_ms=25.0,
        pierdere_siaj_pct=0.0, pierdere_sistem_pct=0.0,
    )
    date.update(overrides)
    return ConfigEolian(**date)


def parc_pv(**overrides) -> Parc:
    date = dict(
        id=new_id(), cod="PV-TST", nume="Parc PV Test", client_id="c1",
        tehnologie=Tehnologie.FOTOVOLTAIC, latitudine=44.43, longitudine=26.10,
        altitudine_m=80.0, fus_orar="Europe/Bucharest", mod_umbra=True,
        config_pv=config_pv_simplu(),
    )
    date.update(overrides)
    return Parc(**date)


def parc_eolian(**overrides) -> Parc:
    date = dict(
        id=new_id(), cod="EOL-TST", nume="Parc Eolian Test", client_id="c1",
        tehnologie=Tehnologie.EOLIAN, latitudine=45.0, longitudine=28.5,
        altitudine_m=150.0, fus_orar="Europe/Bucharest", mod_umbra=True,
        config_eolian=config_eolian_simplu(),
    )
    date.update(overrides)
    return Parc(**date)
