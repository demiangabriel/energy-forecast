"""
Analiza de sensibilitate a parametrilor — secțiunea 8, R-801-802.

Pentru fiecare parametru configurabil, se modifică valoarea curentă cu o
treaptă fixă și se RERULEAZĂ motorul fizic pe un an întreg de vreme reală
istorică (nu o estimare teoretică) — se măsoară cu cât se schimbă energia
anuală prognozată. Rezultatul e o listă ordonată descrescător după impact
(R-802), ca operatorul să știe unde merită precizie maximă la completarea
datelor și unde o aproximare e suficientă.
"""
from __future__ import annotations

import copy
from datetime import date, timedelta

from app.core.meteo_mapping import pregateste_meteo_eolian, pregateste_meteo_pv
from app.core.pv_model import calculeaza_productie_pv
from app.core.weather_client import obtine_meteo_istoric_real
from app.core.wind_model import calculeaza_productie_eoliana
from app.models import ConfigEolian, ConfigPV, Parc, Tehnologie


def _energie_anuala_pv(config: ConfigPV, parc: Parc, meteo_pv) -> float:
    rez = calculeaza_productie_pv(config, parc.latitudine, parc.longitudine, parc.altitudine_m, meteo_pv)
    return float(rez["putere_ac_fizic_kw"].sum())  # kWh, rezoluție orară


def _energie_anuala_eolian(config: ConfigEolian, parc: Parc, meteo_eolian) -> float:
    rez = calculeaza_productie_eoliana(config, parc.altitudine_m, meteo_eolian)
    return float(rez["putere_ac_fizic_kw"].sum())


def _perturbatii_pv(config: ConfigPV) -> list[tuple[str, ConfigPV]]:
    perturbatii = []
    sub = config.subansambluri[0] if config.subansambluri else None
    if sub and sub.inclinare_grade is not None:
        c = copy.deepcopy(config); c.subansambluri[0].inclinare_grade += 5
        perturbatii.append(("Înclinare +5°", c))
    if sub and sub.azimut_grade is not None:
        c = copy.deepcopy(config); c.subansambluri[0].azimut_grade += 10
        perturbatii.append(("Azimut +10°", c))
    c = copy.deepcopy(config); c.randament_invertor = min(0.995, c.randament_invertor + 0.01)
    perturbatii.append(("Randament invertor +1pp", c))
    c = copy.deepcopy(config); c.degradare_anuala_pct += 0.2
    perturbatii.append(("Degradare anuală +0,2pp", c))
    for camp, eticheta in [
        ("pierdere_murdarire_pct", "Pierdere murdărire +1pp"),
        ("pierdere_neuniformitate_pct", "Pierdere neuniformitate +1pp"),
        ("pierdere_cablaj_conexiuni_pct", "Pierdere cablaj/conexiuni +1pp"),
        ("pierdere_lid_pct", "Pierdere LID +1pp"),
        ("pierdere_placuta_pct", "Pierdere plăcuță +1pp"),
        ("pierdere_indisponibilitate_pct", "Pierdere indisponibilitate +1pp"),
    ]:
        c = copy.deepcopy(config)
        setattr(c, camp, getattr(c, camp) + 1.0)
        perturbatii.append((eticheta, c))
    c = copy.deepcopy(config); c.umbrire_orizont_grade += 2.0
    perturbatii.append(("Umbrire orizont +2°", c))
    if config.coeficient_temperatura_pct_per_c is not None:
        c = copy.deepcopy(config); c.coeficient_temperatura_pct_per_c += 0.05
        perturbatii.append(("Coeficient temperatură +0,05pp/°C", c))
    return perturbatii


def _perturbatii_eolian(config: ConfigEolian) -> list[tuple[str, ConfigEolian]]:
    perturbatii = []
    c = copy.deepcopy(config); c.inaltime_butuc_m += 10
    perturbatii.append(("Înălțime butuc +10 m", c))
    c = copy.deepcopy(config); c.pierdere_siaj_pct += 2.0
    perturbatii.append(("Pierdere siaj +2pp", c))
    c = copy.deepcopy(config); c.pierdere_sistem_pct += 1.0
    perturbatii.append(("Pierdere sistem +1pp", c))
    if config.viteza_pornire_ms is not None:
        c = copy.deepcopy(config); c.viteza_pornire_ms += 0.5
        perturbatii.append(("Viteză pornire +0,5 m/s", c))
    if config.viteza_nominala_ms is not None:
        c = copy.deepcopy(config); c.viteza_nominala_ms += 0.5
        perturbatii.append(("Viteză nominală +0,5 m/s", c))
    if config.viteza_oprire_ms is not None:
        c = copy.deepcopy(config); c.viteza_oprire_ms += 1.0
        perturbatii.append(("Viteză oprire +1 m/s", c))
    return perturbatii


def analizeaza_senzitivitate(parc: Parc, ani_istoric: int = 1) -> dict:
    """Notă v2: fiecare perturbație se evaluează O SINGURĂ DATĂ. Înainte,
    aceeași configurație era rulată de două ori (o dată pentru valoarea
    absolută, o dată pentru procent) — analiza dura exact de două ori mai
    mult decât era nevoie, pe un an întreg de date orare.

    Poziția solară e calculată o singură dată și refolosită pentru toate
    perturbațiile (fotovoltaic) — depinde doar de timp și locație, nu de
    parametrii testați."""
    sfarsit = date.today() - timedelta(days=2)   # arhiva reanaliză nu acoperă ultimele zile
    inceput = sfarsit - timedelta(days=365 * ani_istoric)
    meteo_real = obtine_meteo_istoric_real(parc.latitudine, parc.longitudine, inceput, sfarsit)

    if parc.tehnologie == Tehnologie.FOTOVOLTAIC:
        from app.core.solar_position import calculeaza_pozitie_solara
        meteo_pv = pregateste_meteo_pv(meteo_real)
        poz = calculeaza_pozitie_solara(meteo_pv.index, parc.latitudine, parc.longitudine,
                                         parc.altitudine_m)

        def energie(cfg) -> float:
            rez = calculeaza_productie_pv(cfg, parc.latitudine, parc.longitudine,
                                           parc.altitudine_m, meteo_pv, poz_solara=poz)
            return float(rez["putere_ac_fizic_kw"].sum())

        baza = energie(parc.config_pv)
        perturbatii = _perturbatii_pv(parc.config_pv)
    else:
        meteo_eolian = pregateste_meteo_eolian(meteo_real)

        def energie(cfg) -> float:
            return _energie_anuala_eolian(cfg, parc, meteo_eolian)

        baza = energie(parc.config_eolian)
        perturbatii = _perturbatii_eolian(parc.config_eolian)

    rezultate = []
    for eticheta, cfg in perturbatii:
        modificata = energie(cfg)
        rezultate.append({
            "parametru": eticheta,
            "energie_anuala_baza_kwh": round(baza, 0),
            "energie_anuala_modificata_kwh": round(modificata, 0),
            "variatie_pct": round((modificata - baza) / baza * 100, 2) if baza else 0.0,
        })

    rezultate.sort(key=lambda r: abs(r["variatie_pct"]), reverse=True)
    return {"energie_anuala_referinta_kwh": round(baza, 0), "perioada": f"{inceput} – {sfarsit}",
            "rezultate": rezultate}
