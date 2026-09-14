"""Rubrica „Reală vs. prognoză" (app/web/vizualizare.py:serie_comparatie_completa)
— în special alegerea liberă a intervalului analizat (inceput_data/sfarsit_data),
cerută pe lângă scurtăturile fixe 7/14/30/90 de zile."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.models import OraPrognoza, ProductieOrara, Rulare, new_id
from app.web.vizualizare import serie_comparatie_completa
from tests.helpers import parc_pv


def _instaleaza_date(repo, parc, zile: int = 20):
    """Producție reală + o singură rulare cu prognoză finală, pe `zile` zile
    consecutive, o oră pe zi la prânz UTC — suficient să distingem ferestre
    de timp diferite fără zgomotul unei serii orare complete."""
    acum = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    momente = [(acum - timedelta(days=d)).replace(hour=12) for d in range(zile)]
    repo.salveaza_productie([
        ProductieOrara(parc_id=parc.id, timestamp_utc=ts.isoformat(), energie_kwh=100.0 + i)
        for i, ts in enumerate(momente)])
    repo.salveaza_rulare(Rulare(
        id=new_id(), parc_id=parc.id, declansata_la=momente[-1].isoformat(),
        tip="programata", stare="reusita",
        ore=[OraPrognoza(timestamp_utc=ts.isoformat(), putere_ac_fizic_kw=90.0,
                          putere_finala_kw=95.0, fractie_incarcare=0.1) for ts in momente]))
    return momente


def test_interval_implicit_pe_zile_ramane_neschimbat(repo):
    """Comportamentul dinainte (fereastră relativă `zile`, fără dată de
    început/sfârșit) trebuie păstrat exact — e folosit de butoanele 7/14/30/90."""
    parc = parc_pv()
    repo.salveaza_parc(parc)
    _instaleaza_date(repo, parc, zile=20)

    rezultat = serie_comparatie_completa(repo, parc, zile=7)
    assert rezultat["zile"] == 7
    assert rezultat["numar_ore_comune"] == 7


def test_interval_ales_liber_filtreaza_corect(repo):
    """Un interval explicit (inceput_data/sfarsit_data) trebuie să primeze
    peste `zile` și să includă DOAR orele din acel interval calendaristic
    local, nu fereastra relativă la „acum”."""
    parc = parc_pv()
    repo.salveaza_parc(parc)
    momente = _instaleaza_date(repo, parc, zile=20)

    tz = __import__("zoneinfo").ZoneInfo(parc.fus_orar)
    zi_inceput = momente[15].astimezone(tz).date()  # cea mai veche dintre cele 4 zile alese
    zi_sfarsit = momente[12].astimezone(tz).date()  # inclusiv

    rezultat = serie_comparatie_completa(repo, parc, inceput_data=zi_inceput, sfarsit_data=zi_sfarsit)
    assert rezultat["numar_ore_comune"] == 4
    assert rezultat["inceput_data"] == zi_inceput.isoformat()
    assert rezultat["sfarsit_data"] == zi_sfarsit.isoformat()


def test_interval_ales_liber_accepta_capete_inversate(repo):
    """Dacă operatorul completează formularul cu capetele inversate, funcția
    nu trebuie să întoarcă o fereastră goală — le rearanjează, nu eșuează."""
    parc = parc_pv()
    repo.salveaza_parc(parc)
    momente = _instaleaza_date(repo, parc, zile=10)
    tz = __import__("zoneinfo").ZoneInfo(parc.fus_orar)
    a = momente[5].astimezone(tz).date()
    b = momente[2].astimezone(tz).date()

    normal = serie_comparatie_completa(repo, parc, inceput_data=b, sfarsit_data=a)
    inversat = serie_comparatie_completa(repo, parc, inceput_data=a, sfarsit_data=b)
    assert normal["numar_ore_comune"] == inversat["numar_ore_comune"] > 0


def test_ruta_comparatie_accepta_interval_in_url(client, repo):
    """Smoke test HTTP: pagina /comparatie cu ?inceput=...&sfarsit=... nu
    crapă și folosește efectiv intervalul din URL, nu implicitul de 14 zile."""
    parc = parc_pv()
    repo.salveaza_parc(parc)
    momente = _instaleaza_date(repo, parc, zile=10)
    tz = __import__("zoneinfo").ZoneInfo(parc.fus_orar)
    inceput = momente[6].astimezone(tz).date().isoformat()
    sfarsit = momente[3].astimezone(tz).date().isoformat()

    r = client.get(f"/parcuri/{parc.id}/comparatie", params={"inceput": inceput, "sfarsit": sfarsit})
    assert r.status_code == 200
    assert "Interval ales" in r.text
    assert f'value="{inceput}"' in r.text
    assert f'value="{sfarsit}"' in r.text
