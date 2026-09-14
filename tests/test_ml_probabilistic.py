"""Stratul ML v2: țintă normalizată, excluderea orelor cu probleme de
calitate, promovare pe scor de skill și benzi de incertitudine calibrate."""
from __future__ import annotations

import shutil

import numpy as np
import pandas as pd
import pytest

import app.core.ml_corrector as mlc
from app.core.clearsky import putere_referinta
from app.core.meteo_mapping import pregateste_meteo_pv
from app.core.ml_corrector import (MODELE_DIR, NIVELURI_CUANTILE, antreneaza_model,
                                    construieste_features, gaseste_si_aplica_corectie,
                                    pregateste_cadru)
from app.core.pv_model import calculeaza_productie_pv
from app.models import CalitateOra, ProductieOrara, SteagCalitate, Tehnologie
from tests.helpers import parc_pv

LAT, LON, ALT = 44.43, 26.10, 80.0


@pytest.fixture
def curata_modele():
    ids = []
    yield ids
    for pid in ids:
        shutil.rmtree(MODELE_DIR / pid, ignore_errors=True)
    try:
        MODELE_DIR.rmdir()
    except OSError:
        pass


def _meteo(index: pd.DatetimeIndex, seed: int = 0) -> pd.DataFrame:
    from pvlib.location import Location
    cs = Location(LAT, LON, altitude=ALT, tz="UTC").get_clearsky(
        index + pd.Timedelta(minutes=30), model="ineichen")
    cs.index = index
    rng = np.random.RandomState(seed)
    nori = pd.Series(rng.uniform(0, 70, len(index)), index=index)
    return pd.DataFrame({
        "shortwave_radiation": (cs["ghi"] * (1 - nori / 150)).clip(lower=0),
        "direct_normal_irradiance": (cs["dni"] * (1 - nori / 110)).clip(lower=0),
        "diffuse_radiation": cs["dhi"],
        "temperature_2m": 18.0, "wind_speed_10m": 3.0, "wind_speed_100m": 5.0,
        "surface_pressure": 1013.0, "cloud_cover": nori, "cloud_cover_low": nori * 0.6,
        "relative_humidity_2m": 60.0,
    }, index=index)


def _cadru(parc, index: pd.DatetimeIndex, seed: int = 0) -> pd.DataFrame:
    medie = _meteo(index, seed)
    fizic = calculeaza_productie_pv(parc.config_pv, LAT, LON, ALT, pregateste_meteo_pv(medie))
    referinta = putere_referinta(parc, index, medie["temperature_2m"], medie["wind_speed_10m"])
    dispersie = pd.DataFrame({"shortwave_radiation": 25.0, "wind_speed_10m": 0.5}, index=index)
    return pregateste_cadru(fizic, medie, dispersie, referinta)


def _instaleaza_istoric(monkeypatch, parc, index):
    """Reconstrucția istorică e înlocuită cu același lanț fizic, determinist —
    testele nu depind de rețea."""
    cadru = _cadru(parc, index)
    monkeypatch.setattr(mlc, "reconstruieste_prognoza_fizica", lambda p, i, s: cadru)
    return cadru


def test_featurile_contin_contextul_temporal_si_meteo():
    """Erorile de SINCRONIZARE ale modelului meteo (norii vin cu o oră mai
    devreme decât prezis) se pot corecta doar dacă modelul vede și valorile
    vecine în timp, nu doar ora curentă."""
    parc = parc_pv()
    index = pd.date_range("2026-06-01", periods=72, freq="h", tz="UTC")
    cadru = _cadru(parc, index)
    f = construieste_features(cadru, Tehnologie.FOTOVOLTAIC)

    assert "fractie_incarcare" in f.columns, "R-604: obligatorie"
    assert "indice_senin_nwp" in f.columns
    assert any(c.startswith("ghi_nwp_dec") for c in f.columns), "lipsesc decalajele temporale"
    assert "ghi_nwp_delta1" in f.columns, "lipsește diferența temporală"
    assert "nori_total_pct" in f.columns
    assert f.notna().all().all(), "niciun NaN nu trebuie să ajungă în model"


