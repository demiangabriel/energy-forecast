"""
Validare formulare — R-405: "valori imposibile (ex. raport putere DC/putere
AC absurd, indicând o eroare de unitate de măsură) trebuie respinse cu un
mesaj clar, nu acceptate silențios." Toată validarea trăiește aici, explicit,
ca operatorul/programatorul să vadă dintr-o privire ce se acceptă.
"""
from __future__ import annotations

from app.core.timezones import fus_orar_valid


def _numar(form, camp: str, implicit=None):
    val = (form.get(camp) or "").strip()
    if val == "":
        return implicit
    try:
        return float(val.replace(",", "."))
    except ValueError:
        return None


def valideaza_parc_comun(form) -> tuple[dict, list[str]]:
    erori = []
    date: dict = {}

    date["cod"] = (form.get("cod") or "").strip()
    date["nume"] = (form.get("nume") or "").strip()
    if not date["cod"]:
        erori.append("Codul parcului e obligatoriu.")
    if not date["nume"]:
        erori.append("Numele parcului e obligatoriu.")

    lat = _numar(form, "latitudine")
    lon = _numar(form, "longitudine")
    if lat is None or not (-90 <= lat <= 90):
        erori.append("Latitudine invalidă — trebuie să fie între -90 și 90.")
    if lon is None or not (-180 <= lon <= 180):
        erori.append("Longitudine invalidă — trebuie să fie între -180 și 180.")
    date["latitudine"], date["longitudine"] = lat, lon

    date["altitudine_m"] = _numar(form, "altitudine_m", 0.0) or 0.0
    date["fus_orar"] = form.get("fus_orar") or "Europe/Bucharest"
    if not fus_orar_valid(date["fus_orar"]):
        erori.append(f"Fus orar invalid — „{date['fus_orar']}” nu e un fus orar IANA recunoscut "
                     f"(ex. Europe/Bucharest, Europe/Chisinau, Europe/Sofia).")

    # R-511/R-xxx: fusul folosit la interpretarea CSV-ului importat poate fi
    # declarat separat de `fus_orar` (care guvernează prognozele) — gol e
    # valid și înseamnă „identic cu fus_orar" (vezi `fus_orar_pentru_import`).
    fus_import = (form.get("fus_orar_import") or "").strip()
    if fus_import and not fus_orar_valid(fus_import):
        erori.append(f"Fus orar de import invalid — „{fus_import}” nu e un fus orar IANA recunoscut.")
    date["fus_orar_import"] = fus_import

    return date, erori


def valideaza_parc_pv(form) -> tuple[dict, list[str]]:
    date, erori = valideaza_parc_comun(form)

    putere_dc = _numar(form, "putere_dc_kwp")
    putere_ac = _numar(form, "putere_ac_kw")
    if putere_dc is None or putere_dc <= 0:
        erori.append("Puterea DC (kWp) trebuie să fie un număr pozitiv.")
    if putere_ac is None or putere_ac <= 0:
        erori.append("Puterea AC (kW) trebuie să fie un număr pozitiv.")
    if putere_dc and putere_ac:
        raport = putere_dc / putere_ac
        if not (0.8 <= raport <= 3.0):
            erori.append(f"Raportul putere DC/putere AC ({raport:.2f}) e neplauzibil — verificați "
                          f"unitățile de măsură (raportul tipic e între 1,0 și 1,6).")
    date["putere_dc_kwp"], date["putere_ac_kw"] = putere_dc, putere_ac

    inclinare = _numar(form, "inclinare_grade")
    if inclinare is not None and not (0 <= inclinare <= 90):
        erori.append("Înclinarea trebuie să fie între 0 și 90 de grade.")
    azimut = _numar(form, "azimut_grade")
    if azimut is not None and not (0 <= azimut < 360):
        erori.append("Azimutul trebuie să fie între 0 și 360 de grade (180 = sud).")
    date["inclinare_grade"], date["azimut_grade"] = inclinare, azimut

    date["tip_asezare"] = form.get("tip_asezare") or "fixed"
    date["tehnologie_panou"] = form.get("tehnologie_panou") or "monocrystalline"
    return date, erori


def valideaza_parc_eolian(form) -> tuple[dict, list[str]]:
    date, erori = valideaza_parc_comun(form)

    numar_turbine = _numar(form, "numar_turbine")
    if numar_turbine is None or numar_turbine < 1 or numar_turbine != int(numar_turbine):
        erori.append("Numărul de turbine trebuie să fie un întreg pozitiv.")
    putere_nominala = _numar(form, "putere_nominala_kw")
    if putere_nominala is None or putere_nominala <= 0:
        erori.append("Puterea nominală per turbină trebuie să fie un număr pozitiv.")
    inaltime_butuc = _numar(form, "inaltime_butuc_m")
    if inaltime_butuc is None or not (10 <= inaltime_butuc <= 300):
        erori.append("Înălțimea butucului trebuie să fie între 10 și 300 m.")
    date["numar_turbine"] = int(numar_turbine) if numar_turbine else None
    date["putere_nominala_kw"], date["inaltime_butuc_m"] = putere_nominala, inaltime_butuc

    v_in = _numar(form, "viteza_pornire_ms")
    v_nom = _numar(form, "viteza_nominala_ms")
    v_out = _numar(form, "viteza_oprire_ms")
    if None not in (v_in, v_nom, v_out):
        if not (v_in < v_nom < v_out):
            erori.append("Vitezele trebuie să fie crescătoare: pornire < nominală < oprire.")
    else:
        erori.append("Completați cele 3 viteze simple (pornire/nominală/oprire) — "
                      "curba exactă a producătorului se poate introduce ulterior din Modul tehnic.")
    date["viteza_pornire_ms"], date["viteza_nominala_ms"], date["viteza_oprire_ms"] = v_in, v_nom, v_out
    return date, erori
