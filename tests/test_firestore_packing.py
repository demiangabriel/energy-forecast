"""app/storage/firestore_packing.py — împachetarea compactă (un document PER
ZI, nu per oră) folosită de FirestoreRepository pentru producție/calitate.
Funcții PURE, testabile fără credențiale Firestore (care nu există în acest
mediu — FirestoreRepository însuși rămâne netestat live)."""
from __future__ import annotations

from app.models import CalitateOra, ProductieOrara
from app.storage.firestore_packing import (despacheteaza_calitate, despacheteaza_productie,
                                            filtreaza_interval, grupeaza_calitate_pe_zi,
                                            grupeaza_productie_pe_zi, zile_din_interval)


def test_grupeaza_productie_pe_zi_separa_corect_zilele():
    randuri = [
        ProductieOrara(parc_id="p1", timestamp_utc="2026-06-01T23:00:00+00:00", energie_kwh=10.0),
        ProductieOrara(parc_id="p1", timestamp_utc="2026-06-02T00:00:00+00:00", energie_kwh=20.0),
        ProductieOrara(parc_id="p1", timestamp_utc="2026-06-02T01:00:00+00:00", energie_kwh=30.0),
    ]
    grupuri = grupeaza_productie_pe_zi(randuri)
    assert set(grupuri) == {"2026-06-01", "2026-06-02"}
    assert grupuri["2026-06-01"] == {"2026-06-01T23:00:00+00:00": 10.0}
    assert grupuri["2026-06-02"] == {
        "2026-06-02T00:00:00+00:00": 20.0, "2026-06-02T01:00:00+00:00": 30.0,
    }


def test_despacheteaza_productie_e_inversul_grupeaza():
    randuri = [ProductieOrara(parc_id="p1", timestamp_utc=f"2026-06-01T{h:02d}:00:00+00:00", energie_kwh=float(h))
               for h in range(24)]
    grupuri = grupeaza_productie_pe_zi(randuri)
    reconstruit = despacheteaza_productie("p1", grupuri["2026-06-01"] and {"ore": grupuri["2026-06-01"]})
    assert sorted(reconstruit, key=lambda r: r.timestamp_utc) == sorted(randuri, key=lambda r: r.timestamp_utc)


def test_despacheteaza_productie_payload_gol_sau_none():
    assert despacheteaza_productie("p1", None) == []
    assert despacheteaza_productie("p1", {}) == []
    assert despacheteaza_productie("p1", {"ore": {}}) == []


def test_grupeaza_si_despacheteaza_calitate():
    randuri = [
        CalitateOra(parc_id="p1", timestamp_utc="2026-06-01T09:00:00+00:00",
                    steag="indisponibilitate", detaliu="test"),
        CalitateOra(parc_id="p1", timestamp_utc="2026-06-02T09:00:00+00:00",
                    steag="valoare_extrema", detaliu=None),
    ]
    grupuri = grupeaza_calitate_pe_zi(randuri)
    assert set(grupuri) == {"2026-06-01", "2026-06-02"}
    reconstruit_zi1 = despacheteaza_calitate("p1", {"ore": grupuri["2026-06-01"]})
    assert reconstruit_zi1 == [randuri[0]]


def test_zile_din_interval_include_capetele():
    zile = zile_din_interval("2026-06-01T10:00:00+00:00", "2026-06-03T05:00:00+00:00")
    assert zile == ["2026-06-01", "2026-06-02", "2026-06-03"]


def test_zile_din_interval_o_singura_zi():
    assert zile_din_interval("2026-06-01T00:00:00+00:00", "2026-06-01T23:00:00+00:00") == ["2026-06-01"]


def test_zile_din_interval_accepta_capete_inversate():
    a = zile_din_interval("2026-06-03T00:00:00+00:00", "2026-06-01T00:00:00+00:00")
    assert a == ["2026-06-01", "2026-06-02", "2026-06-03"]


def test_filtreaza_interval_taie_orele_din_afara_capetelor_zilei():
    """O zi-document de la capătul intervalului poate conține ore din AFARA
    ferestrei cerute — filtrarea finală trebuie să le taie exact la capete."""
    randuri = [ProductieOrara(parc_id="p1", timestamp_utc=f"2026-06-01T{h:02d}:00:00+00:00", energie_kwh=1.0)
               for h in range(24)]
    filtrat = filtreaza_interval(randuri, "2026-06-01T10:00:00+00:00", "2026-06-01T14:00:00+00:00")
    assert [r.timestamp_utc[11:16] for r in filtrat] == ["10:00", "11:00", "12:00", "13:00", "14:00"]


def test_filtreaza_interval_sorteaza_rezultatul():
    randuri = [
        ProductieOrara(parc_id="p1", timestamp_utc="2026-06-01T05:00:00+00:00", energie_kwh=1.0),
        ProductieOrara(parc_id="p1", timestamp_utc="2026-06-01T02:00:00+00:00", energie_kwh=2.0),
    ]
    filtrat = filtreaza_interval(randuri, "2026-06-01T00:00:00+00:00", "2026-06-01T23:00:00+00:00")
    assert [r.timestamp_utc for r in filtrat] == ["2026-06-01T02:00:00+00:00", "2026-06-01T05:00:00+00:00"]
