"""Cazuri de graniță pentru fus orar (tranziții oră vară/iarnă, ambele
direcții) și an bisect — R-1502 le cere explicit ca teste automate, pentru
că R-511/R-901/R-903/R-1104 depind toate de conversii corecte fus local <-> UTC."""
from __future__ import annotations

import datetime as dt_module
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import app.core.forecast_engine as forecast_engine
from app.core.forecast_engine import _filtreaza_zile_viitoare
from app.core.csv_import import analizeaza_fisier
from app.core.pv_model import calculeaza_productie_pv
from app.core.solar_position import calculeaza_pozitie_solara
from tests.helpers import config_pv_simplu, meteo_pv_cer_senin, parc_pv

TZ_RO = "Europe/Bucharest"


def _fixeaza_acum(monkeypatch, acum_utc: dt_module.datetime):
    class DatetimeFix:
        @staticmethod
        def now(tz=None):
            return acum_utc
    monkeypatch.setattr(forecast_engine, "datetime", DatetimeFix)


def test_filtrare_zile_tranzitie_primavara_23_ore(monkeypatch):
    """2026-03-29 — ora de vară în UE: ceasurile sar de la 03:00 la 04:00,
    ziua locală are doar 23 de ore. Filtrul pe fus local (nu UTC) trebuie
    să prindă exact aceste 23 de ore, toate cu data locală corectă."""
    # "acum" = 28 martie, ora 10:00 UTC (12:00 EET local, înainte de tranziție)
    _fixeaza_acum(monkeypatch, dt_module.datetime(2026, 3, 28, 10, 0, tzinfo=dt_module.timezone.utc))

    index = pd.date_range("2026-03-27 00:00", "2026-03-31 00:00", freq="h", tz="UTC")
    df = pd.DataFrame({"x": range(len(index))}, index=index)

    rezultat = _filtreaza_zile_viitoare(df, TZ_RO, zile_viitor=1)

    assert len(rezultat) == 23, f"ziua de tranziție de primăvară trebuie să aibă 23 de ore, nu {len(rezultat)}"
    tz = ZoneInfo(TZ_RO)
    zile_locale = {ts.tz_convert(tz).date() for ts in rezultat.index}
    assert zile_locale == {dt_module.date(2026, 3, 29)}, "toate orele păstrate trebuie să fie din 29 martie local"


def test_filtrare_zile_tranzitie_toamna_25_ore(monkeypatch):
    """2026-10-25 — ora de iarnă: ceasurile dau înapoi de la 04:00 la 03:00,
    ziua locală are 25 de ore (ora 03:00 apare de două ori)."""
    _fixeaza_acum(monkeypatch, dt_module.datetime(2026, 10, 24, 10, 0, tzinfo=dt_module.timezone.utc))

    index = pd.date_range("2026-10-23 00:00", "2026-10-27 00:00", freq="h", tz="UTC")
    df = pd.DataFrame({"x": range(len(index))}, index=index)

    rezultat = _filtreaza_zile_viitoare(df, TZ_RO, zile_viitor=1)

    assert len(rezultat) == 25, f"ziua de tranziție de toamnă trebuie să aibă 25 de ore, nu {len(rezultat)}"
    tz = ZoneInfo(TZ_RO)
    zile_locale = {ts.tz_convert(tz).date() for ts in rezultat.index}
    assert zile_locale == {dt_module.date(2026, 10, 25)}


def test_filtrare_zile_ziua_curenta_exclusa(monkeypatch):
    """R-901/903: ziua curentă (locală) trebuie exclusă din orizontul de
    prognoză, indiferent de ora UTC la care rulează."""
    _fixeaza_acum(monkeypatch, dt_module.datetime(2026, 6, 15, 6, 0, tzinfo=dt_module.timezone.utc))
    index = pd.date_range("2026-06-14 00:00", "2026-06-18 00:00", freq="h", tz="UTC")
    df = pd.DataFrame({"x": range(len(index))}, index=index)
    rezultat = _filtreaza_zile_viitoare(df, TZ_RO, zile_viitor=2)
    tz = ZoneInfo(TZ_RO)
    zile_locale = sorted({ts.tz_convert(tz).date() for ts in rezultat.index})
    assert dt_module.date(2026, 6, 15) not in zile_locale, "ziua curentă nu trebuie livrată în prognoză"
    assert zile_locale == [dt_module.date(2026, 6, 16), dt_module.date(2026, 6, 17)]


def test_filtrare_include_an_bisect_29_februarie(monkeypatch):
    """2028 e an bisect — 29 februarie trebuie tratată ca orice altă zi,
    fără erori sau excluderi în aritmetica pe zile ordinale."""
    _fixeaza_acum(monkeypatch, dt_module.datetime(2028, 2, 28, 6, 0, tzinfo=dt_module.timezone.utc))
    index = pd.date_range("2028-02-27 00:00", "2028-03-02 00:00", freq="h", tz="UTC")
    df = pd.DataFrame({"x": range(len(index))}, index=index)
    rezultat = _filtreaza_zile_viitoare(df, TZ_RO, zile_viitor=2)
    tz = ZoneInfo(TZ_RO)
    zile_locale = sorted({ts.tz_convert(tz).date() for ts in rezultat.index})
    assert zile_locale == [dt_module.date(2028, 2, 29), dt_module.date(2028, 3, 1)]
    assert len(rezultat) == 48  # ambele zile din 2028 au 24h, fără tranziție DST în jurul lor


