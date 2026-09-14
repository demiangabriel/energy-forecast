"""
Modul tehnic — R-305-307, R-1401-1403. Control complet pe orice câmp din
orice model de date, fără restricții. Zonă separată, clar etichetată.
"""
from __future__ import annotations

import io
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

from app import setari as setari_mod
from app.config import BASE_DIR, MEDIU
from app.core.backup import (backup_complet_json, citeste_randuri_csv, citeste_randuri_xlsx,
                              restaureaza_complet_json, restaureaza_parcuri_din_randuri)
from app.core.sensitivity import analizeaza_senzitivitate
from app.core.timezones import fus_orar_pentru_import, optiuni_select
from app.models import (DirectieUmbrire, ModSeparareRadiatie, ModelTemperaturaCelula,
                         ModelTermicMontaj, ModelTranspozitie, ProfilVertical, ReglajTurbina,
                         Subansamblu, Tehnologie, TehnologiePanou, TipAsezarePV)
from app.web.deps import TEMPLATES, get_repo

router = APIRouter(prefix="/tehnic")


@router.get("")
def index(request: Request):
    repo = get_repo()
    return TEMPLATES.TemplateResponse("tehnic/dashboard_tehnic.html", {
        "request": request, "zona": "tehnic", "parcuri": repo.listeaza_parcuri(),
    })


# --------------------------------------------------------------------------- Config completă parc (R-305)
def _num(text: str) -> float:
    """Float tolerant la virgulă zecimală (convenția din România) — aceeași
    regulă ca `app/web/validare.py:_numar`. Fără ea, un operator care scrie
    „30,5” (obișnuit pe tastatură românească) la înclinare/azimut într-un
    rând din Subansambluri primea `ValueError`, salvarea era respinsă în
    întregime (blocul `try/except` din `parc_config_salveaza`) și — dacă
    mesajul de eroare trecea neobservat — părea pur și simplu că „nu se mai
    poate modifica” valoarea."""
    return float(text.strip().replace(",", "."))


def _serializeaza_subansambluri(subs: list[Subansamblu]) -> str:
    return "\n".join(
        f"{s.nume};{s.putere_dc_kwp};"
        f"{s.inclinare_grade if s.inclinare_grade is not None else ''};"
        f"{s.azimut_grade if s.azimut_grade is not None else ''};{s.tip_asezare.value}"
        for s in subs)


def _parseaza_subansambluri(text: str) -> list[Subansamblu]:
    rezultat = []
    for linie in text.splitlines():
        linie = linie.strip()
        if not linie:
            continue
        parti = [p.strip() for p in linie.split(";")]
        nume, putere = parti[0], _num(parti[1])
        inclinare = _num(parti[2]) if len(parti) > 2 and parti[2] else None
        azimut = _num(parti[3]) if len(parti) > 3 and parti[3] else None
        tip = TipAsezarePV(parti[4]) if len(parti) > 4 and parti[4] else TipAsezarePV.FIX
        rezultat.append(Subansamblu(nume=nume, putere_dc_kwp=putere, inclinare_grade=inclinare,
                                     azimut_grade=azimut, tip_asezare=tip))
    return rezultat


def _serializeaza_curba(curba) -> str:
    if not curba:
        return ""
    return "\n".join(f"{v};{p}" for v, p in curba)


def _parseaza_curba(text: str):
    rezultat = []
    for linie in text.splitlines():
        linie = linie.strip()
        if not linie:
            continue
        v, p = linie.split(";")
        rezultat.append([_num(v), _num(p)])
    return rezultat or None


def _serializeaza_puncte(puncte) -> str:
    """Serializare pentru o listă de perechi [x;y] (curbă de randament)."""
    if not puncte:
        return ""
    return "\n".join(f"{x};{y}" for x, y in puncte)


def _parseaza_puncte(text: str):
    rezultat = []
    for linie in text.splitlines():
        linie = linie.strip()
        if not linie or ";" not in linie:
            continue
        x, y = linie.split(";")
        rezultat.append([_num(x), _num(y)])
    return rezultat or None


