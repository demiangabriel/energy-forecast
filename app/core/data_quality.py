"""
Controlul calității PRODUCȚIEI REALE măsurate (importată din CSV).

De ce e cel mai important modul nou (docs/cercetare/03-literatura-stiintifica.md,
§8): corectorul ML învață diferența dintre producția măsurată și cea calculată.
Dacă în producția măsurată există ore de indisponibilitate (invertor picat),
limitare de putere impusă de rețea, valori blocate de senzor sau un decalaj de
fus orar, modelul nu învață meteorologie — învață defecțiunile parcului și le
împrăștie în toate prognozele viitoare. Curățarea setului de antrenare
îmbunătățește tipic stratul ML cu 5-15% relativ, și e condiția ca orice altă
îmbunătățire să fie măsurabilă.

Principiu (R-611): datele brute importate NU se modifică și NU se șterg
niciodată. Rezultatul acestui modul e un set separat de VERDICTE, stocate în
tabela `calitate_productie`, care se pot recalcula oricând. Ele se folosesc
pentru:
  - excluderea orelor din antrenarea ML;
  - raportarea separată a indicatorilor "doar model" vs. "inclusiv defecțiuni";
  - alertarea operatorului (o indisponibilitate nedetectată e o constatare
    vandabilă către client, nu doar o problemă de date).

Verificările implementate, în ordinea valorii lor:
 1. decalaj de timp (fus orar / oră de vară) — prin corelație încrucișată cu
    puterea de cer senin; o oră de decalaj nedetectată strică tot ce urmează;
 2. producție noaptea — decalaj de metering sau semn inversat;
 3. valori peste puterea instalată — imposibil fizic;
 4. valori blocate (aceeași valoare multe ore la rând) — senzor înghețat;
 5. valori extreme (filtru Hampel — median absolute deviation, robust);
 6. indisponibilitate / limitare de putere — producție mult sub așteptarea
    fizică, susținut, cu varianță mică (curtailment) sau nu (avarie).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.models import CalitateOra, Parc, SteagCalitate, Tehnologie

# Praguri implicite — toate ajustabile din apelant (Setări de operare).
PRAG_NOAPTE_FRACTIE = 0.01          # >1% din instalată noaptea = suspect
PRAG_PESTE_INSTALATA = 1.05         # 5% marjă peste instalată (erori de metering minore)
ORE_VALOARE_BLOCATA = 6             # ore consecutive identice
PRAG_HAMPEL = 6.0                   # câte MAD-uri până la "valoare extremă"
FEREASTRA_HAMPEL = 25               # ore (fereastră centrată)
PRAG_INDISPONIBILITATE = 0.25       # producție sub 25% din așteptarea fizică
ORE_MIN_INDISPONIBILITATE = 2       # susținut cel puțin atâtea ore
PRAG_LIMITARE = 0.90                # producție sub 90% din așteptare...
CV_MAX_LIMITARE = 0.02              # ...dar remarcabil de PLATĂ (coef. de variație mic)
ORE_MIN_LIMITARE = 3


@dataclass
class RaportCalitate:
    verdicte: list[CalitateOra] = field(default_factory=list)
    numar_analizate: int = 0
    numar_probleme: int = 0
    pe_steag: dict[str, int] = field(default_factory=dict)
    decalaj_ore_detectat: float | None = None
    mesaje: list[str] = field(default_factory=list)

    @property
    def procent_curat(self) -> float:
        if not self.numar_analizate:
            return 100.0
        return round((1 - self.numar_probleme / self.numar_analizate) * 100, 1)


def detecteaza_decalaj_timp(productie_kw: pd.Series, referinta_kw: pd.Series,
                             decalaj_maxim_ore: int = 4) -> tuple[float | None, float]:
    """Găsește decalajul (în ore) care maximizează corelația dintre producția
    măsurată și referința de cer senin.

    E cea mai valoroasă verificare din tot modulul: un fișier exportat în ora
    locală și interpretat ca UTC (sau invers) produce un decalaj constant de
    1-3 ore, care nu se vede în niciun indicator agregat, dar strică sistematic
    și calibrarea, și antrenarea ML, și toți indicatorii de eroare.

    Întoarce (decalajul cel mai probabil în ore, corelația la acel decalaj).
    Decalaj 0 (sau None dacă datele nu permit o concluzie) = totul e în regulă.
    """
    comun = productie_kw.index.intersection(referinta_kw.index)
    if len(comun) < 48:
        return None, 0.0
    p = pd.to_numeric(productie_kw.reindex(comun), errors="coerce").astype(float)
    r = pd.to_numeric(referinta_kw.reindex(comun), errors="coerce").astype(float)
    valabil = p.notna() & r.notna()
    if valabil.sum() < 48 or r[valabil].std() < 1e-9 or p[valabil].std() < 1e-9:
        return None, 0.0

    cel_mai_bun_decalaj, cea_mai_buna_corelatie = 0, -np.inf
    for decalaj in range(-decalaj_maxim_ore, decalaj_maxim_ore + 1):
        deplasat = p.shift(decalaj)
        pereche = pd.DataFrame({"p": deplasat, "r": r}).dropna()
        if len(pereche) < 48 or pereche["p"].std() < 1e-9:
            continue
        corelatie = float(pereche["p"].corr(pereche["r"]))
        if np.isfinite(corelatie) and corelatie > cea_mai_buna_corelatie:
            cea_mai_buna_corelatie, cel_mai_bun_decalaj = corelatie, decalaj
    if cea_mai_buna_corelatie == -np.inf:
        return None, 0.0
    return float(cel_mai_bun_decalaj), float(cea_mai_buna_corelatie)


def _valori_blocate(serie: pd.Series, ore: int) -> pd.Series:
    """True pe orele care fac parte dintr-un platou de valori identice mai
    lung decât `ore`. Producția zero de noapte e exclusă — e normală."""
    rotunjit = serie.round(4)
    grup = (rotunjit != rotunjit.shift()).cumsum()
    lungimi = grup.map(grup.value_counts())
    return (lungimi >= ore) & (rotunjit > 0)


def _hampel(serie: pd.Series, fereastra: int, prag: float,
             prag_absolut: float = 0.0) -> pd.Series:
    """Filtru Hampel: outlier dacă |x - mediana locală| > prag · MAD local.
    Robust la valorile extreme înseși (spre deosebire de media ± k·σ).

    `prag_absolut` e o gardă suplimentară: o abatere trebuie să fie și
    semnificativă în valoare absolută, nu doar statistic. Fără ea, în
    perioadele foarte liniștite MAD-ul devine minuscul și orice variație
    normală pare extremă.

    ATENȚIE la scara pe care se aplică: filtrul presupune o serie
    aproximativ staționară. Aplicat direct pe puterea fotovoltaică (care
    parcurge zilnic 0 -> vârf -> 0) marchează masiv fals — de aceea
    apelantul îl aplică pe INDICELE DE SENIN, nu pe kW.
    """
    minim = max(5, fereastra // 3)
    mediana = serie.rolling(fereastra, center=True, min_periods=minim).median()
    abatere = (serie - mediana).abs()
    mad = abatere.rolling(fereastra, center=True, min_periods=minim).median()
    # 1.4826 = factorul care face MAD un estimator consistent al deviației standard
    sigma = 1.4826 * mad
    conditie = (abatere > prag * sigma) & (sigma > 1e-9)
    if prag_absolut > 0:
        conditie = conditie & (abatere > prag_absolut)
    return conditie


def analizeaza_calitate(parc: Parc, productie_kw: pd.Series, asteptare_fizica_kw: pd.Series | None,
                         referinta_cer_senin_kw: pd.Series | None, putere_instalata_kw: float,
                         ) -> RaportCalitate:
    """Produce verdictele de calitate pentru o serie de producție măsurată.

    `productie_kw`            — producția reală, kW (= kWh la pas orar).
    `asteptare_fizica_kw`     — ce ar fi trebuit să producă modelul FIZIC (fără ML);
                                lipsa ei dezactivează detecția de indisponibilitate/limitare.
    `referinta_cer_senin_kw`  — puterea de cer senin (doar fotovoltaic).
    """
    raport = RaportCalitate()
    serie = pd.to_numeric(productie_kw, errors="coerce").dropna().sort_index()
    raport.numar_analizate = int(len(serie))
    if serie.empty:
        return raport

    steaguri: dict[pd.Timestamp, tuple[str, str]] = {}

    def marcheaza(index, steag: str, detaliu: str):
        for ts in index:
            # primul verdict pus pe o oră rămâne — verificările sunt ordonate
            # de la cea mai gravă/sigură la cea mai interpretabilă
            steaguri.setdefault(ts, (steag, detaliu))

    # --- 1. decalaj de timp (nu marchează ore individuale, ci raportează global)
    if referinta_cer_senin_kw is not None:
        decalaj, corelatie = detecteaza_decalaj_timp(serie, referinta_cer_senin_kw)
        raport.decalaj_ore_detectat = decalaj
        if decalaj and abs(decalaj) >= 1:
            raport.mesaje.append(
                f"Posibil decalaj de timp: seria măsurată se potrivește cel mai bine cu "
                f"referința de cer senin dacă e deplasată cu {decalaj:+.0f} h "
                f"(corelație {corelatie:.2f}). Verificați fusul orar al exportului — "
                f"un decalaj constant strică și calibrarea, și antrenarea modelului."
            )

    # --- 2. peste puterea instalată (imposibil fizic)
    if putere_instalata_kw > 0:
        peste = serie[serie > putere_instalata_kw * PRAG_PESTE_INSTALATA]
        marcheaza(peste.index, SteagCalitate.PESTE_INSTALATA.value,
                   f"peste {PRAG_PESTE_INSTALATA:.2f}× puterea instalată")

    # --- 3. producție noaptea (doar fotovoltaic, unde noaptea e definită)
    if parc.tehnologie == Tehnologie.FOTOVOLTAIC and referinta_cer_senin_kw is not None:
        referinta = referinta_cer_senin_kw.reindex(serie.index)
        prag = putere_instalata_kw * PRAG_NOAPTE_FRACTIE
        noapte = (referinta.fillna(0.0) <= 1e-6) & (serie > prag)
        marcheaza(serie.index[noapte], SteagCalitate.NOAPTE_NENUL.value,
                   "producție semnificativă în afara orelor de lumină")

    # --- 4. valori blocate (senzor înghețat)
    blocate = _valori_blocate(serie, ORE_VALOARE_BLOCATA)
    marcheaza(serie.index[blocate.fillna(False)], SteagCalitate.VALOARE_BLOCATA.value,
               f"aceeași valoare ≥{ORE_VALOARE_BLOCATA} ore consecutive")

    # --- 5. indisponibilitate și limitare (au nevoie de așteptarea fizică)
    if asteptare_fizica_kw is not None:
        asteptare = pd.to_numeric(asteptare_fizica_kw.reindex(serie.index), errors="coerce")
        semnificativ = asteptare > putere_instalata_kw * 0.05      # doar unde chiar se aștepta producție
        with np.errstate(divide="ignore", invalid="ignore"):
            raport_realizat = (serie / asteptare.where(asteptare > 1e-9)).replace([np.inf, -np.inf], np.nan)

        sub_asteptare = (raport_realizat < PRAG_INDISPONIBILITATE) & semnificativ
        for inceput, sfarsit in _intervale_adevarate(sub_asteptare.fillna(False), ORE_MIN_INDISPONIBILITATE):
            felie = serie.iloc[inceput:sfarsit]
            marcheaza(felie.index, SteagCalitate.INDISPONIBILITATE.value,
                       "producție mult sub așteptarea fizică, susținut")

        # limitare de putere (curtailment): sub așteptare, dar remarcabil de PLATĂ
        aproape_plat = (raport_realizat < PRAG_LIMITARE) & semnificativ & (serie > putere_instalata_kw * 0.05)
        for inceput, sfarsit in _intervale_adevarate(aproape_plat.fillna(False), ORE_MIN_LIMITARE):
            felie = serie.iloc[inceput:sfarsit]
            medie = float(felie.mean())
            if medie > 1e-9 and float(felie.std(ddof=0)) / medie < CV_MAX_LIMITARE:
                marcheaza(felie.index, SteagCalitate.LIMITARE.value,
                           "plafon plat sub așteptarea fizică — posibilă limitare de putere")

    # --- 6. valori extreme (ultimele, ca să nu ascundă cauzele de mai sus)
    #
    # Scara contează: la fotovoltaic filtrul se aplică pe INDICELE DE SENIN
    # (producție / producție pe cer senin), care nu are ciclu zi-noapte.
    # Aplicat pe kW bruți, filtrul confundă răsăritul și apusul cu anomalii
    # și marchează sute de ore perfect normale.
    if parc.tehnologie == Tehnologie.FOTOVOLTAIC and referinta_cer_senin_kw is not None:
        referinta = referinta_cer_senin_kw.reindex(serie.index)
        zi = referinta > max(putere_instalata_kw * 0.02, 1e-6)
        if zi.any():
            kt = (serie[zi] / referinta[zi]).clip(0.0, 1.5)
            extreme = _hampel(kt, FEREASTRA_HAMPEL, PRAG_HAMPEL, prag_absolut=0.25)
            marcheaza(kt.index[extreme.fillna(False)], SteagCalitate.VALOARE_EXTREMA.value,
                       "indice de senin extrem față de vecinătatea lui (filtru Hampel)")
    else:
        extreme = _hampel(serie, FEREASTRA_HAMPEL, PRAG_HAMPEL,
                           prag_absolut=putere_instalata_kw * 0.05)
        marcheaza(serie.index[extreme.fillna(False)], SteagCalitate.VALOARE_EXTREMA.value,
                   "valoare extremă față de vecinătatea ei (filtru Hampel)")

    raport.verdicte = [
        CalitateOra(parc_id=parc.id, timestamp_utc=ts.isoformat(), steag=steag, detaliu=detaliu)
        for ts, (steag, detaliu) in sorted(steaguri.items())
    ]
    raport.numar_probleme = len(raport.verdicte)
    for v in raport.verdicte:
        raport.pe_steag[v.steag] = raport.pe_steag.get(v.steag, 0) + 1
    return raport


def _intervale_adevarate(masca: pd.Series, lungime_minima: int) -> list[tuple[int, int]]:
    """Intervalele [început, sfârșit) de poziții unde masca e True cel puțin
    `lungime_minima` pași consecutivi."""
    valori = masca.to_numpy(dtype=bool)
    intervale, inceput = [], None
    for i, val in enumerate(valori):
        if val and inceput is None:
            inceput = i
        elif not val and inceput is not None:
            if i - inceput >= lungime_minima:
                intervale.append((inceput, i))
            inceput = None
    if inceput is not None and len(valori) - inceput >= lungime_minima:
        intervale.append((inceput, len(valori)))
    return intervale


# Steagurile care descalifică o oră din ANTRENAREA modelului ML: ele descriu
# starea parcului, nu vremea. `VALOARE_EXTREMA` e inclusă pentru că un outlier
# izolat trage puternic de o funcție de pierdere pe erori absolute.
STEAGURI_EXCLUSE_DIN_ANTRENARE = {
    SteagCalitate.NOAPTE_NENUL.value,
    SteagCalitate.VALOARE_BLOCATA.value,
    SteagCalitate.INDISPONIBILITATE.value,
    SteagCalitate.LIMITARE.value,
    SteagCalitate.VALOARE_EXTREMA.value,
    SteagCalitate.PESTE_INSTALATA.value,
}


def ore_excluse(repo, parc_id: str) -> pd.DatetimeIndex:
    """Orele marcate de controlul de calitate care NU trebuie folosite la
    antrenare și care se raportează separat în verificare."""
    verdicte = repo.listeaza_calitate_productie(parc_id)
    momente = [v.timestamp_utc for v in verdicte if v.steag in STEAGURI_EXCLUSE_DIN_ANTRENARE]
    if not momente:
        return pd.DatetimeIndex([], tz="UTC")
    return pd.DatetimeIndex(pd.to_datetime(momente, utc=True))
