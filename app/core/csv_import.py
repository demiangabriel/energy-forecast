"""
Import producție istorică — Anexa A, secțiunea 11 (R-1101-1104).

FORMATUL DE BAZĂ, OBLIGATORIU: antet exact "timestamp;kWh", separator ';',
iar dată-ora scrisă ISO 8601 CU DECALAJ DE FUS ORAR EXPLICIT:

    timestamp;kWh
    2025-03-30T01:00:00+02:00;1234.5
    2025-03-30T03:00:00+03:00;1301.0

De ce decalajul e obligatoriu, nu opțional
------------------------------------------
O oră scrisă fără decalaj ("2025-10-26 03:00") NU identifică un moment în timp
în România. În noaptea trecerii la ora de iarnă, ora locală 03:00 se petrece de
DOUĂ ori — o dată la +03:00 și o dată la +02:00 — iar la trecerea la ora de
vară ora locală 03:00 nu există deloc. Aceleași cifre pot însemna două momente
diferite sau niciunul, iar diferența e exact o oră de producție atribuită
greșit. Cu decalajul scris în fișier, "2025-10-26T03:00:00+03:00" și
"2025-10-26T03:00:00+02:00" sunt fără nicio îndoială două ore distincte.

Efect practic: dispar cele trei simptome clasice ale unui import ambiguu —
„timestamp duplicat" la trecerea la ora de iarnă, „interval neregulat" la
trecerea la ora de vară și vârful de producție decalat cu o oră față de amiaza
solară. Nu mai e nevoie nici ca fusul de import al parcului să fie ghicit
corect: fișierul îl poartă cu el, linie cu linie.

Formatul vechi (oră locală simplă, fără decalaj)
------------------------------------------------
Multe exporturi SCADA/contor nu scriu decalajul. Acele fișiere se pot importa
în continuare, dar DOAR dacă operatorul confirmă explicit
(`permite_ora_locala=True`, o bifă separată în pagina de import): atunci orele
se interpretează în fusul de IMPORT al parcului (`fus_orar_pentru_import`), iar
orele imposibile sau ambigue de la schimbarea orei sunt semnalate una câte una,
în loc să fie rezolvate tăcut.

Proces în DOI PAȘI separați (R-1102):
 1. analizeaza_fisier() — citește și validează, NU scrie nimic în bază.
 2. importa_randuri() — scrie efectiv, DOAR după ce operatorul a văzut și
    acceptat raportul de la pasul 1.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from pvlib import solarposition
from app.core.timezones import fus_orar_pentru_import
from app.models import Parc, ProductieOrara, Tehnologie

ANTET_ASTEPTAT = "timestamp;kwh"

FORMAT_EXEMPLU = "2025-03-30T01:00:00+02:00"

# Decalaj explicit la finalul textului: „+02:00", „-0500", „+03" sau „Z".
_DECALAJ = re.compile(r"(Z|[+-]\d{2}(:?\d{2})?)$")


def _are_decalaj(ts_text: str) -> bool:
    return bool(_DECALAJ.search(ts_text.strip()))


def _parseaza_cu_decalaj(ts_text: str) -> datetime | None:
    """Acceptă ISO 8601 cu decalaj, cu 'T' sau spațiu între dată și oră, și
    'Z' pentru UTC. Întoarce None dacă textul nu e un moment valid."""
    text = ts_text.strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # „+03" fără minute nu e acceptat de fromisoformat pe Python 3.11
    text = re.sub(r"([+-]\d{2})$", r"\1:00", text)
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _problema_ora_locala(ts_local: datetime, tz: ZoneInfo) -> str | None:
    """Spune dacă o oră LOCALĂ fără decalaj e imposibilă sau ambiguă în fusul
    dat — exact cele două cazuri pe care decalajul explicit le elimină."""
    cu_fus = ts_local.replace(tzinfo=tz)
    dus_intors = cu_fus.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)
    if dus_intors != ts_local:
        return ("oră inexistentă — în noaptea trecerii la ora de VARĂ ceasul sare peste "
                "acest interval, deci momentul nu a existat niciodată")
    if cu_fus.utcoffset() != ts_local.replace(tzinfo=tz, fold=1).utcoffset():
        return ("oră ambiguă — în noaptea trecerii la ora de IARNĂ această oră locală se "
                "petrece de două ori, iar din fișier nu se poate ști care dintre ele e")
    return None


@dataclass
class RaportAnaliza:
    randuri_valide: list[ProductieOrara] = field(default_factory=list)
    erori: list[str] = field(default_factory=list)
    avertismente: list[str] = field(default_factory=list)
    numar_randuri_citite: int = 0

    @property
    def valid(self) -> bool:
        return not self.erori and len(self.randuri_valide) > 0


def _amiaza_solara_pe_zi(zile, latitudine: float, longitudine: float,
                          altitudine_m: float) -> dict:
    """Momentul UTC al amiezii solare pentru fiecare zi din listă.

    Se calculează PE ZI, nu o dată pentru toată perioada, din două motive care
    contează exact cât ora pe care o verificăm: ecuația timpului mută amiaza
    solară cu până la ±16 minute de-a lungul anului, iar ora de vară o mută cu
    o oră întreagă în ora locală. O singură „zi reprezentativă" pentru un
    istoric de mai mulți ani compară, practic, iarna cu vara."""
    grila = pd.DatetimeIndex(
        [pd.Timestamp(z, tz="UTC") + pd.Timedelta(minutes=10 * k)
         for z in zile for k in range(144)])
    poz = solarposition.get_solarposition(
        grila, latitude=latitudine, longitude=longitudine, altitude=altitudine_m)
    elev = poz["apparent_elevation"]
    return {zi: elev[elev.index.date == zi].idxmax() for zi in zile}


# Peste acest număr de zile, verificarea se face pe un eșantion uniform: media
# nu se îmbunătățește semnificativ, iar poziția solară pe zeci de mii de puncte
# ar încetini vizibil analiza unui import de câțiva ani.
ZILE_ESANTION_FUS_ORAR = 60


def _verifica_fus_orar(randuri: list[ProductieOrara], parc: Parc, toleranta_minute: int) -> str | None:
    """R-1104 — verifică fusul orar declarat comparând momentul real al
    producției cu amiaza solară calculată din longitudine, NU doar acceptând
    declarația operatorului. Doar la parcuri fotovoltaice (eolianul nu are un
    vârf legat de poziția soarelui).

    Metoda: pentru fiecare zi se calculează CENTRUL DE GREUTATE energetic al
    producției (media momentelor, ponderată cu energia) și se compară cu amiaza
    solară a ACELEI zile. Pe o zi senină curba de radiație e simetrică față de
    amiaza solară, deci centrul de greutate cade exact pe ea; norii îl
    deplasează, dar MEDIANA pe multe zile rămâne stabilă.

    Varianta dinainte compara ora întreagă a vârfului mediu — calculat peste tot
    istoricul, deci amestecând ore de vară cu ore de iarnă — cu amiaza solară a
    unei singure zile. La un istoric de mai mulți ani putea raporta un decalaj
    de două ore acolo unde nu exista niciunul."""
    if parc.tehnologie != Tehnologie.FOTOVOLTAIC or not randuri:
        return None

    pas = pd.Timedelta(minutes=parc.rezolutie_minute or 60)
    df = pd.DataFrame({
        "ts_utc": pd.to_datetime([r.timestamp_utc for r in randuri], utc=True),
        "kwh": [float(r.energie_kwh) for r in randuri],
    })
    df = df[df["kwh"] > 0]
    if df.empty:
        return None
    # eticheta e începutul intervalului (R-512): energia se produce pe [t, t+pas],
    # deci momentul ei reprezentativ e mijlocul intervalului
    df["mijloc"] = df["ts_utc"] + pas / 2
    df["zi"] = df["mijloc"].dt.date

    zile = sorted(df["zi"].unique())
    if len(zile) > ZILE_ESANTION_FUS_ORAR:
        pasul = len(zile) / ZILE_ESANTION_FUS_ORAR
        zile = [zile[int(i * pasul)] for i in range(ZILE_ESANTION_FUS_ORAR)]

    amiezi = _amiaza_solara_pe_zi(zile, parc.latitudine, parc.longitudine, parc.altitudine_m)

    decalaje_minute = []
    for zi in zile:
        sub = df[df["zi"] == zi]
        energie = sub["kwh"].sum()
        if energie <= 0 or len(sub) < 3:
            continue
        # Media ponderată se face pe DECALAJE (minute față de amiaza zilei), nu
        # pe momente absolute: aritmetica pe epocă ar depinde de unitatea în care
        # pandas expune datele (microsecunde vs nanosecunde, după versiune), iar
        # o unitate greșită dă rezultate absurde, nu o eroare vizibilă.
        fata_de_amiaza = (sub["mijloc"] - amiezi[zi]).dt.total_seconds() / 60.0
        decalaje_minute.append(float((fata_de_amiaza * sub["kwh"]).sum() / energie))

    if len(decalaje_minute) < 3:
        return None      # prea puține zile utile ca să afirmăm ceva

    decalaj_minute = float(pd.Series(decalaje_minute).median())
    if abs(decalaj_minute) <= toleranta_minute:
        return None

    fus_import = fus_orar_pentru_import(parc)
    ore = decalaj_minute / 60.0
    return (f"Fus orar posibil greșit: producția e centrată cu {abs(decalaj_minute):.0f} minute "
            f"({abs(ore):.1f} ore) {'MAI TÂRZIU' if ore > 0 else 'MAI DEVREME'} decât amiaza solară "
            f"reală a locului (longitudine {parc.longitudine:.4f}), măsurat pe {len(decalaje_minute)} "
            f"zile. Fusul de import folosit acum e {fus_import}; un decalaj de aproximativ "
            f"{-ore:+.0f} ore ar alinia datele. Convenția de etichetare nu poate fi cauza: platforma "
            f"folosește peste tot începutul intervalului (12:00 = energia dintre 12:00 și 13:00), la "
            f"fel ca piețele de energie — asigurați-vă că exportul dvs. o folosește și el.")


def analizeaza_fisier(continut: bytes, parc: Parc, toleranta_fus_orar_minute: int = 30,
                       permite_ora_locala: bool = False) -> RaportAnaliza:
    """`permite_ora_locala=True` acceptă formatul vechi (oră locală fără
    decalaj). E o concesie deliberată pentru exporturile SCADA care nu pot
    scrie decalajul — nu implicitul, ca fișierele ambigue să nu intre în bază
    din neatenție."""
    raport = RaportAnaliza()
    try:
        text = continut.decode("utf-8-sig")
    except UnicodeDecodeError:
        raport.erori.append("Fișierul nu e text UTF-8 valid.")
        return raport

    linii = [l for l in text.splitlines() if l.strip() != ""]
    if not linii:
        raport.erori.append("Fișierul e gol.")
        return raport

    antet = linii[0].strip().lower().replace(" ", "")
    if antet != ANTET_ASTEPTAT:
        raport.erori.append(f"Antetul trebuie să fie exact 'timestamp;kWh' (Anexa A) — găsit: '{linii[0].strip()}'.")
        return raport

    fus_import = fus_orar_pentru_import(parc)
    tz = ZoneInfo(fus_import)
    momente_vazute: set[str] = set()
    randuri_temp: list[tuple[datetime, float]] = []
    linii_fara_decalaj = 0

    for numar_linie, linie in enumerate(linii[1:], start=2):
        raport.numar_randuri_citite += 1
        linie = linie.strip()
        parti = linie.split(";")
        if len(parti) != 2:
            raport.erori.append(f"Linia {numar_linie}: se aștepta exact 2 coloane separate prin ';' — "
                                 f"găsit {len(parti)} ('{linie}').")
            continue
        ts_text, val_text = parti[0].strip(), parti[1].strip()

        if _are_decalaj(ts_text):
            moment = _parseaza_cu_decalaj(ts_text)
            if moment is None:
                raport.erori.append(
                    f"Linia {numar_linie}: dată-oră invalidă '{ts_text}' — formatul obligatoriu e "
                    f"ISO 8601 cu decalaj de fus orar, ex. {FORMAT_EXEMPLU}.")
                continue
        else:
            # fără decalaj: acceptat doar dacă operatorul a confirmat explicit
            linii_fara_decalaj += 1
            if not permite_ora_locala:
                if linii_fara_decalaj == 1:
                    raport.erori.append(
                        f"Linia {numar_linie}: ora '{ts_text}' nu are decalaj de fus orar. "
                        f"Formatul obligatoriu e ISO 8601 cu decalaj explicit, ex. "
                        f"{FORMAT_EXEMPLU} (sau '...Z' pentru UTC). Fără el, orele din nopțile "
                        f"schimbării orei sunt ambigue și se importă greșit. Dacă exportul dvs. "
                        f"chiar nu poate scrie decalajul, bifați în pagina de import opțiunea de "
                        f"interpretare ca oră locală — importul continuă atunci pe răspunderea "
                        f"fusului declarat al parcului.")
                continue
            try:
                ts_local = datetime.strptime(ts_text.replace("T", " "), "%Y-%m-%d %H:%M")
            except ValueError:
                try:
                    ts_local = datetime.strptime(ts_text.replace("T", " "), "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    raport.erori.append(
                        f"Linia {numar_linie}: dată-oră invalidă '{ts_text}' — se aștepta "
                        f"AAAA-LL-ZZ HH:MM (format vechi) sau {FORMAT_EXEMPLU} (format de bază).")
                    continue
            problema = _problema_ora_locala(ts_local, tz)
            if problema:
                raport.erori.append(
                    f"Linia {numar_linie}: '{ts_text}' în fusul {fus_import} — {problema}. "
                    f"Scrieți ora cu decalaj explicit (ex. {FORMAT_EXEMPLU}) ca să fie fără dubiu.")
                continue
            moment = ts_local.replace(tzinfo=tz)

        if moment.minute != 0 or moment.second != 0:
            raport.erori.append(f"Linia {numar_linie}: ora '{ts_text}' nu e rotundă — "
                                 f"pasul trebuie să fie o oră.")
            continue
        try:
            valoare = float(val_text.replace(",", "."))
        except ValueError:
            raport.erori.append(f"Linia {numar_linie}: energie invalidă '{val_text}'.")
            continue
        if valoare < 0:
            raport.erori.append(f"Linia {numar_linie}: energie negativă ({valoare}) — valoare imposibilă.")
            continue

        # Duplicatele se judecă pe MOMENTUL absolut (UTC), nu pe text: cu decalaj
        # explicit, două texte diferite pot fi aceeași oră reală, iar două texte
        # identice ca cifre (la schimbarea orei) sunt ore diferite.
        ts_utc = moment.astimezone(timezone.utc).isoformat()
        if ts_utc in momente_vazute:
            raport.avertismente.append(
                f"Linia {numar_linie}: momentul '{ts_text}' apare de mai multe ori "
                f"(aceeași oră reală) — se păstrează ultima valoare.")
        momente_vazute.add(ts_utc)
        randuri_temp.append((moment, valoare))

    if raport.numar_randuri_citite == 0:
        raport.erori.append("Fișierul nu conține niciun rând de date sub antet.")
        return raport
    if raport.erori:
        return raport  # nu continuăm cu conversia dacă structura de bază e deja invalidă

    if linii_fara_decalaj:
        raport.avertismente.append(
            f"{linii_fara_decalaj} din {raport.numar_randuri_citite} linii nu au decalaj de fus "
            f"orar și au fost interpretate ca oră locală în fusul {fus_import}. Formatul de bază "
            f"al platformei include decalajul (ex. {FORMAT_EXEMPLU}); cereți-l furnizorului "
            f"exportului dacă se poate — elimină definitiv erorile de la schimbarea orei.")

    # Eticheta e ÎNCEPUTUL intervalului (R-512), aceeași convenție în tot lanțul
    # și aceeași cu piețele de energie: 12:00 = energia produsă între 12:00 și
    # 13:00. Nu se aplică nicio deplasare — fișierul și baza de date vorbesc
    # deja aceeași limbă.
    for moment, valoare in randuri_temp:
        raport.randuri_valide.append(ProductieOrara(
            parc_id=parc.id, timestamp_utc=moment.astimezone(timezone.utc).isoformat(),
            energie_kwh=valoare,
        ))

    # pas orar regulat — avertisment, nu eroare blocantă (pot exista goluri legitime)
    randuri_sortate = sorted(raport.randuri_valide, key=lambda r: r.timestamp_utc)
    goluri = 0
    for i in range(1, len(randuri_sortate)):
        t0 = datetime.fromisoformat(randuri_sortate[i - 1].timestamp_utc)
        t1 = datetime.fromisoformat(randuri_sortate[i].timestamp_utc)
        if (t1 - t0).total_seconds() != 3600:
            goluri += 1
    if goluri:
        raport.avertismente.append(f"{goluri} interval(e) neregulate detectate (diferite de exact o oră) — "
                                    f"posibile goluri în date.")

    mesaj_fus = _verifica_fus_orar(raport.randuri_valide, parc, toleranta_fus_orar_minute)
    if mesaj_fus:
        raport.avertismente.append(mesaj_fus)

    return raport


def importa_randuri(repo, raport: RaportAnaliza) -> int:
    """Pasul 2 — scrie efectiv în bază. Se apelează DOAR după ce operatorul
    a văzut raportul de la analizeaza_fisier() și a confirmat explicit."""
    if not raport.valid:
        raise ValueError("Raportul de analiză conține erori — importul e blocat până la corectare.")
    return repo.salveaza_productie(raport.randuri_valide)
