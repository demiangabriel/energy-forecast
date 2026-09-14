"""Restaurare din copie de rezervă (modul tehnic) — atât cea de configurare a
parcurilor (xlsx/csv, „adaugă automat toate parcurile”), cât și copia
completă (JSON: parcuri, clienți, producție, calitate, rulări, modele ML,
metadate, setări). Restaurarea trebuie să fie ADITIVĂ — niciodată distructivă."""
from __future__ import annotations

import uuid
from pathlib import Path

from app.core.backup import (backup_complet_json, backup_parcuri_csv, backup_parcuri_xlsx,
                              citeste_randuri_csv, citeste_randuri_xlsx,
                              restaureaza_complet_json, restaureaza_parcuri_din_randuri)
from app.models import CalitateOra, Client, ModelMLInfo, OraPrognoza, ProductieOrara, Rulare, new_id
from tests.helpers import parc_eolian, parc_pv


# --------------------------------------------------------------------------- Restaurare parcuri (xlsx/csv)
def test_restaureaza_din_xlsx_recreeaza_parcuri_lipsa(repo):
    repo.salveaza_parc(parc_pv(cod="PV-A"))
    repo.salveaza_parc(parc_eolian(cod="EO-B"))
    continut = backup_parcuri_xlsx(repo)

    repo2 = _repo_gol()
    randuri = citeste_randuri_xlsx(continut)
    rezultat = restaureaza_parcuri_din_randuri(repo2, randuri)

    assert set(rezultat["adaugati"]) == {"PV-A", "EO-B"}
    assert rezultat["actualizati"] == []
    assert rezultat["erori"] == []
    coduri = {p.cod for p in repo2.listeaza_parcuri()}
    assert coduri == {"PV-A", "EO-B"}


def test_restaureaza_din_csv_recreeaza_parcuri_lipsa(repo):
    repo.salveaza_parc(parc_pv(cod="PV-C"))
    continut = backup_parcuri_csv(repo)

    repo2 = _repo_gol()
    randuri = citeste_randuri_csv(continut.encode("utf-8-sig"))
    rezultat = restaureaza_parcuri_din_randuri(repo2, randuri)

    assert rezultat["adaugati"] == ["PV-C"]
    parc_restaurat = repo2.listeaza_parcuri()[0]
    assert parc_restaurat.config_pv is not None
    assert parc_restaurat.config_pv.putere_dc_kwp == 1000.0


def test_restaureaza_actualizeaza_parc_existent_dupa_cod_fara_sal_pierde_id(repo):
    parc = parc_pv(cod="PV-A", nume="Nume vechi")
    repo.salveaza_parc(parc)
    id_original = parc.id

    randuri = citeste_randuri_xlsx(backup_parcuri_xlsx(repo))
    randuri[0]["nume"] = "Nume nou"
    rezultat = restaureaza_parcuri_din_randuri(repo, randuri)

    assert rezultat["actualizati"] == ["PV-A"]
    assert rezultat["adaugati"] == []
    parc_dupa = [p for p in repo.listeaza_parcuri() if p.cod == "PV-A"][0]
    assert parc_dupa.id == id_original          # NU s-a recreat, s-a actualizat
    assert parc_dupa.nume == "Nume nou"


def test_restaureaza_parcuri_nu_atinge_parcurile_neincluse_in_fisier(repo):
    """Restaurarea e aditivă: un parc care NU e în fișierul restaurat rămâne neatins."""
    repo.salveaza_parc(parc_pv(cod="PV-NEATINS"))
    randuri = [{"cod": "PV-NOU", "nume": "Nou", "tehnologie": "fotovoltaic",
                "latitudine": "44.0", "longitudine": "26.0", "putere_dc_kwp": "500", "putere_ac_kw": "450"}]
    restaureaza_parcuri_din_randuri(repo, randuri)

    coduri = {p.cod for p in repo.listeaza_parcuri()}
    assert coduri == {"PV-NEATINS", "PV-NOU"}


