"""
Client date meteo — Open-Meteo (fără cheie API), cu suport pentru MAI MULTE
modele numerice simultan (R-501) și pentru reconstrucția istorică din arhiva
de "rulări precedente" (R-610).

R-503 — sursă înlocuibilă: interfața standard e chiar semnătura funcțiilor
`obtine_prognoza_meteo` / `obtine_meteo_istoric_reconstruit` de mai jos
(lat, lon, listă de modele, interval de zile -> DataFrame per model). Pentru
un furnizor nou (ex. un abonament plătit ECMWF direct), se scrie un modul cu
aceeași semnătură și se schimbă un singur import în forecast_engine.py —
restul aplicației (motorul fizic, ML, rapoartele) nu vede diferența.

R-504 — nicio cheie API în cod: Open-Meteo nu cere cheie; dacă un furnizor
viitor cere una, ea vine din .env (vezi app/config.py), niciodată hardcodată.

R-1605 — reîncercare cu așteptare progresivă la limite de rată.

--------------------------------------------------------------------------
NOTE v2 (vezi docs/DEBUGGING.md și docs/MODELE.md)

1. `combina_ansamblu` RENORMALIZEAZĂ ponderile oră cu oră, în funcție de ce
   model chiar a întors o valoare la ora respectivă. Înainte, o valoare
   lipsă dintr-un model însemna tăcut o medie mai mică cu ponderea acelui
   model (ex.: 3 modele egale, unul fără date -> medie cu ~33% prea mică).
   Cazul e frecvent: modelele au orizonturi diferite (ICON ~7,5 zile, GFS 16),
   deci la orizonturi lungi lipsesc sistematic valori.

2. Cererea de rețea eșuează RAPID la erorile permanente (4xx, ex. un id de
   model greșit) și reîncearcă doar la erorile tranzitorii (429, 5xx, rețea).
   Înainte, un id greșit costa ~31 s de așteptări inutile.

3. Dacă cererea comună (toate modelele odată) eșuează, se reîncearcă
   model cu model și se păstrează ce răspunde — o singură sursă picată nu
   mai anulează toată rularea.

4. Variabilele meteo sunt împărțite în OBLIGATORII (fără ele nu se poate
   calcula nimic) și SUPLIMENTARE (zăpadă, nori, umiditate, direcția
   vântului, precipitații). Cele suplimentare se cer mereu, dar absența lor
   nu e o eroare — modelele fizice și cele ML le folosesc doar dacă există.
"""
from __future__ import annotations

import time
from datetime import date

import httpx
import numpy as np
import pandas as pd

from app.config import MEDIU

# Variabile FĂRĂ de care motoarele fizice nu pot calcula.
VARIABILE_OBLIGATORII = [
    "shortwave_radiation",       # GHI (W/m2)
    "direct_normal_irradiance",  # DNI (W/m2)
    "diffuse_radiation",         # DHI (W/m2)
    "temperature_2m",            # °C
    "wind_speed_10m",            # m/s
    "wind_speed_100m",           # m/s — folosit pt. extrapolarea la butuc (R-423)
    "surface_pressure",          # hPa — folosit pt. corecția de densitate (R-424)
]

# Variabile care ÎMBUNĂTĂȚESC modelul dacă există, dar a căror absență nu e
# o eroare (unele modele/endpoint-uri nu le expun pe toate).
VARIABILE_SUPLIMENTARE = [
    "relative_humidity_2m",      # % — densitatea aerului cu umiditate (IEC 61400-12-1)
    "wind_direction_10m",        # ° — siaj/teren; intrare pentru corectorul ML
    "wind_direction_100m",       # °
    "cloud_cover",               # % — intrări ML puternice pentru erorile de nebulozitate
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "snowfall",                  # cm — model de acoperire cu zăpadă (pierderi PV iarna)
    "snow_depth",                # m
    "precipitation",             # mm — spălarea murdăriei (model Kimber)
]

