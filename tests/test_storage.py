"""Integritatea stratului de persistență: ștergere în cascadă, toleranță la
evoluția schemei, verdicte de calitate."""
from __future__ import annotations

import json

import pytest

from app.models import (CalitateOra, IncercareEmail, ModelMLInfo, OraPrognoza, ProductieOrara,
                         Rulare, SteagCalitate, new_id)
from app.storage.serialize import din_dict, parc_from_dict, parc_to_dict, rulare_from_dict, rulare_to_dict
from tests.helpers import parc_eolian, parc_pv


def _populeaza(repo, parc):
    repo.salveaza_parc(parc)
    repo.salveaza_productie([
        ProductieOrara(parc_id=parc.id, timestamp_utc=f"2026-06-01T{h:02d}:00:00+00:00", energie_kwh=10.0)
        for h in range(24)
    ])
    repo.salveaza_rulare(Rulare(
        id=new_id(), parc_id=parc.id, declansata_la="2026-06-01T06:00:00+00:00", tip="manuala",
        stare="reusita", ore=[OraPrognoza(timestamp_utc="2026-06-01T12:00:00+00:00", putere_finala_kw=5.0)]))
    repo.salveaza_incercare_email(IncercareEmail(
        id=new_id(), parc_id=parc.id, rulare_id="r", catre=["a@b.c"],
        moment="2026-06-01T07:00:00+00:00", rezultat="sarit", motiv="test"))
    repo.salveaza_model_ml(ModelMLInfo(
        id=new_id(), parc_id=parc.id, antrenat_la="2026-06-01T08:00:00+00:00", activ=True,
        mae_validare_kw=1.0, mae_model_anterior_kw=2.0, numar_ore_antrenare=100,
        cale_fisier="/tmp/inexistent.txt"))
    repo.salveaza_calitate_productie([CalitateOra(
        parc_id=parc.id, timestamp_utc="2026-06-01T03:00:00+00:00",
        steag=SteagCalitate.NOAPTE_NENUL.value, detaliu="test")])
    repo.set_meteo_cache(parc.id, "ecmwf", "2026-06-01", {"x": 1})


def test_stergerea_parcului_curata_toate_datele_derivate(repo):
    """Fără cascadă, baza de date acumula date orfane: ore de producție,
    rulări și modele care apăreau în jurnal fără parc și creșteau la
    nesfârșit dimensiunea fișierului."""
    parc = parc_pv()
    _populeaza(repo, parc)
    assert repo.numar_ore_productie(parc.id) == 24

    repo.sterge_parc(parc.id)

    assert repo.get_parc(parc.id) is None
    assert repo.numar_ore_productie(parc.id) == 0
    assert repo.listeaza_rulari(parc.id) == []
    assert repo.listeaza_incercari_email(parc.id) == []
    assert repo.listeaza_modele_ml(parc.id) == []
    assert repo.listeaza_calitate_productie(parc.id) == []
    assert repo.get_meteo_cache(parc.id, "ecmwf", "2026-06-01") is None


def test_stergerea_unui_parc_nu_atinge_alt_parc(repo):
    a, b = parc_pv(cod="A"), parc_pv(cod="B")
    _populeaza(repo, a)
    _populeaza(repo, b)
    repo.sterge_parc(a.id)
    assert repo.get_parc(b.id) is not None
    assert repo.numar_ore_productie(b.id) == 24
    assert len(repo.listeaza_rulari(b.id)) == 1


def test_sterge_productie_interval_elimina_doar_intervalul_si_verdictele_asociate(repo):
    """Curățarea unui import greșit (ex. fus orar de import greșit pe o
    parte a istoricului), ÎNAINTE de o reimportare curată — vezi
    Repository.sterge_productie_interval. Verdictele de calitate ale orelor
    șterse trebuie eliminate odată cu ele (altfel rămân orfane)."""
    parc = parc_pv()
    repo.salveaza_parc(parc)
    randuri = [ProductieOrara(parc_id=parc.id, timestamp_utc=f"2026-06-0{d}T{h:02d}:00:00+00:00",
                               energie_kwh=10.0) for d in range(1, 4) for h in range(24)]
    repo.salveaza_productie(randuri)
    repo.salveaza_calitate_productie([CalitateOra(
        parc_id=parc.id, timestamp_utc="2026-06-02T10:00:00+00:00",
        steag=SteagCalitate.LIMITARE.value, detaliu="test")])
    assert repo.numar_ore_productie(parc.id) == 72

    n = repo.sterge_productie_interval(parc.id, "2026-06-02T00:00:00+00:00", "2026-06-02T23:59:59+00:00")

    assert n == 24
    assert repo.numar_ore_productie(parc.id) == 48
    assert repo.listeaza_calitate_productie(parc.id) == []
    ramase = repo.listeaza_productie(parc.id, "2026-06-01T00:00:00+00:00", "2026-06-03T23:59:59+00:00")
    assert {r.timestamp_utc[:10] for r in ramase} == {"2026-06-01", "2026-06-03"}


def test_sterge_productie_interval_nu_atinge_alt_parc_sau_alt_interval(repo):
    a, b = parc_pv(cod="AAA"), parc_pv(cod="BBB")
    repo.salveaza_parc(a)
    repo.salveaza_parc(b)
    for p in (a, b):
        repo.salveaza_productie([
            ProductieOrara(parc_id=p.id, timestamp_utc=f"2026-06-01T{h:02d}:00:00+00:00", energie_kwh=5.0)
            for h in range(24)
        ])

    repo.sterge_productie_interval(a.id, "2026-06-01T00:00:00+00:00", "2026-06-01T23:59:59+00:00")

    assert repo.numar_ore_productie(a.id) == 0
    assert repo.numar_ore_productie(b.id) == 24  # alt parc, neatins


