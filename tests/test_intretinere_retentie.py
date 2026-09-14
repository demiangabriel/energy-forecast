"""Retenția datelor istorice (`/tehnic/intretinere`).

Motivația e măsurată: la 50 de parcuri, arhiva de rulări crește cu ordinul
unui GB pe an (o rulare pe zi, per parc, cu toate orele ei) — adică exact
cota gratuită de stocare Firestore, atinsă în ~un an. Vezi
scripts/benchmark_scalare.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import OraPrognoza, ProductieOrara, Rulare, new_id
from tests.helpers import parc_pv


def _rulare(parc, zile_in_urma: int) -> Rulare:
    moment = datetime.now(timezone.utc) - timedelta(days=zile_in_urma)
    return Rulare(
        id=new_id(), parc_id=parc.id, declansata_la=moment.isoformat(),
        tip="programata", stare="reusita",
        ore=[OraPrognoza(timestamp_utc=(moment + timedelta(hours=h)).isoformat(),
                          putere_finala_kw=100.0) for h in range(24)],
    )


def test_sterge_doar_rularile_mai_vechi_decat_limita(repo):
    parc = parc_pv(cod="RET-1")
    repo.salveaza_parc(parc)
    for zile in (5, 100, 400, 800):
        repo.salveaza_rulare(_rulare(parc, zile))
    assert len(repo.listeaza_rulari_sumar(parc.id, limita=100)) == 4

    limita = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    n = repo.sterge_rulari_mai_vechi_de(limita)

    assert n == 2, "rulările de la 400 și 800 de zile trebuiau șterse"
    ramase = repo.listeaza_rulari_sumar(parc.id, limita=100)
    assert len(ramase) == 2
    varste = [(datetime.now(timezone.utc) - datetime.fromisoformat(r.declansata_la)).days
              for r in ramase]
    assert all(v < 365 for v in varste)


def test_retentia_nu_atinge_productia_reala_si_modelele(repo):
    """Garanția cea mai importantă a acestui ecran: se pierde doar istoricul
    „ce am prognozat atunci", niciodată datele măsurate — care sunt sursa de
    adevăr și nu se pot recupera dintr-o altă parte (R-611)."""
    parc = parc_pv(cod="RET-2")
    repo.salveaza_parc(parc)
    vechi = datetime.now(timezone.utc) - timedelta(days=900)
    repo.salveaza_productie([
        ProductieOrara(parc_id=parc.id, timestamp_utc=(vechi + timedelta(hours=h)).isoformat(),
                        energie_kwh=50.0) for h in range(48)])
    repo.salveaza_rulare(_rulare(parc, 900))
    assert repo.numar_ore_productie(parc.id) == 48

    repo.sterge_rulari_mai_vechi_de((datetime.now(timezone.utc) - timedelta(days=365)).isoformat())

    assert repo.numar_ore_productie(parc.id) == 48, "producția reală nu trebuie atinsă"
    assert repo.get_parc(parc.id) is not None


def test_sterge_cache_meteo_dupa_data(repo):
    parc = parc_pv(cod="RET-3")
    repo.salveaza_parc(parc)
    repo.set_meteo_cache(parc.id, "reanaliza", "2024-01-15", {"index": [], "date": {}})
    repo.set_meteo_cache(parc.id, "reanaliza", "2026-08-01", {"index": [], "date": {}})

    n = repo.sterge_meteo_cache_mai_vechi_de("2026-01-01")

    assert n == 1
    assert repo.get_meteo_cache(parc.id, "reanaliza", "2024-01-15") is None
    assert repo.get_meteo_cache(parc.id, "reanaliza", "2026-08-01") is not None


def test_ruta_curatare_refuza_o_retentie_periculos_de_scurta(client, repo):
    """Sub 30 de zile s-ar șterge chiar rulările pe care se sprijină verificarea
    acurateței și ponderile adaptive — o „curățare" care ar strica prognozele."""
    parc = parc_pv(cod="RET-4")
    repo.salveaza_parc(parc)
    repo.salveaza_rulare(_rulare(parc, 10))

    r = client.post("/tehnic/intretinere/curata", data={"zile_rulari": "5"},
                     follow_redirects=False)
    assert r.status_code == 303
    assert "nu%20a%20putut" in r.headers["location"] or "nu a putut" in r.headers["location"]
    assert len(repo.listeaza_rulari_sumar(parc.id, limita=10)) == 1, "nimic nu trebuia șters"


def test_ruta_curatare_functioneaza_si_raporteaza_cifre(client, repo):
    parc = parc_pv(cod="RET-5")
    repo.salveaza_parc(parc)
    repo.salveaza_rulare(_rulare(parc, 500))
    repo.salveaza_rulare(_rulare(parc, 10))

    r = client.post("/tehnic/intretinere/curata", data={"zile_rulari": "400"},
                     follow_redirects=False)
    assert r.status_code == 303
    assert len(repo.listeaza_rulari_sumar(parc.id, limita=10)) == 1


def test_pagina_intretinere_se_deschide(client, repo):
    repo.salveaza_parc(parc_pv(cod="RET-6"))
    r = client.get("/tehnic/intretinere")
    assert r.status_code == 200
    assert "retenție" in r.text.lower()
