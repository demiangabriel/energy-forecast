"""Fus orar de IMPORT separat de fusul de prognoză, per parc (R-511/R-xxx).

Exportul din SCADA/contor poate folosi o convenție orară diferită de fusul
IANA ales pentru prognoze (ex. offset fix, fără oră de vară/iarnă). Un
`fus_orar_import` gol (implicit, la toate parcurile existente) trebuie să se
comporte EXACT ca înainte — fus_orar_pentru_import cade pe `parc.fus_orar`.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd

from app.core.csv_import import analizeaza_fisier
from app.core.timezones import fus_orar_pentru_import
from app.web.validare import valideaza_parc_comun
from tests.helpers import parc_pv


def _linii(*randuri: str) -> bytes:
    return ("timestamp;kWh\n" + "\n".join(randuri)).encode("utf-8")


# --------------------------------------------------------------------- helper


def test_fus_orar_pentru_import_gol_foloseste_fus_orar():
    parc = parc_pv(fus_orar="Europe/Bucharest", fus_orar_import="")
    assert fus_orar_pentru_import(parc) == "Europe/Bucharest"


def test_fus_orar_pentru_import_declarat_separat_are_prioritate():
    parc = parc_pv(fus_orar="Europe/Bucharest", fus_orar_import="UTC")
    assert fus_orar_pentru_import(parc) == "UTC"


# --------------------------------------------------------------------- csv_import.py


def test_import_fara_fus_import_se_comporta_ca_inainte():
    """Compatibilitate retroactivă: un parc fără `fus_orar_import` (implicit
    la toate parcurile existente) convertește exact ca înainte de acest câmp
    — folosind `fus_orar`."""
    parc = parc_pv(fus_orar="Europe/Bucharest", fus_orar_import="")
    continut = _linii("2026-06-01 12:00;500")
    raport = analizeaza_fisier(continut, parc, permite_ora_locala=True)
    assert raport.valid
    ts_utc = pd.Timestamp(raport.randuri_valide[0].timestamp_utc)
    # iunie -> Europe/Bucharest e UTC+3 (ora de vară)
    asteptat = pd.Timestamp("2026-06-01 12:00", tz=ZoneInfo("Europe/Bucharest")).tz_convert("UTC")
    assert ts_utc == asteptat


def test_import_foloseste_fus_import_declarat_separat_nu_fus_orar():
    """Când `fus_orar_import` e declarat, timestamp-urile CSV se convertesc
    cu ACEL fus — NU cu `fus_orar` (care rămâne fusul de prognoză/afișare)."""
    parc = parc_pv(fus_orar="Europe/Bucharest", fus_orar_import="UTC")
    continut = _linii("2026-06-01 12:00;500")
    raport = analizeaza_fisier(continut, parc, permite_ora_locala=True)
    assert raport.valid
    ts_utc = pd.Timestamp(raport.randuri_valide[0].timestamp_utc)
    # fus de import UTC -> 12:00 local == 12:00 UTC, NU 09:00 UTC (cum ar fi
    # fost dacă s-ar fi folosit greșit fus_orar=Europe/Bucharest, +3h vara)
    assert ts_utc == pd.Timestamp("2026-06-01 12:00", tz="UTC")


def test_import_cu_fus_diferit_de_fus_orar_produce_alt_rezultat_decat_fara_el():
    """Aceeași linie CSV, interpretată prin doi parcuri identice cu excepția
    `fus_orar_import`, trebuie să dea ore UTC DIFERITE — dovada că fusul de
    import chiar guvernează conversia, nu doar afișarea unui mesaj."""
    continut = _linii("2026-06-01 12:00;500")

    parc_fara = parc_pv(fus_orar="Europe/Bucharest", fus_orar_import="")
    parc_cu = parc_pv(fus_orar="Europe/Bucharest", fus_orar_import="UTC")

    ts_fara = pd.Timestamp(analizeaza_fisier(continut, parc_fara, permite_ora_locala=True)
                           .randuri_valide[0].timestamp_utc)
    ts_cu = pd.Timestamp(analizeaza_fisier(continut, parc_cu, permite_ora_locala=True)
                         .randuri_valide[0].timestamp_utc)
    assert ts_fara != ts_cu
    assert (ts_cu - ts_fara).total_seconds() == 3 * 3600  # decalajul de vară al României


def test_mesaj_eroare_de_conversie_mentioneaza_fusul_de_import():
    """R-1104 — verificarea de plauzibilitate a vârfului de producție trebuie
    să compare cu fusul EFECTIV de import, nu cu fusul de prognoză, altfel un
    parc corect configurat (fus de import diferit, dar corect) ar primi un
    avertisment fals despre un decalaj care nu există."""
    parc = parc_pv(latitudine=44.43, longitudine=26.10, fus_orar="Europe/Bucharest",
                    fus_orar_import="UTC")
    randuri = []
    for zi in range(1, 8):
        for h in range(24):
            # profil solar centrat la amiaza solară reală (~10:00 UTC vara,
            # la această longitudine) — dacă avertismentul ar folosi greșit
            # fus_orar (Bucharest) în loc de fus_orar_import (UTC), ar
            # detecta un decalaj fals de +3h.
            val = max(0.0, 500 * (1 - ((h - 10) / 4.0) ** 2))
            randuri.append(f"2026-06-{zi:02d} {h:02d}:00;{val:.1f}")
    continut = _linii(*randuri)
    raport = analizeaza_fisier(continut, parc, toleranta_fus_orar_minute=60,
                                permite_ora_locala=True)
    assert raport.valid
    assert not any("Fus orar posibil greșit" in a for a in raport.avertismente), \
        f"nu ar fi trebuit să semnaleze decalaj — fus_orar_import e corect: {raport.avertismente}"


# --------------------------------------------------------------------- validare.py


def test_valideaza_parc_comun_accepta_fus_orar_import_gol():
    form = {
        "cod": "T1", "nume": "Test", "latitudine": "44.0", "longitudine": "26.0",
        "fus_orar": "Europe/Bucharest",
    }
    date_, erori = valideaza_parc_comun(form)
    assert date_["fus_orar_import"] == ""
    assert not any("import" in e.lower() for e in erori)


def test_valideaza_parc_comun_accepta_fus_orar_import_valid():
    form = {
        "cod": "T1", "nume": "Test", "latitudine": "44.0", "longitudine": "26.0",
        "fus_orar": "Europe/Bucharest", "fus_orar_import": "UTC",
    }
    date_, erori = valideaza_parc_comun(form)
    assert date_["fus_orar_import"] == "UTC"
    assert not any("import" in e.lower() for e in erori)


def test_valideaza_parc_comun_respinge_fus_orar_import_invalid():
    form = {
        "cod": "T1", "nume": "Test", "latitudine": "44.0", "longitudine": "26.0",
        "fus_orar": "Europe/Bucharest", "fus_orar_import": "Not/AZone",
    }
    date_, erori = valideaza_parc_comun(form)
    assert any("import" in e.lower() for e in erori)


# --------------------------------------------------------------------- rute HTTP


def test_ruta_import_schimba_fus_orar_seteaza_fus_orar_import_nu_fus_orar(client, repo):
    """Butonul rapid de pe pagina de import trebuie să corecteze STRICT
    interpretarea importului — nu fusul care guvernează prognozele/afișarea
    (granița de zi, ora din grafic) — altfel o corecție de import ar deplasa
    și prognozele deja calculate."""
    parc = parc_pv(cod="TZ-1", fus_orar="Europe/Bucharest", fus_orar_import="")
    repo.salveaza_parc(parc)

    r = client.post(f"/parcuri/{parc.id}/import/fus-orar", data={"fus_orar": "UTC"}, follow_redirects=False)
    assert r.status_code == 303

    parc_reincarcat = repo.get_parc(parc.id)
    assert parc_reincarcat.fus_orar_import == "UTC"
    assert parc_reincarcat.fus_orar == "Europe/Bucharest"  # neschimbat


def test_ruta_import_pagina_afiseaza_fusul_de_import_efectiv(client, repo):
    parc = parc_pv(cod="TZ-2", fus_orar="Europe/Bucharest", fus_orar_import="UTC")
    repo.salveaza_parc(parc)
    r = client.get(f"/parcuri/{parc.id}/import")
    assert r.status_code == 200
    assert "UTC" in r.text


def test_ruta_sterge_productie_interval_foloseste_fusul_de_import(client, repo):
    """Curățarea unui import greșit trebuie să interpreteze datele „de la" /
    „până la" în fusul de IMPORT (nu fusul de prognoză) — același fus care a
    interpretat inițial fișierul CSV, altfel intervalul șters n-ar corespunde
    orelor greșit importate."""
    from app.models import ProductieOrara

    parc = parc_pv(cod="TZ-6", fus_orar="Europe/Bucharest", fus_orar_import="UTC")
    repo.salveaza_parc(parc)
    randuri = [ProductieOrara(parc_id=parc.id, timestamp_utc=f"2026-06-0{d}T{h:02d}:00:00+00:00",
                               energie_kwh=1.0) for d in range(1, 4) for h in range(24)]
    repo.salveaza_productie(randuri)
    assert repo.numar_ore_productie(parc.id) == 72

    r = client.post(f"/tehnic/parcuri/{parc.id}/sterge-productie",
                     data={"inceput": "2026-06-02", "sfarsit": "2026-06-02"}, follow_redirects=False)
    assert r.status_code == 303

    assert repo.numar_ore_productie(parc.id) == 48
    ramase = repo.listeaza_productie(parc.id, "2026-06-01T00:00:00+00:00", "2026-06-03T23:59:59+00:00")
    assert {r_.timestamp_utc[:10] for r_ in ramase} == {"2026-06-01", "2026-06-03"}


def test_ruta_sterge_productie_interval_parc_inexistent_nu_arunca(client):
    r = client.post("/tehnic/parcuri/nu-exista/sterge-productie",
                     data={"inceput": "2026-06-02", "sfarsit": "2026-06-02"}, follow_redirects=False)
    assert r.status_code == 303


# --------------------------------------------------------------------- format obligatoriu (rute)


def test_ruta_import_respinge_fisier_fara_decalaj_daca_nu_s_a_bifat(client, repo):
    """Formatul de bază se impune și prin interfață: un fișier fără decalaj
    nu ajunge la pasul de confirmare decât dacă operatorul bifează explicit
    interpretarea ca oră locală."""
    parc = parc_pv(cod="FMT-1", fus_orar="Europe/Bucharest")
    repo.salveaza_parc(parc)
    fisier = ("timestamp;kWh\n2026-06-01 12:00;500\n").encode("utf-8")

    r = client.post(f"/parcuri/{parc.id}/import/analizeaza",
                     files={"fisier": ("date.csv", fisier, "text/csv")})
    assert r.status_code == 200
    assert "decalaj" in r.text
    assert "Pasul 2" not in r.text, "un fișier ambiguu nu trebuie să ajungă la confirmare"


def test_ruta_import_accepta_formatul_de_baza_cu_decalaj(client, repo):
    parc = parc_pv(cod="FMT-2", fus_orar="Europe/Bucharest")
    repo.salveaza_parc(parc)
    fisier = ("timestamp;kWh\n"
              "2026-06-01T12:00:00+03:00;500\n"
              "2026-06-01T13:00:00+03:00;520\n").encode("utf-8")

    r = client.post(f"/parcuri/{parc.id}/import/analizeaza",
                     files={"fisier": ("date.csv", fisier, "text/csv")})
    assert r.status_code == 200
    assert "Pasul 2" in r.text


def test_alegerea_operatorului_supravietuieste_pana_la_pasul_2(client, repo, monkeypatch):
    """Bifa de la pasul 1 trebuie să însoțească fișierul până la scriere —
    altfel confirmarea ar re-analiza cu regulile stricte și ar respinge un
    fișier pe care operatorul tocmai l-a văzut acceptat."""
    # Confirmarea importului rulează și controlul de calitate, care cere date
    # meteo. Aici ne interesează strict transmiterea alegerii de format, deci
    # îl scurtcircuităm — altfel testul ar aștepta rețeaua.
    import app.web.routes_panou as rp
    monkeypatch.setattr(rp, "ruleaza_control_calitate",
                         lambda *a, **k: type("R", (), {"numar_probleme": 0, "procent_curat": 100.0})())

    parc = parc_pv(cod="FMT-3", fus_orar="Europe/Bucharest")
    repo.salveaza_parc(parc)
    fisier = ("timestamp;kWh\n2026-06-01 12:00;500\n2026-06-01 13:00;520\n").encode("utf-8")

    r = client.post(f"/parcuri/{parc.id}/import/analizeaza",
                     files={"fisier": ("date.csv", fisier, "text/csv")},
                     data={"permite_ora_locala": "1"})
    assert r.status_code == 200 and "Pasul 2" in r.text
    import re as _re
    token = _re.search(r'name="token" value="([0-9a-f]+)"', r.text).group(1)

    r2 = client.post(f"/parcuri/{parc.id}/import/confirma", data={"token": token},
                      follow_redirects=False)
    assert r2.status_code == 303
    assert repo.numar_ore_productie(parc.id) == 2, \
        "importul confirmat trebuia să scrie orele, cu alegerea făcută la pasul 1"