def test_featurile_eoliene_includ_directia_ca_pereche_sin_cos():
    """Direcția vântului e o mărime circulară: 359° și 1° sunt vecine, ceea
    ce un singur număr nu poate exprima."""
    index = pd.date_range("2026-03-01", periods=24, freq="h", tz="UTC")
    cadru = pd.DataFrame({
        "putere_ac_fizic_kw": 100.0, "fractie_incarcare": 0.3,
        "viteza_echivalenta_ms": 8.0, "viteza_butuc_ms": 9.0,
        "directie_vant_grade": np.linspace(0, 359, 24), "exponent_forfecare": 0.14,
        "densitate_aer": 1.22,
    }, index=index)
    f = construieste_features(cadru, Tehnologie.EOLIAN)
    assert {"directie_sin", "directie_cos", "exponent_forfecare"}.issubset(f.columns)
    assert abs(f["directie_sin"].iloc[0] - f["directie_sin"].iloc[-1]) < 0.05


def test_modelul_invata_un_bias_sistematic_si_se_activeaza(repo, monkeypatch, curata_modele):
    """Cazul pentru care există stratul ML: fizica subestimează sistematic
    (ex. o umbrire nedeclarată). Modelul trebuie să învețe corecția și să
    bată atât fizica pură, cât și prognoza de referință."""
    parc = parc_pv()
    repo.salveaza_parc(parc)
    curata_modele.append(parc.id)

    index = pd.date_range("2026-01-01", periods=24 * 90, freq="h", tz="UTC")
    cadru = _instaleaza_istoric(monkeypatch, parc, index)

    rng = np.random.RandomState(4)
    # producția reală = fizica × 1,18 + zgomot mic -> bias clar învățabil
    reala = (cadru["putere_ac_fizic_kw"] * 1.18 +
             rng.normal(0, 3, len(cadru))).clip(lower=0)
    repo.salveaza_productie([ProductieOrara(parc_id=parc.id, timestamp_utc=ts.isoformat(),
                                             energie_kwh=float(v)) for ts, v in reala.items()])

    info = antreneaza_model(repo, parc, prag_min_ore=200)

    assert info.activ is True, f"modelul ar fi trebuit activat: {info.note}"
    assert info.mae_validare_kw < info.mae_model_anterior_kw
    assert info.tinta_antrenare == "reziduu_normalizat"
    assert info.skill_vs_referinta is not None and info.skill_vs_referinta > 0


def test_orele_marcate_de_calitate_sunt_excluse_din_antrenare(repo, monkeypatch, curata_modele):
    """Dacă orele de indisponibilitate ar intra în antrenare, modelul ar
    învăța „uneori producția scade fără motiv meteorologic" și ar împrăștia
    asta în toate prognozele.

    Nu verifică doar NUMĂRUL de ore raportate ca excluse (`numar_ore_excluse_qc`)
    — care ar trece și dacă exclude erau doar NUMĂRATE, nu efectiv scoase din
    antrenare — ci „otrăvește" chiar valorile marcate cu producție absurdă
    (de 20× puterea instalată) și verifică că modelul tot antrenează un skill
    bun: dacă acele ore ar mai fi folosite la antrenare, reziduul otrăvit ar
    distorsiona relația învățată și ar strica sistematic predicțiile pe
    orele de validare (care sunt ulterioare, cronologic, celor otrăvite)."""
    parc = parc_pv()
    repo.salveaza_parc(parc)
    curata_modele.append(parc.id)

    index = pd.date_range("2026-01-01", periods=24 * 60, freq="h", tz="UTC")
    cadru = _instaleaza_istoric(monkeypatch, parc, index)
    rng = np.random.RandomState(9)
    reala = (cadru["putere_ac_fizic_kw"] * 1.1 + rng.normal(0, 2, len(cadru))).clip(lower=0)

    # Ore ferm de zi în ianuarie la 44°N: `numar_ore_excluse_qc` numără doar
    # orele excluse care erau oricum UTILE (cele de noapte sunt scoase separat),
    # iar intervalul 15:00-16:00 cade deja după apus — l-am evita ca marginea
    # amurgului să nu decidă rezultatul testului.
    de_exclus = [ts for ts in index[:400] if 9 <= ts.hour <= 14][:50]
    putere_instalata = float(parc.config_pv.putere_ac_kw)
    reala.loc[de_exclus] = putere_instalata * 20.0  # valoare aberantă, imposibilă fizic

    repo.salveaza_productie([ProductieOrara(parc_id=parc.id, timestamp_utc=ts.isoformat(),
                                             energie_kwh=float(v)) for ts, v in reala.items()])
    repo.salveaza_calitate_productie([
        CalitateOra(parc_id=parc.id, timestamp_utc=ts.isoformat(),
                     steag=SteagCalitate.INDISPONIBILITATE.value, detaliu="test")
        for ts in de_exclus])

    info = antreneaza_model(repo, parc, prag_min_ore=100)
    assert info.numar_ore_excluse_qc == len(de_exclus)
    # dacă orele otrăvite ar fi rămas în antrenare, skill-ul ar fi puternic
    # negativ (modelul ar fi învățat să prezică sporadic o supraproducție de
    # 20×) — un skill pozitiv dovedește că au fost efectiv scoase din `cadru`,
    # nu doar numărate.
    assert info.skill_vs_referinta is not None and info.skill_vs_referinta > 0
    assert info.mae_validare_kw < putere_instalata * 0.5


