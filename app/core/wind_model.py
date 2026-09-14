"""
Motorul fizic eolian — R-520:

    viteza vântului extrapolată la înălțimea butucului -> corecția de
    densitate a aerului -> interpolare pe curba de putere -> înmulțire cu
    numărul de turbine -> pierderi de siaj -> pierderi de sistem.

Implementat direct (fără bibliotecă externă) — lanțul e suficient de simplu
încât o dependență în plus ar adăuga mai multă opacitate decât ar economisi
cod (spiritul R-1403).

--------------------------------------------------------------------------
ÎMBUNĂTĂȚIRI v2 (motivate în docs/cercetare/03-literatura-stiintifica.md, §6):

 1. **Profil de tip lege a puterii cu doi parametri** (implicit): exponentul α
    se deduce exact din perechea 10 m / 100 m,
        α = ln(u₁₀₀/u₁₀) / ln(100/10),  u(z) = u₁₀₀ · (z/100)^α
    Trece exact prin ambele puncte cunoscute, e monoton și nu are modurile de
    eșec ale rezolvării prin bisecție a lungimii de rugozitate. Profilul
    logaritmic rămâne disponibil.

 2. **Densitatea aerului cu umiditate** (IEC 61400-12-1). Neglijarea umidității
    e o eroare mică, dar folosirea densității standard în loc de cea locală
    poate deplasa energia anuală cu ~5%.

 3. **Netezirea curbei de putere cu intensitatea turbulenței.** O curbă de
    putere aplicată pe MEDIA orară a vitezei supraestimează lângă viteza de
    pornire (curba e convexă acolo) și subestimează lângă cea nominală (curba
    e concavă). Corecția e convoluția curbei cu distribuția vitezelor din
    interval: P_ef(u) = ∫ P(v)·N(v; u, σ=TI·u) dv.

 4. **Pierdere de siaj dependentă de viteză.** Siajul e maxim între pornire și
    nominal (unde coeficientul de tracțiune C_T e mare) și dispare practic
    peste viteza nominală. Un procent constant greșește în ambele cozi.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.models import ConfigEolian, ProfilVertical, ReglajTurbina

RHO_STANDARD = 1.225        # kg/m3 — densitatea aerului la condiții standard IEC
R_GAZ_AER_USCAT = 287.05    # J/(kg*K)
R_GAZ_VAPORI = 461.5        # J/(kg*K) — pentru corecția de umiditate (IEC 61400-12-1)
Z0_IMPLICIT_M = 0.10        # lungime de rugozitate implicită — teren agricol deschis,
                             # cazul tipic pentru parcuri eoliene (folosită doar când
                             # NU avem viteza la 100 m ca să derivăm una din date reale)
ALFA_MIN, ALFA_MAX = 0.05, 0.60   # limite fizice rezonabile ale exponentului de forfecare
TI_IMPLICIT = 0.12                # intensitate de turbulență tipică onshore
PUNCTE_CONVOLUTIE = 9             # noduri Gauss-Hermite pentru netezirea curbei


def _presiune_din_altitudine(altitudine_m: float) -> float:
    """Formula atmosferei standard — folosită DOAR ca rezervă, când sursa
    meteo nu oferă presiune la sol."""
    return 101325.0 * (1 - 2.25577e-5 * altitudine_m) ** 5.25588


def _deriva_rugozitate(v10: pd.Series, v100: pd.Series) -> pd.Series:
    """R-423: când avem viteza la ambele niveluri (10 m și 100 m), derivăm
    lungimea de rugozitate z0 direct din cele două măsurători (profil
    logaritmic), în loc să presupunem o valoare fixă. Rezolvare prin bisecție
    (funcția nu are formă închisă pentru z0)."""
    raport = (v10 / v100).clip(lower=1e-6, upper=0.999)
    lo = pd.Series(1e-6, index=v10.index)
    hi = pd.Series(9.999, index=v10.index)
    for _ in range(40):
        mid = (lo + hi) / 2
        f = np.log(10.0 / mid) / np.log(100.0 / mid) - raport
        lo = mid.where(f > 0, lo)
        hi = mid.where(f <= 0, hi)
    return (lo + hi) / 2


def exponent_forfecare(viteza_10m: pd.Series, viteza_100m: pd.Series) -> pd.Series:
    """Exponentul α al legii puterii, dedus din perechea 10 m / 100 m.

    E și o intrare valoroasă pentru corectorul ML: α codifică indirect
    stabilitatea atmosferică (α mare = atmosferă stabilă, noaptea; α mic =
    amestec convectiv, ziua), pe care nu o putem calcula direct fără fluxul
    de căldură."""
    v10 = viteza_10m.clip(lower=0.1)
    v100 = viteza_100m.clip(lower=0.1)
    with np.errstate(divide="ignore", invalid="ignore"):
        alfa = np.log(v100 / v10) / np.log(100.0 / 10.0)
    return pd.Series(alfa, index=viteza_10m.index).replace(
        [np.inf, -np.inf], np.nan).fillna(1.0 / 7.0).clip(ALFA_MIN, ALFA_MAX)


def extrapoleaza_viteza_la_butuc(viteza_10m: pd.Series, inaltime_butuc_m: float,
                                  viteza_100m: pd.Series | None = None,
                                  profil: ProfilVertical = ProfilVertical.POWER_LAW) -> pd.Series:
    """Viteza la înălțimea butucului."""
    if viteza_100m is not None and viteza_100m.notna().any():
        v100 = viteza_100m.fillna(viteza_10m * 1.1)  # rezervă minimă dacă lipsesc puncte izolate
        if profil == ProfilVertical.POWER_LAW:
            alfa = exponent_forfecare(viteza_10m, v100)
            return v100.clip(lower=0.0) * (inaltime_butuc_m / 100.0) ** alfa
        z0 = _deriva_rugozitate(viteza_10m.clip(lower=0.1), v100.clip(lower=0.1))
        return viteza_10m * np.log(inaltime_butuc_m / z0) / np.log(10.0 / z0)

    # fără viteza la 100 m: profil logaritmic cu rugozitate implicită
    z0 = pd.Series(Z0_IMPLICIT_M, index=viteza_10m.index)
    return viteza_10m * np.log(inaltime_butuc_m / z0) / np.log(10.0 / z0)


def densitate_aer(temp_aer_c: pd.Series, presiune_pa: pd.Series | None, altitudine_m: float,
                   umiditate_relativa_pct: pd.Series | None = None) -> pd.Series:
    """Densitatea aerului (kg/m3), formula IEC 61400-12-1 cu umiditate:

        ρ = (1/T)·[ p/R₀ − φ·p_w·(1/R₀ − 1/R_w) ]
        p_w = 0,0000205 · exp(0,0631846·T)     (presiunea vaporilor saturați, Pa)

    Fără umiditate se reduce la formula gazului uscat, ρ = p/(R₀·T).
    """
    temp_k = temp_aer_c + 273.15
    if presiune_pa is not None and presiune_pa.notna().any():
        p = presiune_pa.fillna(_presiune_din_altitudine(altitudine_m))
    else:
        p = pd.Series(_presiune_din_altitudine(altitudine_m), index=temp_aer_c.index)

    if umiditate_relativa_pct is not None and umiditate_relativa_pct.notna().any():
        phi = (umiditate_relativa_pct.fillna(50.0) / 100.0).clip(0.0, 1.0)
        p_vapori = 0.0000205 * np.exp(0.0631846 * temp_k)
        return (1.0 / temp_k) * (p / R_GAZ_AER_USCAT
                                  - phi * p_vapori * (1.0 / R_GAZ_AER_USCAT - 1.0 / R_GAZ_VAPORI))
    return p / (R_GAZ_AER_USCAT * temp_k)


def corecteaza_densitate(viteza_ms: pd.Series, temp_aer_c: pd.Series,
                          presiune_pa: pd.Series | None, altitudine_m: float,
                          umiditate_relativa_pct: pd.Series | None = None) -> pd.Series:
    """R-424 — corecție IEC pentru turbine cu reglaj PITCH: viteza echivalentă
    = viteza măsurată · (ρ_locală/ρ_standard)^(1/3). Aerul mai dens (rece)
    produce mai mult la aceeași viteză -> viteza echivalentă e mai mare, nu
    puterea direct (asta păstrează corect platoul de la puterea nominală)."""
    rho = densitate_aer(temp_aer_c, presiune_pa, altitudine_m, umiditate_relativa_pct)
    return viteza_ms * (rho / RHO_STANDARD) ** (1.0 / 3.0)


def _putere_din_curba(viteza_echiv: pd.Series, curba: list[list[float]]) -> pd.Series:
    curba_ordonata = sorted(curba, key=lambda p: p[0])
    xs = [p[0] for p in curba_ordonata]
    ys = [p[1] for p in curba_ordonata]
    return pd.Series(np.interp(viteza_echiv, xs, ys, left=0.0, right=0.0), index=viteza_echiv.index)


def _putere_din_viteze_simple(viteza_echiv: pd.Series, v_pornire: float, v_nominala: float,
                               v_oprire: float, putere_nominala_kw: float) -> pd.Series:
    """R-422b — aproximare când nu există curba exactă a producătorului:
    rampă cubică între viteza de pornire și cea nominală (formă standard —
    puterea aerodinamică crește cu v^3 înainte de limitarea prin pitch),
    platou la puterea nominală până la viteza de oprire, 0 în afara acestui interval."""
    v = viteza_echiv
    putere = pd.Series(0.0, index=v.index)
    ramp = (v >= v_pornire) & (v < v_nominala)
    putere[ramp] = putere_nominala_kw * (((v[ramp] - v_pornire) / (v_nominala - v_pornire)) ** 3)
    platou = (v >= v_nominala) & (v <= v_oprire)
    putere[platou] = putere_nominala_kw
    return putere.clip(lower=0, upper=putere_nominala_kw)


def _evalueaza_curba(config: ConfigEolian, viteza: pd.Series) -> pd.Series:
    """Puterea unei turbine la vitezele date, indiferent de sursa curbei."""
    if config.curba_putere:
        return _putere_din_curba(viteza, config.curba_putere)
    if None in (config.viteza_pornire_ms, config.viteza_nominala_ms, config.viteza_oprire_ms):
        raise ValueError(
            "Parcul eolian nu are nici curbă de putere, nici cele 3 viteze simple "
            "(pornire/nominală/oprire) — nu se poate calcula producția (R-422)."
        )
    return _putere_din_viteze_simple(
        viteza, config.viteza_pornire_ms, config.viteza_nominala_ms,
        config.viteza_oprire_ms, config.putere_nominala_kw,
    )


def netezeste_cu_turbulenta(config: ConfigEolian, viteza_medie: pd.Series,
                             intensitate_turbulenta: pd.Series) -> pd.Series:
    """Convoluția curbei de putere cu distribuția vitezelor din interval.

    Aplicăm cuadratură Gauss-Hermite: media lui P(v) când v ~ N(u, σ=TI·u).
    Cu 9 noduri, aproximarea e practic exactă pentru o curbă de putere netedă,
    și costă doar 9 evaluări vectorizate ale curbei — neglijabil.

    Efectul: elimină supraestimarea de lângă viteza de pornire și
    subestimarea de lângă cea nominală, care sunt erorile structurale ale
    aplicării unei curbe de putere pe medii orare.
    """
    noduri, ponderi = np.polynomial.hermite_e.hermegauss(PUNCTE_CONVOLUTIE)
    ponderi = ponderi / ponderi.sum()

    sigma = (intensitate_turbulenta.reindex(viteza_medie.index).fillna(TI_IMPLICIT)
             * viteza_medie).clip(lower=1e-6)
    acumulat = pd.Series(0.0, index=viteza_medie.index)
    for nod, pondere in zip(noduri, ponderi):
        viteza_deplasata = (viteza_medie + nod * sigma).clip(lower=0.0)
        acumulat = acumulat + pondere * _evalueaza_curba(config, viteza_deplasata)
    return acumulat.clip(lower=0.0, upper=config.putere_nominala_kw)


def factor_siaj(config: ConfigEolian, viteza_echiv: pd.Series) -> pd.Series:
    """Fracția de putere rămasă după siaj (1 = fără pierdere).

    Dependența de viteză urmează forma coeficientului de tracțiune C_T: siajul
    e maxim în zona de sarcină parțială (între pornire și ~nominal) și scade
    spre zero peste viteza nominală, unde turbina din față extrage puțin din
    fluxul de aer. Modelăm asta cu o rampă care păstrează pierderea declarată
    de operator ca valoare MAXIMĂ, la vitezele de sarcină parțială.
    """
    pierdere_maxima = config.pierdere_siaj_pct / 100.0
    if not config.wake_dependent_de_viteza:
        return pd.Series(1.0 - pierdere_maxima, index=viteza_echiv.index)

    v_pornire = config.viteza_pornire_ms if config.viteza_pornire_ms is not None else 3.0
    v_nominala = config.viteza_nominala_ms if config.viteza_nominala_ms is not None else 12.0
    if config.curba_putere:
        viteze = [p[0] for p in sorted(config.curba_putere, key=lambda x: x[0])]
        puteri = [p[1] for p in sorted(config.curba_putere, key=lambda x: x[0])]
        maxim = max(puteri) if puteri else 0.0
        for v, p in zip(viteze, puteri):
            if maxim > 0 and p >= 0.99 * maxim:
                v_nominala = v
                break

    v = viteza_echiv
    factor_relativ = pd.Series(0.0, index=v.index)
    # sarcină parțială: siaj complet
    partiala = (v >= v_pornire) & (v <= v_nominala)
    factor_relativ[partiala] = 1.0
    # peste nominal: scade liniar spre 0 pe un interval de 1,5× viteza nominală
    peste = v > v_nominala
    if peste.any():
        interval = max(v_nominala * 0.5, 1e-6)
        factor_relativ[peste] = (1.0 - (v[peste] - v_nominala) / interval).clip(lower=0.0)
    return (1.0 - pierdere_maxima * factor_relativ).clip(lower=0.0, upper=1.0)


def calculeaza_productie_eoliana(config: ConfigEolian, altitudine_m: float, meteo: pd.DataFrame) -> pd.DataFrame:
    """meteo: index UTC (eticheta orei finale), coloane:
    viteza_vant_10m_ms (obligatoriu), viteza_vant_100m_ms (opțional),
    temp_aer_c (obligatoriu), presiune_pa (opțional), umiditate_pct (opțional)."""
    for c in ["viteza_vant_10m_ms", "temp_aer_c"]:
        if c not in meteo.columns:
            raise ValueError(f"Coloană meteo lipsă pentru motorul eolian: {c}")

    v100 = meteo["viteza_vant_100m_ms"] if "viteza_vant_100m_ms" in meteo.columns else None
    v_butuc = extrapoleaza_viteza_la_butuc(meteo["viteza_vant_10m_ms"], config.inaltime_butuc_m,
                                            v100, config.profil_vertical)

    presiune = meteo["presiune_pa"] if "presiune_pa" in meteo.columns else None
    umiditate = meteo["umiditate_pct"] if "umiditate_pct" in meteo.columns else None
    rho = densitate_aer(meteo["temp_aer_c"], presiune, altitudine_m, umiditate)

    if config.reglaj == ReglajTurbina.PITCH:
        # turbine cu pas variabil (practic toate cele moderne): se corectează VITEZA
        v_echiv = v_butuc * (rho / RHO_STANDARD) ** (1.0 / 3.0)
        factor_putere_densitate = pd.Series(1.0, index=meteo.index)
    else:
        # turbine cu reglaj prin desprindere (stall): se corectează PUTEREA
        v_echiv = v_butuc
        factor_putere_densitate = rho / RHO_STANDARD

    # intensitatea turbulenței: declarată, altfel estimată din rugozitate
    if config.intensitate_turbulenta is not None:
        ti = pd.Series(float(config.intensitate_turbulenta), index=meteo.index)
    elif v100 is not None and v100.notna().any():
        z0 = _deriva_rugozitate(meteo["viteza_vant_10m_ms"].clip(lower=0.1), v100.clip(lower=0.1))
        ti = (1.0 / np.log(config.inaltime_butuc_m / z0.clip(lower=1e-4))).clip(0.05, 0.35)
    else:
        ti = pd.Series(TI_IMPLICIT, index=meteo.index)

    if config.netezire_turbulenta:
        putere_turbina_kw = netezeste_cu_turbulenta(config, v_echiv, ti)
    else:
        putere_turbina_kw = _evalueaza_curba(config, v_echiv)

    putere_turbina_kw = putere_turbina_kw * factor_putere_densitate

    putere_parc_kw = putere_turbina_kw * config.numar_turbine
    factor_wake = factor_siaj(config, v_echiv)
    putere_dupa_siaj = putere_parc_kw * factor_wake
    putere_finala = putere_dupa_siaj * (1 - config.pierdere_sistem_pct / 100.0)
    putere_finala = putere_finala.clip(lower=0)

    putere_instalata_totala = config.putere_nominala_kw * config.numar_turbine
    fractie_incarcare = (putere_finala / putere_instalata_totala).clip(lower=0, upper=1)

    rezultat = pd.DataFrame({
        "viteza_butuc_ms": v_butuc,
        "viteza_echivalenta_ms": v_echiv,
        "densitate_aer": rho,
        "intensitate_turbulenta": ti,
        "factor_siaj": factor_wake,
        "putere_ac_fizic_kw": putere_finala,
        "fractie_incarcare": fractie_incarcare,
    }, index=meteo.index)

    if v100 is not None and v100.notna().any():
        rezultat["exponent_forfecare"] = exponent_forfecare(meteo["viteza_vant_10m_ms"], v100)
    for sursa, destinatie in [("directie_vant_100m_grade", "directie_vant_grade"),
                               ("directie_vant_10m_grade", "directie_vant_grade")]:
        if sursa in meteo.columns and destinatie not in rezultat.columns:
            rezultat[destinatie] = meteo[sursa]
    return rezultat
