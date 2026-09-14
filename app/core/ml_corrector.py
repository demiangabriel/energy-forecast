"""
Stratul de învățare automată — corector pe REZIDUU (secțiunea 6, R-601-606,
R-620-623).

Principiul de bază (R-601): modelul NU învață producția absolută, ci doar
diferența (reziduul) dintre producția reală și puterea calculată de modelul
fizic. Corectează exact ce fizica nu poate ști (umbriri nedeclarate,
murdărire, biasul sistematic al sursei meteo pe locație) fără să reînvețe
geometria solară de la zero.

Algoritm: gradient boosting pe arbori de decizie — LightGBM (R-602).

R-605: o antrenare nouă devine ACTIVĂ doar dacă e măsurabil mai bună decât
versiunea curent activă, evaluate pe ACELAȘI set de validare. Dacă iese mai
slabă, se salvează pentru istoric, dar rămâne INACTIVĂ — versiunea veche
continuă să lucreze, fără nicio intervenție manuală. Acesta e chiar
mecanismul de rollback automat (R-1505).

--------------------------------------------------------------------------
ÎMBUNĂTĂȚIRI v2 (docs/cercetare/03-literatura-stiintifica.md, §3-§5, §8):

 1. **Ținta e normalizată** la referința de cer senin (fotovoltaic) sau la
    puterea instalată (eolian). Pe scara kW bruți, arborii cheltuiesc
    capacitate pe variația deterministă zi/noapte-anotimp; pe scara indicelui
    de senin rămâne exact ce trebuie învățat — eroarea de nebulozitate.

 2. **Setul de intrări e mult mai bogat**: valori NWP decalate în timp
    (t−3h, t−1h, t+1h, t+3h) care permit corectarea erorilor de SINCRONIZARE
    ale modelului meteo, diferențe temporale, acoperirea cu nori pe straturi,
    umiditate, direcția vântului (siaj/teren), exponentul de forfecare
    (stabilitate atmosferică) și dispersia între modelele meteo.

 3. **Orele cu probleme de calitate sunt EXCLUSE** din antrenare
    (indisponibilitate, limitare de putere, valori blocate, outlieri) — altfel
    modelul învață defecțiunile parcului, nu meteorologia.

 4. **Promovarea se decide pe SCORUL DE SKILL** față de o prognoză de
    referință (persistență inteligentă / climatologie), nu pe MAE brut, și
    doar pe orele utile (zi, la fotovoltaic).

 5. **Cuantile** (P10/P50/P90 etc.) antrenate cu funcția de pierdere pinball,
    plus o calibrare conformă pe o fereastră de calibrare — ca intervalele
    să aibă acoperirea promisă, nu doar o formă plauzibilă.
"""
from __future__ import annotations

from datetime import datetime, timezone

import lightgbm as lgb
import numpy as np
import pandas as pd

from app.config import BASE_DIR
from app.core.clearsky import masca_ore_utile, putere_referinta
from app.core.data_quality import ore_excluse
from app.core.istoric_productie import citeste_productie_df
from app.core.verification import referinta_optima, scor_skill
from app.core.weather_historical import reconstruieste_prognoza_fizica
from app.models import ModelMLInfo, Parc, Tehnologie, new_id

MODELE_DIR = BASE_DIR / "data" / "modele"
FRACTIE_VALIDARE = 0.15  # ultimele ~15% din date, cronologic — evită scurgeri din viitor spre trecut
FRACTIE_CALIBRARE = 0.15  # fereastra pentru calibrarea conformă a intervalelor

NIVELURI_CUANTILE = [0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95]

PARAMETRI_LGB = {
    "objective": "regression", "metric": "mae", "verbosity": -1,
    "num_leaves": 31, "learning_rate": 0.05, "min_data_in_leaf": 20,
}
RUNDE_BOOSTING = 300

# Decalajele temporale aplicate variabilelor meteo (ore). Corectează erorile
# de sincronizare ale modelului numeric — o clasă de erori sistematice pe care
# un model fără context temporal nu are cum să o vadă.
DECALAJE_ORE = [-3, -1, 1, 3]

# Variabilele meteo care primesc decalaje și diferențe, dacă există în date.
VARIABILE_CONTEXT_PV = ["ghi_nwp", "nori_total_pct", "nori_jos_pct", "temp_aer_c"]
VARIABILE_CONTEXT_EOLIAN = ["viteza_vant_10m_ms", "viteza_echivalenta_ms", "nori_total_pct"]