def test_cuantilele_sunt_antrenate_ordonate_si_calibrate(repo, monkeypatch, curata_modele):
    """Benzile P10/P90 trebuie să existe, să fie ordonate (P10 ≤ P50 ≤ P90) și
    să încadreze realizatul aproximativ în proporția promisă."""
    parc = parc_pv()
    repo.salveaza_parc(parc)
    curata_modele.append(parc.id)

    index = pd.date_range("2026-01-01", periods=24 * 120, freq="h", tz="UTC")
    cadru = _instaleaza_istoric(monkeypatch, parc, index)
    rng = np.random.RandomState(21)
    reala = (cadru["putere_ac_fizic_kw"] * 1.15
             + rng.normal(0, 0.05, len(cadru)) * cadru["putere_ac_fizic_kw"]).clip(lower=0)
    repo.salveaza_productie([ProductieOrara(parc_id=parc.id, timestamp_utc=ts.isoformat(),
                                             energie_kwh=float(v)) for ts, v in reala.items()])

    info = antreneaza_model(repo, parc, prag_min_ore=200)
    assert len(info.cai_cuantile) == len(NIVELURI_CUANTILE)
    assert set(info.corectie_conforma_kw) == {str(n) for n in NIVELURI_CUANTILE}

    if not info.activ:
        pytest.skip("modelul nu a fost activat pe acest set; cuantilele se aplică doar modelului activ")

    corectie, sursa, mesaj, cuantile = gaseste_si_aplica_corectie(repo, parc, cadru.tail(500))
    assert corectie is not None and sursa == "propriu"
    assert {"0.1", "0.5", "0.9"}.issubset(cuantile)

    p10, p50, p90 = cuantile["0.1"], cuantile["0.5"], cuantile["0.9"]
    assert (p10 <= p50 + 1e-6).all(), "cuantilele nu trebuie să se încrucișeze"
    assert (p50 <= p90 + 1e-6).all()

    realizat = reala.reindex(p10.index)
    zi = cadru["putere_clearsky_kw"].reindex(p10.index) > parc.config_pv.putere_ac_kw * 0.02
    inauntru = ((realizat >= p10) & (realizat <= p90))[zi]
    assert inauntru.mean() > 0.5, f"acoperire prea mică: {inauntru.mean():.2f}"


def test_modelul_imprumutat_se_foloseste_doar_daca_donatorul_are_model(repo, monkeypatch, curata_modele):
    """R-622: dacă parcul-sursă nu are el însuși model activ, corecția NU se
    aplică și operatorul primește un mesaj explicit — niciodată o corecție
    tăcută cu un model inexistent."""
    donator, beneficiar = parc_pv(cod="DON"), parc_pv(cod="BEN")
    beneficiar.parc_imprumut_corector_id = donator.id
    repo.salveaza_parc(donator)
    repo.salveaza_parc(beneficiar)

    index = pd.date_range("2026-05-01", periods=48, freq="h", tz="UTC")
    cadru = _cadru(beneficiar, index)
    corectie, sursa, mesaj, cuantile = gaseste_si_aplica_corectie(repo, beneficiar, cadru)
    assert corectie is None and sursa is None
    assert mesaj and "R-622" in mesaj


