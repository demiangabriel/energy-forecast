"""
Controale de calitate automate (secțiunea 15, R-1503).

Filozofie (R-1503, secțiunea 15.3): un obiectiv de livrare ~99% e considerat
mai bun decât o "perfecțiune" de 100% care uneori trimite o cifră greșită
netăcut. Din acest motiv controalele de mai jos se împart în DOUĂ categorii:

- BLOCANTE: prognoza e clar imposibilă fizic (putere negativă, peste puterea
  instalată, producție semnificativă noaptea) — o prognoză care pică un
  control blocant NU se trimite NICIODATĂ (R-1503, R-1007), dar se salvează
  pentru investigare.
- AVERTISMENTE: ceva neobișnuit, dar nu imposibil (salt brusc față de
  rularea precedentă, serie meteo suspect de constantă, energie zilnică
  neobișnuită) — prognoza tot se trimite, dar avertismentul rămâne vizibil
  în pagina de trasabilitate (secțiunea 12), ca operatorul să decidă.

Fiecare rezultat: (avertismente: listă de mesaje per oră, blocat: bool per
oră), aliniate pe indexul orar al seriei de intrare.
"""
from __future__ import annotations

import pandas as pd

from app.models import Tehnologie


class RezultatCalitate:
    def __init__(self, index: pd.DatetimeIndex):
        self.avertismente: dict[pd.Timestamp, list[str]] = {ts: [] for ts in index}
        self.blocat: dict[pd.Timestamp, bool] = {ts: False for ts in index}

    def adauga(self, ts, mesaj: str, blocant: bool = False):
        self.avertismente[ts].append(mesaj)
        if blocant:
            self.blocat[ts] = True


