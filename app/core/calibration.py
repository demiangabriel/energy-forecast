"""
Calibrarea geometriei din date istorice — secțiunea 7, R-701-705.

Funcție separată, opțională, pentru parcurile la care operatorul nu cunoaște
exact înclinarea/azimutul reale ale panourilor. Testează automat zeci de
combinații peste vremea REALĂ istorică (reanaliză — vezi
weather_client.obtine_meteo_istoric_real, INTENȚIONAT diferită de arhiva
folosită la antrenarea ML) și le compară cu producția reală măsurată.

R-703: rezultatul se poate APLICA automat doar pentru parcuri cu o singură
orientare fixă — pentru est-vest sau tracker geometria are prea multe grade
de libertate pentru o căutare simplă pe două axe, rezultatul rămâne
informativ.

--------------------------------------------------------------------------
CORECȚII v2 (vezi docs/DEBUGGING.md):

 * Comparația se face DOAR pe orele de zi utile. Înainte, media erorii se
   calcula peste toate orele, inclusiv nopțile, unde toate combinațiile de
   înclinare/azimut dau zero — sute de ore identice care diluau semnalul și
   apropiau artificial scorurile între ele. Practic, rezultatul era decis de
   o minoritate de ore, cu o rezoluție mult mai slabă decât părea.

 * Se exclud orele marcate de controlul de calitate (indisponibilitate,
   limitare, valori blocate): altfel geometria se "calibrează" ca să
   reproducă defecțiuni ale parcului.

 * Se verifică pragul minim DUPĂ intersecția cu datele meteo, nu înainte:
   puteau exista 5000 de ore de producție și doar 100 cu meteo corespondent.

 * Poziția solară se calculează O SINGURĂ DATĂ pentru toate cele ~100 de
   combinații (e identică — depinde doar de timp și locație). Calibrarea a
   devenit de câteva ori mai rapidă, fără nicio schimbare de rezultat.

CORECȚIE v3 (analiză a unui script extern de calibrare furnizat de operator,
care funcționa bine pe alt sistem — comparat cu acest modul ca să se vadă
diferența metodologică):

 * Sondajul de geometrie ERA confundat cu o eroare de SCALĂ. Înainte, fiecare
   combinație de înclinare/azimut era evaluată cu pierderile de sistem și
   degradarea configurate CURENT pe parc — dar acelea sunt EXACT lucrurile pe
   care operatorul nu le cunoaște exact (docstring-ul modulului o spune
   explicit: "nu stii inclinarea exacta a panourilor, nici pierderile reale,
   nici puterea AC efectiva dupa 13 ani de degradare"). Dacă pierderile/
   degradarea configurate sunt greșite, TOATE combinațiile de geometrie erau
   deplasate cu aceeași eroare de magnitudine — iar eroarea absolută (MAE în
   kW) e dominată de acea deplasare constantă, nu de cât de bine se potrivește
   FORMA (variația zilnică/sezonieră) fiecărei geometrii cu producția reală.
   Rezultatul: sondajul putea alege o geometrie greșită doar pentru că
   "întâmplător" se apropia mai mult în magnitudine, nu pentru că avea forma
   corectă.

   Soluția (aceeași ca în scriptul extern analizat): pentru fiecare geometrie
   testată, pierderile de sistem și degradarea sunt SCOASE din model (probă
   "curată"), iar un factor de scalare GLOBAL e potrivit separat, prin cele
   mai mici pătrate, față de producția reală — el absoarbe orice pierdere/
   degradare necunoscută. Geometria e apoi clasată după eroarea rămasă DUPĂ
   scalare, adică după cât de bine se potrivește FORMA, nu magnitudinea.
   Factorul de scalare optim e raportat separat (R-704 tot nu îl aplică
   automat — doar înclinarea/azimutul — dar operatorul vede cât din decalaj
   e geometrie și cât e pierderi/degradare de reconfigurat manual).
"""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd

from app.core.data_quality import ore_excluse
from app.core.istoric_productie import citeste_productie_df
from app.core.meteo_mapping import pregateste_meteo_pv
from app.core.pv_model import calculeaza_productie_pv
from app.core.solar_position import calculeaza_pozitie_solara
from app.core.weather_client import obtine_meteo_istoric_real
from app.models import Parc, Subansamblu, Tehnologie, TipAsezarePV