def test_corectia_ml_e_zero_noaptea_chiar_daca_modelul_ar_prezice_altceva(repo, curata_modele):
    """R-xxx — mască defensivă la INFERENȚĂ: modelul PV e antrenat DOAR pe ore
    de zi (`masca_ore_utile`, la antrenare) — orele de noapte nu au fost
    NICIODATĂ văzute la antrenare, deci o predicție a lui acolo e o
    extrapolare necontrolată. Construim manual un model care „vrea" să
    corecteze cu o valoare mare CONSTANT, pe orice intrare (inclusiv noaptea)
    — fără mască, asta ar produce o corecție de zeci de kW noaptea, exact
    simptomul raportat („producție semnificativă calculată în timpul
    nopții"). Cu masca aplicată, corecția trebuie să fie EXACT 0 pe orele de
    noapte, indiferent ce prezice modelul."""
    import lightgbm as lgb
    from app.core.clearsky import masca_ore_utile
    from app.models import ModelMLInfo, new_id

    parc = parc_pv()
    repo.salveaza_parc(parc)
    curata_modele.append(parc.id)

    index = pd.date_range("2026-06-01 00:00", periods=48, freq="h", tz="UTC")
    cadru = _cadru(parc, index)
    features = construieste_features(cadru, Tehnologie.FOTOVOLTAIC)

    # etichetă CONSTANTĂ — modelul învață trivial să prezică mereu aceeași
    # valoare mare, indiferent de intrare (inclusiv la ore de noapte, complet
    # nevăzute la o antrenare reală, dar aici forțăm exact acest scenariu)
    eticheta = np.full(len(features), 5.0)
    model = lgb.train(
        {"objective": "regression", "metric": "mae", "verbosity": -1, "min_data_in_leaf": 1},
        lgb.Dataset(features, label=eticheta), num_boost_round=50,
    )
    director = MODELE_DIR / parc.id
    director.mkdir(parents=True, exist_ok=True)
    cale = str(director / "model_noapte.txt")
    model.save_model(cale)
    info = ModelMLInfo(
        id=new_id(), parc_id=parc.id, antrenat_la="2026-06-01T00:00:00+00:00", activ=True,
        mae_validare_kw=1.0, mae_model_anterior_kw=None, numar_ore_antrenare=len(index),
        cale_fisier=cale, tinta_antrenare="reziduu_normalizat", note="model sintetic — testează masca de noapte",
    )
    repo.salveaza_model_ml(info)

    corectie, sursa, mesaj, cuantile = gaseste_si_aplica_corectie(repo, parc, cadru)
    assert corectie is not None and sursa == "propriu"

    putere_instalata = float(parc.config_pv.putere_ac_kw)
    ore_utile = masca_ore_utile(parc, cadru["putere_clearsky_kw"], putere_instalata)
    ore_noapte = ~ore_utile

    assert ore_noapte.any(), "testul are nevoie de cel puțin o oră de noapte în fereastra aleasă"
    assert ore_utile.any(), "testul are nevoie de cel puțin o oră de zi în fereastra aleasă"
    assert (corectie[ore_noapte] == 0.0).all(), \
        f"corecția ar trebui forțată la 0 noaptea, dar: {corectie[ore_noapte]}"
    # dovada că masca e ȚINTITĂ (nu suprimă corecția peste tot): ziua, unde
    # modelul chiar a fost antrenat, corecția trebuie să rămână efectivă
    assert (corectie[ore_utile].abs() > 0.01).any(), \
        "masca nu ar trebui să anuleze și corecția din timpul zilei"


def test_fara_model_activ_se_foloseste_fizica_pura(repo):
    parc = parc_pv()
    repo.salveaza_parc(parc)
    index = pd.date_range("2026-05-01", periods=24, freq="h", tz="UTC")
    corectie, sursa, mesaj, cuantile = gaseste_si_aplica_corectie(repo, parc, _cadru(parc, index))
    assert corectie is None and sursa is None and mesaj is None and cuantile == {}
