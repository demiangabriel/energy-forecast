"""Helper mic, reutilizat de ml_corrector.py și calibration.py, ca să nu se
dubleze logica de citire a producției reale importate (Anexa A)."""
from __future__ import annotations

import pandas as pd


def citeste_productie_df(repo, parc_id: str) -> pd.DataFrame:
    productie = repo.listeaza_productie(parc_id, "0001-01-01T00:00:00+00:00", "9999-12-31T23:59:59+00:00")
    if not productie:
        return pd.DataFrame(columns=["productie_kwh"])
    return pd.DataFrame({
        "timestamp": pd.to_datetime([p.timestamp_utc for p in productie], utc=True),
        "productie_kwh": [p.energie_kwh for p in productie],
    }).set_index("timestamp").sort_index()
