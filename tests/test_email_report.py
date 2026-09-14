"""Trimiterea raportului către client — secțiunea 10 (R-1001-1008).

Fiecare test verifică un singur motiv de "sărire" a trimiterii, în ordinea
exactă din email_report.trimite_raport_email, plus jurnalizarea (R-1008)."""
from __future__ import annotations

import dataclasses

import pytest

import app.config as config_mod
import app.core.email_report as email_report
from app.config import MEDIU
from app.models import OraPrognoza, Rulare
from tests.helpers import parc_pv


def _rulare_cu_ore(parc_id: str, stare="reusita", blocate=0, total=3) -> Rulare:
    ore = []
    for i in range(total):
        blocat = i < blocate
        ore.append(OraPrognoza(
            timestamp_utc=f"2026-06-01T{10+i:02d}:00:00+00:00",
            putere_finala_kw=None if blocat else 100.0 + i,
            blocat=blocat,
        ))
    return Rulare(id="r1", parc_id=parc_id, declansata_la="2026-06-01T09:00:00+00:00",
                   tip="manuala", stare=stare, ore=ore)


def test_r1006_mod_umbra_are_prioritate_absoluta(repo):
    """R-1006: mod umbră blochează emailul chiar dacă parcul ARE destinatari
    și rularea a fost perfect reușită — verificarea trebuie să fie prima."""
    parc = parc_pv(mod_umbra=True, destinatari_email=["client@example.com"])
    repo.salveaza_parc(parc)
    rulare = _rulare_cu_ore(parc.id, stare="reusita", blocate=0)

    incercare = email_report.trimite_raport_email(repo, parc, rulare)

    assert incercare.rezultat == "sarit"
    assert "mod umbră" in incercare.motiv.lower()
    assert incercare.catre == []
    jurnalizate = repo.listeaza_incercari_email(parc.id)
    assert len(jurnalizate) == 1


def test_r1003_fara_destinatari_nu_e_eroare(repo):
    parc = parc_pv(mod_umbra=False, destinatari_email=[])
    repo.salveaza_parc(parc)
    rulare = _rulare_cu_ore(parc.id, stare="reusita", blocate=0)

    incercare = email_report.trimite_raport_email(repo, parc, rulare)

    assert incercare.rezultat == "sarit"
    assert "R-1003" in incercare.motiv or "destinatar" in incercare.motiv.lower()


def test_r1007_rulare_esuata_nu_se_trimite(repo):
    parc = parc_pv(mod_umbra=False, destinatari_email=["client@example.com"])
    repo.salveaza_parc(parc)
    rulare = _rulare_cu_ore(parc.id, stare="esuata", blocate=3, total=3)

    incercare = email_report.trimite_raport_email(repo, parc, rulare)

    assert incercare.rezultat == "sarit"
    assert "R-1007" in incercare.motiv or "blocat" in incercare.motiv.lower()


def test_r1007_toate_orele_blocate_nu_se_trimite_chiar_daca_stare_nu_e_esuata(repo):
    """Chiar dacă starea rulării nu e explicit 'esuata', o listă goală de ore
    valide (toate blocate) trebuie tot să blocheze trimiterea."""
    parc = parc_pv(mod_umbra=False, destinatari_email=["client@example.com"])
    repo.salveaza_parc(parc)
    rulare = _rulare_cu_ore(parc.id, stare="partiala", blocate=3, total=3)

    incercare = email_report.trimite_raport_email(repo, parc, rulare)
    assert incercare.rezultat == "sarit"


def test_rulare_partiala_cu_ore_valide_ramase_se_trimite_pe_ce_a_ramas(repo, monkeypatch):
    """O rulare parțial reușită (unele ore blocate) NU trebuie tratată ca eșec
    total — orele valide rămase se raportează normal (R-1503/R-1505: livrare
    parțială > 0 livrare)."""
    parc = parc_pv(mod_umbra=False, destinatari_email=["client@example.com"])
    repo.salveaza_parc(parc)
    rulare = _rulare_cu_ore(parc.id, stare="partiala", blocate=1, total=3)

    mediu_activ = dataclasses.replace(MEDIU, smtp_host="smtp.test.local", smtp_user="u@test.local",
                                       smtp_password="parola")
    monkeypatch.setattr(config_mod, "MEDIU", mediu_activ)
    monkeypatch.setattr(email_report, "_trimite_smtp", lambda mesaj, smtp: None)

    incercare = email_report.trimite_raport_email(repo, parc, rulare)
    assert incercare.rezultat == "reusit"


