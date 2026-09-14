# Ghid: cum actualizez platforma și cum adaug funcții noi

Ghidul e scris pentru situația reală a acestui proiect: **un singur operator**,
care nu programează zilnic, lucrează des împreună cu un asistent AI, și are
nevoie ca platforma să rămână corectă în timp ce crește spre 50 de parcuri.

---

## Partea I — Cum actualizez platforma

### Regula de aur: testele înainte și după

```bash
python -m pytest -q
```

Dacă suita e verde ÎNAINTE de modificare și verde DUPĂ, schimbarea e aproape
sigur în regulă. Dacă era deja roșie înainte, opriți-vă și reparați întâi —
altfel nu mai puteți distinge ce ați stricat de ce era deja stricat.

Suita rulează în ~25 de secunde și nu atinge internetul (vremea e sintetică) și
nici baza de date reală (fiecare test primește un fișier temporar). Nu există
motiv să n-o rulați.

### Pasul cu pasul, pentru o actualizare primită ca arhivă

1. **Faceți o copie de rezervă a datelor.** Modul tehnic → *Copie de rezervă &
   restaurare* → descărcați `complet.json`. Separat, copiați fișierul
   `data/prognoza.db` undeva în afara folderului proiectului. Configurarea
   parcurilor se poate reface din Excel; producția reală importată, nu.
2. **Dezarhivați peste folderul existent**, dar păstrați:
   - `data/` (baza de date, cache-ul, modelele ML antrenate);
   - `.env` (configurarea locală, parolele de email).
   Arhivele livrate nu conțin niciodată aceste două lucruri, tocmai ca să nu
   le poată suprascrie.
3. **Reinstalați dependențele**, dacă s-au schimbat:
   ```bash
   pip install -r requirements.txt
   ```
4. **Rulați testele** (comanda de mai sus). Verde = mergeți mai departe.
5. **Porniți aplicația și deschideți pagina de portofoliu.** Dacă se încarcă
   și arată parcurile corect, baza de date veche a fost citită corect de codul
   nou.
6. **Rulați o prognoză manuală pe un parc** și comparați cu ce era înainte.

### Ce se întâmplă cu baza de date veche

Nu e nevoie de migrări manuale. Citirea trece prin `_doar_campuri_cunoscute`
(`app/storage/serialize.py`), care:

- ignoră câmpurile pe care versiunea curentă nu le cunoaște — o bază scrisă de
  o versiune mai NOUĂ se citește fără să crape;
- lasă câmpurile absente să cadă pe valorile implicite din dataclass — o bază
  scrisă de o versiune mai VECHE merge neschimbată.

Tabelele noi se creează singure la pornire (`CREATE TABLE IF NOT EXISTS` în
`app/storage/db.py`).

Consecința practică pentru dumneavoastră: **un câmp nou adăugat la un model
trebuie să aibă întotdeauna o valoare implicită.** Fără implicit, parcurile
existente devin necitibile.

### După mutarea pe server (GitHub Actions + Firestore)

Aceleași pași, plus:

- verificați secretele din *Settings → Secrets* ale depozitului — ele înlocuiesc
  `.env`;
- după prima rulare automată, deschideți Modul tehnic → *Jurnal* și confirmați
  că toate parcurile au stare `reusita`;
