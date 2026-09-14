"""Orchestrarea unei rulări complete (forecast_engine.ruleaza_prognoza),
fără rețea — sursa meteo e înlocuită cu date sintetice de cer senin.

Scop principal: confirmă automat ce s-a validat manual în timpul dezvoltării
— o corecție ML anormală pe o singură oră e blocată de controalele de
calitate FĂRĂ să strice restul rulării (R-1503/R-1505, "succes parțial")."""
from __future__ import annotations

import datetime as dt_module

import pandas as pd
import pytest
from pvlib.location import Location

import app.core.forecast_engine as forecast_engine
from app.core.forecast_engine import ruleaza_prognoza
from tests.helpers import parc_pv


def _fixeaza_acum(monkeypatch, acum_utc: dt_module.datetime):
    class DatetimeFix:
        @staticmethod
        def now(tz=None):
            return acum_utc
    monkeypatch.setattr(forecast_engine, "datetime", DatetimeFix)


def _meteo_open_meteo_cer_senin(lat, lon, alt, inceput_utc, ore):
    etichete = pd.date_range(inceput_utc, periods=ore, freq="h", tz="UTC")
    loc = Location(lat, lon, altitude=alt, tz="UTC")
    momente = etichete + pd.Timedelta(minutes=30)
    cs = loc.get_clearsky(momente, model="ineichen")
    cs.index = etichete
    return pd.DataFrame({
        "shortwave_radiation": cs["ghi"], "direct_normal_irradiance": cs["dni"],
        "diffuse_radiation": cs["dhi"], "temperature_2m": 20.0, "wind_speed_10m": 2.0,
        "wind_speed_100m": 2.5, "surface_pressure": 1013.0,
    }, index=etichete)


@pytest.fixture
def parc_cu_meteo_falsa(repo, monkeypatch):
    """Parc PV + sursă meteo falsă (cer senin, fără rețea) + 'acum' fixat cu
    o zi înainte de orizontul cerut."""
    parc = parc_pv(mod_umbra=False)
    repo.salveaza_parc(parc)
    _fixeaza_acum(monkeypatch, dt_module.datetime(2026, 6, 14, 6, 0, tzinfo=dt_module.timezone.utc))

    def _fals_obtine_prognoza_meteo(lat, lon, modele, zile_trecut=0, zile_viitor=7):
        df = _meteo_open_meteo_cer_senin(lat, lon, parc.altitudine_m, "2026-06-14 00:00", (zile_viitor + 2) * 24)
        return {m: df for m in modele}

    monkeypatch.setattr(forecast_engine, "obtine_prognoza_meteo", _fals_obtine_prognoza_meteo)
    return repo, parc


def test_rulare_normala_fara_corectie_reuseste_complet(parc_cu_meteo_falsa):
    repo, parc = parc_cu_meteo_falsa
    rulare = ruleaza_prognoza(repo, parc, tip="manuala", zile_viitor=1)
    assert rulare.stare == "reusita"
    assert len(rulare.ore) > 0
    assert all(not o.blocat for o in rulare.ore)
    assert all(o.putere_finala_kw is not None for o in rulare.ore)


def test_corectie_ml_anormala_pe_o_ora_da_succes_partial_nu_crash(parc_cu_meteo_falsa, monkeypatch):
    """O 'corecție ML' care împinge o singură oră mult peste puterea
    instalată trebuie blocată DOAR pe acea oră — restul rulării rămâne
    valid, iar starea finală devine 'partiala', nu 'esuata' și nu crash."""
    repo, parc = parc_cu_meteo_falsa

    def _corectie_anormala(repo_, parc_, fizic):
        corectie = pd.Series(0.0, index=fizic.index)
        ts_anomalie = fizic.index[len(fizic.index) // 2]
        corectie[ts_anomalie] = 1_000_000.0  # împinge puterea finală mult peste orice prag plauzibil
        return corectie, "propriu", None, {}

    monkeypatch.setattr(forecast_engine, "gaseste_si_aplica_corectie", _corectie_anormala)

    rulare = ruleaza_prognoza(repo, parc, tip="manuala", zile_viitor=1)

    assert rulare.stare == "partiala", f"stare neașteptată: {rulare.stare} / {rulare.mesaj}"
    ore_blocate = [o for o in rulare.ore if o.blocat]
    ore_valide = [o for o in rulare.ore if not o.blocat]
    assert len(ore_blocate) == 1, "doar ora cu anomalia trebuie blocată"
    assert len(ore_valide) > 0, "restul orelor trebuie să rămână livrate normal"
    for o in ore_blocate:
        assert o.putere_finala_kw is None
        assert any("Putere peste puterea instalată" in a for a in o.avertismente)
    for o in ore_valide:
        assert o.putere_finala_kw is not None

    # rularea (parțial reușită) trebuie totuși salvată în repository, nu pierdută
    salvata = repo.get_rulare(rulare.id)
    assert salvata is not None
    assert salvata.stare == "partiala"


def test_corectie_ml_negativa_mare_e_taiata_la_zero_nu_blocata(parc_cu_meteo_falsa, monkeypatch):
    """Corecția finală e limitată doar inferior (.clip(lower=0)) — o corecție
    puternic negativă trebuie să ducă la putere zero, nu la o valoare
    negativă și nu neapărat blocată (0 e fizic plauzibil)."""
    repo, parc = parc_cu_meteo_falsa

    def _corectie_negativa(repo_, parc_, fizic):
        corectie = pd.Series(-1_000_000.0, index=fizic.index)
        return corectie, "propriu", None, {}

    monkeypatch.setattr(forecast_engine, "gaseste_si_aplica_corectie", _corectie_negativa)
    rulare = ruleaza_prognoza(repo, parc, tip="manuala", zile_viitor=1)

    for o in rulare.ore:
        if not o.blocat:
            assert o.putere_finala_kw == 0.0


def test_toate_orele_blocate_da_stare_esuata(parc_cu_meteo_falsa, monkeypatch):
    def _corectie_uriasa_peste_tot(repo_, parc_, fizic):
        corectie = pd.Series(1_000_000.0, index=fizic.index)
        return corectie, "propriu", None, {}

    monkeypatch.setattr(forecast_engine, "gaseste_si_aplica_corectie", _corectie_uriasa_peste_tot)
    rulare = ruleaza_prognoza(repo=parc_cu_meteo_falsa[0], parc=parc_cu_meteo_falsa[1],
                               tip="manuala", zile_viitor=1)
    assert rulare.stare == "esuata"
    assert all(o.blocat for o in rulare.ore)
    assert all(o.putere_finala_kw is None for o in rulare.ore)
