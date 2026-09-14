"""
Copie de rezervă a datelor de configurare ale parcurilor — descărcabilă din
Panou, în Excel și CSV (secțiunea 16, migrarea la Firestore).

De ce există separat de export.csv (care exportă o PROGNOZĂ): acolo e vorba
de rezultatul calculului, aici de configurarea propriu-zisă a portofoliului —
exact ce ar trebui refăcut manual dacă baza de date s-ar pierde. Devine mai
important, nu mai puțin, odată cu migrarea la Firestore (R-1602): o bază de
date gestionată de un furnizor extern tot beneficiază de o copie descărcabilă,
pe care operatorul o păstrează sub propriul control.

Un rând per parc — coloanele urmăresc câmpurile importante din Anexele B/C
(vezi excel_template.py), dar cu valorile CURENTE, nu goale ca la șablon.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from app.core.forecast_engine import putere_instalata_kw
from app.models import (CalitateOra, Client, ConfigEolian, ConfigPV, ModelMLInfo, ModelTermicMontaj,
                         Parc, ProductieOrara, Subansamblu, Tehnologie, TehnologiePanou,
                         TipAsezarePV, new_id)
from app.storage.serialize import (din_dict, parc_from_dict, parc_to_dict,
                                     rulare_from_dict, rulare_to_dict)

ANTET_FILL = PatternFill(start_color="1F6F50", end_color="1F6F50", fill_type="solid")
ANTET_FONT = Font(color="FFFFFF", bold=True)

COLOANE = [
    "cod", "nume", "client", "tehnologie", "activ", "mod_umbra",
    "latitudine", "longitudine", "altitudine_m", "fus_orar", "fus_orar_import", "rezolutie_minute",
    "putere_instalata_mw",
    "putere_dc_kwp", "putere_ac_kw", "randament_invertor", "tehnologie_panou",
    "model_termic_montaj", "degradare_anuala_pct", "data_punerii_in_functiune",
    "subansambluri_pv",
    "numar_turbine", "putere_nominala_kw", "inaltime_butuc_m",
    "pierdere_siaj_pct", "pierdere_sistem_pct",
    "surse_meteo", "destinatari_email", "creat_la",
]


def _randuri(repo) -> list[dict]:
    clienti = {c.id: c.nume for c in repo.listeaza_clienti()}
    randuri = []
    for parc in repo.listeaza_parcuri():
        rand = {
            "cod": parc.cod, "nume": parc.nume, "client": clienti.get(parc.client_id, ""),
            "tehnologie": parc.tehnologie.value, "activ": parc.activ, "mod_umbra": parc.mod_umbra,
            "latitudine": parc.latitudine, "longitudine": parc.longitudine,
            "altitudine_m": parc.altitudine_m, "fus_orar": parc.fus_orar,
            "fus_orar_import": parc.fus_orar_import,
            "rezolutie_minute": parc.rezolutie_minute,
            "putere_instalata_mw": round(putere_instalata_kw(parc) / 1000.0, 3),
            "surse_meteo": ", ".join(parc.surse_meteo), "creat_la": parc.creat_la,
            "destinatari_email": ", ".join(parc.destinatari_email),
        }
        if parc.tehnologie == Tehnologie.FOTOVOLTAIC and parc.config_pv:
            cfg = parc.config_pv
            rand.update({
                "putere_dc_kwp": cfg.putere_dc_kwp, "putere_ac_kw": cfg.putere_ac_kw,
                "randament_invertor": cfg.randament_invertor,
                "tehnologie_panou": cfg.tehnologie_panou.value,
                "model_termic_montaj": cfg.model_termic_montaj.value,
                "degradare_anuala_pct": cfg.degradare_anuala_pct,
                "data_punerii_in_functiune": cfg.data_punerii_in_functiune or "",
                "subansambluri_pv": "; ".join(
                    f"{s.nume}: {s.putere_dc_kwp}kWp, {s.inclinare_grade}°/{s.azimut_grade}°, {s.tip_asezare.value}"
                    for s in cfg.subansambluri
                ),
            })
        elif parc.tehnologie == Tehnologie.EOLIAN and parc.config_eolian:
            cfg = parc.config_eolian
            rand.update({
                "numar_turbine": cfg.numar_turbine, "putere_nominala_kw": cfg.putere_nominala_kw,
                "inaltime_butuc_m": cfg.inaltime_butuc_m,
                "pierdere_siaj_pct": cfg.pierdere_siaj_pct, "pierdere_sistem_pct": cfg.pierdere_sistem_pct,
            })
        randuri.append(rand)
    return randuri


def backup_parcuri_xlsx(repo) -> bytes:
    randuri = _randuri(repo)
    wb = Workbook()
    ws = wb.active
    ws.title = "Parcuri"
    for c, nume in enumerate(COLOANE, start=1):
        celula = ws.cell(row=1, column=c, value=nume)
        celula.fill, celula.font = ANTET_FILL, ANTET_FONT
    for r, rand in enumerate(randuri, start=2):
        for c, nume in enumerate(COLOANE, start=1):
            ws.cell(row=r, column=c, value=rand.get(nume, ""))
    for c, nume in enumerate(COLOANE, start=1):
        ws.column_dimensions[ws.cell(row=1, column=c).column_letter].width = max(14, len(nume) + 2)
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def backup_parcuri_csv(repo) -> str:
    randuri = _randuri(repo)
    buf = io.StringIO()
    scriitor = csv.DictWriter(buf, fieldnames=COLOANE, delimiter=";", extrasaction="ignore")
    scriitor.writeheader()
    for rand in randuri:
        scriitor.writerow(rand)
    return buf.getvalue()


# =============================================================================
# RESTAURARE — Modul tehnic: „adaugă automat toate parcurile folosind o
# copie de rezervă" + o copie de rezervă COMPLETĂ (parcuri, clienți,
# producție reală, prognoze/rulări, verdicte de calitate, modele ML,
# setări), descărcabilă și restaurabilă la fel. R-1602/R-305 — zonă
# tehnică, fără restricții.
#
# Restaurarea e ADITIVĂ (upsert), niciodată distructivă: un parc existent
# (potrivit după COD la xlsx/csv, după ID la JSON) se ACTUALIZEAZĂ, nu se
# șterge și recreează; producția/prognozele/verdictele existente rămân
# neatinse dacă nu sunt și în fișierul restaurat. Nu există cale, din
# aceste funcții, să se piardă date deja existente în bază.
# =============================================================================

def citeste_randuri_xlsx(continut: bytes) -> list[dict]:
    """Citește înapoi rândurile dintr-un fișier EXACT în formatul
    `backup_parcuri_xlsx` (prima foaie, primul rând = antet = COLOANE)."""
    wb = load_workbook(io.BytesIO(continut), read_only=True, data_only=True)
    ws = wb.active
    linii = list(ws.iter_rows(values_only=True))
    if not linii:
        return []
    antet = [str(c or "").strip() for c in linii[0]]
    randuri = []
    for linie in linii[1:]:
        if all(v is None or v == "" for v in linie):
            continue
        randuri.append({antet[i]: linie[i] for i in range(min(len(antet), len(linie)))})
    return randuri


def citeste_randuri_csv(continut: bytes) -> list[dict]:
    """Citește înapoi rândurile dintr-un fișier EXACT în formatul
    `backup_parcuri_csv` (antet = COLOANE, separator ';')."""
    text = continut.decode("utf-8-sig")
    cititor = csv.DictReader(io.StringIO(text), delimiter=";")
    return [dict(rand) for rand in cititor]


_RE_SUBANSAMBLU = re.compile(
    r"^\s*(?P<nume>[^:]+):\s*(?P<putere>[\d.,]+)\s*kWp,\s*"
    r"(?P<inclinare>[\d.,]+)°/(?P<azimut>[\d.,]+)°,\s*(?P<asezare>\w+)\s*$"
)


def _num(v, implicit=None):
    if v is None or v == "":
        return implicit
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return implicit


def _bool(v, implicit: bool = False) -> bool:
    if v is None or v == "":
        return implicit
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "adevarat", "da", "x")


def _parseaza_subansambluri(text: str) -> list[Subansamblu]:
    if not text:
        return []
    rezultat = []
    for bucata in str(text).split(";"):
        m = _RE_SUBANSAMBLU.match(bucata)
        if not m:
            continue
        rezultat.append(Subansamblu(
            nume=m.group("nume").strip(), putere_dc_kwp=_num(m.group("putere"), 0.0) or 0.0,
            inclinare_grade=_num(m.group("inclinare")), azimut_grade=_num(m.group("azimut")),
            tip_asezare=TipAsezarePV(m.group("asezare")),
        ))
    return rezultat


def restaureaza_parcuri_din_randuri(repo, randuri: list[dict]) -> dict:
    """Recreează/actualizează parcuri dintr-un rând-per-parc EXACT în
    formatul `backup_parcuri_xlsx`/`backup_parcuri_csv`. Potrivirea cu un
    parc existent se face după COD (unic, R-405): dacă există, se
    actualizează păstrând id-ul; altfel se creează un parc nou. Câmpurile
    v2 care nu sunt în COLOANE (ex. albedo, model_transpozitie) rămân pe
    implicitul din dataclass la un parc NOU — pentru unul existent rămân
    neatinse (nu se suprascriu cu implicituri)."""
    existente = {p.cod: p for p in repo.listeaza_parcuri()}
    clienti_dupa_nume = {c.nume: c for c in repo.listeaza_clienti()}
    adaugati: list[str] = []
    actualizati: list[str] = []
    erori: list[str] = []

    for i, rand in enumerate(randuri, start=2):
        cod = str(rand.get("cod") or "").strip()
        if not cod:
            erori.append(f"Rândul {i}: fără cod de parc — sărit.")
            continue
        try:
            nume_client = str(rand.get("client") or "").strip()
            client = clienti_dupa_nume.get(nume_client)
            if not client and nume_client:
                client = Client(id=new_id(), nume=nume_client)
                repo.salveaza_client(client)
                clienti_dupa_nume[nume_client] = client

            parc_existent = existente.get(cod)
            tehnologie = Tehnologie(str(rand.get("tehnologie") or "fotovoltaic").strip())
            parc = parc_existent or Parc(
                id=new_id(), cod=cod, nume=str(rand.get("nume") or cod),
                client_id=(client.id if client else ""), tehnologie=tehnologie,
                latitudine=0.0, longitudine=0.0,
            )
            parc.nume = str(rand.get("nume") or parc.nume)
            if client:
                parc.client_id = client.id
            parc.tehnologie = tehnologie
            parc.activ = _bool(rand.get("activ"), True)
            parc.mod_umbra = _bool(rand.get("mod_umbra"), parc.mod_umbra)
            parc.latitudine = _num(rand.get("latitudine"), parc.latitudine) or 0.0
            parc.longitudine = _num(rand.get("longitudine"), parc.longitudine) or 0.0
            parc.altitudine_m = _num(rand.get("altitudine_m"), 0.0) or 0.0
            parc.fus_orar = str(rand.get("fus_orar") or parc.fus_orar)
            parc.fus_orar_import = str(rand.get("fus_orar_import") or "") or ""
            parc.rezolutie_minute = int(_num(rand.get("rezolutie_minute"), parc.rezolutie_minute) or 60)
            surse = str(rand.get("surse_meteo") or "").strip()
            if surse:
                parc.surse_meteo = [s.strip() for s in surse.split(",") if s.strip()]
            destinatari = str(rand.get("destinatari_email") or "").strip()
            parc.destinatari_email = [e.strip() for e in destinatari.split(",") if e.strip()]

            if tehnologie == Tehnologie.FOTOVOLTAIC:
                cfg = parc.config_pv or ConfigPV(putere_dc_kwp=0.0, putere_ac_kw=0.0)
                cfg.putere_dc_kwp = _num(rand.get("putere_dc_kwp"), cfg.putere_dc_kwp) or 0.0
                cfg.putere_ac_kw = _num(rand.get("putere_ac_kw"), cfg.putere_ac_kw) or 0.0
                cfg.randament_invertor = _num(rand.get("randament_invertor"), cfg.randament_invertor)
                if rand.get("tehnologie_panou"):
                    cfg.tehnologie_panou = TehnologiePanou(str(rand["tehnologie_panou"]))
                if rand.get("model_termic_montaj"):
                    cfg.model_termic_montaj = ModelTermicMontaj(str(rand["model_termic_montaj"]))
                cfg.degradare_anuala_pct = _num(rand.get("degradare_anuala_pct"), cfg.degradare_anuala_pct)
                cfg.data_punerii_in_functiune = str(rand.get("data_punerii_in_functiune") or "") or None
                subansambluri = _parseaza_subansambluri(rand.get("subansambluri_pv"))
                if subansambluri:
                    cfg.subansambluri = subansambluri
                parc.config_pv = cfg
                parc.config_eolian = None
            else:
                cfg = parc.config_eolian or ConfigEolian(numar_turbine=1, putere_nominala_kw=0.0,
                                                          inaltime_butuc_m=0.0)
                cfg.numar_turbine = int(_num(rand.get("numar_turbine"), cfg.numar_turbine) or 1)
                cfg.putere_nominala_kw = _num(rand.get("putere_nominala_kw"), cfg.putere_nominala_kw) or 0.0
                cfg.inaltime_butuc_m = _num(rand.get("inaltime_butuc_m"), cfg.inaltime_butuc_m) or 0.0
                cfg.pierdere_siaj_pct = _num(rand.get("pierdere_siaj_pct"), cfg.pierdere_siaj_pct)
                cfg.pierdere_sistem_pct = _num(rand.get("pierdere_sistem_pct"), cfg.pierdere_sistem_pct)
                parc.config_eolian = cfg
                parc.config_pv = None

            repo.salveaza_parc(parc)
            (actualizati if parc_existent else adaugati).append(parc.cod)
        except Exception as exc:
            erori.append(f"Rândul {i} (cod {cod}): {exc}")

    return {"adaugati": adaugati, "actualizati": actualizati, "erori": erori}


_LIMITA_UTC_INCEPUT = "0001-01-01T00:00:00+00:00"
_LIMITA_UTC_SFARSIT = "9999-12-31T23:59:59+00:00"
VERSIUNE_BACKUP_COMPLET = 1


def backup_complet_json(repo) -> bytes:
    """Copie de rezervă COMPLETĂ — nu doar configurarea parcurilor (asta e
    `backup_parcuri_xlsx`), ci ȘI producția reală importată, prognozele
    (rulările), verdictele de calitate, metadatele modelelor ML și
    setările de operare. Format JSON, fidel 1:1 (aceleași funcții de
    serializare ca la salvarea în bază — `parc_to_dict`/`rulare_to_dict`),
    ca restaurarea să recreeze exact ce a fost, nu o versiune redusă.

    NU include fișierele antrenate ale modelelor ML (`.txt`, pe disc) — doar
    metadatele lor (cum au fost validate, ce skill au avut). După o
    restaurare pe alt mediu, modelele ML trebuie reantrenate (secțiunea 8);
    prognozele fizice și istoricul rămân intacte oricum."""
    clienti = [asdict(c) for c in repo.listeaza_clienti()]
    parcuri = repo.listeaza_parcuri()
    productie: dict[str, list[dict]] = {}
    calitate: dict[str, list[dict]] = {}
    rulari: dict[str, list[dict]] = {}
    modele_ml: dict[str, list[dict]] = {}
    for parc in parcuri:
        productie[parc.id] = [asdict(p) for p in
                               repo.listeaza_productie(parc.id, _LIMITA_UTC_INCEPUT, _LIMITA_UTC_SFARSIT)]
        calitate[parc.id] = [asdict(c) for c in repo.listeaza_calitate_productie(parc.id)]
        rulari[parc.id] = [rulare_to_dict(r) for r in repo.listeaza_rulari(parc.id, limita=1_000_000)]
        modele_ml[parc.id] = [asdict(m) for m in repo.listeaza_modele_ml(parc.id)]

    payload = {
        "versiune": VERSIUNE_BACKUP_COMPLET,
        "exportat_la": datetime.now(timezone.utc).isoformat(),
        "clienti": clienti,
        "parcuri": [parc_to_dict(p) for p in parcuri],
        "productie": productie,
        "calitate_productie": calitate,
        "rulari": rulari,
        "modele_ml": modele_ml,
        "setari": repo.toate_setarile(),
    }
    return json.dumps(payload, ensure_ascii=False, indent=None).encode("utf-8")


def restaureaza_complet_json(repo, continut: bytes) -> dict:
    """Inversul lui `backup_complet_json` — ADITIV (upsert după ID/cheie
    naturală), niciodată distructiv. Un parc cu un ID care nu mai există în
    bază e recreat CU ACELAȘI ID (păstrează legăturile cu producția/
    prognozele/verdictele din același fișier, care sunt indexate tot după
    parc_id)."""
    try:
        payload = json.loads(continut.decode("utf-8"))
    except Exception as exc:
        return {"eroare": f"Fișierul nu e JSON valid: {exc}"}

    rezultat = {
        "clienti": 0, "parcuri": 0, "ore_productie": 0, "verdicte_calitate": 0,
        "rulari": 0, "modele_ml": 0, "setari": 0, "erori": [],
    }

    for c in payload.get("clienti") or []:
        try:
            repo.salveaza_client(Client(id=c["id"], nume=c.get("nume", "")))
            rezultat["clienti"] += 1
        except Exception as exc:
            rezultat["erori"].append(f"Client {c.get('id')}: {exc}")

    for p in payload.get("parcuri") or []:
        try:
            repo.salveaza_parc(parc_from_dict(p))
            rezultat["parcuri"] += 1
        except Exception as exc:
            rezultat["erori"].append(f"Parc {p.get('cod', p.get('id'))}: {exc}")

    for parc_id, randuri in (payload.get("productie") or {}).items():
        try:
            obiecte = [din_dict(ProductieOrara, {**r, "parc_id": parc_id}) for r in randuri]
            if obiecte:
                rezultat["ore_productie"] += repo.salveaza_productie(obiecte)
        except Exception as exc:
            rezultat["erori"].append(f"Producție parc {parc_id}: {exc}")

    for parc_id, randuri in (payload.get("calitate_productie") or {}).items():
        try:
            obiecte = [din_dict(CalitateOra, {**r, "parc_id": parc_id}) for r in randuri]
            if obiecte:
                rezultat["verdicte_calitate"] += repo.salveaza_calitate_productie(obiecte)
        except Exception as exc:
            rezultat["erori"].append(f"Calitate parc {parc_id}: {exc}")

    for parc_id, randuri in (payload.get("rulari") or {}).items():
        for r in randuri:
            try:
                repo.salveaza_rulare(rulare_from_dict(r))
                rezultat["rulari"] += 1
            except Exception as exc:
                rezultat["erori"].append(f"Rulare parc {parc_id}: {exc}")

    for parc_id, randuri in (payload.get("modele_ml") or {}).items():
        for m in randuri:
            try:
                repo.salveaza_model_ml(din_dict(ModelMLInfo, m))
                rezultat["modele_ml"] += 1
            except Exception as exc:
                rezultat["erori"].append(f"Model ML parc {parc_id}: {exc}")

    for cheie, valoare in (payload.get("setari") or {}).items():
        try:
            repo.set_setare(cheie, valoare)
            rezultat["setari"] += 1
        except Exception as exc:
            rezultat["erori"].append(f"Setare {cheie}: {exc}")

    return rezultat
