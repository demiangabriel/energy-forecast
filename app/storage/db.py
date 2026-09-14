"""
Schema SQLite și conexiunea de bază. Tabelele "grele" (parcuri, rulări,
încercări de email, modele ML) țin obiectul complet ca JSON într-o coloană
TEXT, plus câteva coloane simple folosite pentru interogări/filtrare rapidă.

De ce JSON și nu coloane normalizate pentru fiecare câmp: modelele de date
(mai ales configurația unui parc, cu subansambluri variabile) au structură
ierarhică și opțională. Cerința R-305 ("control complet pe orice câmp din
interfață") e o cerință de INTERFAȚĂ, nu de schemă SQL. JSON-ul rămâne
perfect lizibil (orice unealtă SQLite îl arată clar, pretty-printat) și evită
zeci de tabele de legătură doar pentru a muta complexitatea din Python în SQL,
fără beneficiu real la scara asta (sub 50 de parcuri — R-430).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS clienti (
    id TEXT PRIMARY KEY,
    nume TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parcuri (
    id TEXT PRIMARY KEY,
    cod TEXT UNIQUE NOT NULL,
    client_id TEXT,
    tehnologie TEXT NOT NULL,
    activ INTEGER NOT NULL DEFAULT 1,
    date_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS productie_orara (
    parc_id TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    energie_kwh REAL NOT NULL,
    PRIMARY KEY (parc_id, timestamp_utc)
);
CREATE INDEX IF NOT EXISTS idx_productie_parc ON productie_orara(parc_id);

CREATE TABLE IF NOT EXISTS rulari (
    id TEXT PRIMARY KEY,
    parc_id TEXT NOT NULL,
    declansata_la TEXT NOT NULL,
    stare TEXT NOT NULL,
    date_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rulari_parc ON rulari(parc_id, declansata_la);

CREATE TABLE IF NOT EXISTS incercari_email (
    id TEXT PRIMARY KEY,
    parc_id TEXT NOT NULL,
    moment TEXT NOT NULL,
    date_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_email_parc ON incercari_email(parc_id, moment);

CREATE TABLE IF NOT EXISTS modele_ml (
    id TEXT PRIMARY KEY,
    parc_id TEXT NOT NULL,
    activ INTEGER NOT NULL DEFAULT 0,
    antrenat_la TEXT NOT NULL,
    date_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_modele_parc ON modele_ml(parc_id, antrenat_la);

CREATE TABLE IF NOT EXISTS setari (
    cheie TEXT PRIMARY KEY,
    valoare_json TEXT NOT NULL
);

-- Verdictele controlului de calitate pe producția REALĂ măsurată (v2).
-- Ținute separat de `productie_orara` intenționat: producția importată e
-- sursa de adevăr și nu se modifică niciodată de analize automate (R-611);
-- calitatea e o interpretare, re-calculabilă oricând, și se poate șterge
-- și re-genera fără să atingă datele brute.
CREATE TABLE IF NOT EXISTS calitate_productie (
    parc_id TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    steag TEXT NOT NULL,
    detaliu TEXT,
    PRIMARY KEY (parc_id, timestamp_utc)
);
CREATE INDEX IF NOT EXISTS idx_calitate_parc ON calitate_productie(parc_id, timestamp_utc);

CREATE TABLE IF NOT EXISTS meteo_cache (
    parc_id TEXT NOT NULL,
    sursa TEXT NOT NULL,
    data TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (parc_id, sursa, data)
);
"""


def conecteaza(cale: Path) -> sqlite3.Connection:
    cale.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(cale), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(SCHEMA)
    return conn