VARIABILE_ORARE = VARIABILE_OBLIGATORII + VARIABILE_SUPLIMENTARE

# Numărul MAXIM de active peste care API-ul gratuit Open-Meteo, la volumul
# de cereri al unei rulări zilnice pe portofoliu, riscă limitele de rată
# publice — peste acest prag recomandăm operatorului un abonament plătit
# (R-1605). E o estimare orientativă, editabilă doar în cod (nu e un prag
# fizic, ci o recomandare operațională).
PRAG_ACTIVE_ABONAMENT_RECOMANDAT = 60

# Limita tare a Open-Meteo pentru `forecast_days`; peste ea cererea e respinsă
# cu 400 („Allowed range 0 to 16").
ZILE_PROGNOZA_MAXIM_FURNIZOR = 16

# Cheia sub care se păstrează reanaliza în `meteo_cache`. E VERSIONATĂ pentru că
# datele salvate poartă implicit convenția de etichetare din momentul scrierii:
# intrările de dinaintea trecerii la „eticheta = începutul intervalului" (v10)
# sunt decalate cu o oră față de cele noi. Citite amestecat, produc o serie
# meteo incoerentă — suficient cât să strice vizibil calibrarea geometriei.
# La orice viitoare schimbare de convenție, se incrementează sufixul; intrările
# vechi rămân în tabelă, dar nu mai sunt citite niciodată (se pot șterge din
# Modul tehnic -> Întreținere & retenție).
SURSA_CACHE_REANALIZA = "reanaliza_v2"


class EroareMeteo(RuntimeError):
    pass


class EroareMeteoTemporara(EroareMeteo):
    """Sursa nu a răspuns deloc (rețea căzută, timeout, 5xx, limită de rată
    depășită repetat) — spre deosebire de o respingere 4xx, care înseamnă o
    configurare greșită a parcului și nu se rezolvă prin reîncercare."""


# --------------------------------------------------------------------------- Disjunctor
#
# Fără el, o pană la Open-Meteo costa, la un portofoliu de 50 de parcuri,
# ~800 de cereri HTTP și ~50 de MINUTE de așteptări cumulate, ca apoi să nu
# producă nimic (măsurat). Fiecare parc relua de la zero scara completă de
# reîncercări pentru o sursă despre care se știa deja, de la parcul precedent,
# că nu răspunde.
#
# După `PRAG_ESECURI_CONSECUTIVE` eșecuri TEMPORARE la rând, următoarele cereri
# eșuează imediat, fără rețea, până la expirarea răcirii. Erorile permanente
# (4xx) NU contează aici: ele sunt specifice unui parc prost configurat, nu un
# semn că sursa e căzută.
PRAG_ESECURI_CONSECUTIVE = 3
RACIRE_DISJUNCTOR_SECUNDE = 600


class _Disjunctor:
    def __init__(self) -> None:
        self.esecuri = 0
        self.deschis_pana_la = 0.0
        self.ultimul_motiv = ""

    def e_deschis(self) -> bool:
        if self.deschis_pana_la and time.monotonic() >= self.deschis_pana_la:
            self.reseteaza()      # răcirea a expirat — se mai încearcă o dată
        return bool(self.deschis_pana_la)

    def inregistreaza_esec(self, motiv: str) -> None:
        self.esecuri += 1
        self.ultimul_motiv = motiv
        if self.esecuri >= PRAG_ESECURI_CONSECUTIVE:
            self.deschis_pana_la = time.monotonic() + RACIRE_DISJUNCTOR_SECUNDE

    def inregistreaza_succes(self) -> None:
        self.reseteaza()

    def reseteaza(self) -> None:
        self.esecuri = 0
        self.deschis_pana_la = 0.0


DISJUNCTOR = _Disjunctor()


