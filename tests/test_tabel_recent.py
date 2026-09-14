"""„Tabel recent" din detaliul parcului (app/web/vizualizare.py:tabel_recent_zile)
— cerință: 3 zile (nu 24 de INTRĂRI, greșit la rezoluție sub-orară) + suma
totală de energie estimată pe fiecare zi."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import OraPrognoza, Rulare, new_id
from app.web.vizualizare import tabel_recent_zile
from tests.helpers import parc_pv


def _rulare_orara(parc, zile: int = 5, putere_kw: float = 1000.0) -> Rulare:
    start = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    ore = [OraPrognoza(timestamp_utc=(start + timedelta(hours=h)).isoformat(),
                        putere_finala_kw=putere_kw)
           for h in range(zile * 24)]
    return Rulare(id=new_id(), parc_id=parc.id, declansata_la=start.isoformat(),
                  tip="programata", stare="reusita", ore=ore, rezolutie_minute=60)


def test_fara_rulare_returneaza_lista_vida():
    parc = parc_pv()
    assert tabel_recent_zile(None, parc, zile=3) == []


def test_grupeaza_pe_3_zile_locale_cu_total_pe_zi():
    parc = parc_pv(fus_orar="UTC")
    rulare = _rulare_orara(parc, zile=5, putere_kw=1000.0)

    rezultat = tabel_recent_zile(rulare, parc, zile=3)
    assert len(rezultat) == 3
    # zilele trebuie să fie în ordine crescătoare, primele 3 din interval
    assert [z["zi"].isoformat() for z in rezultat] == ["2026-06-01", "2026-06-02", "2026-06-03"]
    for zi in rezultat:
        assert len(zi["ore"]) == 24
        # 24 ore * 1000 kW * 1h = 24000 kWh
        assert zi["energie_kwh"] == 24000.0


def test_rezolutie_sub_orara_scaleaza_corect_energia_pe_zi():
    """La 15 minute, o zi are 96 de intrări (nu 24) — vechea logică
    (primele 24 de INTRĂRI din rulare.ore) ar fi arătat doar 6 ore dintr-o
    singură zi. Gruparea pe zi locală trebuie să dea 3 zile complete, iar
    totalul de energie trebuie scalat cu pasul de 15 minute (0.25h), nu cu 1h."""
    parc = parc_pv(fus_orar="UTC")
    start = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    ore = [OraPrognoza(timestamp_utc=(start + timedelta(minutes=15 * i)).isoformat(),
                        putere_finala_kw=1000.0)
           for i in range(4 * 24 * 5)]  # 5 zile la pas de 15 minute
    rulare = Rulare(id=new_id(), parc_id=parc.id, declansata_la=start.isoformat(),
                     tip="programata", stare="reusita", ore=ore, rezolutie_minute=15)

    rezultat = tabel_recent_zile(rulare, parc, zile=3)
    assert len(rezultat) == 3
    for zi in rezultat:
        assert len(zi["ore"]) == 96
        # 96 intrări * 1000 kW * 0.25h = 24000 kWh (o zi completă la puterea nominală)
        assert zi["energie_kwh"] == 24000.0


def test_respecta_fusul_orar_local_al_parcului():
    """O oră UTC 21:00 e deja ziua următoare la Europe/Bucharest (UTC+2/+3) —
    gruparea trebuie să folosească fusul PARCULUI, nu UTC brut."""
    parc = parc_pv(fus_orar="Europe/Bucharest")
    start = datetime(2026, 6, 1, 21, 0, tzinfo=timezone.utc)  # 2026-06-02 00:00 local (vara, UTC+3)
    ore = [OraPrognoza(timestamp_utc=(start + timedelta(hours=h)).isoformat(), putere_finala_kw=500.0)
           for h in range(6)]
    rulare = Rulare(id=new_id(), parc_id=parc.id, declansata_la=start.isoformat(),
                     tip="programata", stare="reusita", ore=ore, rezolutie_minute=60)

    rezultat = tabel_recent_zile(rulare, parc, zile=3)
    assert len(rezultat) == 1
    assert rezultat[0]["zi"].isoformat() == "2026-06-02"


def test_ignora_orele_fara_putere_finala_in_suma():
    parc = parc_pv(fus_orar="UTC")
    start = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    ore = [OraPrognoza(timestamp_utc=(start + timedelta(hours=h)).isoformat(),
                        putere_finala_kw=None if h == 0 else 1000.0)
           for h in range(24)]
    rulare = Rulare(id=new_id(), parc_id=parc.id, declansata_la=start.isoformat(),
                     tip="programata", stare="reusita", ore=ore, rezolutie_minute=60)

    rezultat = tabel_recent_zile(rulare, parc, zile=3)
    assert len(rezultat) == 1
    # 23 ore valide * 1000 kW * 1h = 23000 kWh (ora BLOCATĂ nu contribuie)
    assert rezultat[0]["energie_kwh"] == 23000.0