# "Zeci de combinații" (R-701) — grilă rezonabilă, nu o căutare exhaustivă fină.
GRILA_INCLINARE = [0, 10, 15, 20, 25, 30, 35, 40, 45, 50, 60]
GRILA_AZIMUT = [90, 120, 150, 165, 180, 195, 210, 240, 270]

# Orele cu radiație sub acest prag nu discriminează între geometrii.
PRAG_GHI_UTIL_WM2 = 50.0

# Limitele factorului de scalare — la fel ca în scriptul extern analizat:
# suficient de larg cât să absoarbă pierderi/degradare plauzibile (chiar și
# neconfigurate corect), dar nu atât de larg încât optimizarea să "trișeze"
# potrivind zgomotul în loc de forma reală a producției.
LIMITE_SCALARE = (0.5, 1.5)


class CalibrareIndisponibilaError(ValueError):
    pass


def _config_sonda(config_pv, inclinare: float, azimut: float, putere_dc_totala: float):
    """Config de test pentru o singură combinație de geometrie: TOATE
    pierderile de sistem și degradarea sunt scoase (înlocuite de factorul de
    scalare fitat separat, vezi CORECȚIE v3 în docstring-ul modulului) — doar
    geometria (înclinare/azimut) rămâne variabilă. Umbrirea de orizont RĂMÂNE
    ca în configurația curentă: e un fapt fizic despre amplasament, nu o
    necunoscută de magnitudine ca pierderile de sistem."""
    config_test = copy.deepcopy(config_pv)
    config_test.subansambluri = [Subansamblu(
        nume="test", putere_dc_kwp=putere_dc_totala,
        inclinare_grade=inclinare, azimut_grade=azimut, tip_asezare=TipAsezarePV.FIX,
    )]
    config_test.pierdere_murdarire_pct = 0.0
    config_test.pierdere_neuniformitate_pct = 0.0
    config_test.pierdere_cablaj_conexiuni_pct = 0.0
    config_test.pierdere_lid_pct = 0.0
    config_test.pierdere_placuta_pct = 0.0
    config_test.pierdere_indisponibilitate_pct = 0.0
    config_test.degradare_anuala_pct = 0.0
    config_test.soiling_dinamic = False     # ar reintroduce o pierdere de murdărire pe altă cale
    config_test.pierderi_zapada = False     # idem, pentru zăpadă
    return config_test


def _scalare_optima(model_kw: np.ndarray, real_kw: np.ndarray,
                     limite: tuple[float, float] = LIMITE_SCALARE) -> float:
    """Factorul k care minimizează suma pătratelor (real - k·model) —
    soluția închisă e k = Σ(real·model) / Σ(model²). Absoarbe orice pierdere/
    degradare de magnitudine necunoscută, lăsând geometria să fie judecată
    doar după forma temporală a producției."""
    numitor = float((model_kw ** 2).sum())
    if numitor <= 1e-9:
        return 1.0
    k = float((real_kw * model_kw).sum() / numitor)
    return float(np.clip(k, *limite))


def parc_eligibil_pentru_aplicare_automata(parc: Parc) -> bool:
    """R-703."""
    if parc.tehnologie != Tehnologie.FOTOVOLTAIC or not parc.config_pv:
        return False
    subs = parc.config_pv.subansambluri
    return len(subs) == 1 and subs[0].tip_asezare == TipAsezarePV.FIX


