"""Import istoric de producție — Anexa A, secțiunea 11 (R-1101-1104).

Formatul de BAZĂ, obligatoriu, e ISO 8601 cu decalaj de fus orar explicit
(„2026-06-01T12:00:00+03:00"). Formatul vechi — oră locală fără decalaj — se
acceptă doar cu `permite_ora_locala=True`, adică după o confirmare explicită a
operatorului în pagina de import. Vezi docstring-ul din app/core/csv_import.py
pentru motivația completă.
"""
from __future__ import annotations

import pytest

from app.core.csv_import import analizeaza_fisier, importa_randuri
from tests.helpers import parc_eolian, parc_pv


def _linii(*randuri: str) -> bytes:
    return ("timestamp;kWh\n" + "\n".join(randuri)).encode("utf-8")


# România: +03:00 vara (ora de vară), +02:00 iarna.
VARA = "+03:00"


# --------------------------------------------------------------- format de bază


def test_fisier_valid_minimal():
    parc = parc_pv()
    continut = _linii(f"2026-06-01T12:00:00{VARA};500", f"2026-06-01T13:00:00{VARA};520")
    raport = analizeaza_fisier(continut, parc)
    assert raport.valid, raport.erori
    assert not raport.erori
    assert len(raport.randuri_valide) == 2


def test_accepta_spatiu_in_loc_de_T_si_sufixul_Z():
    """Variante ISO 8601 pe care le produc uneltele uzuale (Excel, pandas,
    exporturi SCADA) — toate desemnează fără ambiguitate același moment."""
    parc = parc_pv()
    raport = analizeaza_fisier(_linii(
        "2026-06-01 09:00:00+03:00;100",   # spațiu în loc de „T"
        "2026-06-01T07:00:00Z;110",        # UTC explicit
        "2026-06-01T11:00:00+03:00;120",
    ), parc)
    assert raport.valid, raport.erori
    assert len(raport.randuri_valide) == 3


def test_ora_fara_decalaj_e_respinsa_implicit():
    """Miezul cerinței: fără decalaj, fișierul nu intră în bază din neatenție."""
    parc = parc_pv()
    raport = analizeaza_fisier(_linii("2026-06-01 12:00;500"), parc)
    assert not raport.valid
    assert any("decalaj" in e for e in raport.erori)
    # mesajul trebuie să arate exact ce se așteaptă și cum se poate continua totuși
    assert any("2025-03-30T01:00:00+02:00" in e for e in raport.erori)
    assert any("oră locală" in e for e in raport.erori)


def test_decalajul_din_fisier_are_prioritate_fata_de_fusul_parcului():
    """Dovada că decalajul chiar guvernează conversia: același parc, aceeași
    oră scrisă, două decalaje diferite -> două momente UTC diferite."""
    parc = parc_pv(fus_orar="Europe/Bucharest")
    r1 = analizeaza_fisier(_linii("2026-06-01T12:00:00+03:00;500"), parc)
    r2 = analizeaza_fisier(_linii("2026-06-01T12:00:00+00:00;500"), parc)
    assert r1.valid and r2.valid
    assert r1.randuri_valide[0].timestamp_utc == "2026-06-01T09:00:00+00:00"
    assert r2.randuri_valide[0].timestamp_utc == "2026-06-01T12:00:00+00:00"


def test_decalaj_nevalid_e_semnalat_clar():
    parc = parc_pv()
    raport = analizeaza_fisier(_linii("2026-13-45T99:00:00+03:00;500"), parc)
    assert not raport.valid
    assert any("invalidă" in e for e in raport.erori)


# --------------------------------------------------------------- validări generale


def test_antet_gresit_e_respins():
    parc = parc_pv()
    continut = b"data;energie\n2026-06-01T12:00:00+03:00;500"
    raport = analizeaza_fisier(continut, parc)
    assert not raport.valid
    assert any("Antetul" in e for e in raport.erori)


def test_fisier_gol_e_respins():
    parc = parc_pv()
    raport = analizeaza_fisier(b"", parc)
    assert not raport.valid
    assert raport.erori


def test_coloana_lipsa_pe_un_rand_e_semnalata_cu_numar_de_linie():
    parc = parc_pv()
    continut = _linii(f"2026-06-01T12:00:00{VARA};500", f"2026-06-01T13:00:00{VARA}")
    raport = analizeaza_fisier(continut, parc)
    assert not raport.valid
    assert any("Linia 3" in e for e in raport.erori)


def test_ora_nerotunda_e_respinsa():
    parc = parc_pv()
    raport = analizeaza_fisier(_linii(f"2026-06-01T12:30:00{VARA};500"), parc)
    assert not raport.valid
    assert any("nu e rotundă" in e for e in raport.erori)


def test_energie_negativa_e_respinsa():
    parc = parc_pv()
    raport = analizeaza_fisier(_linii(f"2026-06-01T12:00:00{VARA};-10"), parc)
    assert not raport.valid
    assert any("negativă" in e for e in raport.erori)


def test_virgula_zecimala_e_acceptata():
    parc = parc_pv()
    raport = analizeaza_fisier(_linii(f"2026-06-01T12:00:00{VARA};123,45"), parc)
    assert raport.valid
    assert raport.randuri_valide[0].energie_kwh == pytest.approx(123.45)


def test_energie_nenumerica_e_respinsa():
    parc = parc_pv()
    raport = analizeaza_fisier(_linii(f"2026-06-01T12:00:00{VARA};abc"), parc)
    assert not raport.valid
    assert any("energie invalidă" in e for e in raport.erori)


