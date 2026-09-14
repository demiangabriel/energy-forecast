"""Corectorul ML — pragul de activare campion/provocator (R-605).

Antrenarea reală scrie fișiere de model sub data/modele/<parc_id>/ (calea e
fixă în ml_corrector.MODELE_DIR, nu e injectabilă per test) — fiecare test
își curăță propriul folder la final, ca să nu polueze proiectul real.
"""
from __future__ import annotations

import shutil

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

import app.core.ml_corrector as ml_corrector
from app.core.ml_corrector import MODELE_DIR, antreneaza_model, construieste_features
from app.models import ModelMLInfo, ProductieOrara, Tehnologie, new_id
from tests.helpers import config_eolian_simplu, parc_eolian


def _semnal_curat(index: pd.DatetimeIndex) -> np.ndarray:
    """Componentă determinist-periodică (fără zgomot) — sinusoidă orară."""
    ore = index.hour.values.astype(float)
    zile = index.dayofyear.values.astype(float)
    return 1000.0 + 800.0 * np.sin(2 * np.pi * (ore + zile) / 24.0)


def _fizic_din_semnal(index: pd.DatetimeIndex, semnal: np.ndarray, putere_instalata: float) -> pd.DataFrame:
    """Fizica 'văzută' de model — subestimează sistematic semnalul curat cu 30%."""
    fizic = 0.7 * semnal
    return pd.DataFrame({
        "putere_ac_fizic_kw": fizic,
        "fractie_incarcare": (fizic / putere_instalata).clip(0, 1),
    }, index=index)


def _seteaza_productie(repo, parc_id: str, index: pd.DatetimeIndex, valori: np.ndarray) -> None:
    randuri = [ProductieOrara(parc_id=parc_id, timestamp_utc=ts.isoformat(), energie_kwh=float(v))
               for ts, v in zip(index, valori)]
    repo.salveaza_productie(randuri)


@pytest.fixture
def curata_modele_dupa():
    parc_ids = []
    yield parc_ids
    for pid in parc_ids:
        shutil.rmtree(MODELE_DIR / pid, ignore_errors=True)
    try:
        MODELE_DIR.rmdir()  # doar dacă a rămas goală — nu atinge modele reale ale utilizatorului
    except OSError:
        pass


def test_model_cu_bias_sistematic_bate_fizica_si_se_activeaza(repo, monkeypatch, curata_modele_dupa):
    """Fără niciun model activ existent, un candidat care corectează un bias
    sistematic clar și determinist trebuie să bată net fizica pură și să
    devină activ (R-605)."""
    parc = parc_eolian(config_eolian=config_eolian_simplu(numar_turbine=1, putere_nominala_kw=2000.0))
    repo.salveaza_parc(parc)
    curata_modele_dupa.append(parc.id)

    index = pd.date_range("2026-01-01 00:00", periods=2000, freq="h", tz="UTC")
    semnal = _semnal_curat(index)
    fizic = _fizic_din_semnal(index, semnal, putere_instalata=2000.0)
    _seteaza_productie(repo, parc.id, index, semnal)

    monkeypatch.setattr(ml_corrector, "reconstruieste_prognoza_fizica",
                         lambda p, inceput, sfarsit: fizic)

    info = antreneaza_model(repo, parc, prag_min_ore=100)

    assert info.activ is True, f"modelul ar fi trebuit să se activeze: {info.note}"
    assert info.mae_validare_kw < info.mae_model_anterior_kw
    # fizica pură are eroare sistematică de 30% din semnal (~300+ kW) — modelul
    # antrenat trebuie să reducă asta drastic (sub jumătate din eroarea fizicii pure)
    assert info.mae_validare_kw < info.mae_model_anterior_kw * 0.5
    assert repo.model_ml_activ(parc.id) is not None
    assert repo.model_ml_activ(parc.id).id == info.id


