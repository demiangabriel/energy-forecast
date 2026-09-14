"""
Verificarea prognozei — indicatori, prognoze de REFERINȚĂ și scor de skill.

De ce a fost nevoie de acest modul (vezi docs/DEBUGGING.md, problema V1):
până acum "eroarea medie" se calcula peste TOATE orele (inclusiv noaptea, unde
prognoza și realizatul sunt amândouă zero), amestecând orizonturi de prognoză
diferite și fără nicio referință față de care să se știe dacă modelul aduce
ceva. Un model care nu bate persistența inteligentă nu aduce nimic — dar
vechea metrică nu putea spune asta.

Convențiile implementate aici urmează consensul din literatură
(Yang et al. 2020, *Solar Energy* 210, 20-37; IEA Wind Task 36/51
Recommended Practice Part 3):

  * normalizare la PUTEREA INSTALATĂ (convenția operatorilor de sistem), cu
    raportarea separată și a normalizării la media producției (convenția
    traderilor) — niciodată un "nMAE" fără bază declarată;
  * la fotovoltaic se exclud orele de noapte (altfel indicatorul arată
    artificial de bine — zerourile de noapte se "prognozează" perfect);
  * scor de skill față de o referință explicită:
        s = 1 - RMSE(prognoza) / RMSE(referinta)
    unde referința e combinația convexă optimă dintre persistență și
    climatologie, în spațiul indicelui de senin;
  * indicatorii se pot calcula separat pe orizont de prognoză (câte ore în
    avans a fost emisă valoarea) — un amestec de orizonturi flatează
    rezultatul, pentru că rulările mai recente sunt sistematic mai bune;
  * orele marcate de controlul de calitate (indisponibilitate, limitare de
    putere) se pot exclude: ele măsoară defecțiuni ale parcului, nu calitatea
    modelului.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- indicatori determiniști

@dataclass
class Indicatori:
    numar_ore: int = 0
    mae_kw: Optional[float] = None
    rmse_kw: Optional[float] = None
    bias_kw: Optional[float] = None
    nmae_capacitate_pct: Optional[float] = None
    nrmse_capacitate_pct: Optional[float] = None
    nbias_capacitate_pct: Optional[float] = None
    nmae_medie_pct: Optional[float] = None          # convenția traderilor
    energie_prognozata_kwh: Optional[float] = None
    energie_realizata_kwh: Optional[float] = None
    baza_normalizare_kw: Optional[float] = None
    productie_medie_kw: Optional[float] = None

    def ca_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def calculeaza_indicatori(prognozat_kw: pd.Series, realizat_kw: pd.Series,
                           putere_normalizare_kw: float) -> Indicatori:
    """Indicatorii determiniști standard pe perechile (prognoză, realizat)
    deja aliniate și filtrate de apelant."""
    comun = prognozat_kw.index.intersection(realizat_kw.index)
    f = pd.to_numeric(prognozat_kw.reindex(comun), errors="coerce")
    o = pd.to_numeric(realizat_kw.reindex(comun), errors="coerce")
    valabil = f.notna() & o.notna()
    f, o = f[valabil], o[valabil]
    if len(f) == 0 or not putere_normalizare_kw:
        return Indicatori(numar_ore=len(f))

    eroare = f - o
    mae = float(np.abs(eroare).mean())
    rmse = float(np.sqrt((eroare ** 2).mean()))
    bias = float(eroare.mean())
    medie_productie = float(o.mean())

    return Indicatori(
        numar_ore=int(len(f)),
        mae_kw=mae, rmse_kw=rmse, bias_kw=bias,
        nmae_capacitate_pct=mae / putere_normalizare_kw * 100,
        nrmse_capacitate_pct=rmse / putere_normalizare_kw * 100,
        nbias_capacitate_pct=bias / putere_normalizare_kw * 100,
        nmae_medie_pct=(mae / medie_productie * 100) if medie_productie > 1e-9 else None,
        energie_prognozata_kwh=float(f.sum()),
        energie_realizata_kwh=float(o.sum()),
        baza_normalizare_kw=float(putere_normalizare_kw),
        productie_medie_kw=medie_productie,
    )


# --------------------------------------------------------------------------- prognoze de referință

def referinta_persistenta(realizat_kw: pd.Series, ore_in_avans: float) -> pd.Series:
    """Persistență simplă: "va fi cât a fost acum N ore". Referința clasică
    pentru eolian la orizonturi scurte."""
    pas = max(1, int(round(ore_in_avans)))
    return realizat_kw.shift(pas)


def referinta_persistenta_indice_senin(realizat_kw: pd.Series, referinta_cer_senin_kw: pd.Series,
                                        ore_in_avans: float) -> pd.Series:
    """Persistență INTELIGENTĂ (smart persistence) — se persistă indicele de
    senin kt*, nu puterea:

        P_ref(t) = kt*(t - h) · P_cer_senin(t)

    E referința corectă pentru fotovoltaic: separă "cum a fost cerul" de
    "unde e soarele". Un model fizic + ML care nu bate asta nu aduce valoare.
    """
    from app.core.clearsky import indice_senin
    kt = indice_senin(realizat_kw, referinta_cer_senin_kw)
    pas = max(1, int(round(ore_in_avans)))
    return kt.shift(pas) * referinta_cer_senin_kw


def referinta_climatologie(realizat_kw: pd.Series, referinta_cer_senin_kw: pd.Series | None = None) -> pd.Series:
    """Climatologie: media istorică a indicelui de senin (fotovoltaic) sau a
    puterii (eolian), aplicată uniform. Referința potrivită pentru orizonturi
    lungi, unde persistența nu mai spune nimic."""
    if referinta_cer_senin_kw is not None:
        from app.core.clearsky import indice_senin
        kt = indice_senin(realizat_kw, referinta_cer_senin_kw)
        kt_mediu = float(kt.mean(skipna=True)) if kt.notna().any() else 0.0
        return referinta_cer_senin_kw * kt_mediu
    return pd.Series(float(realizat_kw.mean()), index=realizat_kw.index)


def referinta_optima(realizat_kw: pd.Series, referinta_cer_senin_kw: pd.Series | None,
                      ore_in_avans: float) -> pd.Series:
    """Combinația convexă optimă persistență/climatologie:

        r = ρ_h · persistență + (1 - ρ_h) · climatologie

    unde ρ_h e autocorelația la decalajul h (în spațiul kt* pentru solar).
    Proprietatea utilă, demonstrată în literatură: RMSE al acestei combinații
    e mereu ≤ min(RMSE_persistență, RMSE_climatologie), deci e cea mai severă
    referință "fără model" rezonabilă — exact ce trebuie bătut.
    """
    if referinta_cer_senin_kw is not None:
        from app.core.clearsky import indice_senin
        baza = indice_senin(realizat_kw, referinta_cer_senin_kw)
    else:
        baza = realizat_kw

    pas = max(1, int(round(ore_in_avans)))
    serie = baza.dropna()
    rho = 0.0
    # o serie constantă nu are autocorelație definită (varianță zero) —
    # verificăm explicit, ca să nu producem NaN și avertismente numerice
    if len(serie) > pas + 2 and float(serie.std(ddof=0)) > 1e-12:
        valoare = pd.Series(serie.values).autocorr(lag=pas)
        if valoare is not None and np.isfinite(valoare):
            rho = float(valoare)
    rho = float(np.clip(rho, 0.0, 1.0))

    persistenta = baza.shift(pas)
    climatologie = float(baza.mean(skipna=True)) if baza.notna().any() else 0.0
    combinat = rho * persistenta + (1 - rho) * climatologie

    if referinta_cer_senin_kw is not None:
        return combinat * referinta_cer_senin_kw
    return combinat


def scor_skill(prognozat_kw: pd.Series, realizat_kw: pd.Series, referinta_kw: pd.Series) -> Optional[float]:
    """s = 1 - RMSE(prognoză) / RMSE(referință).

    +1 = perfect; 0 = exact cât referința; negativ = mai rău decât a nu avea
    model. Se evaluează strict pe orele unde TOATE trei seriile există, ca
    numitorul și numărătorul să fie comparabile.
    """
    idx = prognozat_kw.index.intersection(realizat_kw.index).intersection(referinta_kw.index)
    f = pd.to_numeric(prognozat_kw.reindex(idx), errors="coerce")
    o = pd.to_numeric(realizat_kw.reindex(idx), errors="coerce")
    r = pd.to_numeric(referinta_kw.reindex(idx), errors="coerce")
    valabil = f.notna() & o.notna() & r.notna()
    if valabil.sum() < 3:
        return None
    rmse_f = float(np.sqrt(((f[valabil] - o[valabil]) ** 2).mean()))
    rmse_r = float(np.sqrt(((r[valabil] - o[valabil]) ** 2).mean()))
    if rmse_r < 1e-9:
        return None
    return 1.0 - rmse_f / rmse_r


# --------------------------------------------------------------------------- verificare probabilistică

def pierdere_pinball(prognozat_kw: pd.Series, realizat_kw: pd.Series, nivel: float) -> Optional[float]:
    """Pierderea pinball (quantile loss) pentru nivelul τ:
        PL = τ·(y - q)      dacă y ≥ q
             (1-τ)·(q - y)  altfel
    E funcția de scor proprie pentru o cuantilă — singura care nu poate fi
    "păcălită" raportând altceva decât cuantila cerută."""
    idx = prognozat_kw.index.intersection(realizat_kw.index)
    q = pd.to_numeric(prognozat_kw.reindex(idx), errors="coerce")
    y = pd.to_numeric(realizat_kw.reindex(idx), errors="coerce")
    valabil = q.notna() & y.notna()
    if valabil.sum() == 0:
        return None
    q, y = q[valabil], y[valabil]
    sub = y >= q
    pierdere = np.where(sub, nivel * (y - q), (1 - nivel) * (q - y))
    return float(np.mean(pierdere))


def crps_din_cuantile(cuantile_kw: dict[float, pd.Series], realizat_kw: pd.Series) -> Optional[float]:
    """CRPS aproximat dintr-un set de cuantile echidistante, folosind
    identitatea CRPS = 2·∫₀¹ PL_τ dτ (Gneiting & Raftery 2007):

        CRPS ≈ (2/M) · Σ_m PL_{τ_m}

    Cu cât setul de niveluri e mai dens, cu atât aproximarea e mai bună."""
    if not cuantile_kw:
        return None
    pierderi = []
    for nivel, serie in sorted(cuantile_kw.items()):
        pl = pierdere_pinball(serie, realizat_kw, nivel)
        if pl is not None:
            pierderi.append(pl)
    if not pierderi:
        return None
    return float(2.0 * np.mean(pierderi))


def acoperire_empirica(cuantile_kw: dict[float, pd.Series], realizat_kw: pd.Series) -> dict[float, float]:
    """Fiabilitatea (reliability): pentru fiecare nivel τ, fracția observată
    de realizări sub cuantila prognozată. Calibrat = fracția ≈ τ."""
    rezultat = {}
    for nivel, serie in sorted(cuantile_kw.items()):
        idx = serie.index.intersection(realizat_kw.index)
        q = pd.to_numeric(serie.reindex(idx), errors="coerce")
        y = pd.to_numeric(realizat_kw.reindex(idx), errors="coerce")
        valabil = q.notna() & y.notna()
        if valabil.sum() == 0:
            continue
        rezultat[nivel] = float((y[valabil] <= q[valabil]).mean())
    return rezultat


def latime_medie_interval(cuantile_kw: dict[float, pd.Series],
                           jos: float = 0.1, sus: float = 0.9) -> Optional[float]:
    """Ascuțimea (sharpness): lățimea medie a intervalului central. Se
    raportează DOAR împreună cu fiabilitatea — un interval foarte îngust dar
    necalibrat e mai rău decât inutil."""
    if jos not in cuantile_kw or sus not in cuantile_kw:
        return None
    latime = (cuantile_kw[sus] - cuantile_kw[jos]).dropna()
    return float(latime.mean()) if len(latime) else None


# --------------------------------------------------------------------------- raport complet

@dataclass
class RaportVerificare:
    indicatori: Indicatori = field(default_factory=Indicatori)
    skill_vs_referinta: Optional[float] = None
    indicatori_referinta: Indicatori = field(default_factory=Indicatori)
    pe_orizont: dict[str, Indicatori] = field(default_factory=dict)
    crps_kw: Optional[float] = None
    acoperire: dict[float, float] = field(default_factory=dict)
    latime_interval_kw: Optional[float] = None
    ore_excluse_qc: int = 0
    ore_excluse_noapte: int = 0


def verifica(prognozat_kw: pd.Series, realizat_kw: pd.Series, putere_normalizare_kw: float,
             referinta_cer_senin_kw: pd.Series | None = None,
             masca_ore_utile: pd.Series | None = None,
             ore_excluse_qc: pd.Index | None = None,
             ore_in_avans: pd.Series | None = None,
             cuantile_kw: dict[float, pd.Series] | None = None,
             orizont_referinta_ore: float = 24.0) -> RaportVerificare:
    """Verificarea completă a unei serii de prognoze față de producția reală.

    `masca_ore_utile`  — True pe orele care intră în calcul (zi, la PV).
    `ore_excluse_qc`   — ore marcate de controlul de calitate (indisponibilitate,
                         limitare): ele măsoară defecte ale parcului, nu ale modelului.
    `ore_in_avans`     — orizontul fiecărei valori; dacă e dat, se produc și
                         indicatori separați pe intervale de orizont.
    """
    raport = RaportVerificare()

    idx = prognozat_kw.index.intersection(realizat_kw.index)
    f = pd.to_numeric(prognozat_kw.reindex(idx), errors="coerce")
    o = pd.to_numeric(realizat_kw.reindex(idx), errors="coerce")

    if masca_ore_utile is not None:
        utile = masca_ore_utile.reindex(idx).fillna(False).astype(bool)
        raport.ore_excluse_noapte = int((~utile).sum())
        f, o = f[utile], o[utile]

    if ore_excluse_qc is not None and len(ore_excluse_qc):
        de_exclus = f.index.intersection(ore_excluse_qc)
        raport.ore_excluse_qc = int(len(de_exclus))
        f = f.drop(de_exclus)
        o = o.drop(de_exclus)

    raport.indicatori = calculeaza_indicatori(f, o, putere_normalizare_kw)

    referinta = referinta_optima(
        o, referinta_cer_senin_kw.reindex(o.index) if referinta_cer_senin_kw is not None else None,
        orizont_referinta_ore,
    )
    raport.indicatori_referinta = calculeaza_indicatori(referinta, o, putere_normalizare_kw)
    raport.skill_vs_referinta = scor_skill(f, o, referinta)

    if ore_in_avans is not None:
        avans = pd.to_numeric(ore_in_avans.reindex(f.index), errors="coerce")
        intervale = [(0, 6, "0-6 h"), (6, 24, "6-24 h"), (24, 48, "24-48 h"), (48, 1e9, "48+ h")]
        for jos, sus, eticheta in intervale:
            sel = (avans >= jos) & (avans < sus)
            if sel.sum() >= 3:
                raport.pe_orizont[eticheta] = calculeaza_indicatori(
                    f[sel.fillna(False)], o[sel.fillna(False)], putere_normalizare_kw)

    if cuantile_kw:
        cuantile_filtrate = {niv: serie.reindex(f.index) for niv, serie in cuantile_kw.items()}
        raport.crps_kw = crps_din_cuantile(cuantile_filtrate, o)
        raport.acoperire = acoperire_empirica(cuantile_filtrate, o)
        raport.latime_interval_kw = latime_medie_interval(cuantile_filtrate)

    return raport
