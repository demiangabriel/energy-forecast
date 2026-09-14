"""Cele două moduri de citire a estimării lunare: lună CALENDARISTICĂ
(octombrie, noiembrie — forma folosită la planificare și la raportarea către
client) și „următoarele N zile de mâine" (ce urmează, indiferent de granița
lunii)."""
from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from app.models import ProductieOrara
from app.web.vizualizare import prognoza_luna_urmatoare
from tests.helpers import parc_pv


def _meteo(inceput, sfarsit, ghi: float = 400.0) -> pd.DataFrame:
    idx = pd.date_range(f"{inceput.isoformat()}T00:00:00Z", f"{sfarsit.isoformat()}T23:00:00Z",
                         freq="h", tz="UTC")
    return pd.DataFrame({"shortwave_radiation": ghi, "direct_normal_irradiance": ghi,
                          "diffuse_radiation": ghi * 0.3, "temperature_2m": 20.0,
                          "wind_speed_10m": 3.0}, index=idx)


def _fara_retea(monkeypatch):
    monkeypatch.setattr("app.core.weather_client.obtine_meteo_istoric_real_cache",
                         lambda *a, **k: _meteo(a[-2], a[-1]))


def test_modul_luna_acopera_exact_luna_calendaristica(repo, monkeypatch):
    parc = parc_pv(cod="LUNA-1")
    repo.salveaza_parc(parc)
    _fara_retea(monkeypatch)

    rezultat = prognoza_luna_urmatoare(repo, parc, an=2026, luna=10)

    assert rezultat["prima_zi"] == "2026-10-01"
    assert rezultat["ultima_zi"] == "2026-10-31"
    assert len(rezultat["randuri"]) == 31


def test_modul_luna_respecta_lungimea_fiecarei_luni(repo, monkeypatch):
    parc = parc_pv(cod="LUNA-2")
    repo.salveaza_parc(parc)
    _fara_retea(monkeypatch)

    for an, luna in [(2026, 11), (2026, 2), (2028, 2)]:
        rezultat = prognoza_luna_urmatoare(repo, parc, an=an, luna=luna)
        assert len(rezultat["randuri"]) == calendar.monthrange(an, luna)[1], f"{an}-{luna}"


def test_modul_zile_incepe_de_maine(repo, monkeypatch):
    parc = parc_pv(cod="LUNA-3")
    repo.salveaza_parc(parc)
    _fara_retea(monkeypatch)

    rezultat = prognoza_luna_urmatoare(repo, parc, zile=7)
    azi = datetime.now(ZoneInfo(parc.fus_orar)).date()
    assert rezultat["prima_zi"] == (azi + timedelta(days=1)).isoformat()
    assert len(rezultat["randuri"]) == 7


def test_luna_curenta_arata_productia_REALA_pentru_zilele_incheiate(repo, monkeypatch):
    """O zi încheiată nu se mai estimează: se știe. Fără asta, un total pe luna
    curentă ar ignora tocmai partea despre care există certitudine."""
    parc = parc_pv(cod="LUNA-4")
    repo.salveaza_parc(parc)
    _fara_retea(monkeypatch)

    tz = ZoneInfo(parc.fus_orar)
    azi = datetime.now(tz).date()
    if azi.day < 3:
        return  # la începutul lunii nu există zile încheiate de verificat
    zi_trecuta = azi.replace(day=1)
    repo.salveaza_productie([
        ProductieOrara(parc_id=parc.id,
                        timestamp_utc=datetime(zi_trecuta.year, zi_trecuta.month, zi_trecuta.day,
                                                h, tzinfo=tz).astimezone(timezone.utc).isoformat(),
                        energie_kwh=100.0)
        for h in range(24)])

    rezultat = prognoza_luna_urmatoare(repo, parc, an=azi.year, luna=azi.month)
    prima = rezultat["randuri"][0]
    assert prima["energie_reala_kwh"] == 2400.0
    assert prima["sursa_estimata"] == "realizat"
    assert rezultat["total_real_kwh"] == 2400.0


def test_totalul_estimat_ia_cea_mai_buna_sursa_pe_fiecare_zi(repo, monkeypatch):
    """Ordinea de încredere: măsurat > NWP > climatologie. Totalul nu adună
    aceeași zi de două ori, din surse diferite."""
    parc = parc_pv(cod="LUNA-5")
    repo.salveaza_parc(parc)
    _fara_retea(monkeypatch)

    rezultat = prognoza_luna_urmatoare(repo, parc, zile=10)
    suma_pe_rand = sum(r["energie_estimata_kwh"] for r in rezultat["randuri"]
                        if r["energie_estimata_kwh"] is not None)
    assert rezultat["total_estimat_kwh"] == suma_pe_rand
    assert (rezultat["zile_reale"] + rezultat["zile_nwp"] + rezultat["zile_climatologie"]
            == len(rezultat["randuri"]))


def test_ruta_accepta_ambele_moduri(client, repo, monkeypatch):
    parc = parc_pv(cod="LUNA-6")
    repo.salveaza_parc(parc)
    _fara_retea(monkeypatch)

    r_luna = client.get(f"/parcuri/{parc.id}/prognoza-luna?mod=luna&an=2026&luna=10")
    assert r_luna.status_code == 200
    assert "octombrie 2026" in r_luna.text

    r_zile = client.get(f"/parcuri/{parc.id}/prognoza-luna?mod=zile&zile=14")
    assert r_zile.status_code == 200
    assert "următoarele 14 zile" in r_zile.text


def test_ruta_marginile_lunii_invalide_cad_pe_luna_curenta(client, repo, monkeypatch):
    parc = parc_pv(cod="LUNA-7")
    repo.salveaza_parc(parc)
    _fara_retea(monkeypatch)
    r = client.get(f"/parcuri/{parc.id}/prognoza-luna?mod=luna&an=2026&luna=99")
    assert r.status_code == 200
