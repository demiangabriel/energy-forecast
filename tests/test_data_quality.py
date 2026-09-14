"""Controlul calității producției măsurate — verificarea intrărilor pe care
se antrenează modelul. Vezi docs/cercetare/03-literatura-stiintifica.md, §8."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.data_quality import (STEAGURI_EXCLUSE_DIN_ANTRENARE, analizeaza_calitate,
                                    detecteaza_decalaj_timp)
from app.models import SteagCalitate
from tests.helpers import parc_eolian, parc_pv

LAT, LON, ALT = 44.43, 26.10, 80.0


def _zi_solara(n_zile: int = 20, varf: float = 800.0, inceput: str = "2026-06-01") -> pd.Series:
    """Serie sintetică cu formă diurnă realistă (fără noapte negativă)."""
    index = pd.date_range(inceput, periods=24 * n_zile, freq="h", tz="UTC")
    ora = index.hour.values
    forma = np.clip(np.sin((ora - 6) / 12 * np.pi), 0, None) * varf
    return pd.Series(forma, index=index)


def _steaguri(raport) -> dict:
    rezultat = {}
    for v in raport.verdicte:
        rezultat.setdefault(v.steag, []).append(v.timestamp_utc)
    return rezultat


def test_serie_curata_nu_produce_alarme_false():
    """Cel mai important test al modulului: pe date bune, controlul de
    calitate trebuie să tacă. Un detector care marchează sute de ore normale
    ar arunca la gunoi setul de antrenare."""
    parc = parc_pv()
    productie = _zi_solara()
    referinta = productie / 0.8            # cer senin puțin peste producție
    raport = analizeaza_calitate(parc, productie, None, referinta, 850.0)
    assert raport.numar_probleme == 0, f"alarme false: {_steaguri(raport)}"
    assert raport.procent_curat == 100.0


def test_productie_noaptea_este_marcata():
    parc = parc_pv()
    productie = _zi_solara()
    referinta = productie / 0.8
    productie.iloc[3] = 200.0              # 200 kW la ora 03:00
    raport = analizeaza_calitate(parc, productie, None, referinta, 850.0)
    assert SteagCalitate.NOAPTE_NENUL.value in _steaguri(raport)


def test_valoare_peste_puterea_instalata_este_marcata():
    parc = parc_pv()
    productie = _zi_solara()
    productie.iloc[12] = 5000.0
    raport = analizeaza_calitate(parc, productie, None, productie / 0.8, 850.0)
    assert SteagCalitate.PESTE_INSTALATA.value in _steaguri(raport)


def test_valori_blocate_sunt_marcate():
    parc = parc_eolian()
    index = pd.date_range("2026-03-01", periods=48, freq="h", tz="UTC")
    productie = pd.Series(np.linspace(100, 900, 48), index=index)
    productie.iloc[10:20] = 456.0          # senzor înghețat 10 ore
    raport = analizeaza_calitate(parc, productie, None, None, 20000.0)
    assert SteagCalitate.VALOARE_BLOCATA.value in _steaguri(raport)


def test_indisponibilitatea_este_detectata_cu_asteptare_fizica():
    parc = parc_pv()
    productie = _zi_solara()
    asteptare = productie.copy()
    productie.iloc[100:110] = 0.0          # invertor oprit în plină zi
    raport = analizeaza_calitate(parc, productie, asteptare, asteptare / 0.8, 850.0)
    steaguri = _steaguri(raport)
    assert SteagCalitate.INDISPONIBILITATE.value in steaguri


def test_limitarea_de_putere_are_semnatura_de_platou_plat():
    """Curtailment-ul arată altfel decât o avarie: producția nu cade la zero,
    ci stă pe un platou neobișnuit de PLAT, sub așteptarea fizică."""
    parc = parc_pv()
    asteptare = _zi_solara(varf=800.0)
    productie = asteptare.copy()
    # patru ore de plafon fix la 300 kW, în plin vârf
    zi = productie.index.hour
    masca = (productie.index.date == productie.index.date[24 * 3]) & (zi >= 10) & (zi <= 14)
    productie[masca] = 300.0
    raport = analizeaza_calitate(parc, productie, asteptare, asteptare / 0.8, 850.0)
    steaguri = _steaguri(raport)
    assert SteagCalitate.LIMITARE.value in steaguri, f"găsite: {list(steaguri)}"


def test_orele_problematice_sunt_excluse_din_antrenare():
    """Contractul dintre acest modul și corectorul ML: toate steagurile care
    descriu starea PARCULUI (nu vremea) trebuie să fie în lista de excludere."""
    for steag in [SteagCalitate.INDISPONIBILITATE, SteagCalitate.LIMITARE,
                   SteagCalitate.NOAPTE_NENUL, SteagCalitate.VALOARE_BLOCATA,
                   SteagCalitate.PESTE_INSTALATA, SteagCalitate.VALOARE_EXTREMA]:
        assert steag.value in STEAGURI_EXCLUSE_DIN_ANTRENARE


def test_detectarea_decalajului_de_timp():
    """Cea mai valoroasă verificare: un export făcut în ora locală și
    interpretat ca UTC produce un decalaj constant, invizibil în indicatorii
    agregați, dar care strică tot ce urmează."""
    referinta = _zi_solara(n_zile=10)
    productie = referinta.shift(2).fillna(0.0)      # măsurat cu 2 ore mai târziu
    decalaj, corelatie = detecteaza_decalaj_timp(productie, referinta)
    assert decalaj == pytest.approx(-2.0), f"decalaj găsit: {decalaj}"
    assert corelatie > 0.95


def test_fara_decalaj_detectarea_da_zero():
    referinta = _zi_solara(n_zile=10)
    decalaj, corelatie = detecteaza_decalaj_timp(referinta * 0.7, referinta)
    assert decalaj == pytest.approx(0.0)
    assert corelatie > 0.99


def test_decalajul_este_raportat_ca_mesaj_pentru_operator():
    parc = parc_pv()
    referinta = _zi_solara(n_zile=10)
    productie = referinta.shift(2).fillna(0.0) * 0.8
    raport = analizeaza_calitate(parc, productie, None, referinta, 850.0)
    assert raport.decalaj_ore_detectat is not None
    assert any("decalaj" in m.lower() for m in raport.mesaje)


def test_serie_goala_nu_crapa():
    parc = parc_pv()
    raport = analizeaza_calitate(parc, pd.Series(dtype="float64"), None, None, 850.0)
    assert raport.numar_analizate == 0
    assert raport.verdicte == []
