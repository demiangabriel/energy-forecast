"""
Funcții "view-model" — pregătesc date pentru șabloane (calcule de afișare),
separat de rutare (routes_*.py) și de logica de business (core/). Nimic de
aici nu scrie în baza de date.

Corecții v2 (vezi docs/DEBUGGING.md):
  * indicatorii folosesc acum modulul de verificare (ore utile, excluderea
    orelor cu probleme de calitate, scor de skill) în loc de o medie brută
    peste toate orele, inclusiv nopțile;
  * starea "abatere" din portofoliu nu se mai declanșează pe o zi acoperită
    doar parțial de prognoză — înainte, o prognoză care acoperea 6 din 24 de
    ore părea o subestimare de 75% și marca fals parcul ca având abatere;
  * comparațiile de timp se fac pe obiecte datetime, nu pe text ISO.
"""
from __future__ import annotations

import calendar
from datetime import date as date_cls
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from app import setari as setari_mod
from app.core.momente import iso_utc
from app.core.forecast_engine import putere_instalata_kw, putere_normalizare_kw
from app.models import Parc, Rulare, StareParc

# Sub această acoperire a zilei, comparația prognoză/realizat nu e concludentă.
ACOPERIRE_MINIMA_ZI = 0.75


def _marja_orizont_zile(repo) -> int:
    """Cu cât în urmă trebuie căutate rulările ca să acopere sigur o oră dată.

    O rulare declanșată la momentul T conține ore doar în [T, T + orizont], deci
    pentru a găsi prognozele emise pentru un interval care începe la A e de ajuns
    să privim rulările de la A - orizont_maxim încoace. Ziua în plus e o marjă
    pentru granițele de fus orar și pentru rulările manuale cu orizont extins."""
    return int(setari_mod.get(repo, "zile_orizont_maxim")) + 1


def _grupeaza_pe_zi_locala(ore, fus_orar: str) -> dict:
    tz = ZoneInfo(fus_orar)
    pe_zi: dict = {}
    for o in ore:
        if o.putere_finala_kw is None:
            continue
        zi = datetime.fromisoformat(o.timestamp_utc).astimezone(tz).date()
        pe_zi.setdefault(zi, {"energie_kwh": 0.0, "varf_kw": 0.0, "ore": 0})
        pe_zi[zi]["energie_kwh"] += o.putere_finala_kw
        pe_zi[zi]["varf_kw"] = max(pe_zi[zi]["varf_kw"], o.putere_finala_kw)
        pe_zi[zi]["ore"] += 1
    return pe_zi


