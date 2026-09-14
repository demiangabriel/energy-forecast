# Imagine pentru faza 2 (server permanent, secțiunea 16 / R-1602) — NU e
# necesară pentru rularea locală descrisă în README.md ("Pornire rapidă").
#
# Aplicația rămâne exact același cod Python (FastAPI + uvicorn) — singura
# schimbare la migrare e STORAGE_BACKEND=firestore în .env / variabile de
# mediu ale containerului, conform R-1602. Vezi și docker-compose.yml.
FROM python:3.11-slim

WORKDIR /app

# libgomp1 — LightGBM (folosit de corectorul ML) are nevoie de OpenMP la
# rulare; lipsește implicit din imaginea slim și eșuează silențios abia la
# prima antrenare de model dacă nu e instalat aici.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# data/ e folosit doar pentru backend-ul local SQLite — la Firestore, poate
# rămâne gol; îl păstrăm pentru cache-ul meteo local, indiferent de backend.
RUN mkdir -p data/cache data/modele

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
