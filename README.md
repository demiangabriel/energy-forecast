# Platformă de prognoză a producției de energie (solar + eolian)

Aplicație locală, fără infrastructură de server, pentru prognoza orară a
producției unor parcuri fotovoltaice și eoliene — model fizic (pvlib +
model eolian propriu) corectat opțional de un strat de învățare automată pe
reziduu (LightGBM), cu controale de calitate care blochează automat orice
rezultat implauzibil înainte să ajungă la un client.

Construită conform specificației `Specificatie_Platforma_Prognoza.pdf`
(18 secțiuni + 4 anexe, ~150 cerințe numerotate R-xxx, citate în comentariile
codului), apoi extinsă pe baza unei cercetări asupra platformelor comerciale,
a proiectelor open-source relevante și a literaturii de specialitate — vezi
[Documentație](#documentație). Pentru arhitectura completă, fișier cu fișier,
vezi pagina **Modul tehnic → Cod sursă & arhitectură** din aplicația pornită,
sau secțiunea [Arhitectură](#arhitectură) de mai jos.

## Cuprins

- [Pornire rapidă (local)](#pornire-rapidă-local)
- [Configurare (.env)](#configurare-env)
- [Cele două zone ale interfeței](#cele-două-zone-ale-interfeței)
- [Ce face platforma, pe scurt](#ce-face-platforma-pe-scurt)
- [Documentație](#documentație)
- [Rulare automată zilnică](#rulare-automată-zilnică)
- [Testare](#testare)
- [Arhitectură](#arhitectură)
- [Migrare la Firestore + server permanent](#migrare-la-firestore--server-permanent)

## Pornire rapidă (local)

Necesită Python 3.11+ (testat cu 3.11).

**Cel mai simplu — un singur pas**, de fiecare dată: creează automat mediul
virtual și `.env` (doar la prima rulare), instalează/verifică dependențele,
apoi pornește aplicația.

```bash
./start.sh          # Linux/Mac
```
```bat
start.bat            :: Windows (dublu-click, sau din linia de comandă)
```

Echivalent manual, pas cu pas (util dacă vreți control explicit asupra fiecărui pas):

```bash
# 1. Mediu virtual
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. Dependențe
pip install -r requirements.txt

# 3. Configurare — copiază șablonul și completează ce ai nevoie (vezi mai jos)
cp .env.example .env

# 4. Pornire
uvicorn app.main:app --reload
```

Aplicația e disponibilă la `http://127.0.0.1:8000`. La prima pornire se
creează automat `data/prognoza.db` (SQLite) — nu trebuie configurată nicio
bază de date separat.

Nu există niciun cont/login — aplicația e gândită pentru un singur operator,
local (R-201).

## Configurare (.env)

Toate valorile din `.env.example` au un implicit rezonabil sau pot rămâne
goale pentru pornirea locală de bază:

| Variabilă | Rol | Obligatorie pentru pornire? |
|---|---|---|
| `STORAGE_BACKEND` | `sqlite` (implicit) sau `firestore` | Nu — implicit `sqlite` |
| `SQLITE_PATH` | Calea fișierului SQLite local | Nu — implicit `data/prognoza.db` |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` | Trimiterea raportului pe email (secțiunea 10) | Nu — fără ele, aplicația funcționează normal, doar trimiterea de email e dezactivată explicit (jurnalizată ca atare, nu ca eroare ascunsă) |
| `OPEN_METEO_*` | Endpoint-urile Open-Meteo (prognoză / arhivă / reanaliză) | Nu — impliciturile funcționează, Open-Meteo nu cere cheie API |
| `APP_TIMEZONE` | Fus orar implicit pentru parcuri noi | Nu — implicit `Europe/Bucharest` |
| `FIRESTORE_PROJECT_ID` / `GOOGLE_APPLICATION_CREDENTIALS` | Doar la migrarea pe Firestore | Nu, pentru faza locală |

Pragurile operaționale (praguri de antrenare ML, orizont de prognoză, ora
rulării zilnice, praguri ale controalelor de calitate etc.) NU se
configurează din `.env` — sunt editabile direct din aplicație, din
**Modul tehnic → Setări** (R-1401), fiecare cu explicația ei în formular.

**SMTP-ul poate fi mutat și el din `.env` în Setări de operare** — deci în
baza de date (Firestore, după migrare) — dacă la **Modul tehnic → Setări**
se completează „Server SMTP”: din acel moment, valorile salvate acolo câștigă
integral față de `.env`, iar rularea din GitHub Actions poate trimite
rapoarte fără niciun secret SMTP configurat separat în GitHub, doar cu
credențiala Firestore (`FIRESTORE_PROJECT_ID` + fișierul de credențiale
rămân obligatoriu în afara bazei de date — e nevoie de ele CA SĂ AJUNGI la
Firestore, deci nu se pot stoca acolo). Detalii: `app/setari.py:smtp_config()`.

## Cele două zone ale interfeței

- **Panou** (`/`) — zona prietenoasă, ghidată: adăugare parc, rulare prognoză,
  **verificare & acuratețe**, **reală vs. prognoză** (fizic / corecție ML /
  final / dezechilibru, per oră), **calitatea datelor**, calibrare, import
  istoric, rapoarte, export CSV, **copie de rezervă a parcurilor** (Excel/CSV).
  Pornește aici.
- **Modul tehnic** (`/tehnic`) — control complet, fără restricții, pe orice
  câmp din orice model de date, plus setări de operare, jurnal complet și
  documentația în-aplicație (R-305).

Orice acțiune importantă e disponibilă din ambele zone, cu navigare
bidirecțională între ele (R-306).

## Ce face platforma, pe scurt

**Prognoză.** Interoghează mai multe modele meteo simultan, le combină cu ponderi (fixe sau deduse
automat din performanța lor recentă pe parcul respectiv), rulează un lanț fizic complet pentru
fotovoltaic sau eolian, apoi aplică o corecție învățată din producția reală măsurată. Livrează la pas
orar sau de 15 minute, cu benzi de incertitudine P10/P50/P90.

**Verificare.** Fiecare parc are o pagină de acuratețe care arată nMAE, nRMSE, bias și un **scor de
skill** față de o prognoză de referință — cu baza de normalizare declarată explicit, fără orele de
noapte și fără orele în care parcul nu a putut produce din motive care nu țin de prognoză.
Indicatorii sunt defalcați și pe orizont (0-6 h, 6-24 h, 24-48 h, 48+ h).

**Reală vs. prognoză.** Pagina „Reală vs. prognoză” (din detaliul fiecărui parc) pune alături, oră cu
oră: producția reală măsurată, prognoza fizică (înainte de orice corecție), corecția aplicată de
modelul ML, prognoza finală (fizic + corecție ML — ce se raportează efectiv) și **dezechilibrul**
(|reală − finală|). Util pentru a vedea exact cât adaugă (sau strică) corectorul ML față de fizica pură.

**Unitatea de măsură afișată.** Panoul, e-mailul către client și raportul Excel lunar afișează
puterea/energia în **MW/MWh, cu 3 zecimale** (rezoluție de 1 kW). Intern, toate calculele rămân în
kW/kWh — conversia se face STRICT la afișare (`app/core/formatare.py`). Zona tehnică (configurarea
parcului, importul CSV conform Anexei A) continuă să folosească kW/kWh, unitatea fișelor tehnice și a
formatului de import fixat prin specificație.

**Fus orar la import.** Fiecare parc are un fus orar declarat; pagina de import istoric îl afișează
explicit și permite schimbarea lui pe loc, înainte de a încărca fișierul — o conversie greșită
deplasează întreaga zi de producție cu 1-3 ore. Lista din formular acoperă România, Moldova și restul
regiunii europene (`app/core/timezones.py`), dar validarea acceptă orice fus orar IANA valid, nu doar
cele din listă — platforma nu e limitată la parcuri din România/Moldova. Toate sursele meteo
(prognoză curentă, arhivă de rulări precedente, reanaliză pentru calibrare) cer și interpretează
datele STRICT în UTC, deci sunt automat corelate între ele, indiferent de fusul fiecărui parc.

**Calitatea datelor.** După fiecare import de istoric, un control automat marchează orele
problematice: decalaj de fus orar, producție raportată noaptea, valori blocate, indisponibilități,
limitări de putere, valori extreme. Ele sunt excluse din antrenarea modelului și raportate separat —
altfel modelul ar învăța defecțiunile parcului în loc de meteorologie.

**Siguranță.** Modul umbră pentru parcurile noi (calculează, dar nu trimite niciodată email),
controale de plauzibilitate care blochează orele imposibile fizic fără să anuleze restul rulării,
promovare controlată a modelelor (un model nou intră în producție doar dacă e măsurabil mai bun),
trasabilitate completă oră cu oră.

**Livrare.** Email automat către client cu raport CSV, export CSV la cerere, raport lunar Excel cu
formule live și o foaie de verificare metodologică.

## Documentație

| Document | Conținut |
|---|---|
| [`docs/MODELE.md`](docs/MODELE.md) | Toate modelele matematice, cu formule, motivația alegerii și referințe bibliografice |
| [`docs/DEBUGGING.md`](docs/DEBUGGING.md) | Auditul complet al aplicației: fiecare problemă găsită, efectul ei practic, corecția și testul care o blochează |
| [`docs/SINTEZA-CERCETARE.md`](docs/SINTEZA-CERCETARE.md) | Ce oferă piața și literatura, ce am adoptat, ce am respins și de ce |
| [`docs/cercetare/`](docs/cercetare/) | Rapoartele integrale de cercetare, cu surse și linkuri |

Documentația de operare e disponibilă și **în aplicație**: Modul tehnic → Documentație, respectiv
Cod sursă & arhitectură.

## Rulare automată zilnică

Cât timp aplicația rulează local, rularea zilnică (secțiunea 9) nu are un
proces de fundal implicit — trebuie programată de sistemul de operare.
Scriptul de rulat e `scripts/run_daily.py` (vezi și `--help`).

**Linux / macOS — crontab**, de exemplu la 08:00 în fiecare zi:

```cron
0 8 * * * cd /calea/catre/prognoza-platforma && .venv/bin/python scripts/run_daily.py >> data/cache/rulare_zilnica.log 2>&1
```

**Windows — Programator de sarcini (Task Scheduler)**: creați o sarcină nouă,
declanșator zilnic la ora dorită, acțiune "Start a program" cu:
- Program: calea către `.venv\Scripts\python.exe`
- Argumente: `scripts\run_daily.py`
- Start in: rădăcina proiectului (`prognoza-platforma`)

Indiferent de metodă, ora efectivă a rulării trebuie să corespundă cu setarea
**Modul tehnic → Setări → Ora rulării automate zilnice** — cele două nu se
sincronizează automat (schimbarea uneia nu o schimbă pe cealaltă).

**Alegerea orei nu e arbitrară.** Piața pentru ziua următoare (OPCOM/PZU) se închide la 11:00, iar
notificările fizice către Transelectrica se depun până la 16:30 în ziua precedentă. O prognoză
livrată după aceste ore nu mai poate fi folosită pentru ziua respectivă. Recomandarea: rularea să se
încheie până la 09:00-10:00. (Orele provin din procedurile publice consultate în timpul cercetării —
vezi `docs/cercetare/01-platforme-comerciale.md`; verificați revizia curentă înainte de a le trata ca
obligație contractuală.)

Opțiuni utile: `--fara-email` (doar calculează, nu trimite nimic — util
pentru testare), `--zile N` (orizont diferit de cel implicit din Setări).

**Rulare din cloud, gratuit, chiar cu calculatorul oprit (R-902)** — pregătit,
dar inactiv până la migrare (vezi mai jos): `.github/workflows/daily-forecast.yml`
pornește de două ori pe zi în UTC (acoperind ambele posibilități de oră
locală a României) și `scripts/verifica_ora_si_ruleaza.py` decide singur,
uitându-se la ora locală reală, dacă rulează efectiv — nicio intervenție
manuală la schimbarea orei de vară/iarnă (R-903). Acest workflow nu poate
funcționa cât aplicația e doar locală (GitHub Actions n-are cum să ajungă la
un fișier SQLite de pe discul vostru) — activați-l abia după migrarea la
Firestore descrisă mai jos.

## Testare

```bash
source .venv/bin/activate
pytest
```

**173 de teste**, toate offline și deterministe — vremea e generată sintetic (modelul de cer senin al
pvlib, cu date incluse în bibliotecă) sau prin substituirea directă a clientului meteo. Suita acoperă:

- **fizica PV**: producție zero noaptea, sezonalitate, degradare, transpoziție, albedo (inclusiv
  zăpadă), pierderi din zăpadă, murdărire dinamică, curbă de randament al invertorului, cele trei
  topologii de limitare (R-412);
- **fizica eoliană**: cut-in/cut-out, netezire cu turbulența, siaj dependent de viteză, densitate cu
  umiditate, profil vertical;
- **combinarea surselor meteo**: renormalizarea ponderilor când un model lipsește (regresie pentru
  cea mai gravă eroare tăcută găsită la audit), dispersie, închiderea fizică a radiației;
- **verificarea**: indicatori, prognoze de referință, scor de skill, pinball, CRPS, acoperire,
  defalcare pe orizont, efectul includerii nopților;
- **calitatea datelor**: fiecare detector în parte, plus testul că pe date bune nu apar alarme false;
- **persistența**: ștergere în cascadă, toleranță la evoluția schemei (câmpuri din viitor, câmpuri
  lipsă, enum invalid);
- **raportul Excel**: verificare structurală a referințelor de celule (clasa de eroare care a lăsat
  raportul fără indicatori), etichete neambigue;
- **rezoluția de 15 minute**: conservarea energiei, interpolarea prin indicele de senin, convenția
  de etichetare;
- **stratul probabilistic**: cuantile ordonate, calibrare, excluderea orelor cu probleme de calitate,
  promovarea pe scor de skill, împrumutul de corector (R-620-623);
- **cazuri de graniță de timp**: ambele tranziții de oră de vară/iarnă, an bisect;
- **interfața web**: adăugare parc pentru ambele tehnologii, validare, regresii pe rutele de formular.

## Arhitectură

```
HTTP (FastAPI, rutare subțire)
        │
        ▼
logică de business (app/core/*.py — funcții simple, fără framework ascuns)
        │
        ▼
Repository (interfață unică, app/storage/repository.py)
        │
   ┌────┴────┐
   ▼         ▼
SQLite    Firestore   (comutare printr-o singură variabilă: STORAGE_BACKEND)
(azi)     (viitor)
```

- **`app/models.py`** — toate structurile de date, dataclass-uri simple
  (fără ORM — R-307/R-1403: nimic ascuns operatorului).
- **`app/core/`** — toată logica de calcul: fizică PV (`pv_model.py`,
  `solar_position.py`), fizică eoliană (`wind_model.py`), client meteo
  (`weather_client.py`, `meteo_mapping.py`), corector ML
  (`ml_corrector.py`), calibrare geometrie (`calibration.py`), analiză de
  sensibilitate (`sensitivity.py`), controale de calitate
  (`quality_gates.py`), import CSV (`csv_import.py`), email
  (`email_report.py`), rapoarte Excel (`reports.py`,
  `excel_template.py`), orchestrare (`forecast_engine.py`,
  `daily_run.py`).
- **`app/storage/`** — `SqliteRepository` (implementarea locală, folosită
  implicit) și `FirestoreRepository` (structural completă, pregătită
  pentru migrare — vezi mai jos), ambele în spatele aceleiași interfețe
  `Repository`.
- **`app/web/`** — rutele FastAPI (`routes_panou.py`, `routes_tehnic.py`)
  și template-urile Jinja2 server-randate (`templates/`) — HTML simplu +
  JS vanilla + Chart.js prin CDN, fără build step și fără framework SPA
  (R-307/R-1403).
- **`scripts/`** — punctele de intrare pentru rularea automată zilnică.

Detalii fișier cu fișier, cu trimiteri explicite la cerințele din
specificație, sunt disponibile live în aplicație: **Modul tehnic → Cod sursă
& arhitectură** și **Modul tehnic → Documentație**.

## Migrare la Firestore + server permanent

Aplicația trebuie să ruleze local, de la sine, la început (R-1601) — dar
codul e scris de la zero ca migrarea ulterioară (secțiunea 16 / R-1602) să
nu ceară nicio rescriere de logică:

1. Creați un proiect Google Cloud cu Firestore activat și un fișier de
   credențiale de serviciu.
2. În `.env` (sau variabile de mediu ale serverului):
   ```
   STORAGE_BACKEND=firestore
   FIRESTORE_PROJECT_ID=<id-ul proiectului>
   GOOGLE_APPLICATION_CREDENTIALS=<calea către fișierul de credențiale>
   ```
3. Nimic altceva nu se schimbă în cod — `app/storage/__init__.py` e
   singurul punct care alege implementarea, conform acestei variabile.
4. Pentru rularea permanentă în cloud, `Dockerfile` și `docker-compose.yml`
   din rădăcina proiectului dau un punct de plecare simplu (un singur
   serviciu, fără altă infrastructură — `docker compose up -d --build`);
   nu sunt necesare pentru rularea locală de zi cu zi și nu au fost testate
   build-uite în acest mediu de dezvoltare (fără daemon Docker disponibil),
   dar urmează exact tiparul standard pentru o aplicație FastAPI/uvicorn.
5. Activați `.github/workflows/daily-forecast.yml` (rularea automată din
   cloud, R-902) abia după acest pas — până atunci workflow-ul n-are cum să
   ajungă la o bază de date care trăiește doar pe discul vostru local.

`FirestoreRepository` (`app/storage/firestore_repo.py`) e implementată
structural complet, în oglindă cu `SqliteRepository`, dar nu a putut fi
testată efectiv în acest mediu de dezvoltare (fără credențiale Google Cloud
disponibile) — merită o verificare atentă la prima migrare reală.