def _serializeaza_ponderi(ponderi: dict) -> str:
    return "\n".join(f"{k};{v}" for k, v in ponderi.items())


def _parseaza_ponderi(text: str) -> dict:
    rezultat = {}
    for linie in text.splitlines():
        linie = linie.strip()
        if not linie or ";" not in linie:
            continue
        k, v = linie.split(";")
        rezultat[k.strip()] = _num(v)
    return rezultat


@router.get("/parcuri/{parc_id}")
def parc_config(request: Request, parc_id: str):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if not parc:
        return RedirectResponse("/tehnic", status_code=303)
    alte_parcuri = [p for p in repo.listeaza_parcuri() if p.id != parc_id and p.tehnologie == parc.tehnologie]
    return TEMPLATES.TemplateResponse("tehnic/parc_config.html", {
        "request": request, "zona": "tehnic", "parc": parc, "clienti": repo.listeaza_clienti(),
        "alte_parcuri": alte_parcuri,
        "subansambluri_text": _serializeaza_subansambluri(parc.config_pv.subansambluri) if parc.config_pv else "",
        "curba_text": _serializeaza_curba(parc.config_eolian.curba_putere) if parc.config_eolian else "",
        "ponderi_text": _serializeaza_ponderi(parc.ponderi_meteo),
        "surse_text": ", ".join(parc.surse_meteo),
        "destinatari_text": ", ".join(parc.destinatari_email),
        "curba_randament_text": _serializeaza_puncte(
            parc.config_pv.curba_randament_invertor) if parc.config_pv else "",
        "eroare": None,
        "fusuri": optiuni_select(),
    })