def _verifica_disjunctor() -> None:
    if DISJUNCTOR.e_deschis():
        raise EroareMeteoTemporara(
            f"Sursa meteo e considerată indisponibilă după "
            f"{PRAG_ESECURI_CONSECUTIVE} eșecuri consecutive, deci cererea nu a mai fost "
            f"încercată (se reia automat peste cel mult "
            f"{RACIRE_DISJUNCTOR_SECUNDE // 60} minute). Ultimul motiv: "
            f"{DISJUNCTOR.ultimul_motiv}"
        )


def _cerere_cu_reincercare(client: httpx.Client, url: str, params: dict, incercari: int = 4) -> dict:
    """Reîncearcă DOAR erorile tranzitorii. O eroare 4xx (în afară de 429) e
    permanentă — de obicei un parametru greșit — și e semnalată imediat, cu
    textul explicativ întors de Open-Meteo."""
    _verifica_disjunctor()
    asteptare = 1.0
    ultima_eroare: Exception | None = None
    for _ in range(incercari):
        try:
            r = client.get(url, params=params, timeout=30.0)
            if r.status_code == 429:
                time.sleep(asteptare)
                asteptare *= 2
                continue
            if 400 <= r.status_code < 500:
                motiv = ""
                try:
                    motiv = r.json().get("reason", "")
                except Exception:
                    motiv = r.text[:200]
                # eroare de configurare a parcului, nu pană de sursă: nu atinge disjunctorul
                raise EroareMeteo(
                    f"Sursa meteo a respins cererea ({r.status_code}): {motiv or 'fără detalii'}. "
                    f"Verificați id-urile modelelor meteo configurate pentru parc."
                )
            r.raise_for_status()
            DISJUNCTOR.inregistreaza_succes()
            return r.json()
        except EroareMeteo:
            raise
        except httpx.HTTPError as exc:
            ultima_eroare = exc
            time.sleep(asteptare)
            asteptare *= 2
    motiv = f"nu a răspuns după {incercari} încercări: {ultima_eroare}"
    DISJUNCTOR.inregistreaza_esec(motiv)
    raise EroareMeteoTemporara(f"Sursa meteo {motiv}")


def _parseaza_raspuns_multimodel(raspuns: dict, modele: list[str]) -> dict[str, pd.DataFrame]:
    """Open-Meteo, când primește mai multe modele în `models=`, sufixează
    fiecare variabilă cu id-ul modelului (ex: temperature_2m_ecmwf_ifs025).
    Un model care nu întoarce NICIO variabilă obligatorie e ignorat."""
    orar = raspuns.get("hourly", {})
    if "time" not in orar:
        raise EroareMeteo(f"Răspuns Open-Meteo fără serie orară: {raspuns.get('reason', raspuns)}")

    # Etichetele din răspuns sunt ISO 8601 FĂRĂ decalaj ("2026-06-01T12:00") —
    # faptul că sunt UTC vine exclusiv din parametrul `timezone=UTC` al cererii.
    # E singura convenție implicită rămasă în lanțul de date, deci o verificăm
    # explicit: `utc_offset_seconds` diferit de 0 ar însemna că întreaga serie e
    # ora locală, iar interpretarea ei ca UTC ar deplasa tăcut toată producția
    # cu 2-3 ore. Mai bine o eroare clară decât o prognoză greșită plauzibilă.
    decalaj = raspuns.get("utc_offset_seconds")
    if decalaj not in (None, 0):
        raise EroareMeteo(
            f"Sursa meteo a întors ore în fus local (utc_offset_seconds={decalaj}), nu în UTC. "
            f"Cererea platformei include mereu timezone=UTC — dacă vedeți asta, parametrul a fost "
            f"modificat sau furnizorul l-a ignorat; interpretarea ca UTC ar deplasa toată seria.")

    timp = pd.to_datetime(orar["time"], utc=True)
    pe_model: dict[str, pd.DataFrame] = {}
    for model in modele:
        date_model = {}
        for var in VARIABILE_ORARE:
            cheie = f"{var}_{model}" if len(modele) > 1 else var
            if cheie in orar:
                coloana = pd.Series(orar[cheie], index=timp, dtype="float64")
                # o coloană complet goală nu aduce informație — o tratăm ca absentă,
                # ca să nu "dilueze" ponderile din ansamblu
                if coloana.notna().any():
                    date_model[var] = coloana
        are_obligatorii = any(v in date_model for v in VARIABILE_OBLIGATORII)
        if date_model and are_obligatorii:
            pe_model[model] = _reeticheteaza_la_inceput_de_interval(
                pd.DataFrame(date_model, index=timp))
    if not pe_model:
        raise EroareMeteo("Niciun model meteo nu a întors date utilizabile.")
    return pe_model


