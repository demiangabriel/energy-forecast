"""
Acces la setările de operare (R-1401) — combină valorile implicite din
config.SETARI_IMPLICITE cu suprascrierile salvate de operator în Repository.
Restul aplicației cheamă doar funcțiile de aici, niciodată repo.get_setare
direct, ca lista de setări valide să rămână într-un singur loc.
"""
from __future__ import annotations

from app.config import SETARI_IMPLICITE
from app.storage.repository import Repository


def get(repo: Repository, cheie: str):
    if cheie not in SETARI_IMPLICITE:
        raise KeyError(f"Setare necunoscută: {cheie}")
    implicit = SETARI_IMPLICITE[cheie]["valoare"]
    return repo.get_setare(cheie, implicit)


def set(repo: Repository, cheie: str, valoare) -> None:
    if cheie not in SETARI_IMPLICITE:
        raise KeyError(f"Setare necunoscută: {cheie}")
    tip = SETARI_IMPLICITE[cheie]["tip"]
    if tip == "int":
        valoare = int(valoare)
    elif tip == "float":
        valoare = float(valoare)
    else:
        valoare = str(valoare)
    repo.set_setare(cheie, valoare)


def toate(repo: Repository) -> dict:
    """Returnează toate setările cu valoarea curentă + metadatele de afișare (R-1303)."""
    stocate = repo.toate_setarile()
    rezultat = {}
    for cheie, meta in SETARI_IMPLICITE.items():
        rezultat[cheie] = {**meta, "valoare": stocate.get(cheie, meta["valoare"])}
    return rezultat


def smtp_config(repo: Repository) -> dict:
    """Configurarea SMTP efectivă — secțiunea 16 (migrarea la Firestore).

    Dacă operatorul a salvat un `smtp_host` în setările de operare (deci în
    Repository — pe Firestore, după migrare), acela câștigă integral față de
    `.env`: rularea din GitHub Actions poate atunci trimite rapoarte fără
    NICIUN secret SMTP configurat separat în GitHub, doar cu credențiala de
    Firestore. Altfel (instalare locală inițială, fără nimic salvat încă),
    se folosește `.env`, ca înainte.

    Credențiala Firestore în sine (FIRESTORE_PROJECT_ID + fișierul de
    credențiale) NU poate face parte din acest mecanism — e nevoie de ea CA
    SĂ AJUNGI la Firestore, deci rămâne obligatoriu în `.env`/secretele
    GitHub Actions (bootstrap, nu se poate stoca acolo unde ea însăși dă acces)."""
    from app.config import MEDIU

    host = str(get(repo, "smtp_host") or "").strip()
    if not host:
        return {
            "host": MEDIU.smtp_host, "port": MEDIU.smtp_port, "user": MEDIU.smtp_user,
            "password": MEDIU.smtp_password, "from_name": MEDIU.smtp_from_name,
            "use_tls": MEDIU.smtp_use_tls,
        }
    return {
        "host": host,
        "port": int(get(repo, "smtp_port") or 587),
        "user": str(get(repo, "smtp_user") or ""),
        "password": str(get(repo, "smtp_password") or ""),
        "from_name": str(get(repo, "smtp_from_name") or MEDIU.smtp_from_name),
        "use_tls": str(get(repo, "smtp_use_tls") or "true").strip().lower() != "false",
    }
