"""
Zona prietenoasă ("Panoul") — R-301-304, R-401-407, R-1301-1305.

Toate acțiunile importante și de rutină (R-302): adăugare parc, rulare
prognoză, antrenare model, reconstrucție istoric, calibrare geometrie,
vizualizare comparații, import date — sunt disponibile de aici, fără linie
de comandă și fără zona tehnică.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from urllib.parse import quote

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response, StreamingResponse
import io

from app import setari as setari_mod
from app.config import BASE_DIR
from app.core.backup import backup_parcuri_csv, backup_parcuri_xlsx
from app.core.calibration import (CalibrareIndisponibilaError, aplica_calibrare,
                                   calibreaza_geometrie, parc_eligibil_pentru_aplicare_automata)
from app.core.control_calitate import rezumat_calitate, ruleaza_control_calitate
from app.core.csv_import import analizeaza_fisier, importa_randuri
from app.core.email_report import trimite_raport_email
from app.core.excel_template import sablon_eolian, sablon_pv
from app.core.forecast_engine import putere_instalata_kw, ruleaza_prognoza
from app.core.momente import FORMAT_TIMP_IMPLICIT, FORMATE_TIMP
from app.core.ml_corrector import DateInsuficienteError, antreneaza_model
from app.core.reports import genereaza_raport_lunar
from app.core.timezones import fus_orar_pentru_import, fus_orar_valid, optiuni_select
from app.core.weather_historical import reconstruieste_prognoza_fizica
from app.models import Client, ConfigEolian, ConfigPV, Parc, Subansamblu, Tehnologie, TipAsezarePV, new_id
from app.web.deps import TEMPLATES, get_repo
from app.web.validare import valideaza_parc_eolian, valideaza_parc_pv
from app.web.vizualizare import (diagnostic_meteo, indicatori_parc, prognoza_luna_urmatoare,
                                  rezumat_portofoliu_parc, rezumat_portofoliu_total, serie_comparatie_completa,
                                  raport_antrenare, serie_comparatie_meteo, serie_grafic_recenta, tabel_recent_zile,
                                  zile_de_la_creare)

router = APIRouter()
IMPORTURI_DIR = BASE_DIR / "data" / "cache" / "imports"
IMPORTURI_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- Portofoliu
@router.get("/")
def portofoliu(request: Request, tehnologie: str | None = None):
    """`tehnologie` filtrează afișarea (fotovoltaic / eolian); implicit se văd
    TOATE activele. Filtrul lucrează pe lista deja calculată, deci indicatorii
    de sus se referă mereu la ce se vede dedesubt — altfel un total pe tot
    portofoliul lângă un tabel filtrat ar fi doar derutant."""
    repo = get_repo()
    parcuri = repo.listeaza_parcuri()
    filtru = tehnologie if tehnologie in ("fotovoltaic", "eolian") else None
    if filtru:
        parcuri = [p for p in parcuri if p.tehnologie.value == filtru]
    randuri = [rezumat_portofoliu_parc(repo, p) for p in parcuri]

    toate = repo.listeaza_parcuri()
    numaratoare = {
        "toate": len(toate),
        "fotovoltaic": sum(1 for p in toate if p.tehnologie.value == "fotovoltaic"),
        "eolian": sum(1 for p in toate if p.tehnologie.value == "eolian"),
    }
    return TEMPLATES.TemplateResponse("panou/portofoliu.html", {
        "request": request, "zona": "panou", "randuri": randuri,
        "total": rezumat_portofoliu_total(repo, randuri),
        "filtru": filtru, "numaratoare": numaratoare,
    })


@router.get("/rezerva/parcuri.xlsx")
def rezerva_parcuri_xlsx():
    """Copie de rezervă a configurării TUTUROR parcurilor (secțiunea 16) —
    ce ar trebui refăcut manual dacă baza de date s-ar pierde, indiferent
    dacă ea trăiește local (SQLite) sau pe Firestore."""
    repo = get_repo()
    continut = backup_parcuri_xlsx(repo)
    nume_fisier = f"rezerva_parcuri_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(io.BytesIO(continut),
                              media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                              headers={"Content-Disposition": f"attachment; filename={nume_fisier}"})


@router.get("/rezerva/parcuri.csv")
def rezerva_parcuri_csv():
    repo = get_repo()
    continut = backup_parcuri_csv(repo)
    nume_fisier = f"rezerva_parcuri_{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(content=continut, media_type="text/csv",
                     headers={"Content-Disposition": f"attachment; filename={nume_fisier}"})


@router.get("/ghid")
def ghid(request: Request):
    repo = get_repo()
    parcuri = repo.listeaza_parcuri()
    return TEMPLATES.TemplateResponse("panou/ghid_pornire.html", {
        "request": request, "zona": "panou", "are_parcuri": bool(parcuri),
    })


# --------------------------------------------------------------------------- Adăugare parc (R-401-407)
@router.get("/parcuri/noua")
def parc_nou_formular(request: Request, tehnologie: str | None = None):
    repo = get_repo()
    clienti = repo.listeaza_clienti()
    return TEMPLATES.TemplateResponse("panou/adauga_parc.html", {
        "request": request, "zona": "panou", "tehnologie": tehnologie, "clienti": clienti, "erori": [],
        "fusuri": optiuni_select(),
    })


@router.post("/parcuri/noua")
async def parc_nou_creeaza(request: Request):
    repo = get_repo()
    form = await request.form()
    tehnologie = form.get("tehnologie")

    client_id = form.get("client_id")
    if client_id == "__nou__":
        nume_client_nou = (form.get("client_nume_nou") or "").strip()
        if not nume_client_nou:
            erori = ["Introduceți numele noului client."]
            clienti = repo.listeaza_clienti()
            return TEMPLATES.TemplateResponse("panou/adauga_parc.html", {
                "request": request, "zona": "panou", "tehnologie": tehnologie, "clienti": clienti, "erori": erori,
                "fusuri": optiuni_select(),
            }, status_code=400)
        client = Client(id=new_id(), nume=nume_client_nou)
        repo.salveaza_client(client)
        client_id = client.id

    if tehnologie == "fotovoltaic":
        date_, erori = valideaza_parc_pv(form)
    else:
        date_, erori = valideaza_parc_eolian(form)

    if date_.get("cod") and any(p.cod == date_["cod"] for p in repo.listeaza_parcuri()):
        erori.append(f"Există deja un parc cu codul '{date_['cod']}' — codul trebuie să fie unic.")

    if erori:
        clienti = repo.listeaza_clienti()
        return TEMPLATES.TemplateResponse("panou/adauga_parc.html", {
            "request": request, "zona": "panou", "tehnologie": tehnologie, "clienti": clienti, "erori": erori,
            "fusuri": optiuni_select(),
        }, status_code=400)

    destinatari = [e.strip() for e in (form.get("destinatari_email") or "").split(",") if e.strip()]

    parc = Parc(
        id=new_id(), cod=date_["cod"], nume=date_["nume"], client_id=client_id,
        tehnologie=Tehnologie.FOTOVOLTAIC if tehnologie == "fotovoltaic" else Tehnologie.EOLIAN,
        latitudine=date_["latitudine"], longitudine=date_["longitudine"], altitudine_m=date_["altitudine_m"],
        fus_orar=date_["fus_orar"], fus_orar_import=date_["fus_orar_import"],
        mod_umbra=True, destinatari_email=destinatari,
    )
    if tehnologie == "fotovoltaic":
        parc.config_pv = ConfigPV(
            putere_dc_kwp=date_["putere_dc_kwp"], putere_ac_kw=date_["putere_ac_kw"],
            subansambluri=[Subansamblu(
                nume="Principal", putere_dc_kwp=date_["putere_dc_kwp"],
                inclinare_grade=date_["inclinare_grade"], azimut_grade=date_["azimut_grade"],
                tip_asezare=TipAsezarePV(date_["tip_asezare"]),
            )],
            tehnologie_panou=date_["tehnologie_panou"],
        )
    else:
        parc.config_eolian = ConfigEolian(
            numar_turbine=date_["numar_turbine"], putere_nominala_kw=date_["putere_nominala_kw"],
            inaltime_butuc_m=date_["inaltime_butuc_m"], viteza_pornire_ms=date_["viteza_pornire_ms"],
            viteza_nominala_ms=date_["viteza_nominala_ms"], viteza_oprire_ms=date_["viteza_oprire_ms"],
        )

    repo.salveaza_parc(parc)
    return RedirectResponse(f"/parcuri/{parc.id}?nou=1", status_code=303)


@router.get("/sabloane/pv.xlsx")
def sablon_pv_descarca():
    return Response(sablon_pv(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     headers={"Content-Disposition": "attachment; filename=sablon_parc_fotovoltaic.xlsx"})


@router.get("/sabloane/eolian.xlsx")
def sablon_eolian_descarca():
    return Response(sablon_eolian(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     headers={"Content-Disposition": "attachment; filename=sablon_parc_eolian.xlsx"})


# --------------------------------------------------------------------------- Detaliu parc (R-304)
@router.get("/parcuri/{parc_id}")
def parc_detaliu(request: Request, parc_id: str, nou: str | None = None):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if not parc:
        return RedirectResponse("/", status_code=303)

    ultima_rulare = repo.ultima_rulare_reusita(parc.id)
    # Tabelul „Rulări recente" arată doar metadate (moment, tip, stare, mesaj) —
    # nu are nevoie de orele de prognoză ale fiecărei rulări.
    rulari_recente = repo.listeaza_rulari_sumar(parc.id, limita=10)
    indicatori = indicatori_parc(repo, parc)
    serie_grafic = serie_grafic_recenta(ultima_rulare, parc)
    tabel_recent = tabel_recent_zile(ultima_rulare, parc, zile=3)
    model_activ = repo.model_ml_activ(parc.id)
    numar_ore_productie = repo.numar_ore_productie(parc.id)
    setari = setari_mod.toate(repo)
    rezumat = rezumat_portofoliu_parc(repo, parc)

    return TEMPLATES.TemplateResponse("panou/parc_detaliu.html", {
        "request": request, "zona": "panou", "parc": parc, "nou": bool(nou), "rezumat": rezumat,
        "ultima_rulare": ultima_rulare, "rulari_recente": rulari_recente,
        "indicatori": indicatori, "serie_grafic": serie_grafic, "tabel_recent": tabel_recent, "model_activ": model_activ,
        "numar_ore_productie": numar_ore_productie, "putere_instalata": putere_instalata_kw(parc),
        "zile_de_la_creare": zile_de_la_creare(parc),
        "zile_mod_umbra_recomandat": setari["zile_mod_umbra_recomandat"]["valoare"],
        "prag_min_ore_antrenare": setari["prag_min_ore_antrenare_ml"]["valoare"],
        "prag_min_ore_calibrare": setari["prag_min_ore_calibrare"]["valoare"],
        "eligibil_calibrare_automata": parc_eligibil_pentru_aplicare_automata(parc),
        "azi_iso": date.today().isoformat(),
        "rezumat_calitate": rezumat_calitate(repo, parc),
        "modele_ml": repo.listeaza_modele_ml(parc.id)[:5],
        "mesaj": request.query_params.get("mesaj"),
        "formate_timp": FORMATE_TIMP, "format_timp_implicit": FORMAT_TIMP_IMPLICIT,
    })


@router.post("/parcuri/{parc_id}/ruleaza")
def parc_ruleaza(parc_id: str, zile: int | None = None):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if parc:
        ruleaza_prognoza(repo, parc, tip="manuala", zile_viitor=zile)
    return RedirectResponse(f"/parcuri/{parc_id}?actiune=rulat", status_code=303)


@router.post("/parcuri/{parc_id}/trimite-email")
def parc_trimite_email(parc_id: str):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    ultima = repo.ultima_rulare_reusita(parc_id) if parc else None
    if parc and ultima:
        trimite_raport_email(repo, parc, ultima)
    return RedirectResponse(f"/parcuri/{parc_id}?actiune=email", status_code=303)


@router.post("/parcuri/{parc_id}/mod-umbra/dezactiveaza")
def parc_iese_din_mod_umbra(parc_id: str):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if parc:
        parc.mod_umbra = False
        repo.salveaza_parc(parc)
    return RedirectResponse(f"/parcuri/{parc_id}?actiune=mod-umbra", status_code=303)


@router.post("/parcuri/{parc_id}/mod-umbra/activeaza")
def parc_intra_in_mod_umbra(parc_id: str):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if parc:
        parc.mod_umbra = True
        repo.salveaza_parc(parc)
    return RedirectResponse(f"/parcuri/{parc_id}?actiune=mod-umbra", status_code=303)


# --------------------------------------------------------------------------- Import istoric (R-1101-1104, doi pași)
@router.get("/parcuri/{parc_id}/import")
def import_formular(request: Request, parc_id: str):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    return TEMPLATES.TemplateResponse("panou/import_csv.html", {
        "request": request, "zona": "panou", "parc": parc, "raport": None, "token": None,
        "fusuri": optiuni_select(), "fus_import_efectiv": fus_orar_pentru_import(parc) if parc else None,
    })


@router.post("/parcuri/{parc_id}/import/fus-orar")
def import_schimba_fus_orar(parc_id: str, fus_orar: str = Form(...)):
    """Schimbă fusul de IMPORT al parcului direct din pagina de import —
    fără el, timestamp-urile din fișierul CSV nu se pot converti corect la
    UTC (R-511). Operatorul trebuie să vadă și să confirme explicit fusul
    ÎNAINTE de a încărca fișierul, nu să-l descopere greșit după aceea.

    Setează `fus_orar_import` (NU `fus_orar`, care guvernează prognozele/
    afișarea) — exportul din SCADA/contor poate folosi o convenție orară
    diferită de fusul ales pentru prognoze (R-xxx), iar acest buton rapid
    corectează strict interpretarea importului, fără să schimbe ziua/ora
    la care se raportează prognozele deja calculate.

    Acceptă orice fus orar IANA valid, nu doar cele din lista curată
    afișată în formular (R-xxx — platforma acoperă și parcuri din alte
    zone decât România/Moldova)."""
    if not fus_orar_valid(fus_orar):
        return RedirectResponse(f"/parcuri/{parc_id}/import", status_code=303)
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if parc:
        parc.fus_orar_import = fus_orar
        repo.salveaza_parc(parc)
    return RedirectResponse(f"/parcuri/{parc_id}/import", status_code=303)


@router.post("/parcuri/{parc_id}/import/analizeaza")
async def import_analizeaza(request: Request, parc_id: str, fisier: UploadFile,
                             permite_ora_locala: str = Form(default="")):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    setari = setari_mod.toate(repo)
    continut = await fisier.read()

    token = uuid.uuid4().hex
    (IMPORTURI_DIR / f"{token}.csv").write_bytes(continut)

    # Alegerea operatorului trebuie să însoțească fișierul până la pasul 2:
    # altfel confirmarea ar re-analiza cu regulile stricte și ar respinge un
    # fișier pe care tocmai l-a văzut acceptat.
    accepta_ora_locala = bool(permite_ora_locala)
    (IMPORTURI_DIR / f"{token}.mod").write_text("locala" if accepta_ora_locala else "strict")

    raport = analizeaza_fisier(continut, parc, setari["toleranta_fus_orar_minute"]["valoare"],
                                permite_ora_locala=accepta_ora_locala)
    return TEMPLATES.TemplateResponse("panou/import_csv.html", {
        "request": request, "zona": "panou", "parc": parc, "raport": raport, "token": token,
        "fusuri": optiuni_select(), "fus_import_efectiv": fus_orar_pentru_import(parc) if parc else None,
        "permite_ora_locala": accepta_ora_locala,
    })


@router.post("/parcuri/{parc_id}/import/confirma")
async def import_confirma(request: Request, parc_id: str, token: str = Form(default="")):
    """Pasul 2 al importului. Rezultatul (câte ore s-au scris) ajunge la
    operator — înainte mesajul era calculat și aruncat, iar utilizatorul vedea
    doar un „Import finalizat” fără nicio cifră.

    După import se rulează automat controlul de calitate: e momentul firesc,
    iar un import cu decalaj de fus orar sau cu ore de indisponibilitate
    trebuie semnalat imediat, nu descoperit peste săptămâni prin efectele lui
    asupra modelului."""
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if not parc:
        return RedirectResponse("/", status_code=303)
    setari = setari_mod.toate(repo)
    cale = IMPORTURI_DIR / f"{token}.csv"
    cale_mod = IMPORTURI_DIR / f"{token}.mod"
    mesaj = "Nu s-a găsit fișierul încărcat — reîncărcați-l și reluați pasul de analiză."
    if cale.exists():
        continut = cale.read_bytes()
        permite_ora_locala = cale_mod.exists() and cale_mod.read_text().strip() == "locala"
        raport = analizeaza_fisier(continut, parc, setari["toleranta_fus_orar_minute"]["valoare"],
                                    permite_ora_locala=permite_ora_locala)
        if raport.valid:
            n = importa_randuri(repo, raport)
            mesaj = f"S-au importat {n} ore de producție reală."
            try:
                rezumat = ruleaza_control_calitate(repo, parc)
                if rezumat.numar_probleme:
                    mesaj += (f" Controlul de calitate a marcat {rezumat.numar_probleme} ore "
                              f"({rezumat.procent_curat}% date curate) — vedeți pagina de calitate a datelor.")
                else:
                    mesaj += " Controlul de calitate nu a găsit probleme."
            except Exception as exc:   # analiza nu trebuie să anuleze importul reușit
                mesaj += f" (Controlul de calitate nu a putut rula: {exc})"
        else:
            mesaj = "Fișierul nu a trecut validarea — importul a fost oprit."
        cale.unlink(missing_ok=True)
        cale_mod.unlink(missing_ok=True)
    return RedirectResponse(f"/parcuri/{parc_id}?actiune=import&mesaj={quote(mesaj)}", status_code=303)


# --------------------------------------------------------------------------- Antrenare ML (R-601-606)
@router.post("/parcuri/{parc_id}/antreneaza")
def parc_antreneaza(request: Request, parc_id: str):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    setari = setari_mod.toate(repo)
    mesaj, eroare = None, None
    if parc:
        try:
            info = antreneaza_model(repo, parc, setari["prag_min_ore_antrenare_ml"]["valoare"])
            prefix = ("Model ACTIVAT — " if info.activ
                      else "Model antrenat, dar NU a fost activat (nu a fost mai bun decât cel existent) — ")
            mesaj = prefix + (info.note or "")
        except DateInsuficienteError as exc:
            eroare = str(exc)
        except Exception as exc:
            eroare = f"Antrenarea a eșuat: {exc}"
    # Rezultatul antrenării merită un ecran propriu, nu un mesaj care dispare:
    # e momentul în care operatorul află dacă modelul a fost promovat și de ce.
    if eroare:
        return RedirectResponse(f"/parcuri/{parc_id}?actiune=antrenare&eroare={quote(str(eroare))}",
                                 status_code=303)
    return RedirectResponse(f"/parcuri/{parc_id}/antrenare", status_code=303)


@router.get("/parcuri/{parc_id}/antrenare")
def antrenare_raport(request: Request, parc_id: str):
    """Analiza ultimei antrenări: verdictul, cifrele care l-au decis și ce
    urmează. Rămâne accesibilă și mai târziu, nu doar imediat după antrenare."""
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if not parc:
        return RedirectResponse("/", status_code=303)
    modele = repo.listeaza_modele_ml(parc.id)
    return TEMPLATES.TemplateResponse("panou/antrenare.html", {
        "request": request, "zona": "panou", "parc": parc,
        "raport": raport_antrenare(parc, modele[0] if modele else None),
        "istoric": modele[:6],
        "prag_min_ore_antrenare": setari_mod.toate(repo)["prag_min_ore_antrenare_ml"]["valoare"],
        "numar_ore_productie": repo.numar_ore_productie(parc.id),
    })


# --------------------------------------------------------------------------- Calibrare geometrie (R-701-705)
def _ani_functionare(parc) -> float:
    """Vechimea parcului, în ani — dialogul de pierderi are nevoie de ea ca să
    poată converti degradarea anuală într-un procent cumulat comparabil cu
    factorul de scalare (care include și degradarea acumulată)."""
    config = getattr(parc, "config_pv", None)
    if not config or not config.data_punerii_in_functiune:
        return 0.0
    try:
        zile = (date.today() - date.fromisoformat(config.data_punerii_in_functiune)).days
    except ValueError:
        return 0.0
    return round(max(0.0, zile / 365.25), 2)


@router.get("/parcuri/{parc_id}/calibrare")
def calibrare_pagina(request: Request, parc_id: str):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    return TEMPLATES.TemplateResponse("panou/calibrare.html", {
        "request": request, "zona": "panou", "parc": parc, "rezultat": None, "eroare": None,
        "ani_functionare": _ani_functionare(parc),
        "mesaj": request.query_params.get("mesaj"),
    })


@router.post("/parcuri/{parc_id}/calibrare/ruleaza")
def calibrare_ruleaza(request: Request, parc_id: str):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    setari = setari_mod.toate(repo)
    rezultat, eroare = None, None
    try:
        rezultat = calibreaza_geometrie(repo, parc, setari["prag_min_ore_calibrare"]["valoare"])
    except CalibrareIndisponibilaError as exc:
        eroare = str(exc)
    return TEMPLATES.TemplateResponse("panou/calibrare.html", {
        "request": request, "zona": "panou", "parc": parc, "rezultat": rezultat, "eroare": eroare,
        "ani_functionare": _ani_functionare(parc),
        "mesaj": request.query_params.get("mesaj"),
    })


CAMPURI_PIERDERI = ["pierdere_murdarire_pct", "pierdere_neuniformitate_pct",
                     "pierdere_cablaj_conexiuni_pct", "pierdere_lid_pct",
                     "pierdere_placuta_pct", "pierdere_indisponibilitate_pct",
                     "pierdere_nemodelata_pct"]


@router.post("/parcuri/{parc_id}/calibrare/pierderi")
async def calibrare_salveaza_pierderi(request: Request, parc_id: str):
    """Salvează pierderile ajustate de operator în dialogul de pe pagina de
    calibrare.

    Platforma NU distribuie singură factorul de scalare peste aceste câmpuri:
    el adună la un loc pierderi de sistem și degradare, iar repartizarea lor pe
    cauze fizice e o judecată despre parcul real (e murdărire? indisponibilitate?
    plăcuță supraevaluată?), nu un calcul. Dialogul arată totalul cumulat în timp
    real și distanța până la factorul țintă; decizia unde se duc procentele
    rămâne a operatorului, iar astfel nimic nu se poate dubla pe ascuns."""
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if not parc or not parc.config_pv:
        return RedirectResponse(f"/parcuri/{parc_id}", status_code=303)

    form = await request.form()
    try:
        for camp in CAMPURI_PIERDERI:
            brut = (form.get(camp) or "").strip()
            if brut == "":
                continue
            valoare = float(brut.replace(",", "."))
            if not 0.0 <= valoare <= 100.0:
                raise ValueError(f"{camp}: procentul trebuie să fie între 0 și 100 (primit {valoare})")
            setattr(parc.config_pv, camp, valoare)
        degradare = (form.get("degradare_anuala_pct") or "").strip()
        if degradare:
            parc.config_pv.degradare_anuala_pct = float(degradare.replace(",", "."))
        repo.salveaza_parc(parc)
        total_pct = 100 * (1 - parc.config_pv.factor_pierderi_sistem())
        mesaj = (f"Pierderi salvate — total cumulat {total_pct:.1f}%. "
                 f"Recalculați prognoza ca să vedeți efectul.")
    except Exception as exc:
        mesaj = f"Nu s-au putut salva pierderile: {exc}"
    return RedirectResponse(f"/parcuri/{parc_id}/calibrare?mesaj={quote(mesaj)}", status_code=303)


@router.post("/parcuri/{parc_id}/calibrare/aplica")
def calibrare_aplica(parc_id: str, inclinare_grade: float = Form(...), azimut_grade: float = Form(...)):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if parc:
        try:
            aplica_calibrare(repo, parc, inclinare_grade, azimut_grade)
        except CalibrareIndisponibilaError:
            pass
    return RedirectResponse(f"/parcuri/{parc_id}?actiune=calibrare", status_code=303)


# --------------------------------------------------------------------------- Reconstrucție istoric (R-610-612)
@router.get("/parcuri/{parc_id}/reconstructie")
def reconstructie_formular(request: Request, parc_id: str):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    return TEMPLATES.TemplateResponse("panou/reconstructie.html", {
        "request": request, "zona": "panou", "parc": parc, "rezultat": None,
    })


@router.post("/parcuri/{parc_id}/reconstructie")
def reconstructie_ruleaza(request: Request, parc_id: str, inceput: str = Form(...), sfarsit: str = Form(...)):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    rez = reconstruieste_prognoza_fizica(parc, date.fromisoformat(inceput), date.fromisoformat(sfarsit))
    rezultat = {
        "numar_ore": len(rez),
        "energie_totala_kwh": round(float(rez["putere_ac_fizic_kw"].sum()), 0) if len(rez) else 0,
        "varf_kw": round(float(rez["putere_ac_fizic_kw"].max()), 1) if len(rez) else 0,
    }
    return TEMPLATES.TemplateResponse("panou/reconstructie.html", {
        "request": request, "zona": "panou", "parc": parc, "rezultat": rezultat,
    })


# --------------------------------------------------------------------------- Calitatea datelor măsurate
@router.get("/parcuri/{parc_id}/calitate")
def calitate_pagina(request: Request, parc_id: str):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if not parc:
        return RedirectResponse("/", status_code=303)
    from app.core.control_calitate import ETICHETE_STEAG, EXPLICATII_STEAG, rezumat_calitate
    return TEMPLATES.TemplateResponse("panou/calitate.html", {
        "request": request, "zona": "panou", "parc": parc,
        "rezumat": rezumat_calitate(repo, parc),
        "etichete": ETICHETE_STEAG, "explicatii": EXPLICATII_STEAG,
        "mesaj": request.query_params.get("mesaj"),
    })


@router.post("/parcuri/{parc_id}/calitate/analizeaza")
def calitate_analizeaza(parc_id: str):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if not parc:
        return RedirectResponse("/", status_code=303)
    try:
        raport = ruleaza_control_calitate(repo, parc)
        mesaj = (f"Analiză finalizată: {raport.numar_analizate} ore verificate, "
                 f"{raport.numar_probleme} marcate ({raport.procent_curat}% date curate).")
        if raport.mesaje:
            mesaj += " " + " ".join(raport.mesaje)
    except Exception as exc:
        mesaj = f"Analiza nu a putut rula complet: {exc}"
    return RedirectResponse(f"/parcuri/{parc_id}/calitate?mesaj={quote(mesaj)}", status_code=303)


# --------------------------------------------------------------------------- Verificarea prognozei
@router.get("/parcuri/{parc_id}/verificare")
def verificare_pagina(request: Request, parc_id: str, zile: int = 90):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if not parc:
        return RedirectResponse("/", status_code=303)
    from app.core.reports import genereaza_raport_verificare
    zile = max(7, min(int(zile), 730))
    return TEMPLATES.TemplateResponse("panou/verificare.html", {
        "request": request, "zona": "panou", "parc": parc,
        "rezultat": genereaza_raport_verificare(repo, parc, zile), "zile": zile,
    })


# --------------------------------------------------------------------------- Comparație producție (reală / fizic / ML / dezechilibru)
@router.get("/parcuri/{parc_id}/comparatie")
def comparatie_pagina(request: Request, parc_id: str, zile: int = 14,
                       inceput: str | None = None, sfarsit: str | None = None):
    """`inceput`/`sfarsit` (AAAA-LL-ZZ, dată calendaristică LOCALĂ, în fusul
    parcului) au prioritate față de `zile` dacă sunt ambele completate —
    permit un interval ales liber, nu doar scurtăturile fixe 7/14/30/90."""
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if not parc:
        return RedirectResponse("/", status_code=303)
    zile = max(1, min(int(zile), 365))
    inceput_data = sfarsit_data = None
    if inceput and sfarsit:
        try:
            inceput_data = date.fromisoformat(inceput)
            sfarsit_data = date.fromisoformat(sfarsit)
        except ValueError:
            inceput_data = sfarsit_data = None
    date_comparatie = serie_comparatie_completa(
        repo, parc, zile, inceput_data=inceput_data, sfarsit_data=sfarsit_data)
    return TEMPLATES.TemplateResponse("panou/comparatie.html", {
        "request": request, "zona": "panou", "parc": parc,
        "date": date_comparatie, "zile": zile,
        "interval_manual": bool(inceput_data and sfarsit_data),
    })


# --------------------------------------------------------------------------- Reală vs. prognoză — METEO
@router.get("/parcuri/{parc_id}/comparatie-meteo")
def comparatie_meteo_pagina(request: Request, parc_id: str, zile: int = 14,
                             inceput: str | None = None, sfarsit: str | None = None):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if not parc:
        return RedirectResponse("/", status_code=303)
    zile = max(1, min(int(zile), 365))
    inceput_data = sfarsit_data = None
    if inceput and sfarsit:
        try:
            inceput_data = date.fromisoformat(inceput)
            sfarsit_data = date.fromisoformat(sfarsit)
        except ValueError:
            inceput_data = sfarsit_data = None
    date_meteo = serie_comparatie_meteo(repo, parc, zile, inceput_data=inceput_data, sfarsit_data=sfarsit_data)
    return TEMPLATES.TemplateResponse("panou/comparatie_meteo.html", {
        "request": request, "zona": "panou", "parc": parc,
        "date": date_meteo, "zile": zile,
        "interval_manual": bool(inceput_data and sfarsit_data),
    })


# --------------------------------------------------------------------------- Diagnostic meteo ("zonă de siguranță")
@router.get("/parcuri/{parc_id}/diagnostic-meteo")
def diagnostic_meteo_pagina(request: Request, parc_id: str):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if not parc:
        return RedirectResponse("/", status_code=303)
    return TEMPLATES.TemplateResponse("panou/diagnostic_meteo.html", {
        "request": request, "zona": "panou", "parc": parc,
        "diagnostic": diagnostic_meteo(repo, parc),
    })


# --------------------------------------------------------------------------- Prognoză pentru următoarea lună
LUNI_RO = ["ianuarie", "februarie", "martie", "aprilie", "mai", "iunie",
            "iulie", "august", "septembrie", "octombrie", "noiembrie", "decembrie"]


@router.get("/parcuri/{parc_id}/prognoza-luna")
def prognoza_luna_pagina(request: Request, parc_id: str, mod: str = "luna",
                          an: int | None = None, luna: int | None = None, zile: int = 31):
    """Două moduri de citire a aceleiași estimări:

      * `mod=luna` (implicit) — o lună CALENDARISTICĂ întreagă, cum se face
        planificarea și raportarea către client („cât produce parcul în
        octombrie"). Implicit luna curentă;
      * `mod=zile` — următoarele N zile începând de mâine, util când întrebarea
        e „ce urmează", nu „cât face luna asta".
    """
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if not parc:
        return RedirectResponse("/", status_code=303)
    setari = setari_mod.toate(repo)

    azi = datetime.now(ZoneInfo(parc.fus_orar)).date()
    if mod == "zile":
        zile = max(1, min(int(zile), 120))
        date_prognoza = prognoza_luna_urmatoare(repo, parc, zile=zile)
    else:
        mod = "luna"
        an = an or azi.year
        luna = luna if luna and 1 <= luna <= 12 else azi.month
        date_prognoza = prognoza_luna_urmatoare(repo, parc, an=an, luna=luna)

    # Lunile oferite în selector: luna curentă plus următoarele cinci — dincolo
    # de asta rămâne doar climatologie, tot mai puțin utilă ca plan.
    luni_disponibile = []
    for i in range(6):
        m = azi.month - 1 + i
        luni_disponibile.append({"an": azi.year + m // 12, "luna": m % 12 + 1,
                                  "eticheta": f"{LUNI_RO[m % 12]} {azi.year + m // 12}"})

    return TEMPLATES.TemplateResponse("panou/prognoza_luna.html", {
        "request": request, "zona": "panou", "parc": parc,
        "date": date_prognoza,
        "mod": mod, "an": an, "luna": luna, "zile": zile,
        "luni_disponibile": luni_disponibile,
        "eticheta_perioada": (f"{LUNI_RO[luna - 1]} {an}" if mod == "luna"
                               else f"următoarele {zile} zile"),
        "orizont_maxim": int(setari["zile_orizont_maxim"]["valoare"]),
        "mesaj": request.query_params.get("mesaj"),
        "eroare": request.query_params.get("eroare"),
    })


@router.post("/parcuri/{parc_id}/prognoza-luna/extinde")
def prognoza_luna_extinde(parc_id: str):
    """Calculează o prognoză NWP pe orizontul MAXIM disponibil (~16 zile) și o
    salvează, ca zilele 4-16 să apară în tabel cu prognoză meteo reală.

    Rularea zilnică folosește orizontul implicit (3 zile) — suficient pentru
    raportul zilnic și mult mai ieftin ca trafic. Restul orizontului se cere
    explicit, de aici, când operatorul chiar are nevoie de el: modelele meteo
    îl acoperă, dar nu are rost descărcat zilnic pentru toate parcurile."""
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if not parc:
        return RedirectResponse("/", status_code=303)
    setari = setari_mod.toate(repo)
    orizont = int(setari["zile_orizont_maxim"]["valoare"])
    try:
        rulare = ruleaza_prognoza(repo, parc, tip="manuala", zile_viitor=orizont)
        if rulare.stare == "esuata":
            return RedirectResponse(
                f"/parcuri/{parc_id}/prognoza-luna?eroare={quote(rulare.mesaj or 'Rularea a eșuat.')}",
                status_code=303)
        zile_acoperite = len({o.timestamp_utc[:10] for o in rulare.ore})
        mesaj = (f"Prognoză NWP extinsă: {zile_acoperite} zile calculate din modelele meteo "
                 f"(orizont cerut: {orizont} zile).")
    except Exception as exc:
        return RedirectResponse(f"/parcuri/{parc_id}/prognoza-luna?eroare={quote(str(exc))}",
                                 status_code=303)
    return RedirectResponse(f"/parcuri/{parc_id}/prognoza-luna?mesaj={quote(mesaj)}", status_code=303)


# --------------------------------------------------------------------------- Export prognoză (acces la date)
@router.get("/parcuri/{parc_id}/export.csv")
def exporta_prognoza_csv(parc_id: str, rulare_id: str | None = None,
                          format_timp: str | None = None):
    """Exportă prognoza curentă (sau o rulare anume) ca CSV.

    `format_timp` alege cum se scrie coloana `timestamp` (vezi
    `app.core.momente.FORMATE_TIMP`); implicit ISO 8601 cu decalaj local, adică
    exact formatul pe care îl cere importul platformei — un fișier exportat se
    poate reimporta fără conversie. Coloana `timestamp_utc` rămâne mereu
    prezentă, ca sursă de adevăr neambiguă indiferent de alegere."""
    import csv as csv_mod

    from app.core.momente import format_timp_valid, formateaza_moment
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if not parc:
        return RedirectResponse("/", status_code=303)
    rulare = repo.get_rulare(rulare_id) if rulare_id else repo.ultima_rulare_reusita(parc_id)
    if not rulare:
        return RedirectResponse(f"/parcuri/{parc_id}", status_code=303)

    fmt = format_timp_valid(format_timp)
    buf = io.StringIO()
    scriitor = csv_mod.writer(buf, delimiter=";")
    scriitor.writerow(["timestamp", "timestamp_utc", "putere_kw", "p10_kw", "p90_kw",
                        "fizic_kw", "corectie_ml_kw", "ore_in_avans", "blocat"])
    for o in rulare.ore:
        cuantile = o.cuantile_kw or {}
        scriitor.writerow([
            formateaza_moment(o.timestamp_utc, parc.fus_orar, fmt), o.timestamp_utc,
            "" if o.putere_finala_kw is None else f"{o.putere_finala_kw:.3f}",
            f"{cuantile['0.1']:.3f}" if "0.1" in cuantile else "",
            f"{cuantile['0.9']:.3f}" if "0.9" in cuantile else "",
            "" if o.putere_ac_fizic_kw is None else f"{o.putere_ac_fizic_kw:.3f}",
            "" if o.corectie_ml_kw is None else f"{o.corectie_ml_kw:.3f}",
            "" if o.ore_in_avans is None else f"{o.ore_in_avans:.1f}",
            "da" if o.blocat else "nu",
        ])
    nume = f"prognoza_{parc.cod}_{rulare.declansata_la[:10]}.csv"
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                     headers={"Content-Disposition": f"attachment; filename={nume}"})


# --------------------------------------------------------------------------- Rapoarte / comparații (R-1201-1204)
@router.get("/parcuri/{parc_id}/rapoarte")
def rapoarte_pagina(request: Request, parc_id: str):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    acum = datetime.now(timezone.utc)
    return TEMPLATES.TemplateResponse("panou/rapoarte.html", {
        "request": request, "zona": "panou", "parc": parc, "an": acum.year, "luna": acum.month,
        "fusuri": optiuni_select(),
        "formate_timp": FORMATE_TIMP, "format_timp_implicit": FORMAT_TIMP_IMPLICIT,
    })


@router.get("/parcuri/{parc_id}/rapoarte/descarca")
def raport_descarca(parc_id: str, an: int, luna: int, fus_orar: str | None = None,
                     format_timp: str | None = None):
    """Fusul de afișare al coloanei de timp (R-xxx — operatorul alege explicit
    la descărcare, implicit fusul declarat al parcului), plus formatul de
    scriere a momentului (aceeași listă ca la exportul CSV). Timestamp-ul UTC
    brut rămâne oricum în raport (coloana H), ca sursă de adevăr."""
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if fus_orar and not fus_orar_valid(fus_orar):
        fus_orar = None
    continut = genereaza_raport_lunar(repo, parc, an, luna, fus_orar=fus_orar,
                                       format_timp=format_timp)
    nume_fisier = f"raport_{parc.cod}_{an:04d}-{luna:02d}.xlsx"
    return StreamingResponse(io.BytesIO(continut),
                              media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                              headers={"Content-Disposition": f"attachment; filename={nume_fisier}"})