class DateInsuficienteError(ValueError):
    pass


# --------------------------------------------------------------------------- construcția intrărilor

def construieste_features(cadru: pd.DataFrame, tehnologie: Tehnologie) -> pd.DataFrame:
    """Intrările corectorului. R-604: fracția de încărcare a invertorului e
    OBLIGATORIE — puterea fizică (kW) împărțită la puterea nominală AC.

    `cadru` e rezultatul modelului fizic, la care forecast_engine /
    weather_historical au ATAȘAT coloanele meteo relevante. Folosim un singur
    cadru pentru a garanta că antrenarea și predicția văd exact aceleași
    intrări — orice divergență între ele e o sursă clasică de model care
    "merge la antrenare și nu merge în producție".
    """
    idx = cadru.index
    ora_fractionara = idx.hour + idx.minute / 60.0
    zi_an = idx.dayofyear.values.astype(float)

    f = pd.DataFrame(index=idx)
    f["fractie_incarcare"] = cadru["fractie_incarcare"].fillna(0.0) if "fractie_incarcare" in cadru else 0.0
    f["ora_sin"] = np.sin(2 * np.pi * ora_fractionara / 24.0)
    f["ora_cos"] = np.cos(2 * np.pi * ora_fractionara / 24.0)
    f["zi_an_sin"] = np.sin(2 * np.pi * zi_an / 365.25)
    f["zi_an_cos"] = np.cos(2 * np.pi * zi_an / 365.25)

    def preia(nume: str, implicit: float = np.nan) -> pd.Series:
        if nume in cadru.columns:
            return pd.to_numeric(cadru[nume], errors="coerce")
        return pd.Series(implicit, index=idx, dtype="float64")

    if tehnologie == Tehnologie.FOTOVOLTAIC:
        f["solar_zenith"] = preia("solar_zenith")
        f["solar_azimuth"] = preia("solar_azimuth")
        f["dispersie_ghi"] = preia("dispersie_ghi", 0.0)
        f["poa_global"] = preia("poa_global_wm2", 0.0)
        f["indice_senin_nwp"] = preia("indice_senin_nwp", 1.0)
        f["factor_zapada"] = preia("factor_zapada", 1.0)
        variabile_context = VARIABILE_CONTEXT_PV
    else:
        f["viteza_echivalenta_ms"] = preia("viteza_echivalenta_ms")
        f["viteza_butuc_ms"] = preia("viteza_butuc_ms")
        f["dispersie_vant"] = preia("dispersie_vant", 0.0)
        f["exponent_forfecare"] = preia("exponent_forfecare", 1.0 / 7.0)
        f["densitate_aer"] = preia("densitate_aer", 1.225)
        # direcția vântului ca pereche sin/cos — o direcție e o mărime
        # circulară; 359° și 1° sunt vecine, ceea ce un număr simplu nu spune
        directie = preia("directie_vant_grade")
        f["directie_sin"] = np.sin(np.radians(directie))
        f["directie_cos"] = np.cos(np.radians(directie))
        variabile_context = VARIABILE_CONTEXT_EOLIAN

    # variabile meteo comune, dacă sursa le-a oferit
    for nume in ["nori_total_pct", "nori_jos_pct", "nori_mediu_pct", "nori_sus_pct",
                  "umiditate_pct", "temp_aer_c", "viteza_vant_ms"]:
        if nume in cadru.columns:
            f[nume] = preia(nume)

    # context temporal: decalaje și diferențe pe variabilele informative
    for nume in variabile_context:
        if nume not in cadru.columns:
            continue
        baza = preia(nume)
        for decalaj in DECALAJE_ORE:
            f[f"{nume}_dec{decalaj:+d}"] = baza.shift(decalaj)
        f[f"{nume}_delta1"] = baza.diff()

    return f.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def pregateste_cadru(fizic: pd.DataFrame, medie_meteo: pd.DataFrame | None,
                      dispersie: pd.DataFrame | None,
                      referinta_cer_senin: pd.Series | None = None) -> pd.DataFrame:
    """Unește rezultatul fizic cu contextul meteo într-un singur cadru, folosit
    identic la antrenare și la predicție."""
    cadru = fizic.copy()
    if medie_meteo is not None and len(medie_meteo):
        meteo = medie_meteo.reindex(cadru.index)
        redenumiri = {
            "shortwave_radiation": "ghi_nwp",
            "temperature_2m": "temp_aer_c",
            "wind_speed_10m": "viteza_vant_ms",
            "cloud_cover": "nori_total_pct",
            "cloud_cover_low": "nori_jos_pct",
            "cloud_cover_mid": "nori_mediu_pct",
            "cloud_cover_high": "nori_sus_pct",
            "relative_humidity_2m": "umiditate_pct",
        }
        for sursa, destinatie in redenumiri.items():
            if sursa in meteo.columns and destinatie not in cadru.columns:
                cadru[destinatie] = meteo[sursa]
    if dispersie is not None and len(dispersie):
        disp = dispersie.reindex(cadru.index)
        if "shortwave_radiation" in disp.columns and "dispersie_ghi" not in cadru.columns:
            cadru["dispersie_ghi"] = disp["shortwave_radiation"]
        if "wind_speed_10m" in disp.columns and "dispersie_vant" not in cadru.columns:
            cadru["dispersie_vant"] = disp["wind_speed_10m"]
    if referinta_cer_senin is not None:
        cadru["putere_clearsky_kw"] = referinta_cer_senin.reindex(cadru.index)
        # indicele de senin al PROGNOZEI (nu al realizatului): cât de senin
        # spune modelul meteo că va fi, o intrare direct utilă pentru ML
        referinta = cadru["putere_clearsky_kw"]
        with np.errstate(divide="ignore", invalid="ignore"):
            cadru["indice_senin_nwp"] = (cadru["putere_ac_fizic_kw"]
                                          / referinta.where(referinta > 1e-6))
        cadru["indice_senin_nwp"] = cadru["indice_senin_nwp"].replace(
            [np.inf, -np.inf], np.nan).clip(0, 1.5).fillna(0.0)
    return cadru


