"""Rubrica „Reală vs. prognoză" pentru date METEO (distinctă de cea pentru
producție) — app/web/vizualizare.py:serie_comparatie_meteo — și cache-ul
reanalizei ERA5 (app/core/weather_client.py:obtine_meteo_istoric_real_cache),
care pune în sfârșit la treabă tabela `meteo_cache` (exista în schemă, dar
nu era apelată de nimic). Testele NU ating rețeaua: cache-ul e populat
direct, sau `obtine_meteo_istoric_real` e înlocuit cu o funcție sintetică."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.core.weather_client import (SURSA_CACHE_REANALIZA,
                                      obtine_meteo_istoric_real_cache)
from app.models import OraPrognoza, Rulare, new_id
from app.web.vizualizare import serie_comparatie_meteo
from tests.helpers import parc_pv


def _payload_zi(zi: date) -> dict:
    idx = pd.date_range(f"{zi.isoformat()}T00:00:00Z", periods=24, freq="h", tz="UTC")
    return {
        "index": [ts.isoformat() for ts in idx],
        "date": {
            "shortwave_radiation": [100.0] * 24,
            "temperature_2m": [15.0] * 24,
            "wind_speed_10m": [3.0] * 24,
        },
    }


def test_cache_meteo_evita_reteaua_cand_toate_zilele_sunt_deja_in_cache(repo, monkeypatch):
    parc = parc_pv()
    repo.salveaza_parc(parc)
    zi = date(2026, 6, 1)
    repo.set_meteo_cache(parc.id, SURSA_CACHE_REANALIZA, zi.isoformat(), _payload_zi(zi))

    def _esueaza_daca_e_apelat(*a, **k):
        raise AssertionError("nu ar trebui să cheme rețeaua când ziua e deja în cache")
    import app.core.weather_client as wc
    monkeypatch.setattr(wc, "obtine_meteo_istoric_real", _esueaza_daca_e_apelat)

    rezultat = obtine_meteo_istoric_real_cache(repo, parc.id, parc.latitudine, parc.longitudine, zi, zi)
    assert len(rezultat) == 24
    assert float(rezultat["shortwave_radiation"].iloc[0]) == 100.0


def test_cache_meteo_populeaza_zilele_lipsa_din_reteaua_sintetica(repo, monkeypatch):
    parc = parc_pv()
    repo.salveaza_parc(parc)
    zi = date(2026, 6, 2)

    idx = pd.date_range(f"{zi.isoformat()}T00:00:00Z", periods=24, freq="h", tz="UTC")
    raspuns_retea = pd.DataFrame({
        "shortwave_radiation": range(24), "temperature_2m": 20.0, "wind_speed_10m": 4.0,
    }, index=idx)
    import app.core.weather_client as wc
    monkeypatch.setattr(wc, "obtine_meteo_istoric_real", lambda *a, **k: raspuns_retea)

    rezultat = obtine_meteo_istoric_real_cache(repo, parc.id, parc.latitudine, parc.longitudine, zi, zi)
    assert len(rezultat) == 24
    # ziua trebuie să fi ajuns și în cache — o a doua chemare nu mai are nevoie de rețea
    monkeypatch.setattr(wc, "obtine_meteo_istoric_real",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("ar fi trebuit să fie în cache")))
    rezultat2 = obtine_meteo_istoric_real_cache(repo, parc.id, parc.latitudine, parc.longitudine, zi, zi)
    assert len(rezultat2) == 24


def test_serie_comparatie_meteo_calculeaza_mae_pe_variabile_comune(repo, monkeypatch):
    parc = parc_pv()
    repo.salveaza_parc(parc)
    momente = pd.date_range("2026-06-10T00:00:00Z", periods=5, freq="h", tz="UTC")
    repo.salveaza_rulare(Rulare(
        id=new_id(), parc_id=parc.id, declansata_la="2026-06-09T10:00:00+00:00",
        tip="programata", stare="reusita",
        ore=[OraPrognoza(timestamp_utc=ts.isoformat(), ghi_wm2=100.0, temp_aer_c=10.0,
                          viteza_vant_ms=2.0, fractie_incarcare=0.1) for ts in momente]))

    real = pd.DataFrame({
        "shortwave_radiation": 120.0, "temperature_2m": 12.0, "wind_speed_10m": 3.0,
    }, index=momente)
    monkeypatch.setattr("app.core.weather_client.obtine_meteo_istoric_real_cache",
                         lambda *a, **k: real)

    rezultat = serie_comparatie_meteo(repo, parc, inceput_data=date(2026, 6, 10), sfarsit_data=date(2026, 6, 10))
    assert rezultat["are_date"] is True
    assert len(rezultat["randuri"]) == 5
    assert rezultat["mae_ghi"] == pytest.approx(20.0)
    assert rezultat["mae_temp"] == pytest.approx(2.0)
    assert rezultat["mae_vant"] == pytest.approx(1.0)


def test_serie_comparatie_meteo_fara_prognoza_nu_atinge_reteaua(repo, monkeypatch):
    parc = parc_pv()
    repo.salveaza_parc(parc)

    def _esueaza(*a, **k):
        raise AssertionError("fără nicio prognoză salvată nu ar trebui să se ceară deloc reanaliza")
    monkeypatch.setattr("app.core.weather_client.obtine_meteo_istoric_real_cache", _esueaza)

    rezultat = serie_comparatie_meteo(repo, parc, zile=7)
    assert rezultat["are_date"] is False
    assert rezultat["randuri"] == []


def test_ruta_comparatie_meteo_smoke(client, repo, monkeypatch):
    parc = parc_pv()
    repo.salveaza_parc(parc)
    r = client.get(f"/parcuri/{parc.id}/comparatie-meteo")
    assert r.status_code == 200
    assert "Reală vs. prognoză" in r.text


def test_intrarile_de_cache_din_conventia_veche_sunt_ignorate(repo, monkeypatch):
    """Cache-ul poartă implicit convenția de etichetare din momentul scrierii.
    Intrările de dinaintea trecerii la „eticheta = începutul intervalului" sunt
    decalate cu o oră; citite amestecat cu date noi, dau o serie meteo
    incoerentă — destul cât să strice vizibil calibrarea geometriei. Cheia e
    versionată tocmai ca ele să nu mai fie citite niciodată."""
    parc = parc_pv()
    repo.salveaza_parc(parc)
    zi = date(2026, 6, 1)
    repo.set_meteo_cache(parc.id, "reanaliza", zi.isoformat(), _payload_zi(zi))  # cheia VECHE

    apelat = {"da": False}

    def _proaspat(*a, **k):
        apelat["da"] = True
        import pandas as pd
        idx = pd.date_range(f"{zi}T00:00:00Z", periods=24, freq="h", tz="UTC")
        return pd.DataFrame({"shortwave_radiation": 200.0, "temperature_2m": 15.0,
                              "wind_speed_10m": 3.0}, index=idx)

    import app.core.weather_client as wc
    monkeypatch.setattr(wc, "obtine_meteo_istoric_real", _proaspat)

    rezultat = obtine_meteo_istoric_real_cache(repo, parc.id, parc.latitudine, parc.longitudine, zi, zi)
    assert apelat["da"], "intrarea veche trebuia ignorată, iar datele re-descărcate"
    assert float(rezultat["shortwave_radiation"].iloc[0]) == 200.0
