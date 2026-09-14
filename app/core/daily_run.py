"""
Rularea pe tot portofoliul — R-901-906. Punctul de intrare folosit atât de
rularea PROGRAMATĂ zilnică (scripts/run_daily.py), cât și de un declanșator
manual "rulează tot portofoliul acum" (R-904).

R-1603: eșecul unui singur parc (sursă meteo picată, date lipsă) NU trebuie
să oprească procesarea celorlalte — izolare completă per activ.
"""
from __future__ import annotations

from app.core.email_report import trimite_raport_email
from app.core.forecast_engine import ruleaza_prognoza


def ruleaza_portofoliu(repo, tip: str = "programata", zile_viitor: int | None = None,
                        trimite_email: bool = False) -> list[dict]:
    """trimite_email=True e folosit DOAR de rularea programată de bază
    (R-1004 — trimitere automată, fără intervenție manuală zilnică).
    Rulările declanșate manual din interfață NU trebuie să trimită automat
    (R-905) — operatorul apasă separat "Trimite raport" după ce verifică."""
    rezultate = []
    for parc in repo.listeaza_parcuri():
        if not parc.activ:
            continue
        rezultat = {"parc_id": parc.id, "parc_nume": parc.nume, "parc_cod": parc.cod}
        try:
            rulare = ruleaza_prognoza(repo, parc, tip=tip, zile_viitor=zile_viitor)
            rezultat["rulare_id"] = rulare.id
            rezultat["stare"] = rulare.stare
            rezultat["mesaj"] = rulare.mesaj
        except Exception as exc:  # izolare per activ (R-1603) — o eroare neprevăzută nu oprește restul
            rezultat["stare"] = "esuata"
            rezultat["eroare"] = str(exc)
            rezultate.append(rezultat)
            continue

        if trimite_email:
            try:
                incercare = trimite_raport_email(repo, parc, rulare)
                rezultat["email"] = incercare.rezultat
                rezultat["email_motiv"] = incercare.motiv
            except Exception as exc:
                rezultat["email"] = "esuat"
                rezultat["email_motiv"] = str(exc)
        rezultate.append(rezultat)
    return rezultate