- treceți o dată pe lună pe Modul tehnic → *Întreținere & retenție* (vezi mai
  jos, în „Ce trebuie urmărit la 50 de parcuri").

---

## Partea a II-a — Cum adaug o funcție nouă

### Harta codului, în ordinea în care curg datele

| Vreau să schimb...                                  | Fișierul                                   |
|-----------------------------------------------------|--------------------------------------------|
| de unde vine vremea                                  | `app/core/weather_client.py`               |
| fizica fotovoltaicului                               | `app/core/pv_model.py`                     |
| fizica eolianului                                    | `app/core/wind_model.py`                   |
| corecția învățată din date (ML)                      | `app/core/ml_corrector.py`                 |
| ce se întâmplă la o rulare, cap-coadă                | `app/core/forecast_engine.py`              |
| ce se întâmplă la rularea zilnică pe tot portofoliul | `app/core/daily_run.py`                    |
| controalele care blochează ore suspecte              | `app/core/quality_gates.py`                |
| importul de producție reală                          | `app/core/csv_import.py`                   |
| ce se scrie și se citește din bază                   | `app/storage/` (interfață + 2 implementări)|
| ce vede operatorul în zona prietenoasă               | `app/web/routes_panou.py` + `templates/panou/` |
| ce vede în zona tehnică                              | `app/web/routes_tehnic.py` + `templates/tehnic/` |
| calculele care alimentează ecranele                  | `app/web/vizualizare.py`                   |

Regula de separare: **`app/core/` nu știe nimic despre web, iar `app/web/` nu
face fizică.** Dacă vă treziți calculând producție într-o rută, calculul
trebuie mutat în `core/`; dacă vă treziți formatând text pentru un tabel în
`core/`, formatarea trebuie mutată în `vizualizare.py`.

### Rețeta pentru o funcție nouă

1. **Întâi testul.** Scrieți în `tests/` un test care descrie comportamentul
   dorit și care *pică*. Dacă nu reușiți să scrieți testul, de obicei înseamnă
   că cerința nu e încă limpede — e mai ieftin să vă lămuriți acum.
2. **Apoi implementarea**, în stratul potrivit din tabelul de mai sus.
3. **Apoi interfața**, dacă e nevoie — o rută nouă și un șablon.
4. **Rulați toată suita**, nu doar testul nou. Cele mai multe regresii apar în
   altă parte decât ați umblat.
5. **Notați schimbarea** în `docs/CHANGELOG.md`, cu *motivul*, nu doar cu ce
   ați făcut.

### Reguli de casă care chiar contează

**Comentariile explică DE CE, nu CE.** Codul spune deja ce face. Un comentariu
bun spune de ce alegerea asta și nu cea evidentă — vezi `db.py`, care explică
de ce datele stau ca JSON în loc de coloane normalizate. Un comentariu care
repetă codul îmbătrânește prost și induce în eroare.

**Orice număr magic trebuie explicat.** De ce 3 eșecuri consecutive? De ce 30
de zile? Puneți-l ca o constantă cu nume, la începutul modulului, cu o frază
despre cum a fost ales.

**Nimic nu se strică tăcut.** Când platforma nu poate ști ceva sigur, spune
asta — nu ghicește. Importul refuză o oră ambiguă în loc să aleagă una dintre
variante; ponderile adaptive se întorc goale când dovezile sunt insuficiente,
în loc să „optimizeze" pe zgomot. Păstrați tiparul.

**Producția reală importată nu se modifică niciodată automat** (R-611).
Analizele scriu verdicte separat, în `calitate_productie`, care se pot șterge
și regenera fără să atingă datele brute.

**Ora se scrie în UTC în bază, se afișează local.** Orice timestamp salvat e
UTC ISO. Conversia la ora locală se face doar la afișare, în fusul parcului.
Amestecul celor două e sursa numărul unu de erori într-o platformă de energie.

**Zecimala românească e cu virgulă.** Orice câmp numeric din formulare trece
prin `_num()` / `_numar()`, care acceptă și „30,5". Un `float()` direct
respinge intrarea unui operator care scrie normal, în românește.

### Când adăugați un câmp nou la un model

1. Adăugați-l în `app/models.py`, **cu valoare implicită**.
2. Dacă trebuie salvat, nu faceți nimic special — serializarea e generică.
3. Dacă e editabil, adăugați-l în formularul din zona tehnică și în validare.
4. Adăugați un test care salvează și recitește parcul, verificând câmpul.

### Când adăugați o metodă nouă în Repository

Trebuie implementată în **amândouă** depozitele: `sqlite_repo.py` și
`firestore_repo.py`. Interfața din `repository.py` e abstractă tocmai ca să nu
se poată uita una. Testele rulează pe SQLite; pentru Firestore, gândiți-vă
explicit la costul în *documente citite*, care e unitatea de facturare.

---

## Partea a III-a — Ce trebuie urmărit la 50 de parcuri

Cifrele de mai jos sunt măsurate, nu estimate, cu `scripts/benchmark_scalare.py`
(50 de parcuri, un an de producție, 90 de rulări arhivate per parc):

```bash
python scripts/benchmark_scalare.py --parcuri 50
python scripts/benchmark_scalare.py --parcuri 50 --rezolutie 15 --orizont 16   # cazul greu
```

**Ce e în regulă.** Paginile se încarcă sub o secundă. Rularea zilnică a
întregului portofoliu durează ~1,5 minute, din care mai mult de jumătate e
așteptare după Open-Meteo. Baza de date ajunge la câteva sute de MB pe an.

**Ce trebuie urmărit.**

*Stocarea crește tăcut.* Arhiva de rulări adaugă ordinul unui gigabyte pe an la
50 de parcuri. Local e doar un fișier mare. Pe Firestore înseamnă depășirea
cotei gratuite de stocare (1 GiB) în aproximativ un an. Soluția: Modul tehnic →
*Întreținere & retenție*, o dată pe lună. Se șterge doar istoricul „ce am
prognozat atunci" — producția reală, modelele și configurările rămân neatinse.

*Limita de 1 MiB per document Firestore.* O rulare de 16 zile la pas de 15
minute ocupă ~0,81 MiB — la 81% dintr-o limită TARE. Dacă apar multe
avertismente de calitate, salvarea începe să eșueze. Platforma verifică acum
mărimea înainte de scriere și dă un mesaj clar, dar decizia rămâne a
dumneavoastră: nu combinați orizont maxim cu pas de 15 minute pe același parc.

*Rularea zilnică e secvențială.* Parcurile se procesează unul după altul. La 50
de parcuri e în regulă; peste ~150 ar merita paralelizate cererile meteo. Nu s-a
făcut acum pentru că ar complica lucrurile fără un câștig real la scara asta.

*Limitele Open-Meteo.* O rulare zilnică pe 50 de parcuri înseamnă ~50 de cereri
— mult sub limitele gratuite. Atenție însă la reconstrucția istoricului și la
antrenările ML în serie, care cer intervale lungi și cântăresc mult mai mult.
`weather_client.PRAG_ACTIVE_ABONAMENT_RECOMANDAT` marchează pragul de la care
un abonament plătit devine rezonabil.

### Capcanele de performanță de evitat în cod nou

Două tipare au costat, măsurat, ordine de mărime. Merită recunoscute:

**Nu citiți rulări întregi ca să vă uitați la metadate.** O `Rulare` conține
toate orele ei. Pentru un tabel cu „când, ce tip, ce stare", folosiți
`listeaza_rulari_sumar()`. Pagina de portofoliu construia altfel 222.650 de
obiecte pentru a afișa 50 de rânduri — 12,5 secunde în loc de 0,5.

**Nu căutați într-un DataFrame într-o buclă Python.** `df.loc[ts, col]` într-o
buclă de zeci de mii de iterații costă mai mult decât tot restul calculului la
un loc. Transformați coloana într-un dicționar o singură dată, înainte de
buclă. Ponderile adaptive consumau așa mai mult de jumătate din durata unei
rulări.

Regula generală care le acoperă pe amândouă: **filtrați la sursă, nu în
Python.** Metodele `itereaza_rulari(...)` acceptă un interval; baza de date
elimină rândurile inutile mult mai ieftin decât o face un `if` din buclă — iar
pe Firestore e diferența dintre a plăti și a nu plăti pentru acele documente.
