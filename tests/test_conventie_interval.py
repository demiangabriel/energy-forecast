"""Convenția R-512: eticheta unui interval e ÎNCEPUTUL lui.

Ora 12:00 înseamnă intervalul 12:00-13:00 — convenția piețelor de energie
(PZU/OPCOM, ENTSO-E) și a contoarelor, aceeași peste tot în platformă: la
import, în prognoză, în rapoarte, la export.

Testele de aici păzesc proprietatea care contează cu adevărat: schimbarea
convenției mută NUMELE rândurilor, nu fizica. Vârful de producție trebuie să
rămână acolo unde îl pune soarele.
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.core.solar_position import mijloc_interval, pas_din_index
from app.core.weather_client import _reeticheteaza_la_inceput_de_interval
from tests.helpers import parc_pv


# --------------------------------------------------------------- mijlocul intervalului


def test_mijlocul_e_dupa_eticheta_la_pas_orar():
    """Cu eticheta la început, mijlocul intervalului vine DUPĂ ea (12:00 -> 12:30).
    Înainte, cu eticheta la final, era înaintea ei — semnul e tot ce desparte o
    geometrie solară corectă de una deplasată cu o oră."""
    index = pd.date_range("2026-06-01T00:00", periods=5, freq="h", tz="UTC")
    assert list(mijloc_interval(index)) == list(index + pd.Timedelta(minutes=30))


def test_mijlocul_respecta_pasul_de_15_minute():
    """Pasul se deduce din index, nu se presupune o oră: la 15 minute, un
    mijloc calculat cu 30 de minute fix ar cădea în intervalul URMĂTOR."""
    index = pd.date_range("2026-06-01T00:00", periods=8, freq="15min", tz="UTC")
    assert pas_din_index(index) == pd.Timedelta(minutes=15)
    assert list(mijloc_interval(index)) == list(index + pd.Timedelta(minutes=7, seconds=30))


def test_pasul_cade_pe_o_ora_cand_nu_se_poate_deduce():
    index = pd.date_range("2026-06-01T00:00", periods=1, freq="h", tz="UTC")
    assert pas_din_index(index) == pd.Timedelta(hours=1)


# --------------------------------------------------------------- reetichetarea datelor meteo


def test_radiatia_open_meteo_se_muta_cu_un_pas_inapoi():
    """Open-Meteo publică radiația ca „media orei PRECEDENTE": valoarea de la
    12:00 e media intervalului 11:00-12:00. Intervalul care ÎNCEPE la 11:00
    poartă, deci, exact acea valoare."""
    index = pd.date_range("2026-06-01T10:00", periods=4, freq="h", tz="UTC")
    df = pd.DataFrame({"shortwave_radiation": [100.0, 200.0, 300.0, 400.0],
                        "temperature_2m": [10.0, 11.0, 12.0, 13.0]}, index=index)

    rezultat = _reeticheteaza_la_inceput_de_interval(df)

    assert rezultat.loc[pd.Timestamp("2026-06-01T10:00Z"), "shortwave_radiation"] == 200.0
    assert rezultat.loc[pd.Timestamp("2026-06-01T11:00Z"), "shortwave_radiation"] == 300.0


def test_valorile_instantanee_raman_pe_eticheta_lor():
    """Temperatura și vântul sunt valori instantanee LA etichetă, nu medii ale
    orei precedente — nu se deplasează."""
    index = pd.date_range("2026-06-01T10:00", periods=3, freq="h", tz="UTC")
    df = pd.DataFrame({"shortwave_radiation": [100.0, 200.0, 300.0],
                        "temperature_2m": [10.0, 11.0, 12.0],
                        "wind_speed_10m": [3.0, 4.0, 5.0]}, index=index)

    rezultat = _reeticheteaza_la_inceput_de_interval(df)

    assert rezultat.loc[pd.Timestamp("2026-06-01T10:00Z"), "temperature_2m"] == 10.0
    assert rezultat.loc[pd.Timestamp("2026-06-01T10:00Z"), "wind_speed_10m"] == 3.0


def test_ultimul_rand_se_elimina_in_loc_sa_ramana_fara_radiatie():
    index = pd.date_range("2026-06-01T10:00", periods=3, freq="h", tz="UTC")
    df = pd.DataFrame({"shortwave_radiation": [100.0, 200.0, 300.0]}, index=index)
    rezultat = _reeticheteaza_la_inceput_de_interval(df)
    assert len(rezultat) == 2
    assert rezultat["shortwave_radiation"].notna().all()


# --------------------------------------------------------------- proprietatea care contează


def test_varful_de_productie_ramane_la_amiaza_solara():
    """Testul care justifică toată schimbarea: după reetichetare, intervalul cu
    cea mai mare producție trebuie să fie tot cel care CONȚINE amiaza solară.
    Dacă semnul deplasării ar fi greșit undeva în lanț, vârful ar sări cu o oră
    — exact eroarea pe care convenția o previne."""
    from pvlib import solarposition

    from app.core.meteo_mapping import pregateste_meteo_pv
    from app.core.pv_model import calculeaza_productie_pv
    from tests.helpers import config_pv_simplu

    # Invertor supradimensionat DELIBERAT: cu limitarea obișnuită (850 kW AC la
    # 1000 kWp DC) curba se aplatizează ore în șir la prânz, iar `idxmax` ar
    # alege prima oră din platou, nu vârful real — testul ar măsura altceva.
    parc = parc_pv(latitudine=44.43, longitudine=26.10, altitudine_m=80.0,
                    config_pv=config_pv_simplu(putere_ac_kw=2000.0))

    # „Vremea brută", în convenția Open-Meteo: radiație de cer senin etichetată
    # la FINALUL intervalului, adică evaluată la mijlocul orei precedente.
    etichete_brute = pd.date_range("2026-06-15T00:00", periods=48, freq="h", tz="UTC")
    from pvlib.location import Location
    loc = Location(parc.latitudine, parc.longitudine, altitude=parc.altitudine_m, tz="UTC")
    cs = loc.get_clearsky(etichete_brute - pd.Timedelta(minutes=30), model="ineichen")
    cs.index = etichete_brute
    brut = pd.DataFrame({
        "shortwave_radiation": cs["ghi"], "direct_normal_irradiance": cs["dni"],
        "diffuse_radiation": cs["dhi"], "temperature_2m": 20.0, "wind_speed_10m": 2.0,
    }, index=etichete_brute)

    meteo = _reeticheteaza_la_inceput_de_interval(brut)
    productie = calculeaza_productie_pv(parc.config_pv, parc.latitudine, parc.longitudine,
                                         parc.altitudine_m, pregateste_meteo_pv(meteo))

    zi = pd.Timestamp("2026-06-15", tz="UTC")
    dintr_o_zi = productie[(productie.index >= zi) & (productie.index < zi + pd.Timedelta(days=1))]
    eticheta_varf = dintr_o_zi["putere_ac_fizic_kw"].idxmax()

    esantion = pd.date_range(zi, zi + pd.Timedelta(hours=23, minutes=50), freq="10min", tz="UTC")
    poz = solarposition.get_solarposition(esantion, latitude=parc.latitudine,
                                           longitude=parc.longitudine, altitude=parc.altitudine_m)
    amiaza = poz["apparent_elevation"].idxmax()

    assert eticheta_varf <= amiaza < eticheta_varf + pd.Timedelta(hours=1), (
        f"intervalul de vârf ({eticheta_varf}) trebuie să CONȚINĂ amiaza solară ({amiaza}) — "
        f"un semn greșit al deplasării l-ar muta cu o oră")


# --------------------------------------------------------------- import


def test_importul_nu_mai_deplaseaza_nimic():
    """Fișierul și baza de date vorbesc aceeași limbă: eticheta din CSV e deja
    începutul intervalului, deci momentul se păstrează identic."""
    from app.core.csv_import import analizeaza_fisier

    parc = parc_pv()
    continut = b"timestamp;kWh\n2026-06-01T12:00:00+03:00;500\n"
    raport = analizeaza_fisier(continut, parc)
    assert raport.valid, raport.erori
    assert raport.randuri_valide[0].timestamp_utc == "2026-06-01T09:00:00+00:00"
    assert not any("mutate" in a for a in raport.avertismente)


def test_parcul_nu_mai_are_setare_de_eticheta():
    """Convenția e una singură, în tot lanțul — nu mai e nimic de configurat,
    deci nimic de configurat greșit."""
    assert not hasattr(parc_pv(), "eticheta_ora_import")


def test_avertismentul_de_fus_nu_mai_invinovateste_eticheta():
    parc = parc_pv(latitudine=44.43, longitudine=26.10, fus_orar="Europe/Bucharest")
    randuri = []
    for zi in range(1, 8):
        for h in range(24):
            val = max(0.0, 500 * (1 - ((h - 9) / 4.0) ** 2))
            randuri.append(f"2026-06-{zi:02d}T{h:02d}:00:00+03:00;{val:.1f}")
    from app.core.csv_import import analizeaza_fisier
    raport = analizeaza_fisier(("timestamp;kWh\n" + "\n".join(randuri)).encode(), parc,
                                toleranta_fus_orar_minute=30)
    mesaje = [a for a in raport.avertismente if "Fus orar posibil greșit" in a]
    assert mesaje, raport.avertismente
    assert "Convenția de etichetare nu poate fi cauza" in mesaje[0]