def test_restaureaza_parcuri_rand_fara_cod_e_raportat_ca_eroare_fara_sa_opreasca_restul(repo):
    randuri = [
        {"cod": "", "nume": "Fara cod"},
        {"cod": "PV-OK", "nume": "OK", "tehnologie": "fotovoltaic",
         "latitudine": "44.0", "longitudine": "26.0", "putere_dc_kwp": "500", "putere_ac_kw": "450"},
    ]
    rezultat = restaureaza_parcuri_din_randuri(repo, randuri)
    assert rezultat["adaugati"] == ["PV-OK"]
    assert len(rezultat["erori"]) == 1


def test_subansambluri_pv_se_reconstruiesc_din_textul_serializat(repo):
    parc = parc_pv(cod="PV-SUB")
    repo.salveaza_parc(parc)
    randuri = citeste_randuri_xlsx(backup_parcuri_xlsx(repo))
    assert "Principal" in randuri[0]["subansambluri_pv"]

    repo2 = _repo_gol()
    restaureaza_parcuri_din_randuri(repo2, randuri)
    restaurat = repo2.listeaza_parcuri()[0]
    assert len(restaurat.config_pv.subansambluri) == 1
    assert restaurat.config_pv.subansambluri[0].nume == "Principal"
    assert restaurat.config_pv.subansambluri[0].putere_dc_kwp == 1000.0


# --------------------------------------------------------------------------- Copie completă (JSON)
def _populeaza_complet(repo, parc):
    repo.salveaza_parc(parc)
    repo.salveaza_productie([
        ProductieOrara(parc_id=parc.id, timestamp_utc="2026-01-01T10:00:00+00:00", energie_kwh=12.5),
        ProductieOrara(parc_id=parc.id, timestamp_utc="2026-01-01T11:00:00+00:00", energie_kwh=14.0),
    ])
    repo.salveaza_calitate_productie([
        CalitateOra(parc_id=parc.id, timestamp_utc="2026-01-01T10:00:00+00:00", steag="ok", detaliu=None),
    ])
    repo.salveaza_rulare(Rulare(
        id=new_id(), parc_id=parc.id, declansata_la="2026-01-01T09:00:00+00:00",
        tip="manuala", stare="reusita",
        ore=[OraPrognoza(timestamp_utc="2026-01-02T10:00:00+00:00", putere_finala_kw=100.0)],
    ))
    repo.salveaza_model_ml(ModelMLInfo(
        id=new_id(), parc_id=parc.id, antrenat_la="2026-01-01T08:00:00+00:00", activ=True,
        mae_validare_kw=5.0, mae_model_anterior_kw=6.0, numar_ore_antrenare=100,
        cale_fisier="/tmp/model.txt",
    ))
    repo.set_setare("ora_rulare_zilnica_locala", "06:30")


def _repo_gol():
    """Un al doilea Repository SQLite izolat, separat de fixture-ul `repo`
    (pentru testele care restaurează dintr-o bază SURSĂ într-o bază ȚINTĂ
    goală, distinctă)."""
    import tempfile
    from app.storage.sqlite_repo import SqliteRepository
    director = Path(tempfile.mkdtemp())
    return SqliteRepository(director / f"test_{uuid.uuid4().hex}.db")


def test_backup_complet_json_contine_toate_categoriile(repo):
    parc = parc_pv(cod="PV-FULL")
    _populeaza_complet(repo, parc)

    continut = backup_complet_json(repo)
    import json
    payload = json.loads(continut)
    assert payload["versiune"] == 1
    assert len(payload["parcuri"]) == 1
    assert len(payload["productie"][parc.id]) == 2
    assert len(payload["calitate_productie"][parc.id]) == 1
    assert len(payload["rulari"][parc.id]) == 1
    assert len(payload["modele_ml"][parc.id]) == 1
    assert payload["setari"].get("ora_rulare_zilnica_locala") == "06:30"


