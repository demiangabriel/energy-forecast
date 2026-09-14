from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.storage.sqlite_repo import SqliteRepository


@pytest.fixture(autouse=True)
def _reseteaza_disjunctorul_meteo():
    """Disjunctorul din weather_client e stare GLOBALĂ de modul (intenționat —
    „sursa e căzută" e o proprietate a sursei, nu a unui parc). Fără resetare
    între teste, un test care simulează o pană ar face testele următoare să
    eșueze fără nicio legătură între ele."""
    from app.core.weather_client import DISJUNCTOR
    DISJUNCTOR.reseteaza()
    yield
    DISJUNCTOR.reseteaza()


@pytest.fixture
def repo(tmp_path):
    """Un Repository SQLite izolat, pe un fișier temporar per test — nu
    atinge niciodată data/prognoza.db real al aplicației."""
    return SqliteRepository(tmp_path / "test.db")


@pytest.fixture
def client(repo, monkeypatch):
    """TestClient FastAPI, redirecționat spre `repo`-ul de test izolat.
    get_repo() din app/web/deps.py caută numele `get_repository` în spațiul
    de nume al modulului deps — îl înlocuim acolo, ceea ce redirecționează
    automat TOATE apelurile get_repo() din routes_panou.py/routes_tehnic.py
    (aceeași funcție, deci același __globals__)."""
    from fastapi.testclient import TestClient
    import app.web.deps as deps_mod
    monkeypatch.setattr(deps_mod, "get_repository", lambda: repo)
    from app.main import app
    return TestClient(app)
