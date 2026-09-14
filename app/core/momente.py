"""
Formatarea canonică a momentelor de timp.

De ce există acest modul (vezi docs/DEBUGGING.md): timestamp-urile sunt
stocate ca TEXT ISO 8601, iar interogările pe interval le compară ca text
(SQLite `BETWEEN`, comparații de șiruri în Python). Comparația lexicografică
dă rezultatul corect DOAR dacă toate valorile au exact același format.

Problema concretă: `datetime.now(timezone.utc).isoformat()` produce
microsecunde (`...T10:00:00.123456+00:00`), în timp ce momentele salvate din
pandas nu (`...T10:00:00+00:00`). Lexicografic, `'+'` (0x2B) e mai mic decât
`'.'` (0x2E), deci granița de început cu microsecunde EXCLUDE tăcut
înregistrarea de la exact acel moment. E o eroare mică (o înregistrare la
margine), dar e genul care se transformă în ore de căutat mai târziu — și
devine gravă dacă vreodată apare un offset diferit de `+00:00`.

Regula casei: toate momentele se scriu și se interoghează în UTC, cu
secunde întregi, cu sufixul `+00:00`.
"""
from __future__ import annotations

from datetime import datetime, timezone


def iso_utc(moment: datetime) -> str:
    """Reprezentarea canonică a unui moment: UTC, fără microsecunde."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def acum_iso() -> str:
    return iso_utc(datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Formatele de ieșire (export CSV, rapoarte Excel)
# ---------------------------------------------------------------------------
#
# Un singur loc care decide cum arată un moment scris într-un fișier livrat.
# Motivul e concret: importul cere din v8 decalaj de fus orar explicit, dar
# exportul propriu scria ora locală FĂRĂ decalaj — adică un fișier produs de
# platformă nu trecea prin importul platformei, iar orele din nopțile
# schimbării orei erau ambigue exact ca înainte.
#
# Implicit peste tot e `iso_local`: poartă atât ora citibilă de om, cât și
# decalajul care o face neambiguă, deci se poate reimporta fără nicio
# conversie. Celelalte două există pentru sisteme din aval care cer altceva.

FORMATE_TIMP = {
    "iso_local": {
        "eticheta": "ISO 8601 cu decalaj local (recomandat)",
        "exemplu": "2026-06-01T12:00:00+03:00",
        "explicatie": "Ora locală a parcului, cu decalajul scris explicit. Neambiguu la schimbarea "
                       "orei și reimportabil în platformă fără conversie.",
    },
    "iso_utc": {
        "eticheta": "ISO 8601 în UTC",
        "exemplu": "2026-06-01T09:00:00+00:00",
        "explicatie": "Același moment, exprimat în UTC. Neambiguu; potrivit pentru sisteme care "
                       "lucrează intern în UTC.",
    },
    "local_simplu": {
        "eticheta": "Oră locală simplă, fără decalaj (nerecomandat)",
        "exemplu": "2026-06-01 12:00",
        "explicatie": "Formatul vechi. AMBIGUU în nopțile schimbării orei (o oră lipsește, alta se "
                       "repetă) — folosiți-l doar dacă sistemul din aval nu acceptă altceva.",
    },
}

FORMAT_TIMP_IMPLICIT = "iso_local"


def format_timp_valid(nume: str | None) -> str:
    """Cade pe implicit la orice valoare necunoscută — un parametru greșit în
    URL nu trebuie să producă o eroare, ci formatul sigur."""
    return nume if nume in FORMATE_TIMP else FORMAT_TIMP_IMPLICIT


def formateaza_moment(ts_utc: str, fus_orar: str, format_timp: str = FORMAT_TIMP_IMPLICIT) -> str:
    """Scrie un timestamp UTC stocat în formatul de ieșire cerut.

    `fus_orar` e folosit doar de formatele locale; pentru `iso_utc` e ignorat.
    """
    from zoneinfo import ZoneInfo

    moment = datetime.fromisoformat(ts_utc).replace(microsecond=0)
    if format_timp == "iso_utc":
        return moment.astimezone(timezone.utc).isoformat()
    local = moment.astimezone(ZoneInfo(fus_orar))
    if format_timp == "local_simplu":
        return local.strftime("%Y-%m-%d %H:%M")
    return local.isoformat()
