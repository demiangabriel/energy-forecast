"""Modelele fizice PV adăugate în v2: transpoziție Perez, albedo (inclusiv
zăpadă), pierderi din zăpadă, murdărire dinamică, temperatură Faiman, curbă de
randament al invertorului, topologia invertoarelor."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.pv_model import calculeaza_productie_pv
from app.models import ModelTemperaturaCelula, ModelTranspozitie, Subansamblu, TipAsezarePV
from tests.helpers import config_pv_simplu, meteo_pv_cer_senin

LAT, LON, ALT = 44.43, 26.10, 80.0


def _energie(config, meteo) -> float:
    return float(calculeaza_productie_pv(config, LAT, LON, ALT, meteo)["putere_ac_fizic_kw"].sum())


def test_modelele_de_transpozitie_dau_rezultate_apropiate_dar_distincte():
    """Toate modelele anizotrope trebuie să dea răspunsuri de același ordin
    (câteva procente diferență), altfel unul dintre ele e implementat greșit.
    Dar nu identice — altfel opțiunea nu are efect."""
    meteo = meteo_pv_cer_senin("2026-06-21 00:00", 24, LAT, LON, ALT)
    energii = {}
    for model in [ModelTranspozitie.PEREZ_DRIESSE, ModelTranspozitie.PEREZ,
                   ModelTranspozitie.HAY_DAVIES, ModelTranspozitie.IZOTROP]:
        energii[model.value] = _energie(config_pv_simplu(model_transpozitie=model), meteo)

    valori = list(energii.values())
    assert max(valori) / min(valori) < 1.10, f"diferențe implauzibil de mari: {energii}"
    assert energii["perez"] != energii["haydavies"], "alegerea modelului trebuie să aibă efect"


def test_albedo_mai_mare_creste_productia():
    """Radiația reflectată de sol ajunge pe planul înclinat: un sol mai
    reflectant produce mai multă energie."""
    meteo = meteo_pv_cer_senin("2026-06-21 00:00", 24, LAT, LON, ALT)
    mic = _energie(config_pv_simplu(albedo=0.10), meteo)
    mare = _energie(config_pv_simplu(albedo=0.80), meteo)
    assert mare > mic


def test_stratul_de_zapada_comuta_pe_albedo_de_zapada():
    """Iarna, zăpada urcă reflexia solului de la ~0,2 la 0,6-0,8 — o sursă
    reală de energie în plus, exact în perioada în care restul modelului
    tinde să subestimeze."""
    meteo = meteo_pv_cer_senin("2026-01-15 00:00", 24, LAT, LON, ALT)
    meteo["strat_zapada_m"] = 0.0
    config = config_pv_simplu(albedo=0.2, albedo_zapada=0.8, pierderi_zapada=False)
    fara = _energie(config, meteo)

    meteo_zapada = meteo.copy()
    meteo_zapada["strat_zapada_m"] = 0.30
    cu = _energie(config, meteo_zapada)
    assert cu > fara


def test_ninsoarea_reduce_productia_prin_acoperirea_modulelor():
    """Modelul NREL/Marion: zăpada acoperă modulul și alunecă abia când
    radiația și înclinarea o permit. Fără el, zilele cu zăpadă produc erori
    de 50-100%."""
    meteo = meteo_pv_cer_senin("2026-01-15 00:00", 48, LAT, LON, ALT)
    meteo["temp_aer_c"] = -6.0
    meteo["zapada_cm"] = 0.0
    meteo["strat_zapada_m"] = 0.0
    config = config_pv_simplu(pierderi_zapada=True)
    fara = _energie(config, meteo)

    meteo_ninsoare = meteo.copy()
    meteo_ninsoare.iloc[0, meteo_ninsoare.columns.get_loc("zapada_cm")] = 5.0
    meteo_ninsoare["strat_zapada_m"] = 0.10
    cu = _energie(config, meteo_ninsoare)
    assert cu < fara, "ninsoarea trebuie să reducă producția"


def test_modelul_de_zapada_poate_fi_dezactivat():
    meteo = meteo_pv_cer_senin("2026-01-15 00:00", 24, LAT, LON, ALT)
    meteo["temp_aer_c"] = -6.0
    meteo["zapada_cm"] = 5.0
    cu_model = _energie(config_pv_simplu(pierderi_zapada=True), meteo)
    fara_model = _energie(config_pv_simplu(pierderi_zapada=False), meteo)
    assert fara_model > cu_model


def test_murdarirea_dinamica_se_reseteaza_la_ploaie():
    """Model Kimber: murdăria se acumulează în perioadele uscate și e spălată
    de ploile peste prag. O ploaie zdravănă trebuie să readucă producția
    aproape de nivelul curat."""
    meteo = meteo_pv_cer_senin("2026-07-01 00:00", 24 * 40, LAT, LON, ALT)
    meteo["precipitatii_mm"] = 0.0
    config = config_pv_simplu(soiling_dinamic=True, soiling_rata_zi_pct=0.2,
                               soiling_prag_ploaie_mm=5.0)
    uscat = _energie(config, meteo)

    meteo_ploaie = meteo.copy()
    meteo_ploaie.iloc[::48, meteo_ploaie.columns.get_loc("precipitatii_mm")] = 12.0  # ploaie la 2 zile
    cu_ploaie = _energie(config, meteo_ploaie)
    assert cu_ploaie > uscat, "ploaia spală panourile și trebuie să crească producția"


def test_murdarirea_dinamica_inlocuieste_procentul_fix():
    """Când e activă, murdărirea dinamică NU se adună peste procentul fix —
    altfel pierderea s-ar contoriza de două ori."""
    meteo = meteo_pv_cer_senin("2026-07-01 00:00", 48, LAT, LON, ALT)
    meteo["precipitatii_mm"] = 20.0   # spălare permanentă -> murdărire ~0
    config_dinamic = config_pv_simplu(soiling_dinamic=True, pierdere_murdarire_pct=10.0)
    config_fara_pierdere = config_pv_simplu(soiling_dinamic=False, pierdere_murdarire_pct=0.0)
    assert _energie(config_dinamic, meteo) == pytest.approx(_energie(config_fara_pierdere, meteo), rel=0.02)


def test_modelul_faiman_da_rezultate_plauzibile():
    meteo = meteo_pv_cer_senin("2026-06-21 00:00", 24, LAT, LON, ALT)
    sapm = _energie(config_pv_simplu(model_temperatura=ModelTemperaturaCelula.SAPM), meteo)
    faiman = _energie(config_pv_simplu(model_temperatura=ModelTemperaturaCelula.FAIMAN), meteo)
    assert 0.9 < faiman / sapm < 1.1, "cele două modele termice trebuie să fie în același interval"


def test_curba_de_randament_penalizeaza_incarcarile_mici():
    """Randamentul real al invertorului scade la încărcări mici (pierderile
    de mers în gol sunt ~fixe) — exact în orele de dimineață și seară, unde
    un randament constant supraestimează sistematic."""
    meteo = meteo_pv_cer_senin("2026-06-21 00:00", 24, LAT, LON, ALT)
    constant = config_pv_simplu(randament_invertor=0.98)
    cu_curba = config_pv_simplu(randament_invertor=0.98, curba_randament_invertor=[
        [0.0, 0.60], [0.05, 0.88], [0.2, 0.96], [0.5, 0.98], [1.0, 0.98]])

    r_const = calculeaza_productie_pv(constant, LAT, LON, ALT, meteo)
    r_curba = calculeaza_productie_pv(cu_curba, LAT, LON, ALT, meteo)

    assert r_curba["putere_ac_fizic_kw"].sum() < r_const["putere_ac_fizic_kw"].sum()
    varf = r_const["fractie_incarcare"].idxmax()
    assert r_curba["putere_ac_fizic_kw"][varf] == pytest.approx(
        r_const["putere_ac_fizic_kw"][varf], rel=0.02), "la vârf randamentul e același"


def test_topologia_de_clipping_schimba_rezultatul_in_directia_corecta():
    """Trei situații care nu trebuie confundate, pe un parc est-vest cu
    invertor subdimensionat:

    1. **un singur invertor comun** (implicit, R-412): limitarea se aplică pe
       SUMA puterilor DC. Est și Vest au vârfuri decalate, deci suma rareori
       atinge limita — se pierde puțin.
    2. **invertoare separate per subansamblu**: fiecare primește partea lui din
       puterea AC (aici 300 kW din 600), deci fiecare se limitează mai devreme
       -> energie MAI MICĂ. E fizica reală a acelei topologii, motiv pentru
       care trebuie declarată explicit, nu ghicită.
    3. **greșeala interzisă de R-412**: a limita fiecare subansamblu la puterea
       AC TOTALĂ și apoi a însuma — supraestimează (testată separat în
       test_pv_model.py).
    """
    meteo = meteo_pv_cer_senin("2026-06-21 00:00", 24, LAT, LON, ALT)
    subansambluri = [
        Subansamblu(nume="Est", putere_dc_kwp=500.0, inclinare_grade=25.0,
                    azimut_grade=90.0, tip_asezare=TipAsezarePV.FIX),
        Subansamblu(nume="Vest", putere_dc_kwp=500.0, inclinare_grade=25.0,
                    azimut_grade=270.0, tip_asezare=TipAsezarePV.FIX),
    ]
    comun = config_pv_simplu(putere_dc_kwp=1000.0, putere_ac_kw=600.0,
                              randament_invertor=1.0, subansambluri=subansambluri,
                              clipping_per_subansamblu=False)
    separat = config_pv_simplu(putere_dc_kwp=1000.0, putere_ac_kw=600.0,
                                randament_invertor=1.0, subansambluri=subansambluri,
                                clipping_per_subansamblu=True)

    energie_comun = _energie(comun, meteo)
    energie_separat = _energie(separat, meteo)
    assert energie_separat < energie_comun, (
        "invertoare separate, fiecare cu partea lui din puterea AC, se limitează mai devreme")
    assert energie_separat > energie_comun * 0.8, "diferența trebuie să rămână de ordinul procentelor"


def test_coloanele_noi_de_trasabilitate_sunt_expuse():
    """Operatorul trebuie să poată vedea de ce a scăzut producția: factorii de
    zăpadă și murdărire sunt expuși explicit, nu ascunși în rezultat."""
    meteo = meteo_pv_cer_senin("2026-06-21 00:00", 24, LAT, LON, ALT)
    rezultat = calculeaza_productie_pv(config_pv_simplu(), LAT, LON, ALT, meteo)
    for coloana in ["poa_global_wm2", "factor_zapada", "factor_murdarire", "solar_zenith"]:
        assert coloana in rezultat.columns


def test_lipsa_variabilelor_optionale_nu_dezactiveaza_modelul():
    """Datele meteo fără zăpadă/precipitații (unele modele sau endpoint-uri nu
    le expun) nu trebuie să producă erori — funcționalitățile respective se
    dezactivează pur și simplu."""
    meteo = meteo_pv_cer_senin("2026-06-21 00:00", 24, LAT, LON, ALT)
    assert "zapada_cm" not in meteo.columns and "precipitatii_mm" not in meteo.columns
    config = config_pv_simplu(pierderi_zapada=True, soiling_dinamic=True)
    rezultat = calculeaza_productie_pv(config, LAT, LON, ALT, meteo)
    assert (rezultat["factor_zapada"] == 1.0).all()
    assert rezultat["putere_ac_fizic_kw"].notna().all()


def test_pozitia_solara_precalculata_da_acelasi_rezultat():
    """Optimizarea folosită de calibrare și de analiza de sensibilitate nu
    are voie să schimbe rezultatul, doar timpul de calcul."""
    from app.core.solar_position import calculeaza_pozitie_solara
    meteo = meteo_pv_cer_senin("2026-06-21 00:00", 24, LAT, LON, ALT)
    config = config_pv_simplu()
    normal = calculeaza_productie_pv(config, LAT, LON, ALT, meteo)
    poz = calculeaza_pozitie_solara(meteo.index, LAT, LON, ALT)
    optimizat = calculeaza_productie_pv(config, LAT, LON, ALT, meteo, poz_solara=poz)
    assert np.allclose(normal["putere_ac_fizic_kw"].values, optimizat["putere_ac_fizic_kw"].values)
