"""Zona de prognoză pentru următoarea lună — R-906 spune explicit că prognoza
meteo numerică nu e fiabilă dincolo de ~16 zile, deci restul lunii nu poate fi
o prognoză meteorologică propriu-zisă. `forecast_climatologie.py` oferă în loc
o estimare climatologică (media producției din vremea REALĂ — ERA5 — din
aceleași zile calendaristice ale anilor anteriori), iar
`vizualizare.prognoza_luna_urmatoare` o combină cu prognoza NWP reală acolo
unde există. Testele NU ating rețeaua — `obtine_meteo_istoric_real_cache` e
înlocuită cu o funcție sintetică (același tipar ca `test_comparatie_meteo.py`)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.core.forecast_climatologie import _data_analoaga, prognoza_climatologica
from app.models import OraPrognoza, Rulare, new_id
from app.web.vizualizare import prognoza_luna_urmatoare
from tests.helpers import parc_pv


def _meteo_zi_constanta(inceput: date, sfarsit: date, ghi: float = 400.0) -> pd.DataFrame:
    idx = pd.date_range(f"{inceput.isoformat()}T00:00:00Z",
                         f"{sfarsit.isoformat()}T23:00:00Z", freq="h", tz="UTC")
    return pd.DataFrame({
        "shortwave_radiation": ghi, "direct_normal_irradiance": ghi, "diffuse_radiation": ghi * 0.3,
        "temperature_2m": 20.0, "wind_speed_10m": 3.0,
    }, index=idx)


def test_data_analoaga_scade_anii_pastreaza_ziua():
    assert _data_analoaga(date(2026, 10, 15), 1) == date(2025, 10, 15)
    assert _data_analoaga(date(2026, 10, 15), 3) == date(2023, 10, 15)


def test_data_analoaga_29_februarie_cade_pe_28_in_an_nebisect():
    assert _data_analoaga(date(2024, 2, 29), 1) == date(2023, 2, 28)


def test_prognoza_climatologica_mediaza_pe_anii_disponibili(repo, monkeypatch):
    parc = parc_pv(cod="PV-CLIMA")
    repo.salveaza_parc(parc)

    def _meteo_sintetica(repo_, parc_id, lat, lon, inceput, sfarsit):
        # ghi diferit pe fiecare an-analog, ca media să fie verificabilă
        ghi = {2024: 300.0, 2025: 500.0}.get(inceput.year, 400.0)
        return _meteo_zi_constanta(inceput, sfarsit, ghi=ghi)

    monkeypatch.setattr("app.core.weather_client.obtine_meteo_istoric_real_cache", _meteo_sintetica)

    rezultat = prognoza_climatologica(repo, parc, date(2026, 6, 10), date(2026, 6, 10), ani_istoric=2)
    assert rezultat["are_date"] is True
    rand = rezultat["randuri"][0]
    assert rand["data"] == "2026-06-10"
    assert rand["numar_ani_folositi"] == 2
    # media a doi ani cu GHI diferit -> energia estimată trebuie să fie STRICT
    # între cea calculată separat pentru fiecare an (nu egală cu niciuna)
    assert rand["energie_min_kwh"] < rand["energie_estimata_kwh"] < rand["energie_max_kwh"]


def test_prognoza_climatologica_an_lipsa_nu_opreste_calculul(repo, monkeypatch):
    parc = parc_pv(cod="PV-CLIMA2")
    repo.salveaza_parc(parc)

    def _doar_un_an(repo_, parc_id, lat, lon, inceput, sfarsit):
        if inceput.year == date.today().year - 1:
            raise RuntimeError("simulare eșec rețea pentru acest an")
        return _meteo_zi_constanta(inceput, sfarsit)

    monkeypatch.setattr("app.core.weather_client.obtine_meteo_istoric_real_cache", _doar_un_an)

    rezultat = prognoza_climatologica(repo, parc, date(2026, 7, 1), date(2026, 7, 2), ani_istoric=3)
    assert rezultat["are_date"] is True
    assert all(r["numar_ani_folositi"] <= 2 for r in rezultat["randuri"])


def test_prognoza_climatologica_fara_nicio_sursa_marcheaza_zilele_fara_date(repo, monkeypatch):
    parc = parc_pv(cod="PV-CLIMA3")
    repo.salveaza_parc(parc)
    monkeypatch.setattr("app.core.weather_client.obtine_meteo_istoric_real_cache",
                         lambda *a, **k: (_ for _ in ()).throw(RuntimeError("indisponibil")))

    rezultat = prognoza_climatologica(repo, parc, date(2026, 8, 1), date(2026, 8, 1), ani_istoric=2)
    assert rezultat["are_date"] is False
    assert rezultat["randuri"][0]["numar_ani_folositi"] == 0
    assert rezultat["randuri"][0]["energie_estimata_kwh"] is None
    assert rezultat["zile_fara_date"] == 1


# --------------------------------------------------------------------------- prognoza_luna_urmatoare (coloane paralele)
def test_prognoza_luna_arata_nwp_si_climatologie_in_paralel(repo, monkeypatch):
    """Cerință: NWP și climatologie apar ca DOUĂ valori separate pe fiecare
    zi (nu combinate într-un singur rând care alege o sursă) — astfel
    operatorul poate compara direct cele două estimări chiar și pentru
    zilele acoperite de NWP."""
    parc = parc_pv(cod="PV-LUNA")
    repo.salveaza_parc(parc)

    tz = ZoneInfo(parc.fus_orar)
    azi_local = datetime.now(timezone.utc).astimezone(tz).date()
    # o rulare recentă care acoperă doar primele 3 zile viitoare (limitele zilei LOCALE parcului)
    ore = []
    for i in range(1, 4):
        zi_locala = azi_local + timedelta(days=i)
        for h in range(24):
            ts_local = datetime(zi_locala.year, zi_locala.month, zi_locala.day, h, tzinfo=tz)
            ore.append(OraPrognoza(timestamp_utc=ts_local.astimezone(timezone.utc).isoformat(), putere_finala_kw=10.0))
    repo.salveaza_rulare(Rulare(id=new_id(), parc_id=parc.id, declansata_la=datetime.now(timezone.utc).isoformat(),
                                 tip="manuala", stare="reusita", ore=ore))

    monkeypatch.setattr("app.core.weather_client.obtine_meteo_istoric_real_cache",
                         lambda *a, **k: _meteo_zi_constanta(a[-2], a[-1]))

    rezultat = prognoza_luna_urmatoare(repo, parc, zile=10)
    assert len(rezultat["randuri"]) == 10

    # primele 3 zile -> au și NWP, restul -> doar climatologie
    zile_cu_nwp = [r for r in rezultat["randuri"] if r["energie_nwp_kwh"] is not None]
    assert len(zile_cu_nwp) == 3
    assert rezultat["zile_nwp"] == 3
    assert rezultat["zile_climatologie"] == 7
    assert rezultat["total_nwp_kwh"] == pytest.approx(24 * 10.0 * 3, rel=0.01)

    # climatologia se calculează pentru TOATE cele 10 zile, inclusiv cele
    # acoperite de NWP — nu doar pentru zilele lipsă (altfel comparația
    # NWP-vs-climatologie n-ar fi posibilă pe zilele apropiate)
    assert all(r["energie_climatologie_kwh"] is not None for r in rezultat["randuri"])
    for r in zile_cu_nwp:
        assert r["energie_nwp_kwh"] is not None
        assert r["energie_climatologie_kwh"] is not None
        assert r["sursa_estimata"] == "nwp"


def test_prognoza_luna_fara_nicio_rulare_e_toata_climatologie(repo, monkeypatch):
    parc = parc_pv(cod="PV-LUNA2")
    repo.salveaza_parc(parc)
    monkeypatch.setattr("app.core.weather_client.obtine_meteo_istoric_real_cache",
                         lambda *a, **k: _meteo_zi_constanta(a[-2], a[-1]))

    rezultat = prognoza_luna_urmatoare(repo, parc, zile=5)
    assert rezultat["are_rulare"] is False
    assert rezultat["zile_nwp"] == 0
    assert rezultat["zile_climatologie"] == 5


def test_ruta_prognoza_luna_smoke(client, repo, monkeypatch):
    parc = parc_pv(cod="PV-LUNA3")
    repo.salveaza_parc(parc)
    monkeypatch.setattr("app.core.weather_client.obtine_meteo_istoric_real_cache",
                         lambda *a, **k: _meteo_zi_constanta(a[-2], a[-1]))
    r = client.get(f"/parcuri/{parc.id}/prognoza-luna")
    assert r.status_code == 200
    assert "următoarea lună" in r.text


# --------------------------------------------------------------------------- orizont NWP extins


def test_nwp_se_aduna_din_toate_rularile_recente_nu_doar_din_ultima(repo, monkeypatch):
    """Rularea zilnică salvează doar orizontul implicit (3 zile). Dacă pagina
    ar privi o singură rulare, zilele 4-16 calculate de o rulare manuală mai
    lungă s-ar pierde și ar apărea ca „climatologie", deși există prognoză
    meteo reală pentru ele."""
    parc = parc_pv(cod="PV-NWP")
    repo.salveaza_parc(parc)
    tz = ZoneInfo(parc.fus_orar)
    azi = datetime.now(timezone.utc).astimezone(tz).date()
    acum = datetime.now(timezone.utc)

    def ore_pentru(zile: range) -> list[OraPrognoza]:
        ore = []
        for i in zile:
            zi = azi + timedelta(days=i)
            for h in range(24):
                ts = datetime(zi.year, zi.month, zi.day, h, tzinfo=tz)
                ore.append(OraPrognoza(timestamp_utc=ts.astimezone(timezone.utc).isoformat(),
                                        putere_finala_kw=10.0))
        return ore

    # rulare manuală mai VECHE, dar pe 10 zile
    repo.salveaza_rulare(Rulare(id=new_id(), parc_id=parc.id,
                                 declansata_la=(acum - timedelta(hours=6)).isoformat(),
                                 tip="manuala", stare="reusita", ore=ore_pentru(range(1, 11))))
    # rulare zilnică mai NOUĂ, dar doar pe 3 zile
    repo.salveaza_rulare(Rulare(id=new_id(), parc_id=parc.id, declansata_la=acum.isoformat(),
                                 tip="programata", stare="reusita", ore=ore_pentru(range(1, 4))))

    monkeypatch.setattr("app.core.weather_client.obtine_meteo_istoric_real_cache",
                         lambda *a, **k: _meteo_zi_constanta(a[-2], a[-1]))

    rezultat = prognoza_luna_urmatoare(repo, parc, zile=15)
    assert rezultat["zile_nwp"] == 10, (
        f"trebuia să adune 10 zile de NWP din ambele rulări, nu doar 3: "
        f"{rezultat['zile_nwp']}")


def test_ruta_de_extindere_cere_orizontul_maxim(client, repo, monkeypatch):
    """Butonul „Calculează NWP pe N zile" trebuie să ceară chiar orizontul
    maxim configurat, nu orizontul implicit al rulării zilnice."""
    parc = parc_pv(cod="PV-EXT", mod_umbra=False)
    repo.salveaza_parc(parc)

    cerut = {}

    def prognoza_falsa(repo_, parc_, tip="manuala", zile_viitor=None):
        cerut["zile"] = zile_viitor
        return Rulare(id=new_id(), parc_id=parc_.id, declansata_la="2026-09-12T06:00:00+00:00",
                       tip=tip, stare="reusita",
                       ore=[OraPrognoza(timestamp_utc=f"2026-09-{13 + d:02d}T10:00:00+00:00",
                                         putere_finala_kw=5.0) for d in range(16)])

    import app.web.routes_panou as rp
    monkeypatch.setattr(rp, "ruleaza_prognoza", prognoza_falsa)

    r = client.post(f"/parcuri/{parc.id}/prognoza-luna/extinde", follow_redirects=False)
    assert r.status_code == 303
    assert cerut["zile"] == 16, f"trebuia cerut orizontul maxim (16), s-a cerut {cerut['zile']}"
    assert "16%20zile" in r.headers["location"] or "16 zile" in r.headers["location"]


def test_ruta_de_extindere_raporteaza_esecul_meteo(client, repo, monkeypatch):
    parc = parc_pv(cod="PV-EXT2", mod_umbra=False)
    repo.salveaza_parc(parc)

    import app.web.routes_panou as rp
    monkeypatch.setattr(rp, "ruleaza_prognoza", lambda *a, **k: Rulare(
        id=new_id(), parc_id=parc.id, declansata_la="2026-09-12T06:00:00+00:00",
        tip="manuala", stare="esuata", mesaj="Sursa meteo nu a răspuns"))

    r = client.post(f"/parcuri/{parc.id}/prognoza-luna/extinde", follow_redirects=False)
    assert r.status_code == 303
    assert "eroare=" in r.headers["location"]