def test_restaureaza_complet_json_recreeaza_tot_pe_o_baza_goala(repo):
    parc = parc_pv(cod="PV-FULL2")
    _populeaza_complet(repo, parc)
    continut = backup_complet_json(repo)

    repo2 = _repo_gol()
    rezultat = restaureaza_complet_json(repo2, continut)

    assert rezultat["parcuri"] == 1
    assert rezultat["ore_productie"] == 2
    assert rezultat["verdicte_calitate"] == 1
    assert rezultat["rulari"] == 1
    assert rezultat["modele_ml"] == 1
    assert rezultat["setari"] >= 1
    assert rezultat["erori"] == []

    parcuri2 = repo2.listeaza_parcuri()
    assert len(parcuri2) == 1
    assert parcuri2[0].id == parc.id            # ID păstrat — leagă corect producția/rulările
    assert repo2.numar_ore_productie(parc.id) == 2


def test_restaureaza_complet_json_e_aditiv_pastreaza_parcul_neinclus(repo):
    repo.salveaza_parc(parc_pv(cod="PV-NEATINS2"))
    parc_din_backup = parc_eolian(cod="EO-BACKUP")
    repo_sursa = _repo_gol()
    _populeaza_complet(repo_sursa, parc_din_backup)
    continut = backup_complet_json(repo_sursa)

    restaureaza_complet_json(repo, continut)

    coduri = {p.cod for p in repo.listeaza_parcuri()}
    assert coduri == {"PV-NEATINS2", "EO-BACKUP"}


def test_restaureaza_complet_json_fisier_invalid_raporteaza_eroare_clara(repo):
    rezultat = restaureaza_complet_json(repo, b"nu e json {{{")
    assert "eroare" in rezultat


def test_restaureaza_complet_json_o_eroare_pe_un_parc_nu_opreste_restul(repo):
    import json
    parc_ok = parc_pv(cod="PV-BUN")
    payload = {
        "versiune": 1, "clienti": [],
        "parcuri": [
            {"cod": "PV-STRICAT", "tehnologie": "ceva_invalid"},   # va eșua
            {**_ca_dict(parc_ok)},
        ],
        "productie": {}, "calitate_productie": {}, "rulari": {}, "modele_ml": {}, "setari": {},
    }
    rezultat = restaureaza_complet_json(repo, json.dumps(payload).encode("utf-8"))
    assert rezultat["parcuri"] == 1
    assert len(rezultat["erori"]) == 1
    assert any(p.cod == "PV-BUN" for p in repo.listeaza_parcuri())


def _ca_dict(parc):
    from app.storage.serialize import parc_to_dict
    return parc_to_dict(parc)


# --------------------------------------------------------------------------- Rute HTTP (modul tehnic)
def test_ruta_rezerva_pagina_smoke(client, repo):
    r = client.get("/tehnic/rezerva")
    assert r.status_code == 200
    assert "Copie de rezervă" in r.text


def test_ruta_descarca_complet_json_smoke(client, repo):
    repo.salveaza_parc(parc_pv(cod="PV-DL"))
    r = client.get("/tehnic/rezerva/complet.json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    import json
    payload = json.loads(r.content)
    assert len(payload["parcuri"]) == 1


def test_ruta_restaureaza_parcuri_upload_xlsx(client, repo):
    repo.salveaza_parc(parc_pv(cod="PV-UP"))
    continut = backup_parcuri_xlsx(repo)
    r = client.post("/tehnic/rezerva/parcuri",
                     files={"fisier": ("rezerva.xlsx", continut,
                                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200
    assert "PV-UP" in r.text


def test_ruta_restaureaza_complet_upload_json(client, repo):
    repo.salveaza_parc(parc_pv(cod="PV-UPJ"))
    continut = backup_complet_json(repo)
    r = client.post("/tehnic/rezerva/complet",
                     files={"fisier": ("rezerva.json", continut, "application/json")})
    assert r.status_code == 200
    assert "Restaurat" in r.text
