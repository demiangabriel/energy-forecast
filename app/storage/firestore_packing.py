"""
Împachetare compactă pentru datele orare din Firestore (producție reală
măsurată, verdicte de control al calității).

DE CE: `FirestoreRepository` scria inițial UN DOCUMENT PER ORĂ
(`parcuri/{id}/productie/{timestamp_utc}`). Pentru un singur parc cu câțiva
ani de istoric orar, asta înseamnă zeci de mii de documente — și, mai grav,
zeci de mii de SCRIERI dintr-un singur import istoric (secțiunea 11) și tot
atâtea CITIRI la fiecare interogare pe o perioadă lungă (raport lunar,
verificare, comparație). Cota gratuită Firestore e 20.000 de scrieri/zi și
50.000 de citiri/zi — un import de puțin peste 2 ani de istoric orar, sau o
mână de rapoarte anuale generate în aceeași zi, ar epuiza cota doar din
citiri/scrieri de rutină, înainte de a conta costul lor bănesc peste cotă.

SOLUȚIA: se grupează rândurile pe ZIUA CALENDARISTICĂ UTC a timestamp-ului
— un singur document per zi, cu toate orele acelei zile într-o hartă (map).
Asta reduce numărul de documente, citiri și scrieri de ~24×, cu costul
acceptabil al unui document puțin mai mare (o zi la rezoluție de 15 minute
înseamnă 96 de intrări mici — tot cu mult sub limita Firestore de 1 MiB per
document). Aceeași idee ca `meteo_cache` (deja organizat pe zi) — aplicată
acum și la producție/calitate.

Funcțiile de mai jos sunt PURE (dict/listă in, dict/listă out) — nu ating
rețeaua — special ca împachetarea să se poată testa exhaustiv fără
credențiale Firestore (care nu există în acest mediu — vezi
`firestore_repo.py`). `FirestoreRepository` le folosește direct; SQLite nu
are nevoie de ele (o tabelă indexată e deja eficientă la nivel de rând).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.models import CalitateOra, ProductieOrara


def _zi_din_timestamp(ts_utc: str) -> str:
    """Ziua calendaristică UTC (AAAA-LL-ZZ) a unui timestamp ISO 8601 —
    `timestamp_utc` începe mereu cu data, indiferent de offset-ul scris."""
    return ts_utc[:10]


def grupeaza_productie_pe_zi(randuri: list[ProductieOrara]) -> dict[str, dict[str, float]]:
    """{zi_utc: {timestamp_utc: energie_kwh}} — un grup (= un document
    Firestore) per zi calendaristică UTC prezentă în `randuri`."""
    grupuri: dict[str, dict[str, float]] = {}
    for r in randuri:
        grupuri.setdefault(_zi_din_timestamp(r.timestamp_utc), {})[r.timestamp_utc] = r.energie_kwh
    return grupuri


def despacheteaza_productie(parc_id: str, payload: dict | None) -> list[ProductieOrara]:
    """Inversul lui `grupeaza_productie_pe_zi`, pentru payload-ul UNUI
    document (o singură zi)."""
    ore = (payload or {}).get("ore") or {}
    return [ProductieOrara(parc_id=parc_id, timestamp_utc=ts, energie_kwh=val) for ts, val in ore.items()]


def grupeaza_calitate_pe_zi(randuri: list[CalitateOra]) -> dict[str, dict[str, dict]]:
    """{zi_utc: {timestamp_utc: {"steag":..., "detaliu":...}}}."""
    grupuri: dict[str, dict[str, dict]] = {}
    for r in randuri:
        grupuri.setdefault(_zi_din_timestamp(r.timestamp_utc), {})[r.timestamp_utc] = {
            "steag": r.steag, "detaliu": r.detaliu,
        }
    return grupuri


def despacheteaza_calitate(parc_id: str, payload: dict | None) -> list[CalitateOra]:
    ore = (payload or {}).get("ore") or {}
    return [CalitateOra(parc_id=parc_id, timestamp_utc=ts, steag=v.get("steag"), detaliu=v.get("detaliu"))
            for ts, v in ore.items()]


def zile_din_interval(inceput_utc: str, sfarsit_utc: str) -> list[str]:
    """Zilele calendaristice UTC (AAAA-LL-ZZ), capete incluse, acoperite de
    [inceput_utc, sfarsit_utc] — folosite ca listă de ID-uri de documente de
    citit direct (Firestore n-are nevoie de o interogare pe range peste
    fiecare oră, doar de-a citi cele câteva zeci de documente-zi ale
    perioadei)."""
    a = datetime.fromisoformat(inceput_utc).date()
    b = datetime.fromisoformat(sfarsit_utc).date()
    if b < a:
        a, b = b, a
    zile, zi = [], a
    while zi <= b:
        zile.append(zi.isoformat())
        zi += timedelta(days=1)
    return zile


def filtreaza_interval(randuri: list, inceput_utc: str, sfarsit_utc: str) -> list:
    """Zilele-document de la capetele intervalului pot conține și ore din
    AFARA lui (o zi întreagă e citită chiar dacă intervalul cere doar câteva
    ore din ea) — filtrare finală, exactă, pe `timestamp_utc`, aplicată după
    despachetare."""
    return sorted((r for r in randuri if inceput_utc <= r.timestamp_utc <= sfarsit_utc),
                  key=lambda r: r.timestamp_utc)