# --------------------------------------------------------------------------- antrenare

def _scara_normalizare(parc: Parc, cadru: pd.DataFrame, putere_instalata: float) -> pd.Series:
    """Scara pe care se exprimă reziduul. Solar: puterea de cer senin (variază
    oră cu oră); eolian: puterea instalată (constantă). Podeaua evită
    împărțirile instabile la răsărit/apus."""
    if parc.tehnologie == Tehnologie.FOTOVOLTAIC and "putere_clearsky_kw" in cadru.columns:
        scara = pd.to_numeric(cadru["putere_clearsky_kw"], errors="coerce")
        return scara.clip(lower=max(putere_instalata * 0.02, 1e-6))
    return pd.Series(max(putere_instalata, 1e-6), index=cadru.index)


def antreneaza_model(repo, parc: Parc, prag_min_ore: int,
                      antreneaza_cuantile: bool = True) -> ModelMLInfo:
    """Rulează întregul ciclu de antrenare pentru un parc și salvează
    rezultatul (activ sau nu) în repository. Ridică DateInsuficienteError
    dacă nu există destule ore utile de producție reală (R-606)."""
    from app.core.forecast_engine import putere_instalata_kw

    df_prod = citeste_productie_df(repo, parc.id)
    if df_prod.empty:
        raise DateInsuficienteError(
            "Nu există nicio oră de producție reală importată pentru acest parc — "
            "importați istoricul (secțiunea 11) înainte de antrenare."
        )

    fizic = reconstruieste_prognoza_fizica(parc, df_prod.index.min().date(), df_prod.index.max().date())

    comun = df_prod.index.intersection(fizic.index)
    cadru = fizic.loc[comun]
    productie = df_prod.loc[comun, "productie_kwh"]

    putere_instalata = putere_instalata_kw(parc)

    # referința de cer senin pe aceleași ore (pentru scară, filtru de zi și
    # prognoza de referință folosită la scorul de skill)
    referinta_cs = putere_referinta(
        parc, cadru.index,
        cadru["temp_aer_c"] if "temp_aer_c" in cadru.columns else None,
        cadru["viteza_vant_ms"] if "viteza_vant_ms" in cadru.columns else None,
    )
    if "putere_clearsky_kw" not in cadru.columns:
        cadru = cadru.copy()
        cadru["putere_clearsky_kw"] = referinta_cs

    # --- filtrul 1: doar orele utile (zi, la fotovoltaic)
    utile = masca_ore_utile(parc, referinta_cs, putere_instalata)
    cadru, productie = cadru[utile], productie[utile]

    # --- filtrul 2: orele marcate de controlul de calitate
    excluse = ore_excluse(repo, parc.id)
    numar_excluse = 0
    if len(excluse):
        de_scos = cadru.index.intersection(excluse)
        numar_excluse = int(len(de_scos))
        cadru = cadru.drop(de_scos)
        productie = productie.drop(de_scos)

    if len(cadru) < prag_min_ore:
        raise DateInsuficienteError(
            f"Doar {len(cadru)} ore utile de date disponibile după filtrarea orelor de noapte "
            f"și a celor marcate de controlul de calitate ({numar_excluse} excluse) — "
            f"prag minim configurat: {prag_min_ore}. Antrenarea e refuzată explicit (R-606)."
        )

    scara = _scara_normalizare(parc, cadru, putere_instalata)
    fizic_kw = cadru["putere_ac_fizic_kw"]
    reziduu_normalizat = ((productie - fizic_kw) / scara).clip(-2.0, 2.0)
    features = construieste_features(cadru, parc.tehnologie)

    idx_sortat = features.index.sort_values()
    n_valid = max(1, int(len(idx_sortat) * FRACTIE_VALIDARE))
    idx_train, idx_valid = idx_sortat[:-n_valid], idx_sortat[-n_valid:]
    if len(idx_train) < 20:
        raise DateInsuficienteError(
            f"Prea puține ore pentru antrenare după împărțirea cronologică "
            f"({len(idx_train)} ore de antrenare). Importați mai mult istoric."
        )

    X_train, y_train = features.loc[idx_train], reziduu_normalizat.loc[idx_train]
    X_valid = features.loc[idx_valid]
    actual_valid = productie.loc[idx_valid]
    fizic_valid = fizic_kw.loc[idx_valid]
    scara_valid = scara.loc[idx_valid]

    model = lgb.train(PARAMETRI_LGB, lgb.Dataset(X_train, label=y_train), num_boost_round=RUNDE_BOOSTING)

    predictie_candidat = _aplica(model, X_valid, fizic_valid, scara_valid, putere_instalata)
    mae_candidat = float(np.abs(predictie_candidat - actual_valid).mean())
    rmse_candidat = float(np.sqrt(((predictie_candidat - actual_valid) ** 2).mean()))
    mae_fizic_pur = float(np.abs(fizic_valid - actual_valid).mean())

    # --- prognoza de referință și scorurile de skill, pe ACELEAȘI ore de validare
    referinta_valid = referinta_optima(
        actual_valid,
        referinta_cs.reindex(actual_valid.index) if parc.tehnologie == Tehnologie.FOTOVOLTAIC else None,
        ore_in_avans=24.0,
    )
    skill_candidat = scor_skill(predictie_candidat, actual_valid, referinta_valid)

    model_activ_curent = repo.model_ml_activ(parc.id)
    if model_activ_curent:
        try:
            model_vechi = lgb.Booster(model_file=model_activ_curent.cale_fisier)
            predictie_veche = _aplica(model_vechi, X_valid, fizic_valid, scara_valid, putere_instalata,
                                       tinta=model_activ_curent.tinta_antrenare)
            mae_baseline = float(np.abs(predictie_veche - actual_valid).mean())
            skill_baseline = scor_skill(predictie_veche, actual_valid, referinta_valid)
        except Exception:
            # modelul vechi nu se mai poate încărca (fișier lipsă / format schimbat)
            mae_baseline, skill_baseline = mae_fizic_pur, scor_skill(
                fizic_valid, actual_valid, referinta_valid)
    else:
        mae_baseline = mae_fizic_pur
        skill_baseline = scor_skill(fizic_valid, actual_valid, referinta_valid)

    # R-605 reinterpretat: criteriul principal e SKILL-ul (mai bun decât
    # referința), cu MAE ca departajare când skill-ul nu se poate calcula.
    if skill_candidat is not None and skill_baseline is not None:
        devine_activ = skill_candidat > skill_baseline
    else:
        devine_activ = mae_candidat < mae_baseline

    model_id = new_id()
    director = MODELE_DIR / parc.id
    director.mkdir(parents=True, exist_ok=True)
    cale_fisier = str(director / f"{model_id}.txt")
    model.save_model(cale_fisier)

    cai_cuantile: dict[str, str] = {}
    corectie_conforma: dict[str, float] = {}
    if antreneaza_cuantile and parc.produce_cuantile and len(idx_train) >= 100:
        cai_cuantile, corectie_conforma = _antreneaza_cuantile(
            X_train, y_train, X_valid, actual_valid, fizic_valid, scara_valid,
            putere_instalata, director, model_id,
        )

    note = (f"MAE candidat={mae_candidat:.2f} kW, referință anterioară={mae_baseline:.2f} kW "
            f"(fizică pură={mae_fizic_pur:.2f} kW). "
            f"Skill candidat={_format_skill(skill_candidat)}, anterior={_format_skill(skill_baseline)}. "
            f"{len(idx_valid)} ore de validare, {numar_excluse} ore excluse de controlul de calitate.")

    info = ModelMLInfo(
        id=model_id, parc_id=parc.id, antrenat_la=datetime.now(timezone.utc).isoformat(),
        activ=devine_activ, mae_validare_kw=mae_candidat, mae_model_anterior_kw=mae_baseline,
        numar_ore_antrenare=len(idx_train), cale_fisier=cale_fisier, note=note,
        skill_vs_referinta=skill_candidat, skill_model_anterior=skill_baseline,
        rmse_validare_kw=rmse_candidat, cai_cuantile=cai_cuantile,
        corectie_conforma_kw=corectie_conforma, tinta_antrenare="reziduu_normalizat",
        numar_ore_excluse_qc=numar_excluse,
    )
    repo.salveaza_model_ml(info)
    return info


