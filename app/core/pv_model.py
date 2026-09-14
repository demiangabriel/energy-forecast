"""
Motorul fizic fotovoltaic — R-510:

    poziția solară -> transpoziția radiației pe planul înclinat al modulului
    -> unghiul de incidență -> temperatura celulei -> puterea DC ->
    limitarea invertorului (clipping) -> pierderi de sistem -> degradare
    -> limita de injecție.

Un parc poate avea mai multe subansambluri (orientări diferite, R-412);
fiecare subansamblu își are propria înclinare/azimut/putere DC. Implicit toate
împart ACELAȘI invertor — clipping-ul se aplică pe SUMA puterilor DC ale
tuturor subansamblurilor, niciodată pe fiecare separat (altfel se
supraestimează producția cu 4-6%, conform notei din specificație). Dacă
parcul are de fapt invertoare separate per subansamblu, se comută explicit
`clipping_per_subansamblu` — topologia trebuie declarată, nu presupusă.

Folosim pvlib pentru poziția solară, transpoziție și modelul de temperatură —
sunt implementări standard, publice, verificate de industrie; NU e o
abstractizare care ascunde logica aplicației (R-307) — orchestrarea pașilor,
ordinea lor și toate deciziile de business rămân aici, în cod simplu și
comentat.

--------------------------------------------------------------------------
ÎMBUNĂTĂȚIRI v2 (motivate în docs/cercetare/03-literatura-stiintifica.md):
  * transpoziție Perez (formularea continuă Driesse) implicit, în locul lui
    Hay-Davies — modelul de referință al industriei (PVsyst/SAM), cu termenul
    de strălucire a orizontului pe care Hay-Davies nu îl are;
  * albedo configurabil, cu comutare automată pe albedo de zăpadă cât timp
    există strat de zăpadă (iarna, la înclinații mari, contează procente);
  * pierderi din acoperirea cu ZĂPADĂ (model NREL/Marion) — sursa unică cea
    mai mare de erori de 50-100% în zilele de iarnă, imposibil de învățat de
    stratul ML din vreme fără variabila de zăpadă;
  * murdărire DINAMICĂ (model Kimber): acumulare pe zile uscate, spălare la
    ploaie — în locul unui procent fix;
  * model de temperatură Faiman ca alternativă la SAPM (doi parametri,
    calibrabili din producția reală a parcului);
  * randament de invertor dependent de încărcare (curbă), în locul unei
    valori constante — contează exact în orele de dimineață/seară;
  * re-derivarea opțională a DNI/DHI din GHI (modele de separare) și
    impunerea închiderii fizice GHI = DHI + DNI·cos(θz).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pvlib import irradiance, temperature, pvsystem, tracking, iam as iam_module, snow as snow_mod, soiling

from app.core.solar_position import calculeaza_pozitie_solara
from app.models import (ConfigPV, DirectieUmbrire, ModelTemperaturaCelula,
                         ModSeparareRadiatie, Subansamblu, TipAsezarePV)

COLOANE_METEO_NECESARE = ["ghi", "dni", "dhi", "temp_aer_c", "viteza_vant_ms"]

# Parametri impliciți pentru tracker cu un ax orizontal nord-sud (R-411).
# Nu sunt câmpuri în Anexa B — sunt valori industriale uzuale, documentate aici.
TRACKER_MAX_ANGLE = 60.0
TRACKER_GCR = 0.40

# Strat de zăpadă (m) de la care se folosește albedo-ul de zăpadă.
PRAG_STRAT_ZAPADA_M = 0.01


def _mascheaza_umbrire(poa_direct: pd.Series, solar_zenith: pd.Series, solar_azimuth: pd.Series,
                        prag_grade: float, directie: DirectieUmbrire) -> pd.Series:
    """R-414 — umbrire de orizont simplă: sub un anumit unghi de elevație,
    pe direcția declarată, soarele e blocat geometric -> componenta directă
    dispare. NU se modelează un profil complet de orizont, intenționat."""
    if prag_grade <= 0:
        return poa_direct
    elevatie = 90.0 - solar_zenith
    blocat_elevatie = elevatie < prag_grade
    if directie == DirectieUmbrire.EST:
        blocat_directie = solar_azimuth < 180.0
    elif directie == DirectieUmbrire.VEST:
        blocat_directie = solar_azimuth >= 180.0
    else:  # AMBELE
        blocat_directie = pd.Series(True, index=poa_direct.index)
    blocat = blocat_elevatie & blocat_directie
    return poa_direct.where(~blocat, 0.0)


def _unghiuri_suprafata(sub: Subansamblu, poz_solara: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Întoarce (surface_tilt, surface_azimuth) pentru un subansamblu, oră cu
    oră. Fix/est-vest: unghiuri constante, declarate de operator. Tracker:
    calculate dinamic din poziția solară (urmărire pe un ax orizontal)."""
    if sub.tip_asezare == TipAsezarePV.TRACKER:
        axis_azimuth = sub.azimut_grade if sub.azimut_grade is not None else 0.0
        rez = tracking.singleaxis(
            apparent_zenith=poz_solara["apparent_zenith"],
            solar_azimuth=poz_solara["azimuth"],  # pvlib >=0.13.1: fostul apparent_azimuth
            axis_tilt=0, axis_azimuth=axis_azimuth,
            max_angle=TRACKER_MAX_ANGLE, backtrack=True, gcr=TRACKER_GCR,
        )
        tilt = rez["surface_tilt"].fillna(0.0)
        azimut = rez["surface_azimuth"].fillna(axis_azimuth)
        return tilt, azimut
    # fix sau est-vest: unghiuri declarate, constante în timp
    inclinare = sub.inclinare_grade if sub.inclinare_grade is not None else 30.0
    azimut = sub.azimut_grade if sub.azimut_grade is not None else 180.0
    return (pd.Series(inclinare, index=poz_solara.index),
            pd.Series(azimut, index=poz_solara.index))


