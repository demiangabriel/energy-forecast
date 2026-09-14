"""Motorul fizic PV — teste de plauzibilitate fizică (R-1502)."""
from __future__ import annotations

import copy

import pandas as pd
import pytest

from app.core.pv_model import calculeaza_productie_pv
from app.models import Subansamblu, TipAsezarePV
from tests.helpers import config_pv_simplu, meteo_pv_cer_senin

LAT, LON, ALT = 44.43, 26.10, 80.0


def test_productie_zero_noaptea():
    """Nicio putere calculată în orele cu soare sub orizont (zenit >= 95°),
    chiar dacă am forța meteo-ul să raporteze radiație non-nulă 24/24."""
    meteo = meteo_pv_cer_senin("2026-06-21 00:00", 48, LAT, LON, ALT)
    config = config_pv_simplu()
    rez = calculeaza_productie_pv(config, LAT, LON, ALT, meteo)

    ore_noapte = rez[rez["solar_zenith"] >= 95.0]
    assert len(ore_noapte) > 0, "testul are nevoie de ore de noapte în fereastra aleasă"
    assert (ore_noapte["putere_ac_fizic_kw"] <= 1e-6).all(), \
        "producție nenulă calculată în timp ce soarele e sub orizont"


def test_componenta_directa_zero_noaptea_chiar_cu_dni_fals_mare():
    """Chiar dacă sursa meteo ar raporta eronat un DNI mare non-stop (sursă
    defectă), componenta DIRECTĂ a radiației pe planul înclinat trebuie să
    rămână geometric zero noaptea — soarele sub orizont nu poate lumina
    direct panoul, indiferent ce valoare brută vine de la senzor. (GHI și DHI
    rămân 0 aici intenționat, ca să izolăm exact componenta directă — un GHI
    nenul ar introduce reflexie de sol, un fenomen fizic real, dar diferit de
    ce testăm.)"""
    etichete = pd.date_range("2026-06-21 00:00", periods=48, freq="h", tz="UTC")
    meteo = pd.DataFrame({
        "ghi": 0.0, "dni": 900.0, "dhi": 0.0,
        "temp_aer_c": 20.0, "viteza_vant_ms": 2.0,
    }, index=etichete)
    rez = calculeaza_productie_pv(config_pv_simplu(), LAT, LON, ALT, meteo)
    ore_noapte = rez[rez["solar_zenith"] >= 95.0]
    assert len(ore_noapte) > 0, "testul are nevoie de ore de noapte în fereastra aleasă"
    assert (ore_noapte["putere_ac_fizic_kw"] <= 1e-6).all(), \
        "componenta directă de radiație nu poate produce putere cu soarele sub orizont"


def test_energie_vara_mai_mare_decat_iarna():
    """Sanitate sezonieră: la aceeași locație, o zi însorită de vară produce
    mai multă energie decât o zi însorită de iarnă (zile mai lungi, soare
    mai sus pe cer)."""
    config = config_pv_simplu()

    meteo_vara = meteo_pv_cer_senin("2026-06-21 00:00", 24, LAT, LON, ALT)
    meteo_iarna = meteo_pv_cer_senin("2026-12-21 00:00", 24, LAT, LON, ALT)

    energie_vara = calculeaza_productie_pv(config, LAT, LON, ALT, meteo_vara)["putere_ac_fizic_kw"].sum()
    energie_iarna = calculeaza_productie_pv(config, LAT, LON, ALT, meteo_iarna)["putere_ac_fizic_kw"].sum()

    assert energie_vara > energie_iarna * 1.5, \
        f"vara ({energie_vara:.0f} kWh) ar trebui să fie mult peste iarna ({energie_iarna:.0f} kWh)"


def test_degradare_anuala_reduce_productia():
    """R-4xx: un parc pus în funcțiune cu ani în urmă produce mai puțin
    decât unul identic, nou, din cauza degradării anuale liniare."""
    meteo = meteo_pv_cer_senin("2026-06-21 00:00", 24, LAT, LON, ALT)

    config_nou = config_pv_simplu(degradare_anuala_pct=1.0, data_punerii_in_functiune=None)
    config_vechi = config_pv_simplu(degradare_anuala_pct=1.0, data_punerii_in_functiune="2016-06-21")

    energie_noua = calculeaza_productie_pv(config_nou, LAT, LON, ALT, meteo)["putere_ac_fizic_kw"].sum()
    energie_veche = calculeaza_productie_pv(config_vechi, LAT, LON, ALT, meteo)["putere_ac_fizic_kw"].sum()

    assert energie_veche < energie_noua, "degradarea anuală trebuie să reducă producția"
    # 10 ani * 1%/an = ~10% reducere așteptată (clip la 0 doar dincolo de 100 ani)
    raport = energie_veche / energie_noua
    assert 0.85 <= raport <= 0.95, f"raport neplauzibil după 10 ani de degradare: {raport:.3f}"