def _format_skill(valoare: float | None) -> str:
    return "indisponibil" if valoare is None else f"{valoare:+.3f}"


def _antreneaza_cuantile(X_train, y_train, X_valid, actual_valid, fizic_valid, scara_valid,
                          putere_instalata: float, director, model_id: str
                          ) -> tuple[dict[str, str], dict[str, float]]:
    """Antrenează câte un model per nivel de cuantilă, cu funcția de pierdere
    pinball, apoi calibrează CONFORM intervalele pe ultima parte a setului de
    antrenare.

    Calibrarea conformă e pasul care transformă "niște benzi plauzibile" în
    intervale cu acoperire garantată: se măsoară pe date nevăzute cât de des
    realizarea a căzut în afara benzii și se lărgește banda exact cu cât
    trebuie ca acoperirea observată să atingă nivelul promis.
    """
    cai: dict[str, str] = {}
    n_cal = max(20, int(len(X_train) * FRACTIE_CALIBRARE))
    idx_model = X_train.index[:-n_cal]
    idx_cal = X_train.index[-n_cal:]
    if len(idx_model) < 50:
        idx_model, idx_cal = X_train.index, X_train.index

    modele: dict[float, lgb.Booster] = {}
    for nivel in NIVELURI_CUANTILE:
        params = {**PARAMETRI_LGB, "objective": "quantile", "alpha": nivel, "metric": "quantile"}
        m = lgb.train(params, lgb.Dataset(X_train.loc[idx_model], label=y_train.loc[idx_model]),
                       num_boost_round=RUNDE_BOOSTING)
        cale = str(director / f"{model_id}_q{int(nivel * 100):02d}.txt")
        m.save_model(cale)
        cai[str(nivel)] = cale
        modele[nivel] = m

    # calibrare conformă pe reziduul normalizat, în spațiul în care s-a antrenat
    corectii: dict[str, float] = {}
    y_cal = y_train.loc[idx_cal]
    X_cal = X_train.loc[idx_cal]
    for nivel in NIVELURI_CUANTILE:
        prezis = pd.Series(modele[nivel].predict(X_cal), index=idx_cal)
        if nivel >= 0.5:
            # scorul de conformitate: cât de mult a depășit realizarea cuantila
            scoruri = (y_cal - prezis).clip(lower=0.0).sort_values()
            k = min(len(scoruri) - 1, int(np.ceil((len(scoruri) + 1) * nivel)) - 1)
            corectii[str(nivel)] = float(scoruri.iloc[max(k, 0)]) if len(scoruri) else 0.0
        else:
            scoruri = (prezis - y_cal).clip(lower=0.0).sort_values()
            k = min(len(scoruri) - 1, int(np.ceil((len(scoruri) + 1) * (1 - nivel))) - 1)
            corectii[str(nivel)] = -float(scoruri.iloc[max(k, 0)]) if len(scoruri) else 0.0
    return cai, corectii