@router.post("/parcuri/{parc_id}")
async def parc_config_salveaza(request: Request, parc_id: str):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if not parc:
        return RedirectResponse("/tehnic", status_code=303)
    form = await request.form()

    def f(nume, implicit=None):
        v = (form.get(nume) or "").strip()
        return v if v != "" else implicit

    def fl(nume, implicit=None):
        v = f(nume)
        # tolerant la virgulă zecimală (convenția din România) — vezi _num()
        return _num(v) if v is not None else implicit

    try:
        parc.cod = f("cod", parc.cod)
        parc.nume = f("nume", parc.nume)
        parc.client_id = f("client_id", parc.client_id)
        parc.latitudine = fl("latitudine", parc.latitudine)
        parc.longitudine = fl("longitudine", parc.longitudine)
        parc.altitudine_m = fl("altitudine_m", parc.altitudine_m) or 0.0
        parc.fus_orar = f("fus_orar", parc.fus_orar)
        parc.fus_orar_import = f("fus_orar_import", "") or ""
        parc.activ = form.get("activ") == "on"
        parc.mod_umbra = form.get("mod_umbra") == "on"
        parc.destinatari_email = [e.strip() for e in (f("destinatari_text", "") or "").split(",") if e.strip()]
        parc.surse_meteo = [s.strip() for s in (f("surse_text", "") or "").split(",") if s.strip()]
        parc.ponderi_meteo = _parseaza_ponderi(f("ponderi_text", "") or "")
        parc.parc_imprumut_corector_id = f("parc_imprumut_corector_id") or None

        if parc.tehnologie == Tehnologie.FOTOVOLTAIC:
            c = parc.config_pv
            c.putere_dc_kwp = fl("putere_dc_kwp", c.putere_dc_kwp)
            c.putere_ac_kw = fl("putere_ac_kw", c.putere_ac_kw)
            c.randament_invertor = fl("randament_invertor", c.randament_invertor)
            c.limita_injectie_kw = fl("limita_injectie_kw")
            c.tehnologie_panou = TehnologiePanou(f("tehnologie_panou", c.tehnologie_panou.value))
            c.coeficient_temperatura_pct_per_c = fl("coeficient_temperatura_pct_per_c")
            c.model_termic_montaj = ModelTermicMontaj(f("model_termic_montaj", c.model_termic_montaj.value))
            c.degradare_anuala_pct = fl("degradare_anuala_pct", c.degradare_anuala_pct)
            c.data_punerii_in_functiune = f("data_punerii_in_functiune")
            c.pierdere_murdarire_pct = fl("pierdere_murdarire_pct", c.pierdere_murdarire_pct)
            c.pierdere_neuniformitate_pct = fl("pierdere_neuniformitate_pct", c.pierdere_neuniformitate_pct)
            c.pierdere_cablaj_conexiuni_pct = fl("pierdere_cablaj_conexiuni_pct", c.pierdere_cablaj_conexiuni_pct)
            c.pierdere_lid_pct = fl("pierdere_lid_pct", c.pierdere_lid_pct)
            c.pierdere_placuta_pct = fl("pierdere_placuta_pct", c.pierdere_placuta_pct)
            c.pierdere_indisponibilitate_pct = fl("pierdere_indisponibilitate_pct", c.pierdere_indisponibilitate_pct)
            c.pierdere_nemodelata_pct = fl("pierdere_nemodelata_pct", c.pierdere_nemodelata_pct)
            c.umbrire_orizont_grade = fl("umbrire_orizont_grade", c.umbrire_orizont_grade) or 0.0
            c.umbrire_orizont_directie = DirectieUmbrire(f("umbrire_orizont_directie", c.umbrire_orizont_directie.value))
            c.subansambluri = _parseaza_subansambluri(f("subansambluri_text", "") or "")
            # câmpurile rapide de mai jos (afișate doar pentru un parc cu O SINGURĂ
            # orientare) au prioritate față de lista de subansambluri de mai sus —
            # editarea directă e mai puțin predispusă la erori decât editarea unui
            # rând text „nume;putere;înclinare;azimut;tip”
            if len(c.subansambluri) == 1:
                incl_rapid = fl("inclinare_grade")
                azim_rapid = fl("azimut_grade")
                if incl_rapid is not None:
                    c.subansambluri[0].inclinare_grade = incl_rapid
                if azim_rapid is not None:
                    c.subansambluri[0].azimut_grade = azim_rapid
            # --- parametri v2 (toți au implicituri; formularul îi expune integral, R-305)
            c.model_transpozitie = ModelTranspozitie(f("model_transpozitie", c.model_transpozitie.value))
            c.albedo = fl("albedo", c.albedo)
            c.albedo_zapada = fl("albedo_zapada", c.albedo_zapada)
            c.model_temperatura = ModelTemperaturaCelula(f("model_temperatura", c.model_temperatura.value))
            c.faiman_u0 = fl("faiman_u0", c.faiman_u0)
            c.faiman_u1 = fl("faiman_u1", c.faiman_u1)
            c.mod_separare_radiatie = ModSeparareRadiatie(
                f("mod_separare_radiatie", c.mod_separare_radiatie.value))
            c.clipping_per_subansamblu = form.get("clipping_per_subansamblu") == "on"
            c.pierderi_zapada = form.get("pierderi_zapada") == "on"
            c.soiling_dinamic = form.get("soiling_dinamic") == "on"
            c.soiling_rata_zi_pct = fl("soiling_rata_zi_pct", c.soiling_rata_zi_pct)
            c.soiling_prag_ploaie_mm = fl("soiling_prag_ploaie_mm", c.soiling_prag_ploaie_mm)
            c.curba_randament_invertor = _parseaza_puncte(f("curba_randament_text", "") or "")
        else:
            c = parc.config_eolian
            c.numar_turbine = int(fl("numar_turbine", c.numar_turbine))
            c.putere_nominala_kw = fl("putere_nominala_kw", c.putere_nominala_kw)
            c.inaltime_butuc_m = fl("inaltime_butuc_m", c.inaltime_butuc_m)
            c.viteza_pornire_ms = fl("viteza_pornire_ms", c.viteza_pornire_ms)
            c.viteza_nominala_ms = fl("viteza_nominala_ms", c.viteza_nominala_ms)
            c.viteza_oprire_ms = fl("viteza_oprire_ms", c.viteza_oprire_ms)
            c.pierdere_siaj_pct = fl("pierdere_siaj_pct", c.pierdere_siaj_pct)
            c.pierdere_sistem_pct = fl("pierdere_sistem_pct", c.pierdere_sistem_pct)
            c.curba_putere = _parseaza_curba(f("curba_text", "") or "")
            # --- parametri v2
            c.profil_vertical = ProfilVertical(f("profil_vertical", c.profil_vertical.value))
            c.reglaj = ReglajTurbina(f("reglaj", c.reglaj.value))
            c.netezire_turbulenta = form.get("netezire_turbulenta") == "on"
            c.wake_dependent_de_viteza = form.get("wake_dependent_de_viteza") == "on"
            c.intensitate_turbulenta = fl("intensitate_turbulenta")
            c.diametru_rotor_m = fl("diametru_rotor_m")

        # --- parametri v2 la nivel de parc
        parc.rezolutie_minute = int(fl("rezolutie_minute", parc.rezolutie_minute) or 60)
        parc.ponderi_automate = form.get("ponderi_automate") == "on"
        parc.produce_cuantile = form.get("produce_cuantile") == "on"
        parc.putere_referinta_kw = fl("putere_referinta_kw")

        repo.salveaza_parc(parc)
    except Exception as exc:
        return TEMPLATES.TemplateResponse("tehnic/parc_config.html", {
            "request": request, "zona": "tehnic", "parc": parc, "clienti": repo.listeaza_clienti(),
            "alte_parcuri": [p for p in repo.listeaza_parcuri() if p.id != parc_id],
            "subansambluri_text": form.get("subansambluri_text", ""), "curba_text": form.get("curba_text", ""),
            "ponderi_text": form.get("ponderi_text", ""), "surse_text": form.get("surse_text", ""),
            "destinatari_text": form.get("destinatari_text", ""),
            "curba_randament_text": form.get("curba_randament_text", ""),
            "eroare": f"Nu s-a putut salva: {exc}",
            "fusuri": optiuni_select(),
        }, status_code=400)

    return RedirectResponse(f"/tehnic/parcuri/{parc_id}?salvat=1", status_code=303)


