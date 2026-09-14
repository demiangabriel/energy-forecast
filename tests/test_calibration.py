"""Calibrarea geometriei (app/core/calibration.py) — CORECȚIE v3: sondajul de
înclinare/azimut nu mai e confundat cu o eroare de SCALĂ (pierderi de sistem
și degradare configurate greșit pe parc). Vezi docstring-ul modulului pentru
analiza completă — comparată cu un script extern de calibrare furnizat de
operator, care decupla explicit cele două lucruri prin fitarea unui factor
de scalare separat, prin cele mai mici pătrate.

Testele NU ating rețeaua — `obtine_meteo_istoric_real` e înlocuită cu vreme
sintetică de cer senin (aceleași funcții pvlib ca `tests/helpers.py`)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pvlib.location import Location

import app.core.calibration as calib_mod
from app.core.calibration import calibreaza_geometrie
from app.core.pv_model import calculeaza_productie_pv
from app.core.meteo_mapping import pregateste_meteo_pv
from app.models import CalitateOra, ProductieOrara, Subansamblu, TipAsezarePV
from tests.helpers import parc_pv

LAT, LON, ALT = 44.43, 26.10, 80.0


def _meteo_brut(index: pd.DatetimeIndex) -> pd.DataFrame:
    """Vreme sintetică de cer senin, în formatul BRUT întors de Open-Meteo
    (shortwave_radiation/direct_normal_irradiance/...) — calibration.py o
    trece prin `pregateste_meteo_pv` intern, exact ca la vremea reală."""
    cs = Location(LAT, LON, altitude=ALT, tz="UTC").get_clearsky(
        index + pd.Timedelta(minutes=30), model="ineichen")
    cs.index = index
    return pd.DataFrame({
        "shortwave_radiation": cs["ghi"], "direct_normal_irradiance": cs["dni"],
        "diffuse_radiation": cs["dhi"], "temperature_2m": 20.0, "wind_speed_10m": 2.0,
    }, index=index)


def _config_curat(putere_dc_kwp: float, putere_ac_kw: float, inclinare: float, azimut: float):
    """Config FĂRĂ pierderi/degradare — folosită doar ca să generăm o
    „producție reală" sintetică la o geometrie și o scară CUNOSCUTE."""
    from app.models import ConfigPV
    return ConfigPV(
        putere_dc_kwp=putere_dc_kwp, putere_ac_kw=putere_ac_kw,
        subansambluri=[Subansamblu(nume="real", putere_dc_kwp=putere_dc_kwp,
                                    inclinare_grade=inclinare, azimut_grade=azimut,
                                    tip_asezare=TipAsezarePV.FIX)],
        pierdere_murdarire_pct=0.0, pierdere_neuniformitate_pct=0.0,
        pierdere_cablaj_conexiuni_pct=0.0, pierdere_lid_pct=0.0,
        pierdere_placuta_pct=0.0, pierdere_indisponibilitate_pct=0.0,
        degradare_anuala_pct=0.0,
    )


TILT_ADEVARAT, AZIMUT_ADEVARAT = 30, 180
FACTOR_ADEVARAT = 0.75   # pierderi + degradare REALE, necunoscute de operator


def _instaleaza_productie_si_meteo(repo, parc, monkeypatch, zile=20):
    index = pd.date_range("2026-05-01", periods=24 * zile, freq="h", tz="UTC")
    meteo_brut = _meteo_brut(index)
    meteo_pv_ready = pregateste_meteo_pv(meteo_brut)

    config_real = _config_curat(parc.config_pv.putere_dc_kwp, parc.config_pv.putere_ac_kw,
                                 TILT_ADEVARAT, AZIMUT_ADEVARAT)
    fizic_curat = calculeaza_productie_pv(config_real, LAT, LON, ALT, meteo_pv_ready)
    productie_reala_kw = fizic_curat["putere_ac_fizic_kw"] * FACTOR_ADEVARAT

    randuri = [ProductieOrara(parc_id=parc.id, timestamp_utc=ts.isoformat(), energie_kwh=float(v))
               for ts, v in productie_reala_kw.items()]
    repo.salveaza_productie(randuri)

    monkeypatch.setattr(calib_mod, "obtine_meteo_istoric_real", lambda *a, **k: meteo_brut)
    return index


def test_calibrarea_gaseste_geometria_adevarata_chiar_cu_pierderi_configurate_gresit(repo, monkeypatch):
    """Testul central al CORECȚIEI v3: parcul are pierderi/degradare
    configurate FOARTE greșit (mult peste factorul real de 0.75) — dacă
    sondajul n-ar decupla scara de geometrie, magnitudinea greșit configurată
    ar putea trage rezultatul spre o geometrie greșită. Cu decuplare, geometria
    aleasă trebuie să fie tot cea ADEVĂRATĂ."""
    parc = parc_pv(cod="CAL-1", latitudine=LAT, longitudine=LON, altitudine_m=ALT)
    # pierderi configurate GREȘIT — mult mai mari decât factorul real (0.75)
    parc.config_pv.pierdere_murdarire_pct = 15.0
    parc.config_pv.pierdere_neuniformitate_pct = 8.0
    parc.config_pv.pierdere_cablaj_conexiuni_pct = 5.0
    parc.config_pv.pierdere_indisponibilitate_pct = 10.0
    parc.config_pv.degradare_anuala_pct = 5.0
    repo.salveaza_parc(parc)

    _instaleaza_productie_si_meteo(repo, parc, monkeypatch)

    rezultat = calibreaza_geometrie(repo, parc, prag_min_ore=50)

    assert rezultat["optim"]["inclinare_grade"] == TILT_ADEVARAT
    assert rezultat["optim"]["azimut_grade"] == AZIMUT_ADEVARAT
    # factorul de scalare recuperat trebuie să fie aproape de cel real (0.75),
    # NU de "1 - pierderile greșit configurate pe parc"
    assert rezultat["factor_scalare_optim"] == pytest.approx(FACTOR_ADEVARAT, abs=0.05)