def _decalaj_fata_de_amiaza_solara(productie_kw: pd.Series, parc: Parc) -> float | None:
    """Cu câte minute e centrată producția măsurată mai târziu (+) sau mai
    devreme (−) decât amiaza solară a locului.

    Aceeași metodă ca verificarea de la import (`csv_import._verifica_fus_orar`):
    centrul de greutate energetic al fiecărei zile, comparat cu amiaza solară a
    ACELEI zile, apoi mediana. Pe o zi senină curba e simetrică față de amiaza
    solară, deci un centru deplasat înseamnă date deplasate în timp."""
    if productie_kw.empty:
        return None
    pas = pd.Timedelta(minutes=parc.rezolutie_minute or 60)
    cadru = pd.DataFrame({"kwh": productie_kw.values},
                          index=pd.DatetimeIndex(productie_kw.index))
    cadru = cadru[cadru["kwh"] > 0]
    if cadru.empty:
        return None
    cadru["mijloc"] = cadru.index + pas / 2
    cadru["zi"] = cadru["mijloc"].dt.date

    zile = sorted(cadru["zi"].unique())[:60]
    grila = pd.DatetimeIndex([pd.Timestamp(z, tz="UTC") + pd.Timedelta(minutes=10 * k)
                               for z in zile for k in range(144)])
    elev = calculeaza_pozitie_solara(grila, parc.latitudine, parc.longitudine,
                                      parc.altitudine_m)["apparent_elevation"]

    decalaje = []
    for zi in zile:
        sub = cadru[cadru["zi"] == zi]
        energie = sub["kwh"].sum()
        if energie <= 0 or len(sub) < 3:
            continue
        amiaza = elev[elev.index.date == zi].idxmax()
        minute = (sub["mijloc"] - amiaza).dt.total_seconds() / 60.0
        decalaje.append(float((minute * sub["kwh"]).sum() / energie))
    if len(decalaje) < 3:
        return None
    return round(float(pd.Series(decalaje).median()), 1)