@router.post("/parcuri/{parc_id}/sterge")
def parc_sterge(parc_id: str):
    """Ștergerea parcului elimină în cascadă și datele derivate (repository),
    și fișierele modelelor ML de pe disc — altfel rămâneau orfane la
    nesfârșit, ocupând spațiu fără să mai fie referite de nimic."""
    import shutil
    from app.core.ml_corrector import MODELE_DIR

    repo = get_repo()
    repo.sterge_parc(parc_id)
    shutil.rmtree(MODELE_DIR / parc_id, ignore_errors=True)
    return RedirectResponse("/tehnic", status_code=303)


@router.post("/parcuri/{parc_id}/sterge-productie")
def parc_sterge_productie(parc_id: str, inceput: str = Form(...), sfarsit: str = Form(...)):
    """R-xxx — curățarea unui import greșit (tipic: fus orar de import
    greșit pe o parte a istoricului) ÎNAINTE de o reimportare curată. Șterge
    STRICT producția reală și verdictele de calitate asociate dintr-un
    interval de date LOCALE (interpretate în fusul de IMPORT al parcului —
    același fus care a interpretat inițial fișierul CSV) — nu atinge
    configurația parcului, prognozele, sau modelele ML antrenate.

    Necesar pentru că reimportarea e un upsert după `timestamp_utc`: dacă
    prima încărcare a folosit fusul greșit, orele afectate au fost scrise la
    timestamp-uri UTC greșite — o reimportare cu fusul corect le-ar ADĂUGA pe
    lângă cele vechi (timestamp-uri diferite), nu le-ar înlocui."""
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if not parc:
        return RedirectResponse("/tehnic", status_code=303)
    try:
        inceput_data = date.fromisoformat(inceput)
        sfarsit_data = date.fromisoformat(sfarsit)
        if sfarsit_data < inceput_data:
            inceput_data, sfarsit_data = sfarsit_data, inceput_data
        tz = ZoneInfo(fus_orar_pentru_import(parc))
        inceput_utc = datetime.combine(inceput_data, time.min, tzinfo=tz).astimezone(timezone.utc).isoformat()
        sfarsit_utc = datetime.combine(sfarsit_data, time.max, tzinfo=tz).astimezone(timezone.utc).isoformat()
        n = repo.sterge_productie_interval(parc.id, inceput_utc, sfarsit_utc)
        mesaj = f"{n} ore de producție reală șterse din interval ({inceput} — {sfarsit}), împreună cu verdictele de calitate asociate."
    except Exception as exc:
        mesaj = f"Nu s-a putut șterge: {exc}"
    return RedirectResponse(f"/tehnic/parcuri/{parc_id}?mesaj={quote(mesaj)}", status_code=303)


