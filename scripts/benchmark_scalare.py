#!/usr/bin/env python3
"""
Test de anduranță („stress test") pe un portofoliu realist de N parcuri.

De ce există: R-430 presupune „sub 50 de parcuri", dar presupunerea n-a fost
niciodată MĂSURATĂ. Scriptul construiește o bază de date sintetică (parcuri,
ani de producție reală, zeci de rulări per parc) și cronometrează exact
operațiile pe care le face operatorul zilnic — pagina de portofoliu, detaliul
unui parc, comparațiile, rularea zilnică a întregului portofoliu.

Rulare:
    python scripts/benchmark_scalare.py                    # 50 parcuri, implicit
    python scripts/benchmark_scalare.py --parcuri 100 --rulari 120
    python scripts/benchmark_scalare.py --rezolutie 15 --orizont 16   # cazul cel mai greu

NU atinge baza de date reală și NU face cereri de rețea: scrie într-un fișier
temporar, iar vremea e sintetică (aceleași utilitare ca testele).
"""
from __future__ import annotations

import argparse
import random
import sys
import time
import tracemalloc
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from app.models import (ConfigPV, OraPrognoza, Parc, ProductieOrara, Rulare,  # noqa: E402
                         Subansamblu, Tehnologie, TehnologiePanou, TipAsezarePV, new_id)
from app.storage.sqlite_repo import SqliteRepository  # noqa: E402

REZULTATE: list[tuple[str, float, str]] = []


@contextmanager
def cronometru(eticheta: str, detaliu: str = ""):
    start = time.perf_counter()
    yield
    durata = time.perf_counter() - start
    REZULTATE.append((eticheta, durata, detaliu))
    print(f"  {eticheta:<52} {durata:8.3f} s   {detaliu}")


def _config_pv(putere_kwp: float) -> ConfigPV:
    return ConfigPV(
        putere_dc_kwp=putere_kwp, putere_ac_kw=putere_kwp * 0.85,
        subansambluri=[Subansamblu(nume="Principal", putere_dc_kwp=putere_kwp,
                                    inclinare_grade=30.0, azimut_grade=180.0,
                                    tip_asezare=TipAsezarePV.FIX)],
        tehnologie_panou=TehnologiePanou.MONOCRYSTALLINE,
    )


def construieste_portofoliu(repo, numar_parcuri: int, zile_productie: int,
                             rulari_per_parc: int, orizont_zile: int,
                             rezolutie_minute: int) -> list[Parc]:
    """Portofoliu sintetic, dar realist ca ORDIN DE MĂRIME: parcuri împrăștiate
    peste România, ani de producție orară importată, zeci de rulări arhivate
    (rularea zilnică lasă câte una pe zi, deci 90 de rulări ≈ 3 luni de
    funcționare continuă)."""
    rnd = random.Random(42)
    acum = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    pas = timedelta(minutes=rezolutie_minute)
    pasi_per_rulare = int(orizont_zile * 24 * 60 / rezolutie_minute)

    parcuri = []
    for i in range(numar_parcuri):
        putere = rnd.choice([1000.0, 2500.0, 5000.0, 9500.0])
        parc = Parc(
            id=new_id(), cod=f"BM-{i:03d}", nume=f"Parc benchmark {i:03d}", client_id="c-bm",
            tehnologie=Tehnologie.FOTOVOLTAIC,
            latitudine=44.0 + rnd.random() * 3.0, longitudine=23.0 + rnd.random() * 5.0,
            altitudine_m=100.0, fus_orar="Europe/Bucharest",
            rezolutie_minute=rezolutie_minute, mod_umbra=False,
            config_pv=_config_pv(putere),
            destinatari_email=["operator@example.test"],
        )
        repo.salveaza_parc(parc)
        parcuri.append(parc)

        # producție reală importată (istoricul pe care se antrenează ML-ul)
        randuri = []
        for h in range(zile_productie * 24):
            ts = acum - timedelta(hours=h)
            ora_locala = (ts.hour + 3) % 24
            # profil solar grosier: zero noaptea, clopot ziua
            factor = max(0.0, 1 - ((ora_locala - 12) / 6.0) ** 2)
            randuri.append(ProductieOrara(parc_id=parc.id, timestamp_utc=ts.isoformat(),
                                           energie_kwh=putere * 0.8 * factor))
        repo.salveaza_productie(randuri)

        # arhiva de rulări (câte una pe zi, ca rularea programată)
        for r in range(rulari_per_parc):
            declansata = acum - timedelta(days=r)
            ore = []
            for k in range(pasi_per_rulare):
                ts = declansata + pas * k
                ora_locala = (ts.hour + 3) % 24
                factor = max(0.0, 1 - ((ora_locala - 12) / 6.0) ** 2)
                putere_kw = putere * 0.8 * factor
                ore.append(OraPrognoza(
                    timestamp_utc=ts.isoformat(),
                    ghi_wm2=900 * factor, dni_wm2=800 * factor, dhi_wm2=120 * factor,
                    temp_aer_c=22.0, viteza_vant_ms=3.0, dispersie_meteo=40.0 * factor,
                    surse_meteo_folosite=["ecmwf_ifs025", "gfs_seamless", "icon_seamless"],
                    putere_dc_kw=putere_kw * 1.02, putere_ac_fizic_kw=putere_kw,
                    fractie_incarcare=factor, corectie_ml_kw=putere_kw * 0.01,
                    model_folosit="lgbm", putere_finala_kw=putere_kw,
                    cuantile_kw={"0.1": putere_kw * 0.85, "0.5": putere_kw, "0.9": putere_kw * 1.15},
                    putere_clearsky_kw=putere_kw * 1.1, ore_in_avans=float(k),
                ))
            repo.salveaza_rulare(Rulare(
                id=new_id(), parc_id=parc.id, declansata_la=declansata.isoformat(),
                tip="programata", stare="reusita", ore=ore,
                orizont_zile=orizont_zile, rezolutie_minute=rezolutie_minute,
                durata_secunde=4.2,
                surse_meteo_raspuns={"ecmwf_ifs025": True, "gfs_seamless": True},
            ))
        if (i + 1) % 10 == 0:
            print(f"    … {i + 1}/{numar_parcuri} parcuri construite")
    return parcuri


