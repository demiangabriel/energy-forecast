"""
Punctul de intrare FastAPI.

Rulare locală (vezi README.md pentru pași compleți):
    uvicorn app.main:app --reload --port 8000
apoi deschideți http://127.0.0.1:8000 în browser.

De ce FastAPI + șabloane server-side (Jinja2) și nu un front-end separat
(React etc.): R-307 și R-1403 cer explicit ca logica să rămână inspectabilă
și fără framework-uri care o ascund. FastAPI aici e doar rutare HTTP subțire
— fiecare pagină e randată dintr-un șablon simplu, iar toată logica de
business trăiește în app/core/, complet independentă de web/.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.web import routes_panou, routes_tehnic
from app.web.deps import TEMPLATES

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Platformă prognoză producție energie")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "web" / "static")), name="static")
app.include_router(routes_panou.router)
app.include_router(routes_tehnic.router)


@app.exception_handler(Exception)
async def eroare_generala(request: Request, exc: Exception) -> HTMLResponse:
    """Nicio eroare nu trebuie să afișeze un stack trace brut operatorului —
    dar rămâne complet vizibilă în consola unde rulează `uvicorn` (R-1502,
    filozofia secțiunii 15: tăcut/clar, niciodată o cifră greșită netăcut)."""
    import traceback
    traceback.print_exc()
    return TEMPLATES.TemplateResponse(
        "eroare.html", {"request": request, "eroare": str(exc)}, status_code=500,
    )
