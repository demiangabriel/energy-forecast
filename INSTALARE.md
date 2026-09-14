# Instalare de la zero

Arhiva conține **tot codul și toată configurarea necesare rulării**. Ce NU
conține sunt datele dumneavoastră — parcurile configurate, producția importată
și modelele ML antrenate. Motivul e practic: ele trăiesc în folderul `data/` de
pe calculatorul dumneavoastră, iar dacă arhiva ar include un `data/` propriu,
dezarhivarea peste instalarea existentă v-ar **șterge baza de date reală**.
Folderul `data/` din arhivă e gol intenționat.

## Pornire rapidă

```bash
# 1. dependențele (o singură dată)
pip install -r requirements.txt

# 2. pornirea
./start.sh              # Linux/Mac
start.bat               # Windows
```

Apoi deschideți <http://127.0.0.1:8000>.

La prima pornire, baza de date `data/prognoza.db` se creează singură, goală.
Nu e nevoie de niciun pas de migrare.

## Verificare că totul e în regulă

```bash
python -m pytest -q
```

Trebuie să treacă toate testele. Rulează în ~25 de secunde, nu atinge
internetul (vremea e sintetică) și nu atinge baza de date reală (fiecare test
primește un fișier temporar).

## Configurare

`.env` din arhivă e **șablonul**, cu toate câmpurile goale. Completați doar ce
folosiți:

| Variabilă | Pentru ce |
|---|---|
| `SMTP_*` | trimiterea rapoartelor pe email; gol = platforma nu trimite nimic |
| `STORAGE_BACKEND` | `sqlite` (implicit, local) sau `firestore` |
| `FIRESTORE_PROJECT_ID`, `GOOGLE_APPLICATION_CREDENTIALS` | doar la backend Firestore |

Dacă aveți deja un `.env` completat, **nu-l suprascrieți** cu cel din arhivă.

## Actualizarea unei instalări existente

Dezarhivați peste folderul existent, dar păstrați:

- `data/` — baza de date, cache-ul, modelele ML antrenate;
- `.env` — configurarea dumneavoastră.

Apoi `pip install -r requirements.txt` (dacă s-au schimbat dependențele) și
`python -m pytest -q`. Pașii detaliați, inclusiv ce se întâmplă cu o bază de
date scrisă de o versiune mai veche, sunt în `docs/GHID_ACTUALIZARE.md`.

## Rulare în Docker

```bash
docker compose up
```

`Dockerfile` și `docker-compose.yml` sunt incluse. Folderul `data/` se montează
ca volum, deci datele supraviețuiesc reconstruirii imaginii.

## Rulare zilnică automată

```bash
python scripts/run_daily.py
```

Se programează din crontab (Linux/Mac) sau Programator de sarcini (Windows).
Pentru rulare din cloud există `.github/workflows/daily-forecast.yml`, pregătit
pentru momentul mutării pe server.

## Ce e unde

| Folder | Conține |
|---|---|
| `app/core/` | fizica, ML, importul, rapoartele — logica propriu-zisă |
| `app/web/` | rutele și șabloanele interfeței |
| `app/storage/` | accesul la date (SQLite și Firestore, aceeași interfață) |
| `tests/` | suita de teste |
| `scripts/` | rularea zilnică și testul de anduranță |
| `docs/` | modelele matematice, ghidul de actualizare, jurnalul de modificări |