def test_model_antrenat_pe_date_nefolositoare_nu_detroneaza_campionul(repo, monkeypatch, curata_modele_dupa):
    """R-605: un model deja activ ('campion'), antrenat pe multe date curate,
    nu trebuie detronat de un 'provocator' a cărui porțiune de ANTRENARE nu
    conține nicio relație reală între intrări (feature-uri) și rezultat
    (aici, obținută prin amestecarea producției pe zona de antrenare) — un
    astfel de candidat nu poate învăța nimic util și trebuie să iasă net mai
    slab pe validare decât campionul deja bine antrenat. Zona de VALIDARE
    (ultimele ~15%, cronologic) rămâne curată la ambii, pentru o comparație
    corectă pe date nevăzute."""
    parc = parc_eolian(config_eolian=config_eolian_simplu(numar_turbine=1, putere_nominala_kw=2000.0))
    repo.salveaza_parc(parc)
    curata_modele_dupa.append(parc.id)
    putere_instalata = 2000.0
    rng = np.random.RandomState(7)

    # --- 1) antrenăm și activăm manual un "campion" pe un set mare, curat ---
    index_mare = pd.date_range("2020-01-01 00:00", periods=3000, freq="h", tz="UTC")
    semnal_mare = _semnal_curat(index_mare)
    zgomot_mare = rng.normal(0, 150.0, size=len(index_mare))
    fizic_mare = _fizic_din_semnal(index_mare, semnal_mare, putere_instalata)
    productie_mare = semnal_mare + zgomot_mare
    reziduu_mare = productie_mare - fizic_mare["putere_ac_fizic_kw"].values
    features_mare = construieste_features(fizic_mare, Tehnologie.EOLIAN)

    model_campion = lgb.train(
        {"objective": "regression", "metric": "mae", "verbosity": -1,
         "num_leaves": 31, "learning_rate": 0.05, "min_data_in_leaf": 20},
        lgb.Dataset(features_mare, label=reziduu_mare), num_boost_round=300,
    )
    director = MODELE_DIR / parc.id
    director.mkdir(parents=True, exist_ok=True)
    cale_campion = str(director / "campion.txt")
    model_campion.save_model(cale_campion)
    campion_info = ModelMLInfo(
        id=new_id(), parc_id=parc.id, antrenat_la="2026-01-01T00:00:00+00:00", activ=True,
        mae_validare_kw=1.0, mae_model_anterior_kw=None, numar_ore_antrenare=len(index_mare),
        cale_fisier=cale_campion, note="campion sintetic pentru test",
    )
    repo.salveaza_model_ml(campion_info)
    assert repo.model_ml_activ(parc.id).id == campion_info.id

    # --- 2) construim producția "provocatorului": zona de validare (ultimele
    #     ~15% cronologic) rămâne curată; zona de antrenare e amestecată
    #     între ore, ca relația feature -> reziduu să devină neînvățabilă ---
    index_mic = pd.date_range("2026-06-01 00:00", periods=150, freq="h", tz="UTC")
    semnal_mic = _semnal_curat(index_mic)
    zgomot_mic = rng.normal(0, 150.0, size=len(index_mic))
    fizic_mic = _fizic_din_semnal(index_mic, semnal_mic, putere_instalata)
    productie_curata = semnal_mic + zgomot_mic

    n_valid = max(1, int(len(index_mic) * ml_corrector.FRACTIE_VALIDARE))
    n_train = len(index_mic) - n_valid
    productie_amestecata = productie_curata.copy()
    permutare = rng.permutation(n_train)
    productie_amestecata[:n_train] = productie_curata[:n_train][permutare]

    _seteaza_productie(repo, parc.id, index_mic, productie_amestecata)
    monkeypatch.setattr(ml_corrector, "reconstruieste_prognoza_fizica",
                         lambda p, inceput, sfarsit: fizic_mic)

    info = antreneaza_model(repo, parc, prag_min_ore=100)

    assert info.activ is False, (
        f"provocatorul antrenat pe date fără relație reală nu ar fi trebuit să bată "
        f"campionul bine antrenat — mae_candidat={info.mae_validare_kw:.2f}, "
        f"mae_baseline={info.mae_model_anterior_kw:.2f}"
    )
    assert repo.model_ml_activ(parc.id).id == campion_info.id, \
        "campionul existent trebuie să rămână activ — niciun rollback automat nedorit"


def test_fara_productie_importata_ridica_eroare_clara(repo):
    from app.core.ml_corrector import DateInsuficienteError
    parc = parc_eolian()
    repo.salveaza_parc(parc)
    with pytest.raises(DateInsuficienteError):
        antreneaza_model(repo, parc, prag_min_ore=100)


def test_sub_pragul_configurat_de_ore_ridica_eroare(repo, monkeypatch, curata_modele_dupa):
    from app.core.ml_corrector import DateInsuficienteError
    parc = parc_eolian()
    repo.salveaza_parc(parc)
    curata_modele_dupa.append(parc.id)

    index = pd.date_range("2026-01-01 00:00", periods=20, freq="h", tz="UTC")
    semnal = _semnal_curat(index)
    fizic = _fizic_din_semnal(index, semnal, 2000.0)
    _seteaza_productie(repo, parc.id, index, semnal)
    monkeypatch.setattr(ml_corrector, "reconstruieste_prognoza_fizica", lambda p, i, s: fizic)

    with pytest.raises(DateInsuficienteError, match="minim"):
        antreneaza_model(repo, parc, prag_min_ore=1500)