# Variabile pe care Open-Meteo le livrează ca MEDIE / SUMĂ a orei PRECEDENTE
# (documentat: „average of the preceding hour"), deci etichetate la finalul
# intervalului. Restul (temperatură, vânt, presiune, umiditate, nori) sunt
# valori instantanee LA etichetă.
VARIABILE_MEDIA_OREI_PRECEDENTE = [
    "shortwave_radiation", "direct_normal_irradiance", "diffuse_radiation",
    "direct_radiation", "precipitation", "snowfall",
]


def _reeticheteaza_la_inceput_de_interval(df: pd.DataFrame) -> pd.DataFrame:
    """Aduce datele la convenția internă R-512: eticheta = ÎNCEPUTUL intervalului.

    Open-Meteo etichetează mărimile cumulate/mediate prin FINALUL intervalului
    (valoarea de la 12:00 e media orei 11:00-12:00). Intervalul care ÎNCEPE la
    11:00 poartă, deci, valoarea publicată la 12:00 — de unde deplasarea cu un
    pas înapoi. Valorile instantanee (temperatură, vânt) rămân pe eticheta lor:
    ele descriu chiar momentul de început al intervalului.

    Deplasarea NU schimbă fizica — aceeași radiație rămâne pe același interval
    real de timp — ci doar cum se numește rândul. Verificarea care contează:
    vârful de producție trebuie să rămână în jurul amiezii solare și după
    schimbare (vezi tests/test_conventie_interval.py).
    """
    if df.empty or len(df.index) < 2:
        return df
    de_deplasat = [c for c in df.columns if c in VARIABILE_MEDIA_OREI_PRECEDENTE]
    if not de_deplasat:
        return df
    rezultat = df.copy()
    for coloana in de_deplasat:
        rezultat[coloana] = df[coloana].shift(-1)
    # Ultimul rând rămâne fără radiație (valoarea lui ar fi venit din ora
    # următoare, necerută) — se elimină, ca să nu apară o oră cu radiație
    # lipsă care ar strica ponderile de ansamblu și controalele de calitate.
    return rezultat.iloc[:-1]


def _params_comune(latitudine: float, longitudine: float, modele: list[str]) -> dict:
    return {
        "latitude": latitudine, "longitude": longitudine,
        "hourly": ",".join(VARIABILE_ORARE),
        "models": ",".join(modele),
        "timezone": "UTC",
        "wind_speed_unit": "ms",
    }


