"""Analiza de după antrenarea ML (`/parcuri/{id}/antrenare`).

Cifrele existau deja în `ModelMLInfo`, dar ajungeau la operator într-o singură
frază densă, afișată o clipă ca mesaj. Aici devin un verdict, cifrele care
l-au decis și ce urmează de făcut."""
from __future__ import annotations

from app.models import ModelMLInfo
from app.web.vizualizare import raport_antrenare
from tests.helpers import parc_pv


def _model(**overrides) -> ModelMLInfo:
    date_ = dict(id="m1", parc_id="p1", antrenat_la="2026-09-12T08:30:00+00:00", activ=True,
                  mae_validare_kw=40.0, mae_model_anterior_kw=50.0, numar_ore_antrenare=2000,
                  cale_fisier="/tmp/m1.txt", skill_vs_referinta=0.25, skill_model_anterior=0.10,
                  numar_ore_excluse_qc=30)
    date_.update(overrides)
    return ModelMLInfo(**date_)


def test_fara_model_raportul_o_spune_clar():
    assert raport_antrenare(parc_pv(), None) == {"are_model": False}


def test_verdictul_de_activare_e_explicat_nu_doar_afisat():
    r = raport_antrenare(parc_pv(), _model(activ=True))
    assert r["verdict"] == "activat"
    assert "nu le văzuse la antrenare" in r["explicatie"]


def test_verdictul_de_respingere_linisteste_operatorul():
    """Un model respins nu e o defecțiune — versiunea veche continuă să
    lucreze. Mesajul trebuie să spună asta, altfel pare că ceva s-a stricat."""
    r = raport_antrenare(parc_pv(), _model(activ=False))
    assert r["verdict"] == "respins"
    assert "versiunea veche continuă să lucreze" in r["explicatie"]
    assert "nimic nu s-a stricat" in r["explicatie"]


def test_imbunatatirea_fata_de_modelul_anterior_e_calculata():
    r = raport_antrenare(parc_pv(), _model(mae_validare_kw=40.0, mae_model_anterior_kw=50.0))
    assert r["imbunatatire_pct"] == 20.0


def test_primul_model_nu_pretinde_o_comparatie_inexistenta():
    r = raport_antrenare(parc_pv(), _model(mae_model_anterior_kw=None))
    assert r["imbunatatire_pct"] is None
    contexte = " ".join(i["context"] or "" for i in r["indicatori"])
    assert "primul" in contexte


def test_skill_negativ_produce_o_observatie_cu_ce_e_de_verificat():
    r = raport_antrenare(parc_pv(), _model(skill_vs_referinta=-0.05))
    text = " ".join(r["observatii"])
    assert "Skill negativ" in text
    assert "calitatea datelor" in text and "calibrarea" in text


def test_prea_multe_ore_excluse_sunt_semnalate():
    r = raport_antrenare(parc_pv(), _model(numar_ore_antrenare=100, numar_ore_excluse_qc=60))
    assert any("excluse de controlul de calitate" in o for o in r["observatii"])


def test_antrenare_normala_nu_inventeaza_probleme():
    r = raport_antrenare(parc_pv(), _model())
    assert r["observatii"] == ["Nimic neobișnuit: antrenarea a decurs normal."]


def test_eroarea_e_pusa_in_context_fata_de_capacitate():
    """40 kW eroare înseamnă altceva la un parc de 850 kW decât la unul de 9 MW."""
    parc = parc_pv()
    r = raport_antrenare(parc, _model(mae_validare_kw=85.0))
    context_mae = r["indicatori"][0]["context"]
    assert "% din puterea de referință" in context_mae


def test_pagina_de_analiza_se_deschide(client, repo):
    parc = parc_pv(cod="ANT-1")
    repo.salveaza_parc(parc)
    r = client.get(f"/parcuri/{parc.id}/antrenare")
    assert r.status_code == 200
    assert "niciun model antrenat" in r.text


def test_pagina_arata_verdictul_si_cifrele(client, repo):
    parc = parc_pv(cod="ANT-2")
    repo.salveaza_parc(parc)
    repo.salveaza_model_ml(_model(id="m-ant", parc_id=parc.id))
    r = client.get(f"/parcuri/{parc.id}/antrenare")
    assert r.status_code == 200
    assert "MODEL ACTIVAT" in r.text
    assert "Cifrele care au decis" in r.text