def test_acelasi_moment_scris_de_doua_ori_e_avertisment_nu_eroare():
    parc = parc_pv()
    raport = analizeaza_fisier(_linii(f"2026-06-01T12:00:00{VARA};500",
                                       f"2026-06-01T12:00:00{VARA};600"), parc)
    assert raport.valid  # avertisment, nu blochează importul
    assert any("de mai multe ori" in a for a in raport.avertismente)


def test_duplicatul_se_judeca_pe_momentul_real_nu_pe_text():
    """Cu decalaj explicit, două texte DIFERITE pot fi aceeași oră reală —
    trebuie semnalate ca duplicat, altfel o valoare ar suprascrie tăcut alta."""
    parc = parc_pv()
    raport = analizeaza_fisier(_linii("2026-06-01T12:00:00+03:00;500",
                                       "2026-06-01T09:00:00+00:00;600"), parc)
    assert raport.valid
    assert any("de mai multe ori" in a for a in raport.avertismente)


def test_pas_neregulat_genereaza_avertisment():
    parc = parc_pv()
    raport = analizeaza_fisier(_linii(f"2026-06-01T10:00:00{VARA};500",
                                       f"2026-06-01T13:00:00{VARA};500"), parc)
    assert raport.valid
    assert any("neregulate" in a for a in raport.avertismente)


def test_importa_randuri_scrie_in_repository(repo):
    parc = parc_pv()
    repo.salveaza_parc(parc)
    continut = _linii(f"2026-06-01T12:00:00{VARA};500", f"2026-06-01T13:00:00{VARA};520")
    raport = analizeaza_fisier(continut, parc)
    n = importa_randuri(repo, raport)
    assert n == 2
    assert repo.numar_ore_productie(parc.id) == 2


def test_importa_randuri_refuza_raport_invalid(repo):
    parc = parc_pv()
    repo.salveaza_parc(parc)
    raport = analizeaza_fisier(_linii(f"2026-06-01T12:00:00{VARA};-5"), parc)
    with pytest.raises(ValueError):
        importa_randuri(repo, raport)


# --------------------------------------------------------------- R-1104 (verificarea fusului)


def test_eolian_nu_are_verificare_de_fus_orar():
    """R-1104 se aplică doar PV (eolianul nu are vârf legat de poziția
    soarelui) — nu ar trebui să apară niciodată un avertisment de fus orar
    pentru un parc eolian, indiferent cât de „greșit" ar fi decalajul."""
    parc = parc_eolian(fus_orar="Europe/Bucharest")
    randuri = [f"2026-06-{d:02d}T{h:02d}:00:00{VARA};{100 + h}"
               for d in range(1, 4) for h in range(24)]
    raport = analizeaza_fisier(_linii(*randuri), parc)
    assert raport.valid
    assert not any("fus orar" in a.lower() for a in raport.avertismente)


def test_verificare_fus_orar_detecteaza_decalaj_real():
    """R-1104: dacă vârful de producție cade sistematic la o oră incompatibilă
    cu amiaza solară reală (din longitudine), trebuie semnalat. Verificarea
    rămâne utilă și în formatul nou: un decalaj scris GREȘIT în fișier (toate
    liniile cu +03:00 când de fapt erau UTC) e tot o eroare de etichetare, iar
    forma curbei o dă în vileag."""
    parc = parc_pv(latitudine=44.43, longitudine=26.10, fus_orar="Europe/Bucharest")
    randuri = []
    for zi in range(1, 8):
        for h in range(24):
            # profil centrat greșit la ora 9 în loc de ~13 -> decalaj sistematic
            val = max(0.0, 500 * (1 - ((h - 9) / 4.0) ** 2))
            randuri.append(f"2026-06-{zi:02d}T{h:02d}:00:00{VARA};{val:.1f}")
    raport = analizeaza_fisier(_linii(*randuri), parc, toleranta_fus_orar_minute=30)
    assert raport.valid  # avertisment, nu blochează importul
    assert any("Fus orar posibil greșit" in a for a in raport.avertismente), \
        f"ar fi trebuit să detecteze decalajul, avertismente: {raport.avertismente}"


# --------------------------------------------------------------- formatul vechi (compatibilitate)


def test_formatul_vechi_merge_cu_confirmare_explicita():
    parc = parc_pv(fus_orar="Europe/Bucharest")
    raport = analizeaza_fisier(_linii("2026-06-01 12:00;500", "2026-06-01 13:00;520"),
                                parc, permite_ora_locala=True)
    assert raport.valid, raport.erori
    # 12:00 ora României vara = 09:00 UTC
    assert raport.randuri_valide[0].timestamp_utc == "2026-06-01T09:00:00+00:00"


def test_formatul_vechi_avertizeaza_ca_nu_e_formatul_de_baza():
    parc = parc_pv()
    raport = analizeaza_fisier(_linii("2026-06-01 12:00;500"), parc, permite_ora_locala=True)
    assert raport.valid
    assert any("decalaj" in a for a in raport.avertismente), \
        "operatorul trebuie să afle că fișierul lui nu e în formatul de bază"


def test_formatul_vechi_accepta_si_secundele():
    parc = parc_pv()
    raport = analizeaza_fisier(_linii("2026-06-01 12:00:00;500"), parc, permite_ora_locala=True)
    assert raport.valid, raport.erori


def test_fisier_mixt_decalaj_si_fara_decalaj(repo):
    """Un export „reparat pe jumătate" nu trebuie să treacă tăcut: liniile fără
    decalaj rămân semnalate chiar dacă restul fișierului e corect."""
    parc = parc_pv()
    raport = analizeaza_fisier(_linii(f"2026-06-01T12:00:00{VARA};500",
                                       "2026-06-01 13:00;520"), parc)
    assert not raport.valid
    assert any("decalaj" in e for e in raport.erori)