# --------------------------------------------------------------------------- Senzitivitate (R-801-802)
@router.get("/parcuri/{parc_id}/senzitivitate")
def senzitivitate_pagina(request: Request, parc_id: str):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if not parc:
        return RedirectResponse("/tehnic", status_code=303)
    return TEMPLATES.TemplateResponse("tehnic/senzitivitate.html", {
        "request": request, "zona": "tehnic", "parc": parc, "rezultat": None,
    })


@router.post("/parcuri/{parc_id}/senzitivitate")
def senzitivitate_ruleaza(request: Request, parc_id: str):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    if not parc:
        return RedirectResponse("/tehnic", status_code=303)
    rezultat, eroare = None, None
    try:
        rezultat = analizeaza_senzitivitate(parc)
    except Exception as exc:
        eroare = (f"Analiza de sensibilitate nu a putut rula: {exc}. "
                  f"Are nevoie de un an de date meteo istorice (reanaliză) — verificați conexiunea.")
    return TEMPLATES.TemplateResponse("tehnic/senzitivitate.html", {
        "request": request, "zona": "tehnic", "parc": parc, "rezultat": rezultat, "eroare": eroare,
    })


# --------------------------------------------------------------------------- Trasabilitate (R-1201)
@router.get("/parcuri/{parc_id}/trasabilitate/{rulare_id}")
def trasabilitate(request: Request, parc_id: str, rulare_id: str):
    repo = get_repo()
    parc = repo.get_parc(parc_id)
    rulare = repo.get_rulare(rulare_id)
    if not parc or not rulare:
        return RedirectResponse("/tehnic/jurnal", status_code=303)
    return TEMPLATES.TemplateResponse("tehnic/trasabilitate.html", {
        "request": request, "zona": "tehnic", "parc": parc, "rulare": rulare,
    })


# --------------------------------------------------------------------------- Setări globale (R-1401)
@router.get("/setari")
def setari_pagina(request: Request):
    repo = get_repo()
    return TEMPLATES.TemplateResponse("tehnic/setari.html", {
        "request": request, "zona": "tehnic", "setari": setari_mod.toate(repo),
    })


@router.post("/setari")
async def setari_salveaza(request: Request):
    """Fiecare setare e validată separat. Înainte, o valoare nenumerică
    (sau o oră scrisă greșit) arunca o excepție și operatorul primea pagina
    generică de eroare, fără să afle ce câmp era greșit și pierzând tot
    formularul."""
    repo = get_repo()
    form = await request.form()
    from app.config import SETARI_IMPLICITE
    erori: list[str] = []
    for cheie, meta in SETARI_IMPLICITE.items():
        if cheie not in form:
            continue
        brut = (form.get(cheie) or "").strip()
        if brut == "":
            continue
        try:
            if meta["tip"] in ("int", "float"):
                valoare = float(brut.replace(",", "."))
                if valoare < 0:
                    raise ValueError("valoarea nu poate fi negativă")
                setari_mod.set(repo, cheie, valoare)
            else:
                if cheie == "ora_rulare_zilnica_locala":
                    ore, _, minute = brut.partition(":")
                    if not (0 <= int(ore) <= 23 and 0 <= int(minute) <= 59):
                        raise ValueError("ora trebuie să fie între 00:00 și 23:59")
                    brut = f"{int(ore):02d}:{int(minute):02d}"
                setari_mod.set(repo, cheie, brut)
        except Exception as exc:
            erori.append(f"{meta['eticheta']}: valoare invalidă ('{brut}') — {exc}")

    if erori:
        return TEMPLATES.TemplateResponse("tehnic/setari.html", {
            "request": request, "zona": "tehnic", "setari": setari_mod.toate(repo),
            "erori": erori,
        }, status_code=400)
    return RedirectResponse("/tehnic/setari?salvat=1", status_code=303)