def evalueaza_calitate(
    *,
    putere_finala_kw: pd.Series,
    putere_instalata_kw: float,
    tehnologie: Tehnologie,
    solar_zenith_grade: pd.Series | None = None,
    serie_meteo_pt_constanta: pd.Series | None = None,
    putere_rulare_precedenta_kw: pd.Series | None = None,
    praguri: dict | None = None,
    pas_minute: int = 60,
) -> RezultatCalitate:
    """`pas_minute` e pasul seriei (60 implicit, 15 la rezoluție de sfert de
    oră). Contează în două locuri: energia zilnică (suma puterilor NU e
    energie când pasul nu e o oră) și pragul de "serie constantă", exprimat
    de operator în ORE, nu în pași."""
    praguri = praguri or {}
    pas_minute = max(1, int(pas_minute or 60))
    pasi_pe_ora = 60.0 / pas_minute
    prag_salt_pct = praguri.get("prag_salt_brusc_pct", 60.0)
    prag_noapte_pct = praguri.get("prag_productie_noaptea_pct", 1.0)
    prag_serie_ore = int(praguri.get("prag_serie_constanta_ore", 6))
    prag_cf_pv = praguri.get("prag_cf_zilnic_max_pv_pct", 32.0)
    prag_cf_eolian = praguri.get("prag_cf_zilnic_max_eolian_pct", 95.0)

    rezultat = RezultatCalitate(putere_finala_kw.index)

    # 1) Putere negativă sau peste puterea instalată — imposibil fizic (BLOCANT)
    prag_max_kw = putere_instalata_kw * 1.02  # 2% marjă pt. zgomot numeric
    for ts, val in putere_finala_kw.items():
        if val < -0.01:
            rezultat.adauga(ts, "Putere negativă — valoare imposibilă fizic.", blocant=True)
        elif val > prag_max_kw:
            rezultat.adauga(ts, f"Putere peste puterea instalată ({putere_instalata_kw:.0f} kW).", blocant=True)

    # 2) Producție semnificativă noaptea (doar PV, unde avem poziția solară) — BLOCANT
    if tehnologie == Tehnologie.FOTOVOLTAIC and solar_zenith_grade is not None:
        prag_noapte_kw = putere_instalata_kw * prag_noapte_pct / 100.0
        for ts in putere_finala_kw.index:
            zenit = solar_zenith_grade.get(ts)
            if zenit is not None and zenit >= 95.0 and putere_finala_kw[ts] > prag_noapte_kw:
                rezultat.adauga(ts, "Producție semnificativă calculată în timpul nopții.", blocant=True)

    # 3) Serie meteo suspect de constantă — posibilă sursă blocată/înghețată (AVERTISMENT).
    #    La PV, radiația (GHI) e legitim și constant 0 pe zeci de ore consecutive în
    #    timpul nopții — asta NU e o sursă blocată, e fizică normală. Fără excluderea
    #    orelor de noapte (zenit >= 95°, la fel ca la verificarea #2), practic ORICE
    #    prognoză PV declanșa acest avertisment în fiecare noapte (fals-pozitiv sistematic).
    #    La eolian (fără `solar_zenith_grade`) comportamentul rămâne neschimbat.
    if serie_meteo_pt_constanta is not None and prag_serie_ore > 1:
        prag_serie_pasi = max(2, int(round(prag_serie_ore * pasi_pe_ora)))
        valori = serie_meteo_pt_constanta.round(4)
        identice_consecutiv = valori.eq(valori.shift(1)).fillna(False)
        contor = 0
        for ts, e_identic in identice_consecutiv.items():
            zenit = solar_zenith_grade.get(ts) if solar_zenith_grade is not None else None
            e_noapte = zenit is not None and zenit >= 95.0
            if e_noapte:
                contor = 0  # constanța nopții nu contorizează și nu se semnalează
                continue
            contor = contor + 1 if e_identic else 0
            if contor >= (prag_serie_pasi - 1):
                rezultat.adauga(ts, "Serie meteo suspect de constantă pe mai multe ore consecutive "
                                     "— posibilă sursă blocată.")

    # 4) Salt brusc față de rularea precedentă, pentru aceeași oră (AVERTISMENT)
    if putere_rulare_precedenta_kw is not None and len(putere_rulare_precedenta_kw) > 0:
        prag_absolut_kw = putere_instalata_kw * 0.03  # ignoră variații nesemnificative în valoare absolută
        for ts in putere_finala_kw.index:
            if ts not in putere_rulare_precedenta_kw.index:
                continue
            actual = putere_finala_kw[ts]
            anterior = putere_rulare_precedenta_kw[ts]
            baza = max(abs(anterior), abs(actual))
            if baza < prag_absolut_kw:
                continue
            variatie_pct = abs(actual - anterior) / baza * 100
            if variatie_pct > prag_salt_pct:
                rezultat.adauga(ts, f"Salt de {variatie_pct:.0f}% față de rularea anterioară, aceeași oră "
                                     f"({anterior:.0f} kW -> {actual:.0f} kW).")

    # 5) Energie zilnică implauzibilă (AVERTISMENT) — comparată cu un plafon fizic simplu,
    #    nu cu un istoric lunar (care ar putea să nu existe încă pentru un parc nou).
    prag_cf = prag_cf_pv if tehnologie == Tehnologie.FOTOVOLTAIC else prag_cf_eolian
    plafon_zilnic_kwh = putere_instalata_kw * 24 * (prag_cf / 100.0)
    for zi, grup in putere_finala_kw.groupby(putere_finala_kw.index.date):
        # suma puterilor e energie DOAR la pas orar; la 15 minute fiecare
        # valoare acoperă un sfert de oră
        energie_zi_kwh = grup.sum() / pasi_pe_ora
        if energie_zi_kwh > plafon_zilnic_kwh:
            for ts in grup.index:
                rezultat.adauga(ts, f"Energie zilnică implauzibilă: {energie_zi_kwh:.0f} kWh "
                                     f"({zi}), peste plafonul de {plafon_zilnic_kwh:.0f} kWh.")

    return rezultat