def _interogheaza_cu_rezerva(url: str, params_baza: dict, modele: list[str]) -> dict[str, pd.DataFrame]:
    """Întâi o singură cerere pentru toate modelele (rapid, o singură limită
    de rată consumată). Dacă eșuează, se încearcă model cu model, ca o
    singură sursă picată să nu anuleze toată rularea (recomandarea A8 din
    docs/cercetare/01-platforme-comerciale.md)."""
    # Dacă sursa e deja cunoscută ca fiind căzută, nu are rost nici măcar
    # deschiderea unui client HTTP — cu atât mai puțin reluarea model cu model.
    _verifica_disjunctor()
    with httpx.Client() as client:
        try:
            raspuns = _cerere_cu_reincercare(client, url, {**params_baza, "models": ",".join(modele)})
            return _parseaza_raspuns_multimodel(raspuns, modele)
        except EroareMeteo as eroare_comuna:
            if len(modele) == 1:
                raise
            pe_model: dict[str, pd.DataFrame] = {}
            erori: list[str] = []
            doar_temporare = isinstance(eroare_comuna, EroareMeteoTemporara)
            for model in modele:
                try:
                    raspuns = _cerere_cu_reincercare(client, url, {**params_baza, "models": model})
                    pe_model.update(_parseaza_raspuns_multimodel(raspuns, [model]))
                except EroareMeteo as exc:
                    erori.append(f"{model}: {exc}")
                    doar_temporare = doar_temporare and isinstance(exc, EroareMeteoTemporara)
            if not pe_model:
                # Tipul erorii se păstrează: dacă TOATE eșecurile au fost
                # temporare, apelantul (și disjunctorul) trebuie să poată
                # distinge o pană de sursă de o configurare greșită de parc.
                tip = EroareMeteoTemporara if doar_temporare else EroareMeteo
                raise tip(
                    f"Nicio sursă meteo nu a răspuns. Cererea comună: {eroare_comuna}. "
                    f"Individual: {'; '.join(erori)}"
                )
            return pe_model


def obtine_prognoza_meteo(latitudine: float, longitudine: float, modele: list[str],
                           zile_trecut: int = 0, zile_viitor: int = 7) -> dict[str, pd.DataFrame]:
    """Prognoza meteo curentă (folosită la rularea zilnică). Întoarce un
    DataFrame per model, indexat UTC pe eticheta orei FINALE (convenția
    Open-Meteo pentru date orare), coloane = VARIABILE_ORARE disponibile."""
    params = {
        **_params_comune(latitudine, longitudine, modele),
        "past_days": zile_trecut,
        # Open-Meteo acceptă cel mult 16 zile de prognoză și respinge cererea cu
        # 400 dacă se cere mai mult. Motorul adaugă 2 zile de marjă peste
        # orizontul cerut (filtrarea pe fus orar local poate „consuma" o zi la
        # capete), deci la orizont maxim s-ar cere 18 — limita se impune AICI,
        # unde e o proprietate a furnizorului, nu a logicii de business.
        "forecast_days": max(1, min(zile_viitor, ZILE_PROGNOZA_MAXIM_FURNIZOR)),
    }
    return _interogheaza_cu_rezerva(MEDIU.open_meteo_base_url, params, modele)


def obtine_meteo_istoric_reconstruit(latitudine: float, longitudine: float, modele: list[str],
                                      inceput: date, sfarsit: date) -> dict[str, pd.DataFrame]:
    """R-610 — arhiva de "rulări precedente": ce prognoza fiecare model, la
    momentul respectiv, pentru fiecare zi din trecut (NU vremea reală
    reconstruită după fapt). Folosește endpoint-ul de arhivă al Open-Meteo,
    care păstrează exact prognozele istorice emise, oră cu oră."""
    params = {
        **_params_comune(latitudine, longitudine, modele),
        "start_date": inceput.isoformat(),
        "end_date": sfarsit.isoformat(),
    }
    return _interogheaza_cu_rezerva(MEDIU.open_meteo_archive_url, params, modele)


def obtine_meteo_istoric_real(latitudine: float, longitudine: float,
                               inceput: date, sfarsit: date) -> pd.DataFrame:
    """R-701 — vremea REALĂ, istorică (cea mai bună reconstrucție disponibilă
    a ce s-a întâmplat efectiv, reanaliză ERA5), folosită DOAR pentru
    calibrarea geometriei. Intenționat diferită de arhiva de "rulări
    precedente" (obtine_meteo_istoric_reconstruit) folosită la antrenarea ML
    — aici vrem cea mai bună estimare a vremii reale, nu ce s-a prognozat
    la momentul respectiv. Un singur set de date (reanaliza nu are mai multe
    "modele" de combinat)."""
    params = {
        "latitude": latitudine, "longitude": longitudine,
        "hourly": ",".join(VARIABILE_ORARE),
        "timezone": "UTC",
        "start_date": inceput.isoformat(), "end_date": sfarsit.isoformat(),
        "wind_speed_unit": "ms",
    }
    with httpx.Client() as client:
        raspuns = _cerere_cu_reincercare(client, MEDIU.open_meteo_reanaliza_url, params)
    pe_model = _parseaza_raspuns_multimodel(raspuns, ["reanaliza"])
    return pe_model["reanaliza"]


