"""
Orchestrarea unei rulări de prognoză pentru UN parc: meteo -> fizic -> ML ->
controale de calitate -> salvare (R-1201: fiecare pas rămâne trasabil).

NU trimite email — recalcularea și trimiterea sunt acțiuni separate,
niciodată combinate automat (R-905). Vezi app/core/daily_run.py pentru
orchestrarea pe tot portofoliul + trimitere.

Adăugat în v2:
  * benzi de incertitudine P10/P50/P90 (dacă parcul are modele de cuantile);
  * puterea de cer senin și orizontul (ore în avans) salvate pe fiecare oră —
    ambele indispensabile pentru o verificare corectă ulterioară;
  * ponderi de ansamblu adaptive, deduse din performanța recentă per model;
  * rezoluție configurabilă (60 sau 15 minute), cu interpolarea radiației
    prin indicele de senin, nu pe valorile brute.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from app import setari as setari_mod
from app.core.meteo_mapping import pregateste_meteo_eolian, pregateste_meteo_pv
from app.core.ml_corrector import gaseste_si_aplica_corectie, pregateste_cadru
from app.core.pv_model import calculeaza_productie_pv
from app.core.quality_gates import evalueaza_calitate
from app.core.rezolutie import reesantioneaza_meteo
from app.core.weather_client import (EroareMeteo, acoperire_per_model, combina_ansamblu,
                                      obtine_prognoza_meteo)
from app.core.wind_model import calculeaza_productie_eoliana
from app.models import OraPrognoza, Parc, Rulare, Tehnologie, new_id


def putere_instalata_kw(parc: Parc) -> float:
    if parc.tehnologie == Tehnologie.FOTOVOLTAIC:
        return parc.config_pv.putere_ac_kw
    return parc.config_eolian.putere_nominala_kw * parc.config_eolian.numar_turbine


def putere_normalizare_kw(parc: Parc) -> float:
    """Baza de normalizare a indicatorilor. Implicit puterea instalată
    (convenția operatorilor de sistem și recomandarea IEA); dacă parcul are o
    putere de racordare/contractată diferită, aceea are prioritate."""
    return parc.putere_referinta_kw or putere_instalata_kw(parc)


def _filtreaza_zile_viitoare(df: pd.DataFrame, fus_orar: str, zile_viitor: int) -> pd.DataFrame:
    """R-901/906/903: păstrează doar zilele viitoare (ziua curentă exclusă
    din raport), calculate în fusul orar LOCAL al parcului — nu UTC — ca
    schimbarea orei de vară/iarnă să nu deplaseze granița zilei."""
    tz = ZoneInfo(fus_orar)
    azi_local = datetime.now(timezone.utc).astimezone(tz).date()
    zile_pastrate = {azi_local.toordinal() + i for i in range(1, zile_viitor + 1)}
    zile_locale = df.index.tz_convert(tz).date
    mask = [d.toordinal() in zile_pastrate for d in zile_locale]
    return df[mask]


def ruleaza_prognoza(repo, parc: Parc, tip: str = "manuala", zile_viitor: int | None = None) -> Rulare:
    inceput = datetime.now(timezone.utc)
    setari = setari_mod.toate(repo)
    if zile_viitor is None:
        zile_viitor = setari["zile_orizont_implicit"]["valoare"]
    zile_viitor = min(int(zile_viitor), int(setari["zile_orizont_maxim"]["valoare"]))

    rulare = Rulare(id=new_id(), parc_id=parc.id, declansata_la=inceput.isoformat(),
                     tip=tip, orizont_zile=zile_viitor, rezolutie_minute=parc.rezolutie_minute)

    try:
        # +2 zile marjă: filtrarea pe fus orar local poate "consuma" o zi la capete
        pe_model = obtine_prognoza_meteo(parc.latitudine, parc.longitudine, parc.surse_meteo,
                                          zile_trecut=0, zile_viitor=zile_viitor + 2)
    except EroareMeteo as exc:
        rulare.stare, rulare.mesaj = "esuata", f"Eroare la obținerea datelor meteo: {exc}"
        rulare.durata_secunde = (datetime.now(timezone.utc) - inceput).total_seconds()
        repo.salveaza_rulare(rulare)
        return rulare

    rulare.surse_meteo_raspuns = {m: (m in pe_model) for m in parc.surse_meteo}
    acoperire = acoperire_per_model(pe_model)

    ponderi = dict(parc.ponderi_meteo or {})
    if parc.ponderi_automate:
        from app.core.ponderi_adaptive import ponderi_din_performanta
        ponderi_auto = ponderi_din_performanta(repo, parc, list(pe_model.keys()))
        if ponderi_auto:
            ponderi = ponderi_auto
    rulare.ponderi_folosite = {m: round(float(v), 4) for m, v in ponderi.items()} if ponderi else {}

    medie, dispersie = combina_ansamblu(pe_model, ponderi)
    if parc.rezolutie_minute and parc.rezolutie_minute != 60:
        medie, dispersie = reesantioneaza_meteo(medie, dispersie, parc, parc.rezolutie_minute)

    if parc.tehnologie == Tehnologie.FOTOVOLTAIC:
        fizic = calculeaza_productie_pv(parc.config_pv, parc.latitudine, parc.longitudine,
                                         parc.altitudine_m, pregateste_meteo_pv(medie))
    else:
        fizic = calculeaza_productie_eoliana(parc.config_eolian, parc.altitudine_m,
                                              pregateste_meteo_eolian(medie))

    fizic = _filtreaza_zile_viitoare(fizic, parc.fus_orar, zile_viitor)
    if fizic.empty:
        rulare.stare = "esuata"
        rulare.mesaj = "Sursele meteo nu au întors ore pentru orizontul cerut."
        repo.salveaza_rulare(rulare)
        return rulare

    medie_f = medie.reindex(fizic.index)
    dispersie_f = dispersie.reindex(fizic.index)

    from app.core.clearsky import putere_referinta
    referinta_cs = putere_referinta(
        parc, fizic.index,
        medie_f["temperature_2m"] if "temperature_2m" in medie_f.columns else None,
        medie_f["wind_speed_10m"] if "wind_speed_10m" in medie_f.columns else None,
    )
    cadru = pregateste_cadru(fizic, medie_f, dispersie_f, referinta_cs)

    instalata = putere_instalata_kw(parc)
    corectie, sursa_model, mesaj_ml, cuantile = gaseste_si_aplica_corectie(repo, parc, cadru)
    if corectie is not None:
        putere_finala = (cadru["putere_ac_fizic_kw"] + corectie).clip(lower=0)
    else:
        putere_finala = cadru["putere_ac_fizic_kw"]

    rulare_precedenta = repo.ultima_rulare_reusita(parc.id)
    putere_precedenta = None
    if rulare_precedenta and rulare_precedenta.ore:
        valori = {pd.Timestamp(o.timestamp_utc): o.putere_finala_kw
                  for o in rulare_precedenta.ore if o.putere_finala_kw is not None}
        if valori:
            putere_precedenta = pd.Series(valori)
            putere_precedenta.index = pd.to_datetime(putere_precedenta.index, utc=True)

    praguri = {cheie: v["valoare"] for cheie, v in setari.items()}
    if parc.tehnologie == Tehnologie.FOTOVOLTAIC:
        serie_pt_constanta = medie_f.get("shortwave_radiation")
    else:
        serie_pt_constanta = medie_f.get("wind_speed_10m")
    calitate = evalueaza_calitate(
        putere_finala_kw=putere_finala, putere_instalata_kw=instalata, tehnologie=parc.tehnologie,
        solar_zenith_grade=cadru.get("solar_zenith"), serie_meteo_pt_constanta=serie_pt_constanta,
        putere_rulare_precedenta_kw=putere_precedenta, praguri=praguri,
        pas_minute=parc.rezolutie_minute or 60,
    )

    ore: list[OraPrognoza] = []
    for ts in cadru.index:
        blocat = calitate.blocat.get(ts, False)
        valori_cuantile = {}
        if not blocat and cuantile:
            for nivel, serie in cuantile.items():
                valoare = serie.get(ts)
                if valoare is not None and pd.notna(valoare):
                    valori_cuantile[nivel] = round(float(valoare), 3)
        ore_in_avans = (ts - pd.Timestamp(inceput)).total_seconds() / 3600.0
        ore.append(OraPrognoza(
            timestamp_utc=ts.isoformat(),
            ghi_wm2=_val(medie_f, "shortwave_radiation", ts),
            dni_wm2=_val(medie_f, "direct_normal_irradiance", ts),
            dhi_wm2=_val(medie_f, "diffuse_radiation", ts),
            temp_aer_c=_val(medie_f, "temperature_2m", ts),
            viteza_vant_ms=_val(medie_f, "wind_speed_10m", ts),
            dispersie_meteo=_val(dispersie_f, "shortwave_radiation", ts)
                if parc.tehnologie == Tehnologie.FOTOVOLTAIC else _val(dispersie_f, "wind_speed_10m", ts),
            surse_meteo_folosite=list(pe_model.keys()),
            putere_dc_kw=_val(cadru, "putere_dc_kw", ts),
            putere_ac_fizic_kw=float(cadru["putere_ac_fizic_kw"][ts]),
            fractie_incarcare=float(cadru["fractie_incarcare"][ts]),
            corectie_ml_kw=float(corectie[ts]) if corectie is not None else None,
            model_folosit=sursa_model,
            putere_finala_kw=None if blocat else float(putere_finala[ts]),
            avertismente=list(calitate.avertismente.get(ts, [])),
            blocat=blocat,
            cuantile_kw=valori_cuantile,
            putere_clearsky_kw=_val(cadru, "putere_clearsky_kw", ts),
            ore_in_avans=round(ore_in_avans, 2),
        ))

    rulare.ore = ore
    rulare.durata_secunde = (datetime.now(timezone.utc) - inceput).total_seconds()
    numar_blocate = sum(1 for o in ore if o.blocat)
    if numar_blocate == 0:
        rulare.stare = "reusita"
    elif numar_blocate < len(ore):
        rulare.stare = "partiala"
        rulare.mesaj = f"{numar_blocate} din {len(ore)} ore blocate de controalele de calitate."
    else:
        rulare.stare = "esuata"
        rulare.mesaj = "Toate orele au fost blocate de controalele de calitate."

    surse_partiale = [f"{m} ({p:.0%})" for m, p in acoperire.items() if p < 0.95]
    if surse_partiale:
        rulare.mesaj = ((rulare.mesaj + " ") if rulare.mesaj else "") + \
            f"Surse meteo cu acoperire parțială pe orizont: {', '.join(surse_partiale)}."
    if mesaj_ml:
        rulare.mesaj = (rulare.mesaj + " " if rulare.mesaj else "") + mesaj_ml

    repo.salveaza_rulare(rulare)
    return rulare


def _val(df: pd.DataFrame, col: str, ts) -> float | None:
    if col not in df.columns:
        return None
    v = df[col].get(ts)
    if v is None or pd.isna(v):
        return None
    return float(v)
