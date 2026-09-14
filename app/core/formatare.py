"""
Conversie kW/kWh → MW/MWh, DOAR pentru afișare (Panou, e-mail către client,
raportul Excel lunar).

Intern, toate calculele rămân în kW/kWh — fizica (pv_model.py, wind_model.py),
ML-ul și verificarea sunt documentate și testate în această unitate, iar
zona tehnică (configurarea parcului, importul CSV conform Anexei A) continuă
să folosească kW/kWh, pentru că așa sunt specificate formatele de intrare
(R-1101) și fișele tehnice ale echipamentelor (putere DC/AC, kWp). Conversia
la MW se face STRICT în acest modul, o singură dată, ca operatorul văzut de
client (Panoul) să afișeze cifre în unitatea cerută fără să riște vreo
eroare de rotunjire strecurată în calculele propriu-zise.
"""
from __future__ import annotations

ZECIMALE_MW = 3


def kw_in_mw(valoare_kw: float | None) -> float | None:
    """1000 kWh = 1 MWh, 1000 kW = 1 MW — aceeași conversie pentru putere și energie."""
    if valoare_kw is None:
        return None
    return valoare_kw / 1000.0


def fmt_mw(valoare_kw: float | None) -> str:
    """Formatul cerut de operator: MW cu 3 zecimale, ex. 12.345. Rezoluția de
    3 zecimale în MW corespunde exact rezoluției de 1 kW — nu se pierde nimic
    față de afișarea anterioară în kW, doar se schimbă unitatea."""
    mw = kw_in_mw(valoare_kw)
    if mw is None:
        return "—"
    return f"{mw:.{ZECIMALE_MW}f}"