def obtine_meteo_istoric_real_cache(repo, parc_id: str, latitudine: float, longitudine: float,
                                     inceput: date, sfarsit: date) -> pd.DataFrame:
    """Ca `obtine_meteo_istoric_real`, dar trecută prin `meteo_cache`
    (tabela exista în schemă, dar nu era folosită de nimic — R-xxx). O zi
    din TRECUT de reanaliză ERA5 nu se mai schimbă niciodată odată publicată,
    deci se poate păstra definitiv: fiecare re-vizualizare a acelorași zile
    (ex. pagina „Reală vs. prognoză” a datelor meteo) nu mai cere din nou
    rețeaua — mai puține cereri către Open-Meteo, răspuns aproape instant la
    a doua vizualizare a aceleiași perioade."""
    zile = pd.date_range(inceput, sfarsit, freq="D").date
    lipsa: list = []
    bucati: list[pd.DataFrame] = []
    for zi in zile:
        payload = repo.get_meteo_cache(parc_id, SURSA_CACHE_REANALIZA, zi.isoformat())
        if payload is None:
            lipsa.append(zi)
            continue
        bucati.append(pd.DataFrame(payload["date"], index=pd.to_datetime(payload["index"], utc=True)))

    if lipsa:
        proaspat = obtine_meteo_istoric_real(latitudine, longitudine, min(lipsa), max(lipsa))
        for zi in lipsa:
            felie = proaspat[proaspat.index.date == zi]
            if felie.empty:
                continue
            repo.set_meteo_cache(parc_id, SURSA_CACHE_REANALIZA, zi.isoformat(), {
                "index": [ts.isoformat() for ts in felie.index],
                "date": {col: felie[col].where(felie[col].notna(), None).tolist() for col in felie.columns},
            })
        bucati.append(proaspat)

    if not bucati:
        return pd.DataFrame()
    rezultat = pd.concat(bucati).sort_index()
    return rezultat[~rezultat.index.duplicated(keep="last")]


