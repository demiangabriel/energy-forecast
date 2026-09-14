"""„Zona de siguranță" pentru datele meteo — app/web/vizualizare.py:diagnostic_meteo.
Nu există un import de fișier meteo: sursele se interoghează live la fiecare
rulare, iar diagnosticul arată cât de des a răspuns fiecare sursă configurată."""
from __future__ import annotations

from app.models import Rulare, new_id
from app.web.vizualizare import diagnostic_meteo
from tests.helpers import parc_pv


def _rulare(parc_id, surse_raspuns, ponderi=None, stare="reusita", mesaj=None):
    return Rulare(id=new_id(), parc_id=parc_id, declansata_la="2026-06-01T05:00:00+00:00",
                  tip="programata", stare=stare, mesaj=mesaj,
                  surse_meteo_raspuns=surse_raspuns, ponderi_folosite=ponderi or {})


def test_fara_rulari_semnaleaza_clar(repo):
    parc = parc_pv()
    repo.salveaza_parc(parc)
    rezultat = diagnostic_meteo(repo, parc)
    assert rezultat["fara_rulari"] is True
    # sursele configurate apar oricum, dar fără nicio rulare de contorizat
    assert all(r["total"] == 0 and r["procent"] is None for r in rezultat["randuri"])


def test_sursa_mereu_absenta_e_marcata_problema(repo):
    parc = parc_pv()
    parc.surse_meteo = ["ecmwf_ifs025", "gfs_seamless"]
    repo.salveaza_parc(parc)
    for _ in range(10):
        repo.salveaza_rulare(_rulare(parc.id, {"ecmwf_ifs025": True, "gfs_seamless": False},
                                      ponderi={"ecmwf_ifs025": 1.0}))
    rezultat = diagnostic_meteo(repo, parc)
    pe_sursa = {r["sursa"]: r for r in rezultat["randuri"]}
    assert pe_sursa["ecmwf_ifs025"]["procent"] == 100.0
    assert pe_sursa["ecmwf_ifs025"]["problema"] is False
    assert pe_sursa["gfs_seamless"]["procent"] == 0.0
    assert pe_sursa["gfs_seamless"]["problema"] is True
    assert rezultat["are_probleme"] is True


def test_sursa_care_raspunde_constant_nu_e_marcata(repo):
    parc = parc_pv()
    parc.surse_meteo = ["ecmwf_ifs025"]
    repo.salveaza_parc(parc)
    for _ in range(5):
        repo.salveaza_rulare(_rulare(parc.id, {"ecmwf_ifs025": True}, ponderi={"ecmwf_ifs025": 1.0}))
    rezultat = diagnostic_meteo(repo, parc)
    assert rezultat["are_probleme"] is False
    assert rezultat["randuri"][0]["ultima_pondere"] == 1.0


def test_rulare_esuata_inainte_de_apelul_surselor_nu_conteaza_ca_absenta(repo):
    """O rulare eșuată la obținerea datelor meteo (ex. rețea picată) nu are
    deloc `surse_meteo_raspuns` populat — nu trebuie contorizată ca „sursă a
    răspuns fals”, altfel un singur eșec de rețea ar arăta toate sursele ca
    fiind nesigure."""
    parc = parc_pv()
    parc.surse_meteo = ["ecmwf_ifs025"]
    repo.salveaza_parc(parc)
    repo.salveaza_rulare(_rulare(parc.id, {}, stare="esuata", mesaj="Eroare la obținerea datelor meteo"))
    repo.salveaza_rulare(_rulare(parc.id, {"ecmwf_ifs025": True}, ponderi={"ecmwf_ifs025": 1.0}))
    rezultat = diagnostic_meteo(repo, parc)
    assert rezultat["randuri"][0]["total"] == 1  # nu 2
    assert rezultat["randuri"][0]["procent"] == 100.0
    assert rezultat["ultima_rulare_esuata"] is not None