def test_smtp_neconfigurat_marcheaza_esuat_dar_jurnalizeaza(repo):
    """Fără SMTP în .env, emailul nu poate fi trimis efectiv — se
    jurnalizează ca eșuat, nu se ascunde silențios (R-1008)."""
    parc = parc_pv(mod_umbra=False, destinatari_email=["client@example.com"])
    repo.salveaza_parc(parc)
    rulare = _rulare_cu_ore(parc.id, stare="reusita", blocate=0)

    assert not MEDIU.email_configurat(), "acest test presupune SMTP gol în mediul de test"
    incercare = email_report.trimite_raport_email(repo, parc, rulare)

    assert incercare.rezultat == "esuat"
    assert incercare.motiv


def test_trimitere_reusita_cu_smtp_simulat(repo, monkeypatch):
    parc = parc_pv(mod_umbra=False, destinatari_email=["a@example.com", "b@example.com"])
    repo.salveaza_parc(parc)
    rulare = _rulare_cu_ore(parc.id, stare="reusita", blocate=0)

    mediu_activ = dataclasses.replace(MEDIU, smtp_host="smtp.test.local", smtp_user="u@test.local",
                                       smtp_password="parola")
    monkeypatch.setattr(config_mod, "MEDIU", mediu_activ)

    mesaje_trimise = []
    monkeypatch.setattr(email_report, "_trimite_smtp", lambda mesaj, smtp: mesaje_trimise.append(mesaj))

    incercare = email_report.trimite_raport_email(repo, parc, rulare)

    assert incercare.rezultat == "reusit"
    assert incercare.motiv is None
    assert incercare.catre == parc.destinatari_email
    assert len(mesaje_trimise) == 1
    assert mesaje_trimise[0]["To"] == "a@example.com, b@example.com"


def test_eroare_smtp_la_trimitere_se_jurnalizeaza_ca_esuat(repo, monkeypatch):
    parc = parc_pv(mod_umbra=False, destinatari_email=["a@example.com"])
    repo.salveaza_parc(parc)
    rulare = _rulare_cu_ore(parc.id, stare="reusita", blocate=0)

    mediu_activ = dataclasses.replace(MEDIU, smtp_host="smtp.test.local", smtp_user="u@test.local",
                                       smtp_password="parola")
    monkeypatch.setattr(config_mod, "MEDIU", mediu_activ)

    def _esueaza(mesaj, smtp):
        raise ConnectionRefusedError("serverul SMTP a refuzat conexiunea")
    monkeypatch.setattr(email_report, "_trimite_smtp", _esueaza)

    incercare = email_report.trimite_raport_email(repo, parc, rulare)
    assert incercare.rezultat == "esuat"
    assert "refuzat" in incercare.motiv.lower() or "eroare" in incercare.motiv.lower()


def test_fiecare_incercare_se_jurnalizeaza_indiferent_de_rezultat(repo):
    """R-1008: fiecare încercare (reușită, eșuată sau sărită) trebuie
    persistată — testăm cumulativ pe mai multe scenarii succesive."""
    parc = parc_pv(mod_umbra=True, destinatari_email=[])
    repo.salveaza_parc(parc)
    rulare = _rulare_cu_ore(parc.id, stare="reusita", blocate=0)

    email_report.trimite_raport_email(repo, parc, rulare)
    parc.mod_umbra = False
    repo.salveaza_parc(parc)
    email_report.trimite_raport_email(repo, parc, rulare)  # acum sare la R-1003 (fără destinatari)

    jurnal = repo.listeaza_incercari_email(parc.id)
    assert len(jurnal) == 2
    assert all(i.rezultat == "sarit" for i in jurnal)