def _albedo_serie(config: ConfigPV, meteo: pd.DataFrame) -> pd.Series | float:
    """Albedo-ul solului, cu comutare pe valoarea de zăpadă cât timp există
    strat de zăpadă. Zăpada urcă albedo-ul de la ~0,2 la 0,6-0,8, ceea ce la
    module înclinate adaugă un procent semnificativ de radiație reflectată —
    exact în perioada în care restul modelului tinde să subestimeze."""
    if "strat_zapada_m" not in meteo.columns:
        return config.albedo
    strat = pd.to_numeric(meteo["strat_zapada_m"], errors="coerce").fillna(0.0)
    return pd.Series(np.where(strat > PRAG_STRAT_ZAPADA_M, config.albedo_zapada, config.albedo),
                      index=meteo.index)


def _aplica_separare(config: ConfigPV, meteo: pd.DataFrame, poz_solara: pd.DataFrame) -> pd.DataFrame:
    """Re-derivă DNI/DHI din GHI cu un model de separare, dacă operatorul a
    ales asta. DNI-ul întors direct de modelele NWP e în general cel mai slab
    câmp radiativ, deci merită testat per parc — dar nu impunem schimbarea,
    pentru că rezultatul depinde de locație (vezi cercetarea, §1)."""
    mod = config.mod_separare_radiatie
    if mod == ModSeparareRadiatie.NWP:
        return meteo

    ghi = meteo["ghi"].clip(lower=0.0)
    zenit = poz_solara["apparent_zenith"]
    rezultat = meteo.copy()
    try:
        if mod == ModSeparareRadiatie.ERBS:
            out = irradiance.erbs(ghi, zenit, meteo.index)
            rezultat["dni"], rezultat["dhi"] = out["dni"], out["dhi"]
        elif mod == ModSeparareRadiatie.BOLAND:
            out = irradiance.boland(ghi, zenit, meteo.index)
            rezultat["dni"], rezultat["dhi"] = out["dni"], out["dhi"]
        elif mod in (ModSeparareRadiatie.DIRINT, ModSeparareRadiatie.DIRINDEX):
            temp_rocia = None
            dni = irradiance.dirint(ghi, zenit, meteo.index, temp_dew=temp_rocia)
            if mod == ModSeparareRadiatie.DIRINDEX:
                from app.core.clearsky import irradianta_cer_senin
                cs = irradianta_cer_senin(meteo.index, poz_solara.attrs.get("lat", 0.0),
                                           poz_solara.attrs.get("lon", 0.0))
                dni = irradiance.dirindex(ghi, cs["ghi"], cs["dni"], zenit, meteo.index)
            dni = pd.Series(dni, index=meteo.index).fillna(0.0).clip(lower=0.0)
            cos_z = np.cos(np.radians(zenit)).clip(lower=0.0)
            rezultat["dni"] = dni
            rezultat["dhi"] = (ghi - dni * cos_z).clip(lower=0.0)
    except Exception:
        # un model de separare care eșuează nu trebuie să oprească prognoza —
        # rămânem pe valorile din sursa meteo
        return meteo
    return rezultat


