"""Verificarea prognozei: indicatori, referințe și scor de skill."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.verification import (acoperire_empirica, calculeaza_indicatori, crps_din_cuantile,
                                    latime_medie_interval, pierdere_pinball, referinta_climatologie,
                                    referinta_optima, referinta_persistenta,
                                    referinta_persistenta_indice_senin, scor_skill, verifica)

IDX = pd.date_range("2026-06-01 00:00", periods=48, freq="h", tz="UTC")


def test_indicatori_pe_prognoza_perfecta():
    real = pd.Series(np.linspace(0, 500, 48), index=IDX)
    ind = calculeaza_indicatori(real, real, putere_normalizare_kw=1000)
    assert ind.mae_kw == pytest.approx(0.0)
    assert ind.rmse_kw == pytest.approx(0.0)
    assert ind.nmae_capacitate_pct == pytest.approx(0.0)
    assert ind.numar_ore == 48


def test_indicatori_cu_eroare_constanta():
    real = pd.Series(100.0, index=IDX)
    prognoza = real + 50.0
    ind = calculeaza_indicatori(prognoza, real, putere_normalizare_kw=1000)
    assert ind.mae_kw == pytest.approx(50.0)
    assert ind.bias_kw == pytest.approx(50.0), "supraestimarea trebuie să dea bias POZITIV"
    assert ind.nmae_capacitate_pct == pytest.approx(5.0)
    assert ind.nmae_medie_pct == pytest.approx(50.0), "normalizarea la medie dă cifre mult mai mari"


def test_cele_doua_normalizari_sunt_raportate_distinct():
    """Un nMAE fără bază declarată nu e interpretabil: aceeași eroare dă 5%
    raportată la capacitate și 50% raportată la producția medie."""
    real = pd.Series(100.0, index=IDX)
    ind = calculeaza_indicatori(real + 50.0, real, putere_normalizare_kw=1000)
    assert ind.baza_normalizare_kw == 1000
    assert ind.productie_medie_kw == pytest.approx(100.0)
    assert ind.nmae_capacitate_pct != ind.nmae_medie_pct


def test_persistenta_simpla_deplaseaza_seria():
    real = pd.Series(np.arange(48, dtype=float), index=IDX)
    ref = referinta_persistenta(real, ore_in_avans=24)
    assert np.isnan(ref.iloc[0])
    assert ref.iloc[24] == pytest.approx(0.0)
    assert ref.iloc[47] == pytest.approx(23.0)


def test_persistenta_inteligenta_persista_indicele_nu_puterea():
    """Esențialul pentru solar: se persistă CÂT DE SENIN a fost cerul, nu câți
    kW au fost. Cu cerul identic în două zile consecutive, persistența
    inteligentă reproduce exact ziua următoare, chiar dacă puterea diferă ca
    formă în interiorul zilei."""
    cer_senin = pd.Series(np.concatenate([np.sin(np.linspace(0, np.pi, 24)) * 800] * 2), index=IDX)
    real = cer_senin * 0.6                       # ambele zile la 60% din cer senin
    ref = referinta_persistenta_indice_senin(real, cer_senin, ore_in_avans=24)
    valabile = ref.dropna()
    assert np.allclose(valabile.values, real.reindex(valabile.index).values, atol=1e-6)


def test_climatologia_foloseste_media_indicelui():
    cer_senin = pd.Series(np.concatenate([np.sin(np.linspace(0, np.pi, 24)) * 800] * 2), index=IDX)
    real = cer_senin * 0.5
    ref = referinta_climatologie(real, cer_senin)
    assert np.allclose(ref.dropna().values, (cer_senin * 0.5).reindex(ref.dropna().index).values, atol=1e-6)


def test_scorul_de_skill_semnaleaza_un_model_mai_slab_decat_referinta():
    real = pd.Series(np.sin(np.linspace(0, 8 * np.pi, 48)) * 100 + 200, index=IDX)
    referinta = real.shift(1).bfill()
    prognoza_buna = real + 1.0
    prognoza_slaba = real + 200.0
    assert scor_skill(prognoza_buna, real, referinta) > 0.5
    assert scor_skill(prognoza_slaba, real, referinta) < 0, \
        "un model mai rău decât referința trebuie să dea skill NEGATIV"


def test_skill_zero_cand_prognoza_este_chiar_referinta():
    real = pd.Series(np.random.RandomState(0).normal(200, 50, 48), index=IDX)
    referinta = real.shift(2).bfill()
    assert scor_skill(referinta, real, referinta) == pytest.approx(0.0, abs=1e-9)


def test_referinta_optima_nu_e_mai_slaba_decat_componentele():
    """Proprietatea care face din combinația convexă referința corectă:
    pe un proces STAȚIONAR, eroarea ei nu poate fi mai mare decât a celei mai
    bune componente (persistență sau climatologie).

    Folosim un AR(1) — exact clasa de procese pentru care e demonstrată
    proprietatea. Pe o serie nestaționară (mers aleator) garanția nu se aplică
    strict, pentru că autocorelația estimată pe eșantion nu mai e parametrul
    optim; vezi testul următor.
    """
    rng = np.random.RandomState(3)
    n = 2000
    index = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    phi, valori = 0.75, [0.0]
    for _ in range(n - 1):
        valori.append(phi * valori[-1] + rng.normal(0, 1))
    real = pd.Series(np.array(valori) * 40 + 300, index=index)

    optima = referinta_optima(real, None, ore_in_avans=3)
    persistenta = referinta_persistenta(real, 3)
    climatologie = referinta_climatologie(real)

    def rmse(serie):
        pereche = pd.DataFrame({"f": serie, "o": real}).dropna()
        return float(np.sqrt(((pereche["f"] - pereche["o"]) ** 2).mean()))

    assert rmse(optima) <= min(rmse(persistenta), rmse(climatologie)) + 1e-6


def test_referinta_optima_tinde_spre_persistenta_la_orizont_scurt():
    """La orizont scurt, autocorelația e mare, deci combinația se apropie de
    persistență — comportamentul așteptat fizic: „peste o oră va fi cam ca
    acum”. La orizont lung colapsează spre climatologie."""
    rng = np.random.RandomState(11)
    index = pd.date_range("2026-01-01", periods=500, freq="h", tz="UTC")
    valori = np.cumsum(rng.normal(0, 2, 500)) + 300
    real = pd.Series(valori, index=index)

    scurt = referinta_optima(real, None, ore_in_avans=1)
    persistenta = referinta_persistenta(real, 1)
    pereche = pd.DataFrame({"a": scurt, "b": persistenta}).dropna()
    diferenta_relativa = float((pereche["a"] - pereche["b"]).abs().mean() / real.mean())
    assert diferenta_relativa < 0.05, "la orizont de o oră referința trebuie să fie ~persistență"


def test_pinball_este_asimetric():
    """Pinball penalizează diferit sub- și supraestimarea, în funcție de nivel:
    pentru P90, a fi SUB realizat e penalizat de 9 ori mai mult."""
    real = pd.Series([100.0], index=IDX[:1])
    sub = pd.Series([90.0], index=IDX[:1])
    peste = pd.Series([110.0], index=IDX[:1])
    assert pierdere_pinball(sub, real, 0.9) == pytest.approx(0.9 * 10)
    assert pierdere_pinball(peste, real, 0.9) == pytest.approx(0.1 * 10)
    assert pierdere_pinball(sub, real, 0.5) == pierdere_pinball(peste, real, 0.5)


def test_crps_scade_cand_cuantilele_sunt_mai_bune():
    real = pd.Series(np.full(48, 100.0), index=IDX)
    bune = {n: pd.Series(np.full(48, 100.0 + (n - 0.5) * 10), index=IDX)
            for n in [0.1, 0.25, 0.5, 0.75, 0.9]}
    slabe = {n: pd.Series(np.full(48, 100.0 + (n - 0.5) * 400), index=IDX)
             for n in [0.1, 0.25, 0.5, 0.75, 0.9]}
    assert crps_din_cuantile(bune, real) < crps_din_cuantile(slabe, real)


def test_acoperirea_empirica_detecteaza_intervale_prea_inguste():
    rng = np.random.RandomState(5)
    real = pd.Series(rng.normal(100, 30, 48), index=IDX)
    # interval absurd de îngust: acoperirea observată va fi departe de nivel
    inguste = {0.1: pd.Series(99.0, index=IDX), 0.9: pd.Series(101.0, index=IDX)}
    acoperire = acoperire_empirica(inguste, real)
    assert acoperire[0.9] < 0.7, "un interval prea îngust are acoperire sub nivelul promis"
    assert latime_medie_interval(inguste) == pytest.approx(2.0)


def test_verifica_exclude_noaptea_si_orele_de_calitate():
    real = pd.Series(np.concatenate([np.zeros(12), np.full(12, 100.0)] * 2), index=IDX)
    prognoza = real.copy()
    prognoza.iloc[12:24] = 150.0          # eroare doar ziua
    utile = pd.Series(np.concatenate([[False] * 12, [True] * 12] * 2), index=IDX)

    fara_filtru = verifica(prognoza, real, 1000).indicatori
    cu_filtru = verifica(prognoza, real, 1000, masca_ore_utile=utile).indicatori

    assert cu_filtru.numar_ore == 24
    assert cu_filtru.nmae_capacitate_pct > fara_filtru.nmae_capacitate_pct, \
        "includerea nopților diluează eroarea reală și face indicatorul artificial de bun"

    excluse = IDX[12:18]
    cu_qc = verifica(prognoza, real, 1000, masca_ore_utile=utile, ore_excluse_qc=excluse)
    assert cu_qc.ore_excluse_qc == 6
    assert cu_qc.indicatori.numar_ore == 18


def test_verifica_separa_indicatorii_pe_orizont():
    real = pd.Series(100.0, index=IDX)
    prognoza = real.copy()
    prognoza.iloc[24:] = 200.0                                  # orizontul lung e mult mai slab
    avans = pd.Series(np.concatenate([np.full(24, 3.0), np.full(24, 30.0)]), index=IDX)
    raport = verifica(prognoza, real, 1000, ore_in_avans=avans)
    assert "0-6 h" in raport.pe_orizont and "24-48 h" in raport.pe_orizont
    assert raport.pe_orizont["0-6 h"].nmae_capacitate_pct == pytest.approx(0.0)
    assert raport.pe_orizont["24-48 h"].nmae_capacitate_pct == pytest.approx(10.0)