def test_pierderi_sistem_reduc_productia_proportional():
    meteo = meteo_pv_cer_senin("2026-06-21 00:00", 24, LAT, LON, ALT)
    config_fara_pierderi = config_pv_simplu()
    config_cu_pierderi = config_pv_simplu(
        pierdere_murdarire_pct=5.0, pierdere_neuniformitate_pct=2.0,
        pierdere_cablaj_conexiuni_pct=1.5, pierdere_lid_pct=1.0,
        pierdere_placuta_pct=1.0, pierdere_indisponibilitate_pct=2.0,
    )
    e1 = calculeaza_productie_pv(config_fara_pierderi, LAT, LON, ALT, meteo)["putere_ac_fizic_kw"].sum()
    e2 = calculeaza_productie_pv(config_cu_pierderi, LAT, LON, ALT, meteo)["putere_ac_fizic_kw"].sum()
    factor_asteptat = config_cu_pierderi.factor_pierderi_sistem()
    assert e2 < e1
    assert abs(e2 / e1 - factor_asteptat) < 0.01


def test_subansambluri_clipping_combinat_nu_supraestimeaza():
    """R-412: cu mai multe subansambluri (ex. est-vest) pe ACELAȘI invertor,
    clipping-ul trebuie aplicat pe SUMA puterilor DC, nu pe fiecare
    subansamblu separat — altfel se supraestimează producția cu 4-6%
    (nota explicită din specificație). Verificăm asta comparând rezultatul
    real al funcției cu o reconstrucție "naivă" (clip separat, apoi sumă)."""
    meteo = meteo_pv_cer_senin("2026-06-21 00:00", 24, LAT, LON, ALT)

    # invertor subdimensionat intenționat față de DC, ca fenomenul de
    # clipping să fie activ la prânz
    putere_ac = 700.0
    config = config_pv_simplu(
        putere_dc_kwp=1000.0, putere_ac_kw=putere_ac, randament_invertor=1.0,
        subansambluri=[
            Subansamblu(nume="Est", putere_dc_kwp=500.0, inclinare_grade=25.0,
                        azimut_grade=90.0, tip_asezare=TipAsezarePV.FIX),
            Subansamblu(nume="Vest", putere_dc_kwp=500.0, inclinare_grade=25.0,
                        azimut_grade=270.0, tip_asezare=TipAsezarePV.FIX),
        ],
    )
    rezultat_corect = calculeaza_productie_pv(config, LAT, LON, ALT, meteo)
    energie_corecta = rezultat_corect["putere_ac_fizic_kw"].sum()

    # reconstrucție naivă: fiecare subansamblu clipat SEPARAT la putere_ac_kw,
    # apoi însumat — exact greșeala pe care specificația o interzice explicit
    from app.core.pv_model import _putere_dc_subansamblu
    from app.core.solar_position import calculeaza_pozitie_solara
    poz = calculeaza_pozitie_solara(meteo.index, LAT, LON, ALT)
    poz.attrs["lat"], poz.attrs["lon"] = LAT, LON
    energie_naiva = 0.0
    for sub in config.subansambluri:
        dc, _poa = _putere_dc_subansamblu(sub, config, meteo, poz, config.albedo)
        ac_naiv = dc.clip(upper=putere_ac)  # GREȘIT: clip per-subansamblu
        energie_naiva += ac_naiv.sum()

    assert energie_corecta <= energie_naiva, \
        "clipping-ul combinat nu ar trebui NICIODATĂ să dea mai multă energie decât cel naiv"
    supraestimare_pct = (energie_naiva - energie_corecta) / energie_corecta * 100
    assert supraestimare_pct > 0.5, (
        f"testul nu a reușit să provoace deloc clipping combinat — supraestimare "
        f"naivă doar {supraestimare_pct:.2f}%, crește puterea DC/scade AC în test"
    )


def test_tracker_produce_diferit_de_fix():
    """Un subansamblu pe tracker (urmărire pe un ax) trebuie să dea o curbă
    de producție diferită de unul fix — confirmă că ramura de tracking din
    _unghiuri_suprafata chiar e exercitată, nu ignorată silențios."""
    meteo = meteo_pv_cer_senin("2026-06-21 00:00", 24, LAT, LON, ALT)
    config_fix = config_pv_simplu(subansambluri=[
        Subansamblu(nume="Fix", putere_dc_kwp=1000.0, inclinare_grade=30.0,
                    azimut_grade=180.0, tip_asezare=TipAsezarePV.FIX),
    ])
    config_tracker = config_pv_simplu(subansambluri=[
        Subansamblu(nume="Tracker", putere_dc_kwp=1000.0, inclinare_grade=None,
                    azimut_grade=0.0, tip_asezare=TipAsezarePV.TRACKER),
    ])
    e_fix = calculeaza_productie_pv(config_fix, LAT, LON, ALT, meteo)["putere_ac_fizic_kw"]
    e_tracker = calculeaza_productie_pv(config_tracker, LAT, LON, ALT, meteo)["putere_ac_fizic_kw"]
    assert not e_fix.equals(e_tracker), "tracker-ul ar trebui să producă o curbă diferită de instalația fixă"
    # trackerul urmărește soarele toată ziua -> ar trebui să genereze mai multă
    # energie zilnică decât panoul fix orientat sud la aceeași putere DC
    assert e_tracker.sum() > e_fix.sum()


def test_lipsa_coloane_meteo_ridica_eroare_clara():
    meteo = meteo_pv_cer_senin("2026-06-21 00:00", 6, LAT, LON, ALT).drop(columns=["dni"])
    with pytest.raises(ValueError, match="Coloane meteo lipsă"):
        calculeaza_productie_pv(config_pv_simplu(), LAT, LON, ALT, meteo)
