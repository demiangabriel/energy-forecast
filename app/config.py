"""
Configurare aplicație — DOUĂ niveluri distincte, conform R-1401 / R-1402:

1. Configurare de MEDIU (.env) — chei API, parole SMTP, calea bazei de date,
   ce storage backend se folosește. Se schimbă rar, ține de UNDE rulează
   aplicația, nu de CUM calculează. Nu e editabilă din interfață și nu se
   scrie niciodată în cod (R-504).

2. Setări de OPERARE (tabela `setari` din baza de date, editabilă din
   Modulul tehnic -> Setări). Sunt praguri, procente, ore — tot ce operatorul
   ar putea vrea să schimbe fără să atingă codul (R-1401). Fiecare are aici
   o valoare implicită și o explicație, afișate direct în formular (R-1303).

Ce NU e aici, conform R-1402: logica propriu-zisă de calcul (cum se calculează
puterea, cum se combină sursele meteo) — aceea rămâne în cod, în core/.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Loader minimal pentru .env — fără dependență externă, ca fișierul să
    rămână ușor de citit de la un capăt la altul (R-1403). Nu suprascrie
    variabile deja setate direct în mediul de execuție."""
    if not path.exists():
        return
    for linie in path.read_text(encoding="utf-8").splitlines():
        linie = linie.strip()
        if not linie or linie.startswith("#") or "=" not in linie:
            continue
        cheie, _, valoare = linie.partition("=")
        os.environ.setdefault(cheie.strip(), valoare.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class MediuConfig:
    storage_backend: str = os.environ.get("STORAGE_BACKEND", "sqlite")
    sqlite_path: str = os.environ.get("SQLITE_PATH", "data/prognoza.db")
    firestore_project_id: str = os.environ.get("FIRESTORE_PROJECT_ID", "")
    smtp_host: str = os.environ.get("SMTP_HOST", "")
    smtp_port: int = int(os.environ.get("SMTP_PORT", "587") or 587)
    smtp_user: str = os.environ.get("SMTP_USER", "")
    smtp_password: str = os.environ.get("SMTP_PASSWORD", "")
    smtp_from_name: str = os.environ.get("SMTP_FROM_NAME", "Prognoza Producție Energie")
    smtp_use_tls: bool = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
    open_meteo_base_url: str = os.environ.get("OPEN_METEO_BASE_URL", "https://api.open-meteo.com/v1/forecast")
    open_meteo_archive_url: str = os.environ.get(
        "OPEN_METEO_ARCHIVE_URL", "https://historical-forecast-api.open-meteo.com/v1/forecast"
    )
    open_meteo_reanaliza_url: str = os.environ.get(
        "OPEN_METEO_REANALIZA_URL", "https://archive-api.open-meteo.com/v1/archive"
    )
    app_timezone: str = os.environ.get("APP_TIMEZONE", "Europe/Bucharest")

    def email_configurat(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

    def sqlite_path_absolut(self) -> Path:
        p = Path(self.sqlite_path)
        return p if p.is_absolute() else (BASE_DIR / p)


MEDIU = MediuConfig()


# ---------------------------------------------------------------------------
# Setări de operare — praguri editabile din interfață (R-1401).
# Fiecare intrare: valoare implicită + etichetă + explicație afișate operatorului.
# ---------------------------------------------------------------------------

SETARI_IMPLICITE: dict[str, dict] = {
    "prag_min_ore_antrenare_ml": {
        "valoare": 1500, "tip": "int",
        "eticheta": "Prag minim de ore pentru antrenarea modelului ML",
        "explicatie": "Sub acest număr de ore cu producție reală cunoscută, antrenarea "
                       "corectorului ML este refuzată explicit (R-606). Implicit ~1500 ore (~6 luni).",
    },
    "prag_min_ore_calibrare": {
        "valoare": 300, "tip": "int",
        "eticheta": "Prag minim de ore pentru calibrarea geometriei",
        "explicatie": "Sub acest număr de ore istorice disponibile, calibrarea înclinării/"
                       "azimutului refuză explicit rezultatul (R-705).",
    },
    "zile_mod_umbra_recomandat": {
        "valoare": 30, "tip": "int",
        "eticheta": "Zile recomandate în mod umbră înainte de dezactivare",
        "explicatie": "Recomandare, nu blocaj: câte zile de probă să treacă înainte ca "
                       "operatorul să scoată manual un parc din modul umbră (R-1504).",
    },
    "ora_rulare_zilnica_locala": {
        "valoare": "08:00", "tip": "str",
        "eticheta": "Ora rulării automate zilnice (ora României)",
        "explicatie": "Rularea de bază are loc o dată pe zi, la această oră locală (R-901).",
    },
    "zile_orizont_implicit": {
        "valoare": 3, "tip": "int",
        "eticheta": "Orizont implicit de prognoză (zile)",
        "explicatie": "Câte zile înainte se calculează la rularea de bază, pe lângă ziua "
                       "curentă (R-901). Ziua curentă e exclusă din raport.",
    },
    "zile_orizont_maxim": {
        "valoare": 16, "tip": "int",
        "eticheta": "Orizont maxim permis la cerere (zile)",
        "explicatie": "Limită practică peste care prognozele meteo nu mai sunt fiabile (R-906).",
    },
    "prag_productie_noaptea_pct": {
        "valoare": 1.0, "tip": "float",
        "eticheta": "Prag producție acceptabilă noaptea (% din puterea instalată)",
        "explicatie": "Control de calitate: producție peste acest procent din puterea "
                       "instalată în afara ferestrei orare plauzibile e semnalată (R-1503).",
    },
    "prag_salt_brusc_pct": {
        "valoare": 60.0, "tip": "float",
        "eticheta": "Prag salt brusc față de rularea precedentă (%)",
        "explicatie": "O schimbare mai mare decât acest procent față de rularea anterioară, "
                       "pentru aceeași oră, e semnalată ca posibilă eroare (R-1503).",
    },
    "toleranta_fus_orar_minute": {
        "valoare": 30, "tip": "int",
        "eticheta": "Toleranță verificare fus orar la import (minute)",
        "explicatie": "Diferența maximă acceptată între amiaza solară reală și amiaza "
                       "aparentă din fișierul importat, înainte de a semnala o neconcordanță (R-1104).",
    },
    "prag_serie_constanta_ore": {
        "valoare": 6, "tip": "int",
        "eticheta": "Prag serie suspect de constantă (ore consecutive)",
        "explicatie": "Un număr de ore consecutive cu exact aceeași valoare meteo sugerează "
                       "o sursă blocată/înghețată (R-1503).",
    },
    "prag_cf_zilnic_max_pv_pct": {
        "valoare": 32.0, "tip": "float",
        "eticheta": "Factor de capacitate zilnic maxim plauzibil — fotovoltaic (%)",
        "explicatie": "Energia zilnică peste acest procent din (putere AC x 24h) e semnalată ca "
                       "implauzibilă pentru un parc fotovoltaic, în orice lună (R-1503).",
    },
    "prag_abatere_portofoliu_pct": {
        "valoare": 20.0, "tip": "float",
        "eticheta": "Prag abatere pentru starea 'abatere' pe ecranul de portofoliu (%)",
        "explicatie": "Diferența față de producția reală a zilei anterioare peste care un "
                       "parc e marcat cu starea 'abatere' pe ecranul de portofoliu (R-303).",
    },
    "prag_cf_zilnic_max_eolian_pct": {
        "valoare": 95.0, "tip": "float",
        "eticheta": "Factor de capacitate zilnic maxim plauzibil — eolian (%)",
        "explicatie": "Energia zilnică peste acest procent din (putere instalată x 24h) e "
                       "semnalată ca implauzibilă pentru un parc eolian (R-1503).",
    },
    # -----------------------------------------------------------------------
    # SMTP — INTENȚIONAT în setările de operare, NU doar în .env (secțiunea 16).
    # Motivul: setările de operare trăiesc în Repository — pe Firestore, ajung
    # automat acolo, împreună cu restul bazei de date (R-1602). Rularea din
    # GitHub Actions nu mai are nevoie de secrete SMTP separate — doar de
    # credențiala Firestore (care, ea, TREBUIE să rămână în afara bazei de
    # date: e nevoie de ea CA SĂ AJUNGI la Firestore, deci nu se poate stoca
    # acolo — vezi setari.smtp_config()). O valoare goală aici înseamnă
    # "folosește .env" — pentru compatibilitate cu instalarea locală inițială.
    "smtp_host": {
        "valoare": "", "tip": "str", "secret": False,
        "eticheta": "Server SMTP (gol = folosește .env)",
        "explicatie": "Suprascrie SMTP_HOST din .env. Se salvează în baza de date (Firestore, "
                       "după migrare) — utilă mai ales pentru rularea automată din GitHub Actions, "
                       "care altfel ar avea nevoie de secrete SMTP separate.",
    },
    "smtp_port": {
        "valoare": 587, "tip": "int", "secret": False,
        "eticheta": "Port SMTP (gol = folosește .env)",
        "explicatie": "Suprascrie SMTP_PORT din .env.",
    },
    "smtp_user": {
        "valoare": "", "tip": "str", "secret": False,
        "eticheta": "Utilizator SMTP (gol = folosește .env)",
        "explicatie": "Suprascrie SMTP_USER din .env.",
    },
    "smtp_password": {
        "valoare": "", "tip": "str", "secret": True,
        "eticheta": "Parolă SMTP (gol = folosește .env)",
        "explicatie": "Suprascrie SMTP_PASSWORD din .env. Câmp mascat în formular.",
    },
    "smtp_from_name": {
        "valoare": "", "tip": "str", "secret": False,
        "eticheta": "Nume expeditor (gol = folosește .env)",
        "explicatie": "Suprascrie SMTP_FROM_NAME din .env.",
    },
    "smtp_use_tls": {
        "valoare": "", "tip": "str", "secret": False,
        "eticheta": "STARTTLS — true/false (gol = folosește .env)",
        "explicatie": "Suprascrie SMTP_USE_TLS din .env.",
    },
}
