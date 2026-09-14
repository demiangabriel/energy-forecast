#!/usr/bin/env bash
# Pornire cu UN SINGUR COMANDĂ — creează mediul virtual dacă lipsește,
# instalează dependențele, pregătește .env din șablon dacă lipsește, apoi
# lansează aplicația. Înlocuiește secvența manuală din README (venv/activate/
# pip install/uvicorn) cu `./start.sh`.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "→ Creez mediul virtual (.venv)..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if [ ! -f .env ]; then
  echo "→ Creez .env din .env.example (completați SMTP etc. mai târziu, dacă e nevoie)..."
  cp .env.example .env
fi

echo "→ Verific dependențele (rapid, dacă sunt deja instalate)..."
pip install -q -r requirements.txt

echo
echo "→ Pornesc aplicația — deschideți http://127.0.0.1:8000 în browser."
echo "  (Opriți cu Ctrl+C)"
echo
exec uvicorn app.main:app --reload
