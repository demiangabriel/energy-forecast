"""
Emailul către clienți — secțiunea 10 (R-1001-1008).

Regulile de business critice sunt toate aici, explicit, NU ascunse în
straturi de configurare:
 - un singur email pe zi, PER parc (R-1001);
 - parc în mod umbră -> NICIODATĂ niciun email, indiferent de altă acțiune
   (R-1006) — verificarea e prima din funcție, ca să nu poată fi ocolită;
 - fără destinatari configurați -> nu e eroare, doar nu se trimite (R-1003);
 - prognoză blocată de controale de calitate / rulare eșuată -> nu se
   trimite niciodată (R-1007);
 - fiecare încercare (reușită, eșuată sau sărită) se jurnalizează (R-1008).

R-905: trimiterea e o acțiune SEPARATĂ, explicită — niciodată declanșată
automat doar pentru că s-a calculat o prognoză (excepție: rularea
PROGRAMATĂ de bază, unde trimiterea e parte din secvența zilnică prin
app/core/daily_run.py, dar tot ca pas distinct, nu contopit în calcul).
"""
from __future__ import annotations

import csv
import io
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from app import setari as setari_mod
from app.core.formatare import kw_in_mw
from app.models import IncercareEmail, OraPrognoza, Parc, Rulare, new_id


def _construieste_rezumat_zilnic(ore: list[OraPrognoza], fus_orar: str) -> list[dict]:
    tz = ZoneInfo(fus_orar)
    pe_zi: dict = {}
    for o in ore:
        ts_local = datetime.fromisoformat(o.timestamp_utc).astimezone(tz)
        zi = ts_local.date()
        pe_zi.setdefault(zi, {"energie_kwh": 0.0, "varf_kw": 0.0})
        pe_zi[zi]["energie_kwh"] += o.putere_finala_kw or 0.0
        pe_zi[zi]["varf_kw"] = max(pe_zi[zi]["varf_kw"], o.putere_finala_kw or 0.0)
    return [{"zi": zi, **date} for zi, date in sorted(pe_zi.items())]


def _construieste_csv(ore: list[OraPrognoza], fus_orar: str) -> str:
    """Aceeași structură ca Anexa A (timestamp;kWh) — dar în ORA LOCALĂ a
    parcului, conform R-511 ("conversia la ora locală se face DOAR la
    afișare și la trimiterea raportului")."""
    tz = ZoneInfo(fus_orar)
    buf = io.StringIO()
    scriitor = csv.writer(buf, delimiter=";")
    scriitor.writerow(["timestamp", "kWh_prognozat"])
    for o in ore:
        ts_local = datetime.fromisoformat(o.timestamp_utc).astimezone(tz)
        scriitor.writerow([ts_local.strftime("%Y-%m-%d %H:%M"),
                            f"{(o.putere_finala_kw or 0):.2f}"])
    return buf.getvalue()


def _construieste_mesaj(parc: Parc, ore: list[OraPrognoza], smtp: dict) -> EmailMessage:
    rezumat = _construieste_rezumat_zilnic(ore, parc.fus_orar)
    linii = [f"Prognoză de producție — {parc.nume} ({parc.cod})", ""]
    for zi in rezumat:
        # rezumatul din corpul emailului e în MW/MWh (cerința operatorului); atașamentul
        # CSV rămâne în kWh, ca format de date, conform Anexei A (R-1101).
        linii.append(f"  {zi['zi'].strftime('%d.%m.%Y')}: energie prognozată "
                      f"{kw_in_mw(zi['energie_kwh']):.3f} MWh, vârf de putere {kw_in_mw(zi['varf_kw']):.3f} MW")
    linii += ["", "În atașament: date orare complete (fișier CSV).", "",
              "Acest raport e generat automat de platforma de prognoză."]

    mesaj = EmailMessage()
    mesaj["Subject"] = f"Prognoză producție — {parc.nume} — {datetime.now().strftime('%d.%m.%Y')}"
    mesaj["From"] = f"{smtp['from_name']} <{smtp['user']}>"
    mesaj["To"] = ", ".join(parc.destinatari_email)
    mesaj.set_content("\n".join(linii))

    continut_csv = _construieste_csv(ore, parc.fus_orar)
    mesaj.add_attachment(continut_csv.encode("utf-8"), maintype="text", subtype="csv",
                          filename=f"prognoza_{parc.cod}_{datetime.now().strftime('%Y%m%d')}.csv")
    return mesaj


def _trimite_smtp(mesaj: EmailMessage, smtp: dict) -> None:
    with smtplib.SMTP(smtp["host"], smtp["port"], timeout=30) as server:
        if smtp["use_tls"]:
            server.starttls()
        server.login(smtp["user"], smtp["password"])
        server.send_message(mesaj)


def trimite_raport_email(repo, parc: Parc, rulare: Rulare) -> IncercareEmail:
    moment = datetime.now(timezone.utc).isoformat()

    def _salveaza(rezultat: str, catre: list[str], motiv: str | None) -> IncercareEmail:
        incercare = IncercareEmail(id=new_id(), parc_id=parc.id, rulare_id=rulare.id,
                                    catre=catre, moment=moment, rezultat=rezultat, motiv=motiv)
        repo.salveaza_incercare_email(incercare)
        return incercare

    # R-1006 — prima verificare, absolut, indiferent de restul stării.
    if parc.mod_umbra:
        return _salveaza("sarit", [], "Parcul e în mod umbră — nu se trimite niciun email (R-1006).")

    if not parc.destinatari_email:
        return _salveaza("sarit", [], "Niciun destinatar configurat pentru acest parc — "
                                       "funcționare normală, nu eroare (R-1003).")

    ore_valide = [o for o in rulare.ore if not o.blocat and o.putere_finala_kw is not None]
    if rulare.stare == "esuata" or not ore_valide:
        return _salveaza("sarit", parc.destinatari_email,
                          "Prognoza a fost blocată de controalele de calitate sau rularea a eșuat "
                          "— nu se trimite (R-1007).")

    smtp = setari_mod.smtp_config(repo)
    if not (smtp["host"] and smtp["user"] and smtp["password"]):
        return _salveaza("esuat", parc.destinatari_email,
                          "SMTP neconfigurat (nici în .env, nici în Setări de operare) — "
                          "emailul nu a fost trimis efectiv.")

    try:
        mesaj = _construieste_mesaj(parc, ore_valide, smtp)
        _trimite_smtp(mesaj, smtp)
    except Exception as exc:
        return _salveaza("esuat", parc.destinatari_email, f"Eroare la trimitere: {exc}")

    return _salveaza("reusit", parc.destinatari_email, None)