# --------------------------------------------------------------------------- Întreținere / retenție
@router.get("/intretinere")
def intretinere_pagina(request: Request):
    """Câtă stocare ocupă platforma și ce se poate elibera fără pierderi.

    Ecranul există pentru că datele cresc TĂCUT: arhiva de rulări adaugă, la
    50 de parcuri, ordinul unui gigabyte pe an (o rulare pe zi, per parc, cu
    toate orele ei), iar cache-ul meteo nu se golește niciodată singur.
    Local înseamnă doar un fișier mare; pe Firestore înseamnă depășirea cotei
    gratuite de stocare în aproximativ un an."""
    repo = get_repo()
    parcuri = repo.listeaza_parcuri()
    info = {
        "numar_parcuri": len(parcuri),
        "cale_baza": None, "marime_mb": None,
        "backend": MEDIU.storage_backend,
    }
    cale = BASE_DIR / "data" / "prognoza.db"
    if MEDIU.storage_backend == "sqlite" and cale.exists():
        info["cale_baza"] = str(cale)
        info["marime_mb"] = round(cale.stat().st_size / 1024 / 1024, 1)
    return TEMPLATES.TemplateResponse("tehnic/intretinere.html", {
        "request": request, "zona": "tehnic", "info": info,
        "mesaj": request.query_params.get("mesaj"),
    })


@router.post("/intretinere/curata")
def intretinere_curata(zile_rulari: str = Form(default=""), zile_cache: str = Form(default="")):
    """Retenție aplicată explicit de operator, niciodată automat: ștergerea
    unor date istorice e o decizie, nu o optimizare pe care platforma s-o ia
    singură în spatele lui (același principiu ca la R-611 pentru producția
    reală, care nu se modifică niciodată automat)."""
    repo = get_repo()
    mesaje = []
    try:
        acum = datetime.now(timezone.utc)
        if zile_rulari.strip():
            zile = int(_num(zile_rulari))
            if zile < 30:
                raise ValueError("păstrați cel puțin 30 de zile de rulări")
            limita = (acum - timedelta(days=zile)).isoformat()
            n = repo.sterge_rulari_mai_vechi_de(limita)
            mesaje.append(f"{n} rulări mai vechi de {zile} zile au fost șterse "
                          f"(producția reală și modelele ML NU au fost atinse).")
        if zile_cache.strip():
            zile = int(_num(zile_cache))
            if zile < 1:
                raise ValueError("păstrați cel puțin o zi de cache meteo")
            limita_data = (acum - timedelta(days=zile)).date().isoformat()
            n = repo.sterge_meteo_cache_mai_vechi_de(limita_data)
            mesaje.append(f"{n} zile de cache meteo au fost golite (se re-descarcă la nevoie).")
        if not mesaje:
            mesaje.append("Nu a fost cerută nicio curățare.")
    except Exception as exc:
        mesaje = [f"Curățarea nu a putut rula: {exc}"]
    return RedirectResponse(f"/tehnic/intretinere?mesaj={quote(' '.join(mesaje))}", status_code=303)


