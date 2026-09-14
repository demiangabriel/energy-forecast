"""
Orchestrarea controlului de calitate pe producția măsurată a unui parc.

Separat de `data_quality.py` (care conține algoritmii puri, fără acces la
baza de date) pentru același motiv pentru care motoarele fizice nu știu de
Repository: algoritmii trebuie să rămână testabili direct, pe serii de date,
fără infrastructură.

Aici se face doar legătura: citește producția și configurația parcului,
calculează așteptarea fizică, cheamă analiza, salvează verdictele.
"""
from __future__ import annotations

from app.core.clearsky import putere_referinta
from app.core.data_quality import RaportCalitate, analizeaza_calitate
from app.core.istoric_productie import citeste_productie_df
from app.models import Parc, Tehnologie


def ruleaza_control_calitate(repo, parc: Parc, foloseste_asteptare_fizica: bool = True) -> RaportCalitate:
    """Rulează controlul de calitate și SALVEAZĂ verdictele.

    `foloseste_asteptare_fizica` cere reconstrucția istorică a prognozei
    fizice (interogare de rețea către arhiva meteo). Dacă nu e disponibilă,
    analiza continuă fără detecția de indisponibilitate/limitare — restul
    verificărilor (decalaj de timp, producție noaptea, valori blocate,
    outlieri) nu au nevoie de ea.
    """
    from app.core.forecast_engine import putere_instalata_kw

    productie = citeste_productie_df(repo, parc.id)
    if productie.empty:
        return RaportCalitate()

    serie = productie["productie_kwh"]
    instalata = putere_instalata_kw(parc)

    referinta_cs = None
    if parc.tehnologie == Tehnologie.FOTOVOLTAIC:
        try:
            referinta_cs = putere_referinta(parc, serie.index)
        except Exception:
            referinta_cs = None

    asteptare = None
    if foloseste_asteptare_fizica:
        try:
            from app.core.weather_historical import reconstruieste_prognoza_fizica
            fizic = reconstruieste_prognoza_fizica(
                parc, serie.index.min().date(), serie.index.max().date())
            asteptare = fizic["putere_ac_fizic_kw"].reindex(serie.index)
        except Exception:
            asteptare = None

    raport = analizeaza_calitate(parc, serie, asteptare, referinta_cs, instalata)

    # verdictele înlocuiesc complet analiza anterioară (re-rularea e idempotentă)
    repo.sterge_calitate_productie(parc.id)
    if raport.verdicte:
        repo.salveaza_calitate_productie(raport.verdicte)
    return raport


def rezumat_calitate(repo, parc: Parc) -> dict:
    """Rezumatul pentru interfață: câte ore sunt marcate, pe ce categorii."""
    verdicte = repo.listeaza_calitate_productie(parc.id)
    total_ore = repo.numar_ore_productie(parc.id)
    pe_steag: dict[str, int] = {}
    for v in verdicte:
        pe_steag[v.steag] = pe_steag.get(v.steag, 0) + 1
    return {
        "total_ore": total_ore,
        "ore_marcate": len(verdicte),
        "procent_curat": round((1 - len(verdicte) / total_ore) * 100, 1) if total_ore else None,
        "pe_steag": dict(sorted(pe_steag.items(), key=lambda kv: -kv[1])),
        "exemple": verdicte[:200],
    }


ETICHETE_STEAG = {
    "noapte_nenul": "Producție raportată noaptea",
    "valoare_blocata": "Valoare blocată (senzor înghețat)",
    "indisponibilitate": "Indisponibilitate (producție mult sub așteptare)",
    "limitare": "Limitare de putere (curtailment)",
    "valoare_extrema": "Valoare extremă (outlier)",
    "peste_instalata": "Peste puterea instalată",
    "ok": "Fără probleme",
}

EXPLICATII_STEAG = {
    "noapte_nenul": "Contorul raportează energie când soarele e sub orizont. De obicei un "
                     "decalaj de fus orar la export sau un semn inversat.",
    "valoare_blocata": "Aceeași valoare se repetă multe ore la rând — tipic pentru un senzor "
                        "sau o conexiune de date înghețată, nu pentru producție reală.",
    "indisponibilitate": "Producția a fost mult sub ce permitea vremea, susținut. Tipic: invertor "
                          "oprit, lucrări, avarie. Aceste ore NU se folosesc la antrenarea modelului.",
    "limitare": "Producția a stat pe un platou neobișnuit de plat, sub așteptarea fizică — semnătura "
                 "unei limitări de putere impuse. Se exclude din antrenare și se raportează separat.",
    "valoare_extrema": "Valoare izolată, foarte diferită de vecinătatea ei (filtru Hampel).",
    "peste_instalata": "Valoare peste puterea instalată a parcului — imposibilă fizic; de obicei o "
                        "eroare de unitate de măsură sau de contor.",
}