def test_pozitie_solara_continua_peste_29_februarie():
    """calculeaza_pozitie_solara nu trebuie să producă NaN/erori la trecerea
    peste 29 februarie într-un an bisect."""
    index = pd.date_range("2028-02-28 12:00", "2028-03-01 12:00", freq="h", tz="UTC")
    poz = calculeaza_pozitie_solara(index, 44.43, 26.10, 80.0)
    assert poz["apparent_zenith"].notna().all()
    assert poz["azimuth"].notna().all()


def test_productie_pv_pe_29_februarie_e_plauzibila():
    """Producția calculată chiar în ziua de 29 februarie a unui an bisect
    trebuie să fie o valoare fizică normală (nu NaN, nu eroare)."""
    meteo = meteo_pv_cer_senin("2028-02-29 00:00", 24, 44.43, 26.10, 80.0)
    rez = calculeaza_productie_pv(config_pv_simplu(), 44.43, 26.10, 80.0, meteo)
    assert rez["putere_ac_fizic_kw"].notna().all()
    assert rez["putere_ac_fizic_kw"].sum() > 0, "ar trebui să existe producție solară în timpul zilei"


def test_ora_ambigua_de_toamna_e_respinsa_in_formatul_vechi():
    """Ora locală 03:00 din noaptea trecerii la ora de iarnă apare de DOUĂ ori.
    Fără decalaj în fișier, platforma nu are cum să știe care dintre cele două
    e — înainte alegea tăcut prima apariție (fold=0), adică ghicea. Acum o
    respinge explicit, cu instrucțiunea de corectare: e singurul răspuns
    onest, iar ora „ghicită" greșit strica atât antrenarea ML, cât și
    verificarea acurateței."""
    parc = parc_pv(fus_orar=TZ_RO)
    continut = (
        "timestamp;kWh\n"
        "2026-10-25 02:00;100\n"
        "2026-10-25 03:00;90\n"   # ora ambiguă — apare de două ori local
        "2026-10-25 04:00;80\n"
    ).encode("utf-8")
    raport = analizeaza_fisier(continut, parc, toleranta_fus_orar_minute=999999,
                                permite_ora_locala=True)
    assert not raport.valid
    assert any("ambiguă" in e for e in raport.erori), raport.erori
    assert any("decalaj" in e for e in raport.erori), \
        "eroarea trebuie să spună și CUM se corectează, nu doar că e greșit"


def test_aceeasi_noapte_se_importa_fara_probleme_cu_decalaj_explicit():
    """Perechea testului de mai sus — și motivul pentru care decalajul e acum
    formatul de bază: aceeași noapte, scrisă cu decalaj, se importă curat, iar
    cele două ore locale „03:00" devin două momente distincte, în ordine."""
    parc = parc_pv(fus_orar=TZ_RO)
    continut = (
        "timestamp;kWh\n"
        "2026-10-25T02:00:00+03:00;100\n"
        "2026-10-25T03:00:00+03:00;90\n"   # prima apariție (încă ora de vară)
        "2026-10-25T03:00:00+02:00;85\n"   # a doua apariție (deja ora de iarnă)
        "2026-10-25T04:00:00+02:00;80\n"
    ).encode("utf-8")
    raport = analizeaza_fisier(continut, parc, toleranta_fus_orar_minute=999999)
    assert raport.valid, raport.erori
    assert len(raport.randuri_valide) == 4
    timpi = [pd.Timestamp(r.timestamp_utc) for r in raport.randuri_valide]
    assert timpi == sorted(timpi)
    assert len(set(timpi)) == 4, "cele patru ore trebuie să fie momente DISTINCTE"
    # exact o oră între ele, fără „interval neregulat" și fără „duplicat"
    assert not any("neregulate" in a for a in raport.avertismente), raport.avertismente
    assert not any("de mai multe ori" in a for a in raport.avertismente), raport.avertismente


def test_ora_inexistenta_de_primavara_e_respinsa_in_formatul_vechi():
    """Ora locală 03:00 din noaptea trecerii la ora de vară nu există: ceasul
    sare de la 03:00 la 04:00. O valoare de producție etichetată așa e o
    eroare de export, nu un moment real."""
    parc = parc_pv(fus_orar=TZ_RO)
    continut = (
        "timestamp;kWh\n"
        "2026-03-29 03:00;50\n"
    ).encode("utf-8")
    raport = analizeaza_fisier(continut, parc, toleranta_fus_orar_minute=999999,
                                permite_ora_locala=True)
    assert not raport.valid
    assert any("inexistentă" in e for e in raport.erori), raport.erori