# --------------------------------------------------------------------------- Jurnal (R-1008, R-1510-1511)
@router.get("/jurnal")
def jurnal(request: Request, parc_id: str | None = None):
    repo = get_repo()
    parcuri = {p.id: p for p in repo.listeaza_parcuri()}
    incercari_email = repo.listeaza_incercari_email(parc_id=parc_id, limita=150)
    # Jurnalul afișează metadate, nu ore de prognoză. Varianta „toate parcurile"
    # cerea înainte rulările parc cu parc (zeci de interogări, fiecare aducând
    # rulări complete, ca apoi să fie tăiate la primele 150) — acum e o singură
    # interogare peste toate parcurile, ordonată deja de baza de date.
    rulari = repo.listeaza_rulari_sumar(parc_id, limita=100 if parc_id else 150)
    return TEMPLATES.TemplateResponse("tehnic/jurnal.html", {
        "request": request, "zona": "tehnic", "parcuri": parcuri, "incercari_email": incercari_email,
        "rulari": rulari, "parc_id_filtrat": parc_id,
    })


# --------------------------------------------------------------------------- Copie de rezervă & restaurare (modul tehnic)
@router.get("/rezerva")
def rezerva_pagina(request: Request):
    return TEMPLATES.TemplateResponse("tehnic/rezerva.html", {
        "request": request, "zona": "tehnic", "rezultat_restaurare": None, "rezultat_parcuri": None,
        "eroare": None,
    })


@router.get("/rezerva/complet.json")
def rezerva_complet_descarca():
    """Copie de rezervă COMPLETĂ: parcuri, clienți, producție reală, prognoze
    (rulări), verdicte de calitate, metadate modele ML, setări — tot ce nu e
    deja acoperit de copia de rezervă a parcurilor (Panou -> „Copie de
    rezervă parcuri”)."""
    repo = get_repo()
    continut = backup_complet_json(repo)
    nume_fisier = f"rezerva_completa_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    return Response(content=continut, media_type="application/json",
                     headers={"Content-Disposition": f"attachment; filename={nume_fisier}"})


@router.post("/rezerva/complet")
async def rezerva_complet_restaureaza(request: Request, fisier: UploadFile):
    continut = await fisier.read()
    repo = get_repo()
    rezultat = restaureaza_complet_json(repo, continut)
    return TEMPLATES.TemplateResponse("tehnic/rezerva.html", {
        "request": request, "zona": "tehnic", "rezultat_restaurare": rezultat, "rezultat_parcuri": None,
        "eroare": None,
    })


@router.post("/rezerva/parcuri")
async def rezerva_parcuri_restaureaza(request: Request, fisier: UploadFile):
    """„Adaugă automat toate parcurile folosind o copie de rezervă” — acceptă
    fișierul EXACT în formatul descărcat de la Panou -> „Copie de rezervă
    parcuri” (.xlsx sau .csv). Aditiv: parcurile existente (potrivite după
    cod) se actualizează, nu se recreează."""
    continut = await fisier.read()
    nume = (fisier.filename or "").lower()
    try:
        if nume.endswith(".csv"):
            randuri = citeste_randuri_csv(continut)
        else:
            randuri = citeste_randuri_xlsx(continut)
    except Exception as exc:
        return TEMPLATES.TemplateResponse("tehnic/rezerva.html", {
            "request": request, "zona": "tehnic", "rezultat_restaurare": None, "rezultat_parcuri": None,
            "eroare": f"Fișierul nu a putut fi citit: {exc}",
        }, status_code=400)

    repo = get_repo()
    rezultat = restaureaza_parcuri_din_randuri(repo, randuri)
    return TEMPLATES.TemplateResponse("tehnic/rezerva.html", {
        "request": request, "zona": "tehnic", "rezultat_restaurare": None, "rezultat_parcuri": rezultat,
        "eroare": None,
    })


# --------------------------------------------------------------------------- Documentație (R-1304) și cod sursă (R-1403)
@router.get("/documentatie")
def documentatie(request: Request):
    return TEMPLATES.TemplateResponse("tehnic/documentatie.html", {"request": request, "zona": "tehnic"})


@router.get("/cod-sursa")
def cod_sursa(request: Request):
    return TEMPLATES.TemplateResponse("tehnic/cod_sursa.html", {"request": request, "zona": "tehnic"})
