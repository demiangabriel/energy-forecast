"""
Fusurile orare disponibile pentru parcuri — la creare, la reconfigurare
tehnică și la importul istoricului (R-511).

Platforma rulează, ca aplicație, în ora României și a Moldovei (R-xxx —
afișarea și rapoartele folosesc `APP_TIMEZONE`), dar fiecare PARC are
propriul fus orar declarat, folosit doar ca să interpreteze corect orele
LOCALE dintr-un fișier CSV importat (R-511). Inițial erau acceptate doar
Europe/Bucharest și Europe/Chisinau; lista de mai jos o extinde, ca
platforma să poată acoperi și parcuri din alte țări din regiune.

Validarea (`fus_orar_valid`) nu se limitează însă la lista afișată în
`<select>` — acceptă ORICE fus orar IANA valid (`zoneinfo`), ca un fus
tastat direct sau importat dintr-o configurare externă să nu fie respins
doar pentru că nu e în lista curată de mai jos.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo, available_timezones

# Fusurile „principale" — primele afișate, cele mai folosite de platformă.
FUSURI_PRINCIPALE = ["Europe/Bucharest", "Europe/Chisinau"]

# Alte fusuri europene relevante pentru parcuri fotovoltaice/eoliene din
# regiune — completare, nu înlocuire. Ordonate aproximativ geografic.
FUSURI_EUROPA_EXTINSA = [
    "Europe/Sofia", "Europe/Budapest", "Europe/Belgrade", "Europe/Zagreb",
    "Europe/Ljubljana", "Europe/Sarajevo", "Europe/Skopje", "Europe/Podgorica",
    "Europe/Tirane", "Europe/Athens", "Europe/Istanbul", "Europe/Kyiv",
    "Europe/Warsaw", "Europe/Prague", "Europe/Bratislava", "Europe/Vienna",
    "Europe/Berlin", "Europe/Rome", "Europe/Madrid", "Europe/Paris",
    "Europe/Amsterdam", "Europe/Brussels", "Europe/Lisbon", "Europe/Dublin",
    "Europe/London", "Europe/Helsinki", "Europe/Stockholm", "Europe/Oslo",
    "Europe/Copenhagen", "Europe/Riga", "Europe/Vilnius", "Europe/Tallinn",
    "Europe/Moscow", "UTC",
]

_ETICHETE = {
    "Europe/Bucharest": "Europe/Bucharest (România)",
    "Europe/Chisinau": "Europe/Chisinau (Moldova)",
    "Europe/Sofia": "Europe/Sofia (Bulgaria)",
    "Europe/Budapest": "Europe/Budapest (Ungaria)",
    "Europe/Belgrade": "Europe/Belgrade (Serbia)",
    "Europe/Zagreb": "Europe/Zagreb (Croația)",
    "Europe/Ljubljana": "Europe/Ljubljana (Slovenia)",
    "Europe/Sarajevo": "Europe/Sarajevo (Bosnia și Herțegovina)",
    "Europe/Skopje": "Europe/Skopje (Macedonia de Nord)",
    "Europe/Podgorica": "Europe/Podgorica (Muntenegru)",
    "Europe/Tirane": "Europe/Tirane (Albania)",
    "Europe/Athens": "Europe/Athens (Grecia)",
    "Europe/Istanbul": "Europe/Istanbul (Turcia)",
    "Europe/Kyiv": "Europe/Kyiv (Ucraina)",
    "Europe/Warsaw": "Europe/Warsaw (Polonia)",
    "Europe/Prague": "Europe/Prague (Cehia)",
    "Europe/Bratislava": "Europe/Bratislava (Slovacia)",
    "Europe/Vienna": "Europe/Vienna (Austria)",
    "Europe/Berlin": "Europe/Berlin (Germania)",
    "Europe/Rome": "Europe/Rome (Italia)",
    "Europe/Madrid": "Europe/Madrid (Spania)",
    "Europe/Paris": "Europe/Paris (Franța)",
    "Europe/Amsterdam": "Europe/Amsterdam (Olanda)",
    "Europe/Brussels": "Europe/Brussels (Belgia)",
    "Europe/Lisbon": "Europe/Lisbon (Portugalia)",
    "Europe/Dublin": "Europe/Dublin (Irlanda)",
    "Europe/London": "Europe/London (Marea Britanie)",
    "Europe/Helsinki": "Europe/Helsinki (Finlanda)",
    "Europe/Stockholm": "Europe/Stockholm (Suedia)",
    "Europe/Oslo": "Europe/Oslo (Norvegia)",
    "Europe/Copenhagen": "Europe/Copenhagen (Danemarca)",
    "Europe/Riga": "Europe/Riga (Letonia)",
    "Europe/Vilnius": "Europe/Vilnius (Lituania)",
    "Europe/Tallinn": "Europe/Tallinn (Estonia)",
    "Europe/Moscow": "Europe/Moscow (Rusia)",
    "UTC": "UTC",
}


def _fara_duplicate(fusuri: list[str]) -> list[str]:
    vazute: set[str] = set()
    rezultat: list[str] = []
    for f in fusuri:
        if f not in vazute:
            vazute.add(f)
            rezultat.append(f)
    return rezultat


FUSURI_SELECT = _fara_duplicate(FUSURI_PRINCIPALE + FUSURI_EUROPA_EXTINSA)


def eticheta(fus: str) -> str:
    return _ETICHETE.get(fus, fus)


def optiuni_select() -> list[tuple[str, str]]:
    """(valoare, etichetă) pentru `<select>`, în ordinea de afișare —
    România/Moldova primele, apoi restul regiunii."""
    return [(f, eticheta(f)) for f in FUSURI_SELECT]


def fus_orar_pentru_import(parc) -> str:
    """R-511/R-xxx: fusul folosit ca să interpreteze orele LOCALE dintr-un
    CSV importat poate fi diferit de `parc.fus_orar` (care guvernează
    granița de zi/afișarea prognozelor) — de exemplu când exportul din
    SCADA/contor folosește o convenție orară FIXĂ (fără oră de vară/iarnă),
    diferită de fusul IANA ales pentru prognoze. Un `fus_orar_import` gol
    (implicit, la toate parcurile existente) înseamnă „folosește `fus_orar`",
    exact comportamentul dinainte de introducerea acestui câmp."""
    return parc.fus_orar_import or parc.fus_orar


def fus_orar_valid(fus: str | None) -> bool:
    """Acceptă orice fus orar IANA valid, nu doar cele din lista afișată
    în `<select>` — lista curată de mai sus e comoditate de interfață,
    nu o restricție reală asupra a ce poate acoperi platforma."""
    if not fus:
        return False
    try:
        if fus in available_timezones():
            return True
    except Exception:
        pass
    try:
        ZoneInfo(fus)
        return True
    except Exception:
        return False