def combina_ansamblu(pe_model: dict[str, pd.DataFrame],
                      ponderi: dict[str, float] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """R-501/502 — combină modelele printr-o medie ponderată configurabilă
    și calculează dispersia (abaterea standard între modele) ca proxy al
    incertitudinii zilei respective, folosită mai departe ca intrare pt.
    stratul ML. Ponderile lipsă => pondere egală.

    IMPORTANT (corecție v2): ponderile se RENORMALIZEAZĂ pe fiecare oră, în
    funcție de modelele care chiar au date la ora respectivă. Altfel, orele
    unde un model lipsește (orizont mai scurt, variabilă neexpusă) ar primi
    o medie sistematic prea mică — o eroare tăcută de până la zeci de
    procente la orizonturi lungi.

    Dispersia cu un singur model disponibil e 0 (nu NaN): "un singur model nu
    ne spune nimic despre dezacord", iar NaN s-ar propaga în intrările ML.
    """
    modele = list(pe_model.keys())
    if not modele:
        return pd.DataFrame(), pd.DataFrame()

    ponderi = ponderi or {}
    p = {m: max(0.0, float(ponderi.get(m, 1.0))) for m in modele}
    if sum(p.values()) <= 0:      # toate ponderile zero/negative -> revenim la egal
        p = {m: 1.0 for m in modele}

    toate_coloanele: set[str] = set()
    for df in pe_model.values():
        toate_coloanele |= set(df.columns)

    index_comun = None
    for df in pe_model.values():
        index_comun = df.index if index_comun is None else index_comun.union(df.index)

    medii, dispersii = {}, {}
    for col in toate_coloanele:
        serii_brute = {}
        for m in modele:
            if col in pe_model[m].columns:
                serii_brute[m] = pe_model[m][col].reindex(index_comun)
        if not serii_brute:
            continue

        valori = pd.DataFrame(serii_brute)                      # coloane = modele
        ponderi_col = pd.Series({m: p[m] for m in valori.columns}, dtype="float64")
        # masca de disponibilitate, oră cu oră
        disponibil = valori.notna()
        ponderi_efective = disponibil.mul(ponderi_col, axis=1)
        suma_ponderi = ponderi_efective.sum(axis=1)
        produs = (valori.fillna(0.0) * ponderi_efective).sum(axis=1)
        medie = produs.divide(suma_ponderi.where(suma_ponderi > 0))   # 0 ponderi -> NaN, corect
        medii[col] = medie

        # dispersia: abaterea standard între modelele DISPONIBILE la ora respectivă
        numar_disponibile = disponibil.sum(axis=1)
        disp = valori.std(axis=1, ddof=1)
        disp = disp.where(numar_disponibile > 1, 0.0)
        disp = disp.where(numar_disponibile > 0)                 # fără niciun model -> NaN
        dispersii[col] = disp

    return pd.DataFrame(medii, index=index_comun), pd.DataFrame(dispersii, index=index_comun)


def acoperire_per_model(pe_model: dict[str, pd.DataFrame]) -> dict[str, float]:
    """Fracția de ore pentru care fiecare model chiar a întors valori pentru
    variabilele obligatorii — afișată în trasabilitate, ca operatorul să vadă
    imediat dacă o sursă a acoperit doar o parte din orizont."""
    if not pe_model:
        return {}
    index_total = None
    for df in pe_model.values():
        index_total = df.index if index_total is None else index_total.union(df.index)
    rezultat = {}
    for model, df in pe_model.items():
        coloane = [c for c in VARIABILE_OBLIGATORII if c in df.columns]
        if not coloane or len(index_total) == 0:
            rezultat[model] = 0.0
            continue
        valabile = df[coloane].reindex(index_total).notna().all(axis=1).sum()
        rezultat[model] = round(float(valabile) / len(index_total), 4)
    return rezultat


def asigura_inchidere_radiatie(meteo: pd.DataFrame, cos_zenit: pd.Series | None = None) -> pd.DataFrame:
    """Impune consistența fizică GHI = DHI + DNI·cos(θz).

    Open-Meteo produce cele trei câmpuri pe căi de post-procesare diferite,
    deci ele NU se închid exact. Motorul fizic le folosește pe toate trei, așa
    că o inconsistență se traduce direct în eroare de producție. Recalculăm
    componenta directă din GHI și DHI (ambele mai fiabile decât DNI în NWP)
    când avem unghiul solar; altfel lăsăm datele neatinse.

    Vezi docs/cercetare/03-literatura-stiintifica.md, secțiunea 1.
    """
    if cos_zenit is None or not {"ghi", "dhi"}.issubset(meteo.columns):
        return meteo
    rezultat = meteo.copy()
    cos_z = cos_zenit.reindex(rezultat.index).clip(lower=0.0)
    difuz = rezultat["dhi"].clip(lower=0.0)
    global_ = rezultat["ghi"].clip(lower=0.0)
    difuz = np.minimum(difuz, global_)                     # difuza nu poate depăși globala
    with np.errstate(divide="ignore", invalid="ignore"):
        dni_inchis = (global_ - difuz) / cos_z.replace(0.0, np.nan)
    dni_inchis = dni_inchis.replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(lower=0.0, upper=1367.0)
    # sub 3° elevație (cos θz < 0,05) împărțirea devine instabilă: păstrăm 0,
    # contribuția energetică fiind oricum neglijabilă
    dni_inchis = dni_inchis.where(cos_z > 0.05, 0.0)
    rezultat["dni"] = dni_inchis
    rezultat["dhi"] = difuz
    return rezultat
