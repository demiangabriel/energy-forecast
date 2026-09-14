"""
Conversii explicite dataclass <-> dict, folosite de toate implementările de
Repository. Sunt scrise "de mână", câmp cu câmp, în loc de o bibliotecă
generică de serializare — ca oricine să poată citi exact ce se întâmplă cu
fiecare câmp (R-1403), fără magie de reflecție ascunsă.

TOLERANȚĂ LA EVOLUȚIA SCHEMEI (adăugat în v2): baza de date poate conține
obiecte scrise de o versiune mai veche SAU mai nouă a aplicației. Citirea
trece prin `_doar_campuri_cunoscute`, care:
  - ignoră cheile pe care versiunea curentă nu le cunoaște (JSON scris de o
    versiune mai nouă -> nu crapă la downgrade);
  - lasă câmpurile absente să cadă pe valorile implicite din dataclass
    (JSON scris de o versiune mai veche -> parcurile existente merg neschimbat).
Fără asta, orice câmp nou adăugat ar face vechea bază de date necitibilă.
"""
from __future__ import annotations

from dataclasses import asdict, fields

from app.models import (
    Parc, ConfigPV, ConfigEolian, Subansamblu, Tehnologie, TipAsezarePV,
    TehnologiePanou, ModelTermicMontaj, DirectieUmbrire, ModelTranspozitie,
    ModelTemperaturaCelula, ModSeparareRadiatie, ProfilVertical, ReglajTurbina,
    Rulare, OraPrognoza,
)


# Numele câmpurilor unui dataclass nu se schimbă în timpul rulării, dar
# `dataclasses.fields()` reconstruiește tuplul la fiecare apel. Funcția de mai
# jos e chemată o dată pentru FIECARE oră de prognoză citită din baza de date
# (sute de mii, la un portofoliu mare), deci recalcularea costa secunde întregi
# de CPU pur risipit — măsurat cu scripts/benchmark_scalare.py. Memorăm setul
# o singură dată per clasă.
_CAMPURI_CUNOSCUTE: dict[type, frozenset[str]] = {}


def _campuri(cls) -> frozenset[str]:
    cunoscute = _CAMPURI_CUNOSCUTE.get(cls)
    if cunoscute is None:
        cunoscute = frozenset(f.name for f in fields(cls))
        _CAMPURI_CUNOSCUTE[cls] = cunoscute
    return cunoscute


def _doar_campuri_cunoscute(cls, d: dict) -> dict:
    """Păstrează din `d` doar cheile care sunt câmpuri ale dataclass-ului `cls`."""
    cunoscute = _campuri(cls)
    return {k: v for k, v in d.items() if k in cunoscute}


def _enum(tip, valoare, implicit):
    """Convertește o valoare text în enum, cu revenire la implicit dacă valoarea
    stocată nu mai e validă (ex. o opțiune eliminată între versiuni)."""
    if valoare is None:
        return implicit
    try:
        return tip(valoare)
    except ValueError:
        return implicit


def parc_to_dict(p: Parc) -> dict:
    return asdict(p)


def parc_from_dict(d: dict) -> Parc:
    d = dict(d)
    d["tehnologie"] = Tehnologie(d["tehnologie"])

    if d.get("config_pv"):
        cpv = dict(d["config_pv"])
        cpv["tehnologie_panou"] = _enum(TehnologiePanou, cpv.get("tehnologie_panou"),
                                         TehnologiePanou.MONOCRYSTALLINE)
        cpv["model_termic_montaj"] = _enum(ModelTermicMontaj, cpv.get("model_termic_montaj"),
                                            ModelTermicMontaj.DESCHIS_STICLA_STICLA)
        cpv["umbrire_orizont_directie"] = _enum(DirectieUmbrire, cpv.get("umbrire_orizont_directie"),
                                                 DirectieUmbrire.AMBELE)
        cpv["model_transpozitie"] = _enum(ModelTranspozitie, cpv.get("model_transpozitie"),
                                           ModelTranspozitie.PEREZ_DRIESSE)
        cpv["model_temperatura"] = _enum(ModelTemperaturaCelula, cpv.get("model_temperatura"),
                                          ModelTemperaturaCelula.SAPM)
        cpv["mod_separare_radiatie"] = _enum(ModSeparareRadiatie, cpv.get("mod_separare_radiatie"),
                                              ModSeparareRadiatie.NWP)
        cpv["subansambluri"] = [
            Subansamblu(**_doar_campuri_cunoscute(
                Subansamblu, {**s, "tip_asezare": _enum(TipAsezarePV, s.get("tip_asezare"), TipAsezarePV.FIX)}))
            for s in cpv.get("subansambluri", [])
        ]
        d["config_pv"] = ConfigPV(**_doar_campuri_cunoscute(ConfigPV, cpv))
    else:
        d["config_pv"] = None

    if d.get("config_eolian"):
        ceo = dict(d["config_eolian"])
        ceo["profil_vertical"] = _enum(ProfilVertical, ceo.get("profil_vertical"), ProfilVertical.POWER_LAW)
        ceo["reglaj"] = _enum(ReglajTurbina, ceo.get("reglaj"), ReglajTurbina.PITCH)
        d["config_eolian"] = ConfigEolian(**_doar_campuri_cunoscute(ConfigEolian, ceo))
    else:
        d["config_eolian"] = None

    return Parc(**_doar_campuri_cunoscute(Parc, d))


def rulare_to_dict(r: Rulare) -> dict:
    return asdict(r)


def rulare_from_dict(d: dict) -> Rulare:
    d = dict(d)
    d["ore"] = [OraPrognoza(**_doar_campuri_cunoscute(OraPrognoza, o)) for o in d.get("ore", [])]
    return Rulare(**_doar_campuri_cunoscute(Rulare, d))


def din_dict(cls, d: dict):
    """Constructor tolerant generic, pentru dataclass-urile plate
    (IncercareEmail, ModelMLInfo, CalitateOra, ProductieOrara)."""
    return cls(**_doar_campuri_cunoscute(cls, d))