def test_verdictele_de_calitate_sunt_idempotente(repo):
    parc = parc_pv()
    repo.salveaza_parc(parc)
    verdict = CalitateOra(parc_id=parc.id, timestamp_utc="2026-06-01T12:00:00+00:00",
                           steag=SteagCalitate.LIMITARE.value, detaliu="prima analiză")
    repo.salveaza_calitate_productie([verdict])
    verdict.detaliu = "a doua analiză"
    verdict.steag = SteagCalitate.INDISPONIBILITATE.value
    repo.salveaza_calitate_productie([verdict])

    stocate = repo.listeaza_calitate_productie(parc.id)
    assert len(stocate) == 1, "re-rularea analizei nu trebuie să dubleze verdictele"
    assert stocate[0].steag == SteagCalitate.INDISPONIBILITATE.value


def test_citirea_tolereaza_campuri_necunoscute_in_json():
    """Scenariu real: baza de date a fost scrisă de o versiune mai NOUĂ a
    aplicației (sau un câmp a fost eliminat între versiuni). Fără filtrare,
    construcția dataclass-ului ar arunca TypeError și parcul ar deveni
    necitibil — adică baza de date ar părea coruptă."""
    parc = parc_pv()
    date = parc_to_dict(parc)
    date["camp_din_viitor"] = "ceva nou"
    date["config_pv"]["alt_camp_nou"] = 42
    date["config_pv"]["subansambluri"][0]["necunoscut"] = True

    reconstruit = parc_from_dict(date)
    assert reconstruit.cod == parc.cod
    assert reconstruit.config_pv.putere_dc_kwp == parc.config_pv.putere_dc_kwp


def test_citirea_completeaza_campurile_lipsa_cu_implicituri():
    """Scenariu invers: baza de date scrisă de o versiune VECHE nu conține
    câmpurile adăugate ulterior. Parcurile existente trebuie să funcționeze
    neschimbat, cu implicitul noului câmp."""
    parc = parc_pv()
    date = parc_to_dict(parc)
    for camp in ["rezolutie_minute", "produce_cuantile", "ponderi_automate", "putere_referinta_kw"]:
        date.pop(camp, None)
    for camp in ["model_transpozitie", "albedo", "pierderi_zapada", "clipping_per_subansamblu"]:
        date["config_pv"].pop(camp, None)

    reconstruit = parc_from_dict(date)
    assert reconstruit.rezolutie_minute == 60
    assert reconstruit.produce_cuantile is True
    assert reconstruit.config_pv.model_transpozitie.value == "perez-driesse"
    assert reconstruit.config_pv.albedo == 0.20


def test_valoare_de_enum_invalida_cade_pe_implicit():
    parc = parc_pv()
    date = parc_to_dict(parc)
    date["config_pv"]["model_transpozitie"] = "un_model_care_nu_mai_exista"
    reconstruit = parc_from_dict(date)
    assert reconstruit.config_pv.model_transpozitie.value == "perez-driesse"


def test_rulare_cu_cuantile_face_dus_intors_prin_json():
    rulare = Rulare(id="r1", parc_id="p1", declansata_la="2026-06-01T06:00:00+00:00",
                     tip="manuala", stare="reusita", rezolutie_minute=15,
                     ponderi_folosite={"ecmwf_ifs025": 0.5, "gfs_seamless": 0.5},
                     ore=[OraPrognoza(timestamp_utc="2026-06-01T12:00:00+00:00",
                                       putere_finala_kw=100.0, ore_in_avans=6.0,
                                       putere_clearsky_kw=150.0,
                                       cuantile_kw={"0.1": 80.0, "0.5": 100.0, "0.9": 125.0})])
    refacut = rulare_from_dict(json.loads(json.dumps(rulare_to_dict(rulare))))
    assert refacut.rezolutie_minute == 15
    assert refacut.ponderi_folosite["ecmwf_ifs025"] == 0.5
    assert refacut.ore[0].cuantile_kw["0.9"] == 125.0
    assert refacut.ore[0].ore_in_avans == 6.0


def test_model_ml_vechi_fara_campuri_noi_se_citeste(repo):
    """Modelele antrenate înainte de introducerea cuantilelor trebuie să
    rămână utilizabile."""
    date_vechi = {"id": "m1", "parc_id": "p1", "antrenat_la": "2026-01-01T00:00:00+00:00",
                   "activ": True, "mae_validare_kw": 5.0, "mae_model_anterior_kw": 6.0,
                   "numar_ore_antrenare": 500, "cale_fisier": "/tmp/x.txt", "note": "vechi"}
    model = din_dict(ModelMLInfo, date_vechi)
    assert model.cai_cuantile == {}
    assert model.tinta_antrenare == "reziduu_kw", \
        "modelele vechi au fost antrenate pe reziduul în kW, nu pe cel normalizat"
    assert model.skill_vs_referinta is None


def test_parc_eolian_face_dus_intors_cu_campurile_noi():
    parc = parc_eolian()
    parc.config_eolian.intensitate_turbulenta = 0.14
    refacut = parc_from_dict(json.loads(json.dumps(parc_to_dict(parc))))
    assert refacut.config_eolian.intensitate_turbulenta == 0.14
    assert refacut.config_eolian.profil_vertical.value == "power_law"
    assert refacut.config_eolian.reglaj.value == "pitch"