def _aplica(model: lgb.Booster, features: pd.DataFrame, fizic_kw: pd.Series,
            scara: pd.Series, putere_instalata: float,
            tinta: str = "reziduu_normalizat") -> pd.Series:
    """Aplică un model peste rezultatul fizic și întoarce puterea finală (kW),
    limitată la intervalul fizic [0, putere instalată]."""
    brut = pd.Series(model.predict(features), index=features.index)
    corectie = brut * scara if tinta == "reziduu_normalizat" else brut
    return (fizic_kw + corectie).clip(lower=0.0, upper=putere_instalata)


# --------------------------------------------------------------------------- aplicarea la prognoză

def _incarca(cale: str) -> lgb.Booster:
    return lgb.Booster(model_file=cale)


def gaseste_si_aplica_corectie(repo, parc: Parc, cadru: pd.DataFrame
                                ) -> tuple[pd.Series | None, str | None, str | None, dict[str, pd.Series]]:
    """R-601-606, R-620-623. Întoarce:
    (corecție în kW sau None, sursă, mesaj informativ sau None, cuantile în kW).

    Sursa e 'propriu' / 'imprumutat:<id_parc>' / None (fizică pură).
    """
    from app.core.forecast_engine import putere_instalata_kw

    model_info = repo.model_ml_activ(parc.id)
    sursa = "propriu"
    mesaj = None

    if not model_info and parc.parc_imprumut_corector_id:
        model_info = repo.model_ml_activ(parc.parc_imprumut_corector_id)
        if not model_info:
            return None, None, (
                f"Parcul ales ca sursă de împrumut a corectorului ({parc.parc_imprumut_corector_id}) "
                f"nu are el însuși niciun model activ — corecția NU se aplică (fizică pură), R-622."
            ), {}
        sursa = f"imprumutat:{parc.parc_imprumut_corector_id}"

    if not model_info:
        return None, None, None, {}

    putere_instalata = putere_instalata_kw(parc)
    features = construieste_features(cadru, parc.tehnologie)
    fizic_kw = cadru["putere_ac_fizic_kw"]
    scara = _scara_normalizare(parc, cadru, putere_instalata)

    try:
        model = _incarca(model_info.cale_fisier)
    except Exception as exc:
        return None, None, f"Modelul activ nu a putut fi încărcat ({exc}) — se folosește fizica pură.", {}

    # R-xxx: modelul PV e antrenat DOAR pe ore de zi (`masca_ore_utile`, la
    # antrenare) — extrapolarea lui la ore de noapte (niciodată văzute la
    # antrenare) e un risc necontrolat, chiar dacă fizica îl ține de obicei
    # aproape de 0. Aplicăm aceeași mască defensiv, la INFERENȚĂ: corecția
    # (și cuantilele) sunt forțate la 0 noaptea, indiferent ce prezice modelul.
    # Nu se aplică la eolian — nu are ciclu zi/noapte.
    ore_utile_zi = None
    if parc.tehnologie == Tehnologie.FOTOVOLTAIC and "putere_clearsky_kw" in cadru.columns:
        ore_utile_zi = masca_ore_utile(
            parc, pd.to_numeric(cadru["putere_clearsky_kw"], errors="coerce").reindex(cadru.index),
            putere_instalata)

    putere_corectata = _aplica(model, features, fizic_kw, scara, putere_instalata,
                                tinta=model_info.tinta_antrenare)
    corectie = putere_corectata - fizic_kw
    if ore_utile_zi is not None:
        corectie = corectie.where(ore_utile_zi, 0.0)

    cuantile: dict[str, pd.Series] = {}
    for nivel_text, cale in (model_info.cai_cuantile or {}).items():
        try:
            model_q = _incarca(cale)
        except Exception:
            continue
        brut = pd.Series(model_q.predict(features), index=features.index)
        ajustare = float((model_info.corectie_conforma_kw or {}).get(nivel_text, 0.0))
        valoare = fizic_kw + (brut + ajustare) * scara
        valoare = valoare.clip(lower=0.0, upper=putere_instalata)
        if ore_utile_zi is not None:
            valoare = valoare.where(ore_utile_zi, fizic_kw)
        cuantile[nivel_text] = valoare

    if cuantile:
        # cuantilele antrenate independent se pot "încrucișa" (P90 sub P50);
        # sortarea pe fiecare oră e corecția standard și păstrează calibrarea
        niveluri = sorted(cuantile, key=float)
        matrice = pd.DataFrame({n: cuantile[n] for n in niveluri})
        sortate = pd.DataFrame(np.sort(matrice.values, axis=1), index=matrice.index, columns=niveluri)
        cuantile = {n: sortate[n] for n in niveluri}

    return corectie, sursa, mesaj, cuantile