def masoara_web(repo, parcuri: list[Parc]) -> None:
    """Cronometrează exact paginile pe care le deschide operatorul."""
    from fastapi.testclient import TestClient

    import app.web.deps as deps
    from app.main import app

    # get_repo() rezolvă `get_repository` în spațiul de nume al modulului deps
    # (același mecanism folosit de fixture-ul `client` din tests/conftest.py)
    deps.get_repository = lambda: repo
    client = TestClient(app)

    print("\n[2] Pagini web (timp de răspuns pe server, fără rețea/browser)")
    with cronometru("GET /  (portofoliu, toate parcurile)", f"{len(parcuri)} parcuri"):
        r = client.get("/")
        assert r.status_code == 200, r.status_code

    parc = parcuri[0]
    with cronometru("GET /parcuri/{id}  (detaliu parc)"):
        r = client.get(f"/parcuri/{parc.id}")
        assert r.status_code == 200, r.status_code

    with cronometru("GET /parcuri/{id}/comparatie?zile=30"):
        r = client.get(f"/parcuri/{parc.id}/comparatie?zile=30")
        assert r.status_code == 200, r.status_code

    with cronometru("GET /parcuri/{id}/verificare?zile=90"):
        r = client.get(f"/parcuri/{parc.id}/verificare?zile=90")
        assert r.status_code == 200, r.status_code

    with cronometru("GET /parcuri/{id}/calitate"):
        r = client.get(f"/parcuri/{parc.id}/calitate")
        assert r.status_code == 200, r.status_code