def calibreaza_geometrie(repo, parc: Parc, prag_min_ore: int) -> dict:
    if parc.tehnologie != Tehnologie.FOTOVOLTAIC:
        raise CalibrareIndisponibilaError("Calibrarea geometriei e disponibilă doar pentru parcuri fotovoltaice.")

    df_prod = citeste_productie_df(repo, parc.id)
    if df_prod.empty:
        raise CalibrareIndisponibilaError(
            "Nu există producție reală importată pentru acest parc — calibrarea are nevoie "
            "de istoric măsurat ca să compare geometriile (secțiunea 11)."
        )

    inceput, sfarsit = df_prod.index.min().date(), df_prod.index.max().date()
    meteo_real = obtine_meteo_istoric_real(parc.latitudine, parc.longitudine, inceput, sfarsit)
    meteo_pv = pregateste_meteo_pv(meteo_real)

    comun = df_prod.index.intersection(meteo_pv.index)
    meteo_pv = meteo_pv.loc[comun]
    productie_comuna = df_prod.loc[comun, "productie_kwh"]

    # --- doar orele care chiar discriminează între geometrii
    utile = meteo_pv["ghi"] > PRAG_GHI_UTIL_WM2
    excluse = ore_excluse(repo, parc.id)
    if len(excluse):
        utile = utile & ~meteo_pv.index.isin(excluse)
    meteo_pv = meteo_pv[utile]
    productie_comuna = productie_comuna[utile]

    if len(meteo_pv) < prag_min_ore:
        raise CalibrareIndisponibilaError(
            f"Doar {len(meteo_pv)} ore utile (de zi, cu date meteo corespondente și necontaminate "
            f"de probleme de calitate) — prag minim configurat: {prag_min_ore}. "
            f"Calibrarea refuză explicit rezultatul (R-705)."
        )

    # Alinierea în timp a producției față de soare — verificată ÎNAINTE de a
    # judeca geometria. Calibrarea deduce înclinarea și azimutul din FORMA
    # curbei zilnice; dacă seria măsurată e decalată în timp (fus de import
    # greșit, ore amestecate din importuri succesive cu setări diferite),
    # sondajul compensează decalajul rotind azimutul — și întoarce o geometrie
    # care „potrivește" datele, dar nu descrie parcul. Rezultatul arată aberant
    # fără să spună de ce, de-aceea decalajul se măsoară și se raportează.
    decalaj_minute = _decalaj_fata_de_amiaza_solara(productie_comuna, parc)

    # poziția solară e aceeași pentru toate combinațiile testate
    poz_solara = calculeaza_pozitie_solara(meteo_pv.index, parc.latitudine, parc.longitudine,
                                            parc.altitudine_m)

    putere_dc_totala = parc.config_pv.putere_dc_kwp
    valori_reale = productie_comuna.values
    rezultate = []
    for inclinare in GRILA_INCLINARE:
        for azimut in GRILA_AZIMUT:
            config_test = _config_sonda(parc.config_pv, inclinare, azimut, putere_dc_totala)
            rez = calculeaza_productie_pv(config_test, parc.latitudine, parc.longitudine,
                                           parc.altitudine_m, meteo_pv, poz_solara=poz_solara)
            model_curat = rez["putere_ac_fizic_kw"].values
            k = _scalare_optima(model_curat, valori_reale)
            eroare = model_curat * k - valori_reale
            rezultate.append({
                "inclinare_grade": inclinare, "azimut_grade": azimut,
                "factor_scalare": round(k, 4),
                "eroare_mae_kw": round(float(np.abs(eroare).mean()), 3),
                "eroare_rmse_kw": round(float(np.sqrt((eroare ** 2).mean())), 3),
                "bias_kw": round(float(eroare.mean()), 3),
            })

    rezultate.sort(key=lambda r: r["eroare_mae_kw"])
    actual = parc.config_pv.subansambluri[0] if parc.config_pv.subansambluri else None

    factor_optim = rezultate[0]["factor_scalare"]
    pierderi_si_degradare_pct = round(100 * (1 - factor_optim), 1)
    info_degradare = None
    if parc.config_pv.data_punerii_in_functiune:
        try:
            pus_in_functiune = pd.Timestamp(parc.config_pv.data_punerii_in_functiune, tz="UTC")
            ani = (productie_comuna.index.to_series().mean() - pus_in_functiune).days / 365.25
            rata = parc.config_pv.degradare_anuala_pct / 100.0
            if ani > 0 and 0 < rata < 1:
                explicat_de_degradare_pct = round(100 * (1 - (1 - rata) ** ani), 1)
                rest_pierderi_sistem_pct = round(
                    100 * (1 - factor_optim / ((1 - rata) ** ani)), 1)
                info_degradare = {
                    "ani": round(ani, 1),
                    "explicat_de_degradare_pct": explicat_de_degradare_pct,
                    "rest_pierderi_sistem_pct": rest_pierderi_sistem_pct,
                }
        except Exception:
            info_degradare = None

    return {
        "optim": rezultate[0],
        "alternative": rezultate[:8],
        "numar_ore_folosite": int(len(meteo_pv)),
        "numar_ore_excluse_qc": int(len(set(comun).intersection(set(excluse)))) if len(excluse) else 0,
        "se_poate_aplica_automat": parc_eligibil_pentru_aplicare_automata(parc),
        "configuratie_curenta": {
            "inclinare_grade": actual.inclinare_grade if actual else None,
            "azimut_grade": actual.azimut_grade if actual else None,
        },
        "decalaj_minute_fata_de_soare": decalaj_minute,
        "factor_scalare_optim": factor_optim,
        "pierderi_si_degradare_cumulate_pct": pierderi_si_degradare_pct,
        "info_degradare": info_degradare,
    }


def aplica_calibrare(repo, parc: Parc, inclinare_grade: float, azimut_grade: float) -> Parc:
    """R-704: scrie DIRECT pe configurația curentă — nu creează o configurație
    nouă datată. Doar pentru parcuri eligibile (R-703)."""
    if not parc_eligibil_pentru_aplicare_automata(parc):
        raise CalibrareIndisponibilaError(
            "Rezultatul calibrării e informativ pentru acest parc (est-vest sau tracker) — "
            "nu se poate aplica automat (R-703)."
        )
    if not (0 <= inclinare_grade <= 90):
        raise CalibrareIndisponibilaError("Înclinarea trebuie să fie între 0 și 90 de grade.")
    if not (0 <= azimut_grade < 360):
        raise CalibrareIndisponibilaError("Azimutul trebuie să fie între 0 și 360 de grade.")
    parc.config_pv.subansambluri[0].inclinare_grade = inclinare_grade
    parc.config_pv.subansambluri[0].azimut_grade = azimut_grade
    repo.salveaza_parc(parc)
    return parc