def _putere_dc_subansamblu(sub: Subansamblu, config: ConfigPV, meteo: pd.DataFrame,
                            poz_solara: pd.DataFrame, albedo) -> tuple[pd.Series, pd.Series]:
    """Întoarce (putere_dc_kw, poa_global) — POA e păstrată pentru că modelul
    de zăpadă are nevoie de radiația pe planul modulului."""
    tilt, azimut = _unghiuri_suprafata(sub, poz_solara)

    model = config.model_transpozitie.value
    total_irr = irradiance.get_total_irradiance(
        surface_tilt=tilt, surface_azimuth=azimut,
        solar_zenith=poz_solara["apparent_zenith"], solar_azimuth=poz_solara["azimuth"],
        dni=meteo["dni"], ghi=meteo["ghi"], dhi=meteo["dhi"],
        dni_extra=poz_solara["dni_extra"], albedo=albedo, model=model,
    )
    poa_direct = _mascheaza_umbrire(
        total_irr["poa_direct"], poz_solara["apparent_zenith"], poz_solara["azimuth"],
        config.umbrire_orizont_grade, config.umbrire_orizont_directie,
    )

    # Unghiul de incidență (R-510, pas explicit) -> pierdere prin reflexie la
    # incidență razantă, model fizic standard (Martin & Ruiz / De Soto).
    aoi = irradiance.aoi(tilt, azimut, poz_solara["apparent_zenith"], poz_solara["azimuth"])
    factor_iam = iam_module.physical(aoi.clip(lower=0, upper=90))
    poa_direct_dupa_iam = poa_direct * factor_iam.fillna(0.0)
    poa_difuz = (total_irr["poa_diffuse"]).fillna(0.0)
    poa_global = (poa_direct_dupa_iam + poa_difuz).clip(lower=0)

    temp_celula = _temperatura_celula(config, poa_global, meteo)

    gamma_pdc = -config.coeficient_temperatura() / 100.0
    dc_kw = pvsystem.pvwatts_dc(
        effective_irradiance=poa_global, temp_cell=temp_celula,  # pvlib >=0.13.0: fostul g_poa_effective
        pdc0=sub.putere_dc_kwp, gamma_pdc=gamma_pdc, temp_ref=25.0,
    )
    return dc_kw.clip(lower=0).fillna(0.0), poa_global


def _temperatura_celula(config: ConfigPV, poa_global: pd.Series, meteo: pd.DataFrame) -> pd.Series:
    """SAPM (arhetipuri de montaj) sau Faiman (doi parametri, calibrabili).

    Faiman e forma standardizată IEC 61853-2:
        T_cel = T_aer + POA / (U0 + U1·v_vant)
    """
    if config.model_temperatura == ModelTemperaturaCelula.FAIMAN:
        return temperature.faiman(
            poa_global=poa_global, temp_air=meteo["temp_aer_c"],
            wind_speed=meteo["viteza_vant_ms"], u0=config.faiman_u0, u1=config.faiman_u1,
        )
    parametri_termici = temperature.TEMPERATURE_MODEL_PARAMETERS["sapm"][config.model_termic_montaj.value]
    return temperature.sapm_cell(
        poa_global=poa_global, temp_air=meteo["temp_aer_c"], wind_speed=meteo["viteza_vant_ms"],
        a=parametri_termici["a"], b=parametri_termici["b"], deltaT=parametri_termici["deltaT"],
    )


def _factor_degradare(config: ConfigPV, momente: pd.DatetimeIndex) -> pd.Series:
    if not config.data_punerii_in_functiune:
        return pd.Series(1.0, index=momente)
    punere = pd.Timestamp(config.data_punerii_in_functiune, tz="UTC")
    ani = (momente - punere).total_seconds() / (365.25 * 24 * 3600)
    ani = np.clip(ani, 0, None)
    factor = 1.0 - (config.degradare_anuala_pct / 100.0) * ani
    return pd.Series(np.clip(factor, 0.0, 1.0), index=momente)


