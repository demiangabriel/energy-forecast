"""Dependențe comune pentru rute — un singur loc care creează instanțele
partajate (șabloane Jinja2, acces la Repository).

CompatJinja2Templates există pentru că Starlette a schimbat semnătura lui
TemplateResponse: versiunile vechi acceptau TemplateResponse(nume, context)
cu request-ul ascuns în context; versiunile noi cer TemplateResponse(request,
nume, context) explicit și scot un avertisment (apoi o eroare) pe forma
veche. Restul aplicației apelează în continuare stilul vechi, pe care îl
traducem aici într-un singur loc, ca upgrade-ul de Starlette să nu însemne
umblat prin fiecare rută."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from starlette.templating import Jinja2Templates as StarletteJinja2Templates

from app.core.formatare import fmt_mw
from app.storage import get_repository


class CompatJinja2Templates(StarletteJinja2Templates):
    def TemplateResponse(self, *args, **kwargs):
        # Adaptor de compatibilitate pentru versiunile noi de Starlette/FastAPI,
        # care cer request-ul ca prim argument pozițional.
        if len(args) >= 2 and isinstance(args[0], str) and isinstance(args[1], dict):
            name = args[0]
            context = args[1]
            request = context.get("request")
            return super().TemplateResponse(request, name, context, **kwargs)
        return super().TemplateResponse(*args, **kwargs)


TEMPLATES = CompatJinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
TEMPLATES.env.cache = None


def get_repo():
    return get_repository()


def fmt_kw(v) -> str:
    if v is None:
        return "—"
    return f"{v:,.0f}".replace(",", ".")


def fmt_pct(v) -> str:
    if v is None:
        return "—"
    return f"{v:+.1f}%" if v else "0,0%"


def fmt_local(ts_utc: str | None, fus_orar: str) -> str:
    """Convertește un timestamp UTC ISO în ora LOCALĂ a fusului dat, afișată
    în interfață — R-xxx: operatorul citește ora în fusul parcului
    (România/Moldova sau altul), nu în UTC brut, ca „ziua curentă” din
    prognoză (care exclude local ziua de azi) să nu pară că include ziua
    curentă doar din cauza graniței de zi în UTC."""
    if not ts_utc:
        return "—"
    try:
        dt = datetime.fromisoformat(ts_utc).astimezone(ZoneInfo(fus_orar))
    except Exception:
        return ts_utc[:16].replace("T", " ")
    return dt.strftime("%d.%m.%Y %H:%M")


TEMPLATES.env.filters["kw"] = fmt_kw
TEMPLATES.env.filters["mw"] = fmt_mw
TEMPLATES.env.filters["pct"] = fmt_pct
TEMPLATES.env.filters["local"] = fmt_local