def masoara_rulare_zilnica(repo, parcuri: list[Parc], orizont_zile: int) -> None:
    """Rularea zilnică a întregului portofoliu, cu vremea INJECTATĂ (fără
    rețea) — măsoară strict costul de calcul + scriere al platformei, ca să
    se vadă separat de latența Open-Meteo."""
    import app.core.forecast_engine as motor
    from app.core.daily_run import ruleaza_portofoliu

    acum = pd.Timestamp.now(tz="UTC").floor("h")
    index = pd.date_range(acum, periods=(orizont_zile + 2) * 24, freq="h", tz="UTC")
    ora_locala = (index.hour + 3) % 24
    factor = [max(0.0, 1 - ((h - 12) / 6.0) ** 2) for h in ora_locala]
    cadru = pd.DataFrame({
        "shortwave_radiation": [900 * f for f in factor],
        "direct_normal_irradiance": [800 * f for f in factor],
        "diffuse_radiation": [120 * f for f in factor],
        "temperature_2m": 22.0, "wind_speed_10m": 3.0, "wind_speed_100m": 5.0,
        "surface_pressure": 1013.0, "cloud_cover": 20.0,
    }, index=index)

    latenta_retea = [0.0]

    def meteo_fals(lat, lon, modele, zile_trecut=0, zile_viitor=7):
        # simulează latența reală a unei cereri Open-Meteo (măsurabil separat)
        if latenta_retea[0]:
            time.sleep(latenta_retea[0])
        return {m: cadru.copy() for m in modele}

    original = motor.obtine_prognoza_meteo
    motor.obtine_prognoza_meteo = meteo_fals
    try:
        print("\n[3] Rularea zilnică a portofoliului (R-901)")
        with cronometru("ruleaza_portofoliu — CALCUL PUR (rețea 0 ms)", f"{len(parcuri)} parcuri"):
            rezultate = ruleaza_portofoliu(repo, tip="programata", zile_viitor=orizont_zile,
                                            trimite_email=False)
        esecuri = [r for r in rezultate if r["stare"] == "esuata"]
        if esecuri:
            print(f"    ATENȚIE: {len(esecuri)} parcuri eșuate, primul motiv: "
                  f"{esecuri[0].get('eroare') or esecuri[0].get('mesaj')}")

        # a doua oară, cu latență realistă de rețea per parc, ca să se vadă
        # cât din durata totală e strict așteptare după Open-Meteo
        latenta_retea[0] = 0.8
        with cronometru("ruleaza_portofoliu — cu 0,8 s latență meteo/parc",
                         "secvențial, cum e acum"):
            ruleaza_portofoliu(repo, tip="programata", zile_viitor=orizont_zile,
                                trimite_email=False)
    finally:
        motor.obtine_prognoza_meteo = original


def main() -> None:
    p = argparse.ArgumentParser(description="Test de anduranță pe portofoliu mare.")
    p.add_argument("--parcuri", type=int, default=50)
    p.add_argument("--zile-productie", type=int, default=365)
    p.add_argument("--rulari", type=int, default=90, help="rulări arhivate per parc")
    p.add_argument("--orizont", type=int, default=3, help="zile de orizont per rulare")
    p.add_argument("--rezolutie", type=int, default=60, choices=[15, 60])
    p.add_argument("--db", default=None, help="unde se scrie baza sintetică")
    p.add_argument("--pastreaza", action="store_true", help="nu șterge baza la final")
    args = p.parse_args()

    cale = Path(args.db) if args.db else Path("data/benchmark_temp.db")
    if cale.exists():
        cale.unlink()

    print(f"Portofoliu sintetic: {args.parcuri} parcuri · {args.zile_productie} zile producție · "
          f"{args.rulari} rulări/parc · orizont {args.orizont} zile · pas {args.rezolutie} min")
    print(f"Bază de date: {cale}\n")

    repo = SqliteRepository(cale)
    tracemalloc.start()

    print("[1] Construirea portofoliului")
    with cronometru("populare bază de date sintetică"):
        parcuri = construieste_portofoliu(repo, args.parcuri, args.zile_productie,
                                           args.rulari, args.orizont, args.rezolutie)
    marime_mb = cale.stat().st_size / 1024 / 1024
    print(f"    dimensiune bază: {marime_mb:.1f} MB")

    masoara_web(repo, parcuri)
    masoara_rulare_zilnica(repo, parcuri, args.orizont)

    varf_mb = tracemalloc.get_traced_memory()[1] / 1024 / 1024
    tracemalloc.stop()

    print("\n" + "=" * 78)
    print("REZUMAT")
    print("=" * 78)
    for eticheta, durata, detaliu in REZULTATE:
        semnal = "  " if durata < 1 else ("!!" if durata > 5 else " !")
        print(f"{semnal} {eticheta:<52} {durata:8.3f} s  {detaliu}")
    print(f"\n   dimensiune bază de date : {marime_mb:.1f} MB")
    print(f"   vârf memorie Python     : {varf_mb:.0f} MB")
    print("\n   Legendă:  ' !' peste 1 s (se simte)   '!!' peste 5 s (blochează operatorul)")

    if not args.pastreaza:
        cale.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