def _randament_invertor(config: ConfigPV, fractie_incarcare: pd.Series) -> pd.Series:
    """Randamentul invertorului. Cu o curbă declarată, randamentul scade la
    încărcări mici (realitatea fizică: pierderile de mers în gol sunt ~fixe),
    ceea ce contează tocmai în orele de dimineață și seară, acolo unde
    modelul cu randament constant supraestimează sistematic."""
    if not config.curba_randament_invertor:
        return pd.Series(config.randament_invertor, index=fractie_incarcare.index)
    puncte = sorted(config.curba_randament_invertor, key=lambda p: p[0])
    xs = [p[0] for p in puncte]
    ys = [p[1] for p in puncte]
    valori = np.interp(fractie_incarcare.clip(lower=0, upper=1), xs, ys, left=ys[0], right=ys[-1])
    return pd.Series(valori, index=fractie_incarcare.index).clip(lower=0.0, upper=1.0)


def _factor_zapada(config: ConfigPV, meteo: pd.DataFrame, poa_global: pd.Series,
                    inclinare_medie: float) -> pd.Series:
    """Fracția de putere rămasă după acoperirea cu zăpadă (1 = fără pierdere).

    Model NREL (Marion et al. 2013): zăpada acoperă modulul la ninsoare și
    alunecă atunci când radiația pe plan și temperatura permit; pierderea de
    putere e proporțională cu numărul de șiruri acoperite. Fără acest model,
    zilele cu zăpadă produc erori de 50-100%, pe care nici corectorul ML nu
    le poate anticipa dacă nu "vede" zăpada.
    """
    if not config.pierderi_zapada or "zapada_cm" not in meteo.columns:
        return pd.Series(1.0, index=meteo.index)
    ninsoare = pd.to_numeric(meteo["zapada_cm"], errors="coerce").fillna(0.0)
    if not (ninsoare > 0).any() and "strat_zapada_m" not in meteo.columns:
        return pd.Series(1.0, index=meteo.index)
    try:
        acoperire = snow_mod.coverage_nrel(
            snowfall=ninsoare,
            poa_irradiance=poa_global,
            temp_air=meteo["temp_aer_c"],
            surface_tilt=inclinare_medie,
        )
        # num_strings: numărul de șiruri pe verticală; 8 e o valoare uzuală și
        # face pierderea gradată în loc de tot-sau-nimic
        pierdere = snow_mod.dc_loss_nrel(acoperire, num_strings=8)
        return (1.0 - pd.Series(pierdere, index=meteo.index).fillna(0.0)).clip(lower=0.0, upper=1.0)
    except Exception:
        return pd.Series(1.0, index=meteo.index)


def _factor_murdarire(config: ConfigPV, meteo: pd.DataFrame) -> pd.Series:
    """Fracția rămasă după murdărire. Dinamic (Kimber) dacă e activat ȘI
    există date de precipitații; altfel procentul fix din configurație."""
    if not config.soiling_dinamic or "precipitatii_mm" not in meteo.columns:
        return pd.Series(1.0 - config.pierdere_murdarire_pct / 100.0, index=meteo.index)
    ploaie = pd.to_numeric(meteo["precipitatii_mm"], errors="coerce").fillna(0.0)
    try:
        pierdere = soiling.kimber(
            rainfall=ploaie,
            cleaning_threshold=config.soiling_prag_ploaie_mm,
            soiling_loss_rate=config.soiling_rata_zi_pct / 100.0,
            grace_period=14,
            max_soiling=0.3,
        )
        return (1.0 - pd.Series(pierdere, index=meteo.index).fillna(0.0)).clip(lower=0.0, upper=1.0)
    except Exception:
        return pd.Series(1.0 - config.pierdere_murdarire_pct / 100.0, index=meteo.index)


def _factor_pierderi_fara_murdarire(config: ConfigPV) -> float:
    """Compunerea multiplicativă a pierderilor de sistem, FĂRĂ murdărire
    (care e tratată separat, ca să poată fi dinamică)."""
    pierderi = [
        config.pierdere_neuniformitate_pct, config.pierdere_cablaj_conexiuni_pct,
        config.pierdere_lid_pct, config.pierdere_placuta_pct,
        config.pierdere_indisponibilitate_pct,
        config.pierdere_nemodelata_pct,
    ]
    factor = 1.0
    for p in pierderi:
        factor *= (1 - p / 100.0)
    return factor


