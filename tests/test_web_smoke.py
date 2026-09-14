"""Fum-test al interfeței web (R-301-304) — flux principal: adăugare parc
pentru ambele tehnologii, cu validare, plus regresie pentru cele două rute
care foloseau parametri simpli în loc de Form() (reconstrucție, calibrare)."""
from __future__ import annotations

import re


def test_portofoliu_gol_se_incarca(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Prognoză" in r.text or "Portofoliu" in r.text or "parcuri" in r.text.lower()


def test_ghid_pornire_se_incarca(client):
    r = client.get("/ghid")
    assert r.status_code == 200


def test_formular_parc_nou_ambele_tehnologii(client):
    assert client.get("/parcuri/noua").status_code == 200
    assert client.get("/parcuri/noua?tehnologie=fotovoltaic").status_code == 200
    assert client.get("/parcuri/noua?tehnologie=eolian").status_code == 200


def test_adauga_parc_pv_validare_raport_dc_ac_absurd(client):
    """R-405: un raport DC/AC absurd (eroare tipică de unitate de măsură)
    trebuie respins cu mesaj clar, nu acceptat silențios."""
    form = {
        "tehnologie": "fotovoltaic", "client_id": "__nou__", "client_nume_nou": "Client X",
        "cod": "PV-VAL-01", "nume": "Parc validare", "latitudine": "44.0", "longitudine": "26.0",
        "putere_dc_kwp": "1000", "putere_ac_kw": "10",  # raport 100:1 — absurd
        "tip_asezare": "fixed",
    }
    r = client.post("/parcuri/noua", data=form)
    assert r.status_code == 400
    assert "neplauzibil" in r.text


def test_adauga_parc_pv_latitudine_invalida(client):
    form = {
        "tehnologie": "fotovoltaic", "client_id": "__nou__", "client_nume_nou": "Client X",
        "cod": "PV-VAL-02", "nume": "Parc validare", "latitudine": "999", "longitudine": "26.0",
        "putere_dc_kwp": "1000", "putere_ac_kw": "900", "tip_asezare": "fixed",
    }
    r = client.post("/parcuri/noua", data=form)
    assert r.status_code == 400
    assert "Latitudine" in r.text


def test_adauga_parc_eolian_viteze_necrescatoare(client):
    form = {
        "tehnologie": "eolian", "client_id": "__nou__", "client_nume_nou": "Client X",
        "cod": "EOL-VAL-01", "nume": "Parc validare eolian", "latitudine": "45.0", "longitudine": "28.0",
        "numar_turbine": "5", "putere_nominala_kw": "2000", "inaltime_butuc_m": "100",
        "viteza_pornire_ms": "15", "viteza_nominala_ms": "12", "viteza_oprire_ms": "25",  # pornire > nominală
    }
    r = client.post("/parcuri/noua", data=form)
    assert r.status_code == 400
    assert "crescătoare" in r.text


def test_adauga_parc_pv_succes_porneste_in_mod_umbra(client):
    form = {
        "tehnologie": "fotovoltaic", "client_id": "__nou__", "client_nume_nou": "Client Succes PV",
        "cod": "PV-OK-01", "nume": "Parc PV OK", "latitudine": "44.43", "longitudine": "26.10",
        "altitudine_m": "80", "putere_dc_kwp": "1000", "putere_ac_kw": "850",
        "tehnologie_panou": "monocrystalline", "inclinare_grade": "30", "azimut_grade": "180",
        "tip_asezare": "fixed", "destinatari_email": "op@example.com",
    }
    r = client.post("/parcuri/noua", data=form)
    assert r.status_code == 200
    assert "?nou=1" in str(r.url)
    assert "mod umbră" in r.text.lower()


def test_adauga_parc_eolian_succes(client):
    form = {
        "tehnologie": "eolian", "client_id": "__nou__", "client_nume_nou": "Client Succes Eolian",
        "cod": "EOL-OK-01", "nume": "Parc Eolian OK", "latitudine": "45.0", "longitudine": "28.5",
        "altitudine_m": "150", "numar_turbine": "10", "putere_nominala_kw": "2000",
        "inaltime_butuc_m": "100", "viteza_pornire_ms": "3", "viteza_nominala_ms": "12",
        "viteza_oprire_ms": "25",
    }
    r = client.post("/parcuri/noua", data=form)
    assert r.status_code == 200
    assert "?nou=1" in str(r.url)


def test_cod_duplicat_e_respins(client):
    form = {
        "tehnologie": "eolian", "client_id": "__nou__", "client_nume_nou": "Client Dup",
        "cod": "EOL-DUP-01", "nume": "Primul", "latitudine": "45.0", "longitudine": "28.5",
        "numar_turbine": "5", "putere_nominala_kw": "2000", "inaltime_butuc_m": "100",
        "viteza_pornire_ms": "3", "viteza_nominala_ms": "12", "viteza_oprire_ms": "25",
    }
    r1 = client.post("/parcuri/noua", data=form)
    assert r1.status_code == 200
    form2 = dict(form, nume="Al doilea, alt nume, cod identic")
    r2 = client.post("/parcuri/noua", data=form2)
    assert r2.status_code == 400
    assert "unic" in r2.text.lower()


def _creeaza_parc_pv(client) -> str:
    form = {
        "tehnologie": "fotovoltaic", "client_id": "__nou__", "client_nume_nou": "Client Regresie",
        "cod": "PV-REG-01", "nume": "Parc regresie", "latitudine": "44.43", "longitudine": "26.10",
        "altitudine_m": "80", "putere_dc_kwp": "1000", "putere_ac_kw": "850",
        "tehnologie_panou": "monocrystalline", "inclinare_grade": "30", "azimut_grade": "180",
        "tip_asezare": "fixed",
    }
    r = client.post("/parcuri/noua", data=form)
    m = re.search(r"/parcuri/([a-f0-9]+)", str(r.url))
    assert m, f"nu s-a putut extrage id-ul parcului din {r.url}"
    return m.group(1)


def test_reconstructie_accepta_body_form_encoded(client, monkeypatch):
    """Regresie: ruta folosea `inceput: str, sfarsit: str` fără Form(), deci
    FastAPI le aștepta ca query params — formularul HTML real le trimite ca
    body form-encoded și lua 422 la fiecare submit. Verificăm că acum merge.

    Reconstrucția reală ar interoga arhiva meteo; o înlocuim, ca testul să
    verifice DOAR decodarea formularului și să rămână independent de rețea."""
    import pandas as pd
    import app.web.routes_panou as routes_panou

    def _reconstructie_falsa(parc, inceput, sfarsit):
        idx = pd.date_range(f"{inceput}", f"{sfarsit}", freq="h", tz="UTC")
        return pd.DataFrame({"putere_ac_fizic_kw": 100.0, "fractie_incarcare": 0.1}, index=idx)

    monkeypatch.setattr(routes_panou, "reconstruieste_prognoza_fizica", _reconstructie_falsa)

    parc_id = _creeaza_parc_pv(client)
    r = client.post(f"/parcuri/{parc_id}/reconstructie",
                     data={"inceput": "2026-08-01", "sfarsit": "2026-08-03"})
    assert r.status_code != 422, "reconstrucția respinge submit-ul formularului HTML real (body form-encoded)"
    assert r.status_code == 200
    assert "ore reconstruite" in r.text


def test_calibrare_aplica_accepta_body_form_encoded(client):
    """Aceeași regresie ca mai sus, pentru /calibrare/aplica — formularul din
    calibrare.html trimite inclinare_grade/azimut_grade ca hidden inputs în
    body, nu ca query string."""
    parc_id = _creeaza_parc_pv(client)
    r = client.post(f"/parcuri/{parc_id}/calibrare/aplica",
                     data={"inclinare_grade": "32.5", "azimut_grade": "190"})
    assert r.status_code != 422


def test_parc_detaliu_are_link_de_trasabilitate_dupa_o_rulare_falsa(client, repo):
    """Legătura adăugată din Panou -> Modul tehnic / trasabilitate trebuie să
    existe și să ducă la o pagină validă (R-306 — navigare bidirecțională)."""
    from app.models import OraPrognoza, Rulare
    parc_id = _creeaza_parc_pv(client)
    parc = repo.get_parc(parc_id)
    rulare = Rulare(id="rt1", parc_id=parc.id, declansata_la="2026-06-01T09:00:00+00:00",
                     tip="manuala", stare="reusita",
                     ore=[OraPrognoza(timestamp_utc="2026-06-01T10:00:00+00:00", putere_finala_kw=10.0)])
    repo.salveaza_rulare(rulare)

    r = client.get(f"/parcuri/{parc_id}")
    assert r.status_code == 200
    assert f"/tehnic/parcuri/{parc_id}/trasabilitate/rt1" in r.text

    r2 = client.get(f"/tehnic/parcuri/{parc_id}/trasabilitate/rt1")
    assert r2.status_code == 200


def test_tehnic_pagini_statice_se_incarca(client):
    for ruta in ["/tehnic", "/tehnic/setari", "/tehnic/jurnal", "/tehnic/documentatie", "/tehnic/cod-sursa"]:
        r = client.get(ruta)
        assert r.status_code == 200, f"{ruta} -> {r.status_code}"


def test_sabloane_excel_se_descarca(client):
    r1 = client.get("/sabloane/pv.xlsx")
    r2 = client.get("/sabloane/eolian.xlsx")
    assert r1.status_code == 200 and r1.content[:2] == b"PK"  # semnătura fișierelor .xlsx (zip)
    assert r2.status_code == 200 and r2.content[:2] == b"PK"


def test_parc_inexistent_redirecteaza_la_portofoliu(client):
    r = client.get("/parcuri/nu-exista-asa-ceva")
    assert r.status_code == 200
    assert str(r.url).rstrip("/").endswith("8000") or str(r.url).endswith("/")


# --------------------------------------------------------------------------- filtru portofoliu


def test_portofoliul_arata_implicit_toate_activele(client, repo):
    from tests.helpers import parc_eolian, parc_pv
    repo.salveaza_parc(parc_pv(cod="F-PV1"))
    repo.salveaza_parc(parc_eolian(cod="F-EOL1"))

    r = client.get("/")
    assert r.status_code == 200
    assert "F-PV1" in r.text and "F-EOL1" in r.text


def test_filtrul_de_tehnologie_restrange_lista(client, repo):
    from tests.helpers import parc_eolian, parc_pv
    repo.salveaza_parc(parc_pv(cod="F-PV2"))
    repo.salveaza_parc(parc_eolian(cod="F-EOL2"))

    doar_pv = client.get("/?tehnologie=fotovoltaic")
    assert "F-PV2" in doar_pv.text and "F-EOL2" not in doar_pv.text

    doar_eol = client.get("/?tehnologie=eolian")
    assert "F-EOL2" in doar_eol.text and "F-PV2" not in doar_eol.text


def test_indicatorii_de_sus_urmeaza_filtrul(client, repo):
    """Un total pe TOT portofoliul deasupra unui tabel filtrat ar fi derutant:
    cifrele trebuie să descrie exact ce se vede dedesubt."""
    from tests.helpers import parc_eolian, parc_pv
    repo.salveaza_parc(parc_pv(cod="F-PV3"))
    repo.salveaza_parc(parc_pv(cod="F-PV4"))
    repo.salveaza_parc(parc_eolian(cod="F-EOL3"))

    r = client.get("/?tehnologie=fotovoltaic")
    assert "2 din 3" in r.text, "trebuie spus clar câte active sunt afișate din total"


def test_filtru_necunoscut_arata_tot(client, repo):
    from tests.helpers import parc_eolian, parc_pv
    repo.salveaza_parc(parc_pv(cod="F-PV5"))
    repo.salveaza_parc(parc_eolian(cod="F-EOL5"))

    r = client.get("/?tehnologie=aiurea")
    assert "F-PV5" in r.text and "F-EOL5" in r.text