def rezumat_portofoliu_parc(repo, parc: Parc) -> dict:
    setari = setari_mod.toate(repo)
    prag_abatere = setari["prag_abatere_portofoliu_pct"]["valoare"]
    tz = ZoneInfo(parc.fus_orar)
    azi = datetime.now(timezone.utc).astimezone(tz).date()
    maine = azi + timedelta(days=1)
    ieri = azi - timedelta(days=1)

    rezultat = {
        "parc": parc, "stare": StareParc.FARA_DATE.value,
        "energie_maine_kwh": None, "varf_maine_kw": None, "abatere_ieri_pct": None,
    }

    if parc.mod_umbra:
        rezultat["stare"] = StareParc.MOD_UMBRA.value

    ultima = repo.ultima_rulare_reusita(parc.id)
    if not ultima or not ultima.ore:
        return rezultat

    pas_ore = (ultima.rezolutie_minute or 60) / 60.0
    pe_zi = _grupeaza_pe_zi_locala(ultima.ore, parc.fus_orar)
    if maine in pe_zi:
        # la rezoluție sub-orară, suma puterilor NU e energie fără scalare
        rezultat["energie_maine_kwh"] = pe_zi[maine]["energie_kwh"] * pas_ore
        rezultat["varf_maine_kw"] = pe_zi[maine]["varf_kw"]

    # abaterea față de realizat din ziua precedentă (R-303) — dacă avem ambele:
    # o prognoză salvată pentru "ieri" și producție reală importată pentru "ieri"
    inceput_ieri = datetime.combine(ieri, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    sfarsit_ieri = inceput_ieri + timedelta(days=1)
    productie_ieri = repo.listeaza_productie(parc.id, iso_utc(inceput_ieri), iso_utc(sfarsit_ieri))
    energie_reala_ieri = sum(p.energie_kwh for p in productie_ieri)

    prognoza_ieri_kwh = 0.0
    ore_acoperite = 0
    # Numai rulările emise în fereastra [ieri - orizont_maxim, sfârșitul zilei
    # de ieri] pot conține ore din ziua de ieri: o rulare mai veche nu ajunge
    # până acolo, una mai nouă prognozează doar zile viitoare. Fără această
    # margine se citeau zeci de rulări întregi pentru o singură zi.
    orizont_maxim = int(setari["zile_orizont_maxim"]["valoare"])
    for rulare in repo.itereaza_rulari(
            parc.id, limita=60,
            declansata_dupa=iso_utc(inceput_ieri - timedelta(days=orizont_maxim + 1)),
            declansata_inainte=iso_utc(sfarsit_ieri)):
        pas_rulare = (rulare.rezolutie_minute or 60) / 60.0
        suma, numar = 0.0, 0
        for o in rulare.ore:
            if o.putere_finala_kw is None:
                continue
            ts = datetime.fromisoformat(o.timestamp_utc)
            if inceput_ieri <= ts < sfarsit_ieri:
                suma += o.putere_finala_kw * pas_rulare
                numar += 1
        if numar:
            prognoza_ieri_kwh, ore_acoperite = suma, numar * pas_rulare
            break  # cea mai recentă rulare care acoperă ziua respectivă

    acoperire_suficienta = ore_acoperite >= 24 * ACOPERIRE_MINIMA_ZI
    if acoperire_suficienta and energie_reala_ieri > 0:
        abatere = (prognoza_ieri_kwh - energie_reala_ieri) / energie_reala_ieri * 100
        rezultat["abatere_ieri_pct"] = abatere
        if not parc.mod_umbra:
            rezultat["stare"] = StareParc.ABATERE.value if abs(abatere) > prag_abatere else StareParc.LIVRAT.value
    elif not parc.mod_umbra:
        are_model = repo.model_ml_activ(parc.id) is not None or bool(parc.parc_imprumut_corector_id)
        rezultat["stare"] = StareParc.LIVRAT.value if are_model else StareParc.DOAR_FIZIC.value

    return rezultat


def _serie_din_dict(dictionar: dict) -> pd.Series:
    if not dictionar:
        return pd.Series(dtype="float64")
    serie = pd.Series(dictionar)
    serie.index = pd.to_datetime(serie.index, utc=True)
    return serie.sort_index()


def indicatori_parc(repo, parc: Parc, zile: int = 14) -> dict:
    """Indicatori pentru ecranul de detaliu parc (R-304), calculați corect:
    doar pe orele utile, fără orele cu probleme de calitate, și cu scorul de
    skill față de prognoza de referință."""
    from app.core.clearsky import masca_ore_utile, putere_referinta
    from app.core.data_quality import ore_excluse
    from app.core.verification import verifica

    sfarsit = datetime.now(timezone.utc)
    inceput = sfarsit - timedelta(days=zile)
    productie = {p.timestamp_utc: p.energie_kwh
                 for p in repo.listeaza_productie(parc.id, iso_utc(inceput), iso_utc(sfarsit))}

    prognoza: dict[str, float] = {}
    orizonturi: dict[str, float] = {}
    for rulare in repo.itereaza_rulari(
            parc.id, limita=200,
            declansata_dupa=iso_utc(inceput - timedelta(days=_marja_orizont_zile(repo))),
            declansata_inainte=iso_utc(sfarsit)):
        for o in rulare.ore:
            if o.putere_finala_kw is None or o.timestamp_utc in prognoza:
                continue
            ts = datetime.fromisoformat(o.timestamp_utc)
            if inceput <= ts <= sfarsit:
                prognoza[o.timestamp_utc] = o.putere_finala_kw
                if o.ore_in_avans is not None:
                    orizonturi[o.timestamp_utc] = o.ore_in_avans

    serie_prognoza = _serie_din_dict(prognoza)
    serie_realizat = _serie_din_dict(productie)
    comune = sorted(set(productie) & set(prognoza))
    serie = [{"timestamp": ts, "prognozat": prognoza[ts], "realizat": productie[ts]} for ts in comune]

    gol = {"serie": serie, "nmae_pct": None, "bias_pct": None, "skill": None,
           "energie_prognozata_kwh": None, "energie_realizata_kwh": None, "numar_ore": 0,
           "ore_excluse_qc": 0, "ore_excluse_noapte": 0,
           "baza_normalizare_kw": putere_normalizare_kw(parc), "nmae_medie_pct": None,
           "nmae_referinta_pct": None}
    if not comune:
        return gol

    baza = putere_normalizare_kw(parc)
    index_comun = serie_prognoza.index.intersection(serie_realizat.index)
    referinta_cs = putere_referinta(parc, index_comun)
    raport = verifica(
        prognozat_kw=serie_prognoza, realizat_kw=serie_realizat, putere_normalizare_kw=baza,
        referinta_cer_senin_kw=referinta_cs,
        masca_ore_utile=masca_ore_utile(parc, referinta_cs, baza),
        ore_excluse_qc=ore_excluse(repo, parc.id),
        ore_in_avans=_serie_din_dict(orizonturi) if orizonturi else None,
    )
    ind = raport.indicatori
    return {
        "serie": serie,
        "nmae_pct": ind.nmae_capacitate_pct,
        "nmae_medie_pct": ind.nmae_medie_pct,
        "bias_pct": ind.nbias_capacitate_pct,
        "skill": raport.skill_vs_referinta,
        "nmae_referinta_pct": raport.indicatori_referinta.nmae_capacitate_pct,
        "energie_prognozata_kwh": ind.energie_prognozata_kwh,
        "energie_realizata_kwh": ind.energie_realizata_kwh,
        "numar_ore": ind.numar_ore,
        "ore_excluse_qc": raport.ore_excluse_qc,
        "ore_excluse_noapte": raport.ore_excluse_noapte,
        "baza_normalizare_kw": baza,
        "pe_orizont": raport.pe_orizont,
    }


def serie_comparatie_meteo(repo, parc: Parc, zile: int = 14,
                            inceput_data: date_cls | None = None,
                            sfarsit_data: date_cls | None = None) -> dict:
    """Rubrica „Reală vs. prognoză" pentru datele METEO (distinctă de cea
    pentru producție): pentru fiecare oră din interval, GHI/temperatură/vânt
    din prognoza cea mai RECENTĂ emisă pentru acea oră (aceeași convenție ca
    la producție) alături de vremea REALĂ — cea mai bună reconstrucție
    disponibilă, reanaliza ERA5 (R-701), NU „rulările precedente" (care sunt
    tot niște prognoze istorice, folosite la antrenarea ML — vezi
    weather_client.py). Util ca să se vadă direct dacă eroarea de producție
    vine din modelul fizic/ML sau chiar din prognoza meteo primită."""
    from app.core.weather_client import obtine_meteo_istoric_real_cache

    tz = ZoneInfo(parc.fus_orar)
    if inceput_data is not None and sfarsit_data is not None:
        if sfarsit_data < inceput_data:
            inceput_data, sfarsit_data = sfarsit_data, inceput_data
        inceput = datetime.combine(inceput_data, time.min, tzinfo=tz).astimezone(timezone.utc)
        sfarsit = datetime.combine(sfarsit_data, time.max, tzinfo=tz).astimezone(timezone.utc)
        zile = (sfarsit_data - inceput_data).days + 1
    else:
        sfarsit = datetime.now(timezone.utc)
        inceput = sfarsit - timedelta(days=zile)

    prognoza: dict[str, dict] = {}
    for rulare in repo.itereaza_rulari(
            parc.id, limita=200,
            declansata_dupa=iso_utc(inceput - timedelta(days=_marja_orizont_zile(repo))),
            declansata_inainte=iso_utc(sfarsit)):
        for o in rulare.ore:
            if o.timestamp_utc in prognoza:
                continue
            ts = datetime.fromisoformat(o.timestamp_utc)
            if inceput <= ts <= sfarsit:
                prognoza[o.timestamp_utc] = {
                    "ghi": o.ghi_wm2, "temp": o.temp_aer_c, "vant": o.viteza_vant_ms,
                }
    if not prognoza:
        return {"randuri": [], "zile": zile, "are_date": False,
                "inceput_data": inceput.astimezone(tz).date().isoformat(),
                "sfarsit_data": sfarsit.astimezone(tz).date().isoformat(),
                "mae_ghi": None, "mae_temp": None, "mae_vant": None}

    try:
        real = obtine_meteo_istoric_real_cache(
            repo, parc.id, parc.latitudine, parc.longitudine,
            inceput.astimezone(tz).date(), sfarsit.astimezone(tz).date())
    except Exception as exc:
        return {"randuri": [], "zile": zile, "are_date": False, "eroare": str(exc),
                "inceput_data": inceput.astimezone(tz).date().isoformat(),
                "sfarsit_data": sfarsit.astimezone(tz).date().isoformat(),
                "mae_ghi": None, "mae_temp": None, "mae_vant": None}

    randuri = []
    sume = {"ghi": 0.0, "temp": 0.0, "vant": 0.0}
    numar = {"ghi": 0, "temp": 0, "vant": 0}
    for ts_text in sorted(prognoza):
        ts = pd.Timestamp(ts_text)
        prog = prognoza[ts_text]
        # vezi nota din serie_comparatie_completa: ordonarea se face pe momentul
        # real, nu pe textul „zz.ll.aaaa", care ar amesteca lunile
        rand = {"ts_utc": ts_text, "timestamp": ts.tz_convert(tz).strftime("%d.%m.%Y %H:%M")}
        for cheie, col in (("ghi", "shortwave_radiation"), ("temp", "temperature_2m"), ("vant", "wind_speed_10m")):
            reala = None
            if col in real.columns and ts in real.index:
                v = real.at[ts, col]
                reala = float(v) if pd.notna(v) else None
            rand[f"{cheie}_prognozat"] = prog[cheie]
            rand[f"{cheie}_real"] = reala
            if reala is not None and prog[cheie] is not None:
                sume[cheie] += abs(reala - prog[cheie])
                numar[cheie] += 1
        randuri.append(rand)

    randuri.sort(key=lambda r: r["ts_utc"], reverse=True)
    return {
        "randuri": randuri, "zile": zile, "are_date": True,
        "inceput_data": inceput.astimezone(tz).date().isoformat(),
        "sfarsit_data": sfarsit.astimezone(tz).date().isoformat(),
        "mae_ghi": (sume["ghi"] / numar["ghi"]) if numar["ghi"] else None,
        "mae_temp": (sume["temp"] / numar["temp"]) if numar["temp"] else None,
        "mae_vant": (sume["vant"] / numar["vant"]) if numar["vant"] else None,
    }


def diagnostic_meteo(repo, parc: Parc, numar_rulari: int = 30) -> dict:
    """„Zona de siguranță” pentru datele meteo: pe ultimele `numar_rulari`
    rulări, cât de des a răspuns FIECARE sursă meteo configurată
    (`parc.surse_meteo`), plus ponderea efectivă cu care a contat ultima
    dată când a răspuns. Nu există un „import” de date meteo — sursele se
    interoghează live la fiecare rulare (R-501) — deci diagnosticul e despre
    FIABILITATEA acelor interogări, nu despre un fișier încărcat.

    O sursă care răspunde constant sub prag e semnalată explicit: modelul
    de ansamblu renormalizează automat ponderile (nu strică rularea), dar o
    sursă mereu absentă înseamnă, în practică, mai puțină diversitate de
    modele decât crede operatorul că are."""
    # Diagnosticul privește DOAR antetul fiecărei rulări (ce sursă a răspuns,
    # cu ce pondere, în ce stare s-a încheiat) — orele de prognoză nu se ating.
    rulari = repo.listeaza_rulari_sumar(parc.id, limita=numar_rulari)
    surse = list(parc.surse_meteo)

    pe_sursa: dict[str, dict] = {s: {"raspunsuri": 0, "total": 0, "ultima_pondere": None} for s in surse}
    rulari_analizate = 0
    ultima_rulare_esuata = None
    for r in rulari:
        rulari_analizate += 1
        raspuns = r.surse_meteo_raspuns or {}
        for s in surse:
            if s not in raspuns:
                continue  # rulare eșuată înainte de a apela sursele — nu se contorizează ca „absentă”
            pe_sursa[s]["total"] += 1
            if raspuns[s]:
                pe_sursa[s]["raspunsuri"] += 1
            if pe_sursa[s]["ultima_pondere"] is None and s in (r.ponderi_folosite or {}):
                pe_sursa[s]["ultima_pondere"] = r.ponderi_folosite[s]
        if r.stare == "esuata" and ultima_rulare_esuata is None:
            ultima_rulare_esuata = r

    randuri = []
    are_probleme = False
    for s in surse:
        info = pe_sursa[s]
        procent = round(100 * info["raspunsuri"] / info["total"], 1) if info["total"] else None
        problema = procent is not None and procent < 80.0
        are_probleme = are_probleme or problema
        randuri.append({
            "sursa": s, "raspunsuri": info["raspunsuri"], "total": info["total"],
            "procent": procent, "ultima_pondere": info["ultima_pondere"], "problema": problema,
        })

    ultima = rulari[0] if rulari else None
    return {
        "randuri": randuri,
        "rulari_analizate": rulari_analizate,
        "are_probleme": are_probleme,
        "ultima_stare": ultima.stare if ultima else None,
        "ultima_mesaj": ultima.mesaj if ultima else None,
        "ultima_declansata_la": ultima.declansata_la if ultima else None,
        "ultima_rulare_esuata": ultima_rulare_esuata,
        "fara_rulari": not rulari,
    }


def serie_grafic_recenta(rulare: Rulare | None, parc: Parc) -> list[dict]:
    """Serie din ultima rulare (fizică + finală + benzi de incertitudine),
    pentru graficul din detaliul parcului — chiar dacă nu există încă
    producție reală de comparat."""
    if not rulare:
        return []
    tz = ZoneInfo(parc.fus_orar)
    rezultat = []
    for o in rulare.ore:
        ts_local = datetime.fromisoformat(o.timestamp_utc).astimezone(tz)
        cuantile = o.cuantile_kw or {}
        rezultat.append({
            "timestamp": ts_local.strftime("%d.%m %H:%M"),
            "fizic": round(o.putere_ac_fizic_kw, 1) if o.putere_ac_fizic_kw is not None else None,
            "final": round(o.putere_finala_kw, 1) if o.putere_finala_kw is not None else None,
            "p10": round(cuantile["0.1"], 1) if "0.1" in cuantile else None,
            "p90": round(cuantile["0.9"], 1) if "0.9" in cuantile else None,
            "blocat": o.blocat,
        })
    return rezultat


def tabel_recent_zile(rulare: Rulare | None, parc: Parc, zile: int = 3) -> list[dict]:
    """„Tabel recent" din detaliul parcului — grupează orele celei mai
    recente rulări pe ZI LOCALĂ (parc.fus_orar), limitat la primele `zile`
    zile care au date, fiecare cu totalul zilei alături de orele individuale.

    Înainte se arătau primele 24 de INTRĂRI din `rulare.ore` — corect doar la
    rezoluție orară; la 15 minute însemna doar 6 ore dintr-o singură zi, nu 3
    zile. Gruparea pe zi locală e corectă indiferent de rezoluție."""
    if not rulare or not rulare.ore:
        return []
    tz = ZoneInfo(parc.fus_orar)
    pas_ore = (rulare.rezolutie_minute or 60) / 60.0
    pe_zi: dict[date_cls, list] = {}
    for o in rulare.ore:
        zi = datetime.fromisoformat(o.timestamp_utc).astimezone(tz).date()
        pe_zi.setdefault(zi, []).append(o)

    rezultat = []
    for zi in sorted(pe_zi.keys())[:zile]:
        ore_zi = pe_zi[zi]
        # suma puterilor NU e energie fără scalare la rezoluție sub-orară (R-512)
        energie_kwh = sum(o.putere_finala_kw for o in ore_zi if o.putere_finala_kw is not None) * pas_ore
        rezultat.append({"zi": zi, "ore": ore_zi, "energie_kwh": energie_kwh})
    return rezultat


def serie_comparatie_completa(repo, parc: Parc, zile: int = 14,
                               inceput_data: date_cls | None = None,
                               sfarsit_data: date_cls | None = None) -> dict:
    """Datele pentru rubrica „Comparație producție": pe fiecare oră comună,
    producția reală alături de TOATE etapele prognozei — fizică (înainte de
    orice corecție), corecția ML aplicată, și finala (fizic + corecție ML,
    ce se raportează efectiv clientului) — plus dezechilibrul, |realizat -
    prognozat final| (R-304: operatorul trebuie să vadă direct cât a
    contribuit corectorul ML, nu doar rezultatul final).

    Pentru fiecare oră se ia cea mai RECENTĂ prognoză disponibilă (aceeași
    convenție ca la indicatori_parc/reports.py) — un istoric cu mai multe
    rulări succesive pentru aceeași oră ar altfel amesteca prognoze emise la
    momente diferite.

    Intervalul se poate alege liber (`inceput_data`/`sfarsit_data`, date
    calendaristice LOCALE, în fusul parcului) — dacă lipsesc, se folosește
    fereastra relativă `zile` (comportamentul dinainte, păstrat ca implicit
    și ca scurtătură din butoanele 7/14/30/90)."""
    tz = ZoneInfo(parc.fus_orar)
    if inceput_data is not None and sfarsit_data is not None:
        if sfarsit_data < inceput_data:
            inceput_data, sfarsit_data = sfarsit_data, inceput_data
        inceput = datetime.combine(inceput_data, time.min, tzinfo=tz).astimezone(timezone.utc)
        sfarsit = datetime.combine(sfarsit_data, time.max, tzinfo=tz).astimezone(timezone.utc)
        zile = (sfarsit_data - inceput_data).days + 1
    else:
        sfarsit = datetime.now(timezone.utc)
        inceput = sfarsit - timedelta(days=zile)
    inceput_text, sfarsit_text = iso_utc(inceput), iso_utc(sfarsit)

    productie = {p.timestamp_utc: p.energie_kwh
                 for p in repo.listeaza_productie(parc.id, inceput_text, sfarsit_text)}

    prognoza: dict[str, dict] = {}
    for rulare in repo.itereaza_rulari(
            parc.id, limita=200,
            declansata_dupa=iso_utc(inceput - timedelta(days=_marja_orizont_zile(repo))),
            declansata_inainte=iso_utc(sfarsit)):
        for o in rulare.ore:
            if o.timestamp_utc in prognoza or o.putere_finala_kw is None:
                continue
            ts = datetime.fromisoformat(o.timestamp_utc)
            if inceput <= ts <= sfarsit:
                prognoza[o.timestamp_utc] = {
                    "fizic_kw": o.putere_ac_fizic_kw,
                    "corectie_ml_kw": o.corectie_ml_kw,
                    "final_kw": o.putere_finala_kw,
                }

    toate_orele = sorted(set(productie) | set(prognoza))
    randuri = []
    suma_reala = suma_fizic = suma_finala = suma_dezechilibru = 0.0
    numar_dezechilibru = 0
    for ts in toate_orele:
        reala = productie.get(ts)
        prog = prognoza.get(ts, {})
        final = prog.get("final_kw")
        dezechilibru = abs(reala - final) if reala is not None and final is not None else None
        randuri.append({
            # `ts_utc` e cheia de ORDONARE; `timestamp` e doar pentru citit.
            # Fără ea, sortarea se făcea pe textul „zz.ll.aaaa", iar „01.09" ajungea
            # înaintea lui „29.08" (lexicografic, „0" < „2") — graficul începea cu o
            # zi din luna următoare, apoi sărea înapoi.
            "ts_utc": ts,
            "timestamp": datetime.fromisoformat(ts).astimezone(tz).strftime("%d.%m.%Y %H:%M"),
            "reala_kwh": reala,
            "fizic_kw": prog.get("fizic_kw"),
            "corectie_ml_kw": prog.get("corectie_ml_kw"),
            "final_kw": final,
            "dezechilibru_kw": dezechilibru,
        })
        if reala is not None:
            suma_reala += reala
        if prog.get("fizic_kw") is not None:
            suma_fizic += prog["fizic_kw"]
        if final is not None:
            suma_finala += final
        if dezechilibru is not None:
            suma_dezechilibru += dezechilibru
            numar_dezechilibru += 1

    randuri.sort(key=lambda r: r["ts_utc"], reverse=True)
    return {
        "randuri": randuri,
        "zile": zile,
        "inceput_data": inceput.astimezone(tz).date().isoformat(),
        "sfarsit_data": sfarsit.astimezone(tz).date().isoformat(),
        "numar_ore_comune": numar_dezechilibru,
        "energie_reala_kwh": suma_reala or None,
        "energie_fizic_kwh": suma_fizic or None,
        "energie_finala_kwh": suma_finala or None,
        "dezechilibru_mediu_kw": (suma_dezechilibru / numar_dezechilibru) if numar_dezechilibru else None,
    }


def prognoza_luna_urmatoare(repo, parc: Parc, zile: int = 31,
                             an: int | None = None, luna: int | None = None) -> dict:
    """Zonă de prognoză pentru următoarea lună — arată, zi cu zi, ÎN PARALEL
    (nu combinate într-o singură valoare):
      - prognoza NWP REALĂ, cât timp o acoperă cea mai recentă rulare
        reușită (practic ~16 zile — R-906, `zile_orizont_maxim`) — `None`
        dincolo de acoperirea ei;
      - o estimare CLIMATOLOGICĂ (`app.core.forecast_climatologie`), pentru
        FIECARE zi din interval, nu doar pentru zilele necoperite de NWP —
        media producției din aceleași zile calendaristice ale anilor
        anteriori, NU o prognoză meteorologică.

    Înainte cele două surse erau combinate într-un singur rând per zi (NWP
    dacă exista, altfel climatologie) — utilizatorul nu putea compara direct
    cele două estimări pe aceeași zi. Fiecare rând arată acum ambele valori,
    separat (docs/LIMITARI.md, §4 — cele două NU se amestecă vizual)."""
    from app.core.forecast_climatologie import prognoza_climatologica

    tz = ZoneInfo(parc.fus_orar)
    azi = datetime.now(timezone.utc).astimezone(tz).date()
    if an and luna:
        # Lună CALENDARISTICĂ întreagă (octombrie, noiembrie…) — forma cerută de
        # planificarea lunară și de raportarea către client. Include și zilele
        # deja trecute din luna curentă, pentru care se arată producția REALĂ,
        # nu o estimare: altfel un total pe septembrie privit pe 12 septembrie
        # ar ignora tocmai jumătatea de lună despre care se știe totul.
        ultima_zi_luna = calendar.monthrange(an, luna)[1]
        prima_zi = date_cls(an, luna, 1)
        ultima_zi = date_cls(an, luna, ultima_zi_luna)
    else:
        prima_zi = azi + timedelta(days=1)   # ziua curentă rămâne exclusă (#33/R-901)
        ultima_zi = azi + timedelta(days=zile)
    toate_zilele = []
    z = prima_zi
    while z <= ultima_zi:
        toate_zilele.append(z)
        z += timedelta(days=1)

    # NWP se adună din TOATE rulările recente, nu doar din ultima: rularea
    # zilnică salvează doar orizontul implicit (3 zile), deci privind o singură
    # rulare se pierdeau zilele 4-16 calculate de o rulare manuală mai lungă,
    # deși modelele meteo le acoperă. Se parcurg de la cea mai nouă la cea mai
    # veche, iar prima valoare găsită pentru o zi o câștigă — aceeași convenție
    # „cea mai recentă prognoză disponibilă" ca în restul aplicației.
    ultima_rulare = repo.ultima_rulare_reusita(parc.id)
    pe_zi_nwp: dict = {}
    for rulare in repo.itereaza_rulari(
            parc.id, limita=30,
            declansata_dupa=iso_utc(datetime.now(timezone.utc) - timedelta(days=3))):
        for zi, info in _grupeaza_pe_zi_locala(rulare.ore, parc.fus_orar).items():
            if zi >= prima_zi and zi not in pe_zi_nwp:
                # la rezoluție sub-orară, suma puterilor NU e energie fără scalare
                pas_ore = (rulare.rezolutie_minute or 60) / 60.0
                pe_zi_nwp[zi] = {**info, "energie_kwh": info["energie_kwh"] * pas_ore}

    clima = prognoza_climatologica(repo, parc, toate_zilele[0], toate_zilele[-1])
    clima_pe_zi: dict[str, dict] = {r["data"]: r for r in clima["randuri"]}

    # Producția REALĂ pentru zilele deja trecute din interval (relevantă doar în
    # modul „lună calendaristică", unde luna curentă începe în trecut). O zi
    # încheiată nu se mai estimează: se știe.
    real_pe_zi: dict[date_cls, float] = {}
    if toate_zilele[0] <= azi:
        inceput_real = datetime.combine(toate_zilele[0], time.min, tzinfo=tz)
        sfarsit_real = datetime.combine(min(azi, toate_zilele[-1]), time.max, tzinfo=tz)
        for p in repo.listeaza_productie(parc.id, iso_utc(inceput_real), iso_utc(sfarsit_real)):
            zi = datetime.fromisoformat(p.timestamp_utc).astimezone(tz).date()
            real_pe_zi[zi] = real_pe_zi.get(zi, 0.0) + p.energie_kwh

    randuri = []
    for z in toate_zilele:
        info_nwp = pe_zi_nwp.get(z)
        r = clima_pe_zi.get(z.isoformat())
        reala = real_pe_zi.get(z)
        # „Cea mai bună cifră" a zilei, în ordinea încrederii: ce s-a măsurat >
        # ce prognozează modelele meteo > tiparul climatologic. Doar ea intră în
        # totalul lunii; coloanele rămân separate, ca sursele să nu se amestece.
        if reala is not None:
            estimata, sursa = reala, "realizat"
        elif info_nwp:
            estimata, sursa = info_nwp["energie_kwh"], "nwp"
        else:
            estimata, sursa = (r["energie_estimata_kwh"] if r else None), "climatologie"
        randuri.append({
            "data": z.isoformat(),
            "energie_reala_kwh": reala,
            "energie_nwp_kwh": info_nwp["energie_kwh"] if info_nwp else None,
            "energie_climatologie_kwh": r["energie_estimata_kwh"] if r else None,
            "energie_climatologie_min_kwh": r["energie_min_kwh"] if r else None,
            "energie_climatologie_max_kwh": r["energie_max_kwh"] if r else None,
            "numar_ani_folositi": r["numar_ani_folositi"] if r else 0,
            "energie_estimata_kwh": estimata,
            "sursa_estimata": sursa,
        })

    zile_reale = sum(1 for r in randuri if r["energie_reala_kwh"] is not None)
    zile_nwp = sum(1 for r in randuri
                    if r["energie_reala_kwh"] is None and r["energie_nwp_kwh"] is not None)
    return {
        "randuri": randuri,
        "prima_zi": toate_zilele[0].isoformat(),
        "ultima_zi": toate_zilele[-1].isoformat(),
        "an": an, "luna": luna,
        "total_real_kwh": sum(r["energie_reala_kwh"] for r in randuri
                               if r["energie_reala_kwh"] is not None),
        "total_nwp_kwh": sum(r["energie_nwp_kwh"] for r in randuri if r["energie_nwp_kwh"] is not None),
        "total_climatologie_kwh": sum(r["energie_climatologie_kwh"] for r in randuri
                                       if r["energie_climatologie_kwh"] is not None),
        # totalul pe care îl caută operatorul: măsurat unde există, prognozat în rest
        "total_estimat_kwh": sum(r["energie_estimata_kwh"] for r in randuri
                                  if r["energie_estimata_kwh"] is not None),
        "zile_reale": zile_reale,
        "zile_nwp": zile_nwp,
        "zile_climatologie": len(randuri) - zile_reale - zile_nwp,
        "are_rulare": ultima_rulare is not None,
    }


def raport_antrenare(parc: Parc, info) -> dict:
    """Ce s-a întâmplat la ultima antrenare ML, în formă citibilă.

    Toate cifrele existau deja în `ModelMLInfo`, dar ajungeau la operator
    într-o singură frază densă, afișată o clipă ca mesaj de confirmare. Aici
    devin: un verdict, cifrele care l-au decis, și ce urmează de făcut.
    """
    if info is None:
        return {"are_model": False}

    baza_kw = putere_normalizare_kw(parc)

    def pct_din_capacitate(kw):
        return (kw / baza_kw * 100) if kw is not None and baza_kw else None

    imbunatatire_pct = None
    if info.mae_model_anterior_kw:
        imbunatatire_pct = (info.mae_model_anterior_kw - info.mae_validare_kw) \
            / info.mae_model_anterior_kw * 100

    # Verdictul: de ce a fost sau nu activat modelul (R-605 — promovarea se
    # decide pe date NEVĂZUTE la antrenare, nu pe cele pe care a învățat).
    if info.activ:
        verdict = "activat"
        explicatie = ("Modelul nou a bătut modelul anterior pe ore pe care nu le văzuse la "
                      "antrenare, deci a fost promovat automat și corectează de acum prognozele.")
    else:
        verdict = "respins"
        explicatie = ("Modelul nou NU a fost mai bun decât cel existent pe ore nevăzute la "
                      "antrenare, deci versiunea veche continuă să lucreze. Modelul rămâne "
                      "salvat, dar inactiv — nimic nu s-a stricat, doar nu s-a îmbunătățit.")

    indicatori = [
        {"eticheta": "Eroare medie (MAE) pe orele de validare",
         "valoare": info.mae_validare_kw, "unitate": "mw",
         "context": (f"{pct_din_capacitate(info.mae_validare_kw):.1f}% din puterea de referință"
                     if pct_din_capacitate(info.mae_validare_kw) is not None else None)},
        {"eticheta": "MAE al modelului anterior",
         "valoare": info.mae_model_anterior_kw, "unitate": "mw",
         "context": (f"{imbunatatire_pct:+.1f}% față de modelul nou" if imbunatatire_pct is not None
                     else "nu exista un model anterior — acesta e primul")},
        {"eticheta": "Skill față de prognoza de referință",
         "valoare": info.skill_vs_referinta, "unitate": "skill",
         "context": ("peste 0 = mai bun decât o prognoză naivă; sub 0 = mai slab decât "
                     "„a nu avea model”")},
        {"eticheta": "Skill-ul modelului anterior",
         "valoare": info.skill_model_anterior, "unitate": "skill",
         "context": "criteriul efectiv de promovare (R-605)"},
    ]

    date_folosite = [
        {"eticheta": "Ore folosite la antrenare", "valoare": info.numar_ore_antrenare},
        {"eticheta": "Ore excluse de controlul de calitate", "valoare": info.numar_ore_excluse_qc},
    ]

    observatii = []
    if info.skill_vs_referinta is not None and info.skill_vs_referinta <= 0:
        observatii.append(
            "Skill negativ sau nul: pe datele de validare, modelul nu bate o prognoză naivă. "
            "De obicei înseamnă istoric prea scurt, date măsurate cu probleme, sau o geometrie "
            "a parcului configurată greșit — verificați întâi calitatea datelor și calibrarea.")
    if info.numar_ore_excluse_qc and info.numar_ore_antrenare:
        procent = info.numar_ore_excluse_qc / (info.numar_ore_antrenare + info.numar_ore_excluse_qc) * 100
        if procent > 15:
            observatii.append(
                f"{procent:.0f}% din orele disponibile au fost excluse de controlul de calitate. "
                f"E mult: modelul învață dintr-un istoric mult mai sărac decât pare.")
    if info.corectie_conforma_kw:
        observatii.append(
            "S-au antrenat și benzile de incertitudine (P10/P90), calibrate conform pe date "
            "nevăzute — prognozele vor include de acum un interval, nu doar o valoare centrală.")
    if not observatii:
        observatii.append("Nimic neobișnuit: antrenarea a decurs normal.")

    return {
        "are_model": True, "info": info, "verdict": verdict, "explicatie": explicatie,
        "indicatori": indicatori, "date_folosite": date_folosite, "observatii": observatii,
        "imbunatatire_pct": imbunatatire_pct,
        "antrenat_la": info.antrenat_la[:16].replace("T", " "),
    }


def zile_de_la_creare(parc: Parc) -> int:
    creat = datetime.fromisoformat(parc.creat_la)
    return (datetime.now(timezone.utc) - creat).days


def rezumat_portofoliu_total(repo, randuri: list[dict]) -> dict:
    """Agregarea la nivel de portofoliu (R-301 extins): suma energiei
    prognozate pentru mâine și numărul de parcuri pe fiecare stare.

    Notă metodologică: energia se poate însuma direct, dar INCERTITUDINEA nu —
    P10-urile individuale nu se adună (erorile parcurilor sunt parțial
    independente, deci banda agregată e mai îngustă decât suma benzilor).
    De-aceea aici agregăm doar valorile centrale.
    """
    total_energie = 0.0
    total_putere_instalata = 0.0
    pe_stare: dict[str, int] = {}
    for rand in randuri:
        if rand.get("energie_maine_kwh"):
            total_energie += rand["energie_maine_kwh"]
        total_putere_instalata += putere_instalata_kw(rand["parc"])
        stare = rand["stare"]
        pe_stare[stare] = pe_stare.get(stare, 0) + 1
    return {
        "numar_parcuri": len(randuri),
        "energie_maine_kwh": total_energie or None,
        "putere_instalata_kw": total_putere_instalata,
        "pe_stare": pe_stare,
    }