def calculeaza_productie_pv(config: ConfigPV, latitudine: float, longitudine: float,
                             altitudine_m: float, meteo: pd.DataFrame,
                             poz_solara: pd.DataFrame | None = None) -> pd.DataFrame:
    """meteo: DataFrame indexat pe timestamp UTC (eticheta orei FINALE, R-512),
    cu coloanele COLOANE_METEO_NECESARE (plus, opțional, zapada_cm,
    strat_zapada_m, precipitatii_mm). Întoarce un DataFrame cu rezultatele
    intermediare + finale ale lanțului, păstrate pt. trasabilitate (R-1201).

    `poz_solara` poate fi pre-calculată de apelant. Nu schimbă rezultatul — e
    strict o economie de timp pentru apelanții care rulează lanțul de zeci de
    ori peste ACELEAȘI momente și aceeași locație (calibrarea geometriei,
    analiza de sensibilitate), unde poziția solară e identică de fiecare dată.
    """
    lipsa = [c for c in COLOANE_METEO_NECESARE if c not in meteo.columns]
    if lipsa:
        raise ValueError(f"Coloane meteo lipsă pentru motorul PV: {lipsa}")

    if poz_solara is None:
        poz_solara = calculeaza_pozitie_solara(meteo.index, latitudine, longitudine, altitudine_m)
    poz_solara.attrs["lat"], poz_solara.attrs["lon"] = latitudine, longitudine
    meteo = _aplica_separare(config, meteo, poz_solara)
    albedo = _albedo_serie(config, meteo)

    subansambluri = config.subansambluri or [
        Subansamblu(nume="implicit", putere_dc_kwp=config.putere_dc_kwp,
                    inclinare_grade=None, azimut_grade=None, tip_asezare=TipAsezarePV.FIX)
    ]

    putere_dc_totala_declarata = sum(s.putere_dc_kwp for s in subansambluri) or config.putere_dc_kwp
    dc_total = pd.Series(0.0, index=meteo.index)
    poa_ponderat = pd.Series(0.0, index=meteo.index)
    ac_per_subansamblu = pd.Series(0.0, index=meteo.index)
    inclinari = []

    for sub in subansambluri:
        dc_sub, poa_sub = _putere_dc_subansamblu(sub, config, meteo, poz_solara, albedo)
        dc_total = dc_total.add(dc_sub, fill_value=0.0)
        pondere = (sub.putere_dc_kwp / putere_dc_totala_declarata) if putere_dc_totala_declarata else 0.0
        poa_ponderat = poa_ponderat.add(poa_sub * pondere, fill_value=0.0)
        inclinari.append(sub.inclinare_grade if sub.inclinare_grade is not None else 30.0)

        if config.clipping_per_subansamblu:
            # topologie cu invertor separat per subansamblu: limita AC se
            # împarte proporțional cu puterea DC a fiecăruia
            limita_sub = config.putere_ac_kw * pondere
            randament = _randament_invertor(config, (dc_sub / max(limita_sub, 1e-9)).clip(0, 1))
            ac_per_subansamblu = ac_per_subansamblu.add(
                (dc_sub * randament).clip(upper=limita_sub), fill_value=0.0)

    if config.clipping_per_subansamblu:
        ac_dupa_clipping = ac_per_subansamblu
    else:
        # R-412 — implicit: un singur invertor, clipping pe SUMA puterilor DC
        fractie_bruta = (dc_total / max(config.putere_ac_kw, 1e-9)).clip(lower=0, upper=2.0)
        randament = _randament_invertor(config, fractie_bruta)
        ac_dupa_invertor = dc_total * randament
        ac_dupa_clipping = ac_dupa_invertor.clip(upper=config.putere_ac_kw)

    fractie_incarcare = (ac_dupa_clipping / config.putere_ac_kw).clip(lower=0, upper=1)

    factor_murdarire = _factor_murdarire(config, meteo)
    factor_zapada = _factor_zapada(config, meteo, poa_ponderat,
                                    float(np.mean(inclinari)) if inclinari else 30.0)

    ac_dupa_pierderi = (ac_dupa_clipping
                         * _factor_pierderi_fara_murdarire(config)
                         * factor_murdarire
                         * factor_zapada)
    ac_dupa_degradare = ac_dupa_pierderi * _factor_degradare(config, meteo.index)

    ac_final = ac_dupa_degradare
    if config.limita_injectie_kw is not None:
        ac_final = ac_final.clip(upper=config.limita_injectie_kw)
    ac_final = ac_final.clip(lower=0).fillna(0.0)

    return pd.DataFrame({
        "putere_dc_kw": dc_total,
        "putere_ac_fizic_kw": ac_final,
        "fractie_incarcare": fractie_incarcare,
        "solar_zenith": poz_solara["apparent_zenith"],
        "solar_azimuth": poz_solara["azimuth"],
        "poa_global_wm2": poa_ponderat,
        "factor_zapada": factor_zapada,
        "factor_murdarire": factor_murdarire,
    }, index=meteo.index)