def test_alternativele_apropiate_de_geometria_adevarata_au_eroare_mai_mica(repo, monkeypatch):
    """Monotonie de bun-simț: geometriile apropiate de cea adevărată (30°/180°)
    trebuie să aibă eroare mai mică decât una foarte îndepărtată (0°/270°)."""
    parc = parc_pv(cod="CAL-2", latitudine=LAT, longitudine=LON, altitudine_m=ALT)
    repo.salveaza_parc(parc)
    _instaleaza_productie_si_meteo(repo, parc, monkeypatch)

    rezultat = calibreaza_geometrie(repo, parc, prag_min_ore=50)
    pe_geometrie = {(r["inclinare_grade"], r["azimut_grade"]): r["eroare_mae_kw"]
                     for r in rezultat["alternative"]}
    eroare_adevarata = pe_geometrie.get((TILT_ADEVARAT, AZIMUT_ADEVARAT))
    assert eroare_adevarata is not None
    assert eroare_adevarata == min(pe_geometrie.values())


def test_ore_marcate_de_calitate_sunt_excluse_din_calibrare(repo, monkeypatch):
    parc = parc_pv(cod="CAL-3", latitudine=LAT, longitudine=LON, altitudine_m=ALT)
    repo.salveaza_parc(parc)
    index = _instaleaza_productie_si_meteo(repo, parc, monkeypatch)

    # marcăm câteva ore de zi ca indisponibilitate — nu trebuie să mai conteze la calibrare
    verdicte = [CalitateOra(parc_id=parc.id, timestamp_utc=ts.isoformat(), steag="indisponibilitate")
                for ts in index[10:15]]
    repo.salveaza_calitate_productie(verdicte)

    rezultat = calibreaza_geometrie(repo, parc, prag_min_ore=50)
    assert rezultat["numar_ore_excluse_qc"] >= 1


def test_prag_minim_refuza_rezultatul_cu_prea_putine_ore(repo, monkeypatch):
    from app.core.calibration import CalibrareIndisponibilaError
    parc = parc_pv(cod="CAL-4", latitudine=LAT, longitudine=LON, altitudine_m=ALT)
    repo.salveaza_parc(parc)
    _instaleaza_productie_si_meteo(repo, parc, monkeypatch, zile=3)

    with pytest.raises(CalibrareIndisponibilaError):
        calibreaza_geometrie(repo, parc, prag_min_ore=10_000)


def test_ruta_calibrare_afiseaza_factorul_de_scalare(client, repo, monkeypatch):
    parc = parc_pv(cod="CAL-5", latitudine=LAT, longitudine=LON, altitudine_m=ALT)
    repo.salveaza_parc(parc)
    # zile suficiente ca orele utile de zi să treacă de pragul implicit (300 — Setări de operare)
    _instaleaza_productie_si_meteo(repo, parc, monkeypatch, zile=30)

    r = client.post(f"/parcuri/{parc.id}/calibrare/ruleaza")
    assert r.status_code == 200
    assert "Factor de scalare" in r.text or "factor de scalare" in r.text.lower()


# --------------------------------------------------------------------------- alinierea in timp


def test_calibrarea_raporteaza_alinierea_producției_fata_de_soare(repo, monkeypatch):
    """Calibrarea deduce geometria din FORMA curbei zilnice. Dacă seria
    măsurată e decalată în timp, sondajul compensează rotind azimutul și
    întoarce o geometrie care potrivește datele fără să descrie parcul —
    rezultatul pare aberant fără să spună de ce. Decalajul se măsoară acum."""
    parc = parc_pv(latitudine=LAT, longitudine=LON, altitudine_m=ALT)
    repo.salveaza_parc(parc)
    _instaleaza_productie_si_meteo(repo, parc, monkeypatch)

    rez = calibreaza_geometrie(repo, parc, prag_min_ore=100)
    decalaj = rez["decalaj_minute_fata_de_soare"]
    assert decalaj is not None
    assert abs(decalaj) < 25, f"date aliniate corect, dar s-a raportat un decalaj de {decalaj} minute"


def test_decalajul_real_al_datelor_e_detectat(repo, monkeypatch):
    """Producție deplasată deliberat cu două ore — exact simptomul unui fus de
    import greșit. Trebuie raportat, ca operatorul să nu ia geometria de bună."""
    import pandas as pd

    from app.core.calibration import _decalaj_fata_de_amiaza_solara

    index = pd.date_range("2026-05-01", periods=24 * 20, freq="h", tz="UTC")
    meteo_pv_ready = pregateste_meteo_pv(_meteo_brut(index))
    config_real = _config_curat(1000.0, 2000.0, TILT_ADEVARAT, AZIMUT_ADEVARAT)
    fizic = calculeaza_productie_pv(config_real, LAT, LON, ALT, meteo_pv_ready)
    parc = parc_pv(latitudine=LAT, longitudine=LON, altitudine_m=ALT)

    corect = fizic["putere_ac_fizic_kw"]
    deplasat = corect.copy()
    deplasat.index = deplasat.index + pd.Timedelta(hours=2)

    assert abs(_decalaj_fata_de_amiaza_solara(corect, parc)) < 25
    assert _decalaj_fata_de_amiaza_solara(deplasat, parc) == pytest.approx(120, abs=15)
