"""
Ponderi de ansamblu adaptive — "meta-prognoza".

Ideea (folosită comercial de energy & meteo systems și alții, vezi
docs/cercetare/01-platforme-comerciale.md, B2): în loc de ponderi fixe pentru
modelele meteo, le deducem din performanța lor RECENTĂ pe acest parc anume.
Un model numeric poate fi excelent pe o locație și mediocru pe alta, iar
ierarhia se schimbă în timp (upgrade-uri de model, sezon).

Metoda, deliberat simplă și robustă:
  1. se iau rulările recente, care păstrează per oră valorile meteo folosite;
  2. pentru fiecare model se măsoară eroarea absolută medie a producției
     rezultate din acel model singur, față de producția reală măsurată;
  3. ponderea e invers proporțională cu eroarea (1/eroare), normalizată.

Precauții deliberate:
  - dacă nu există suficiente ore comune, se întorc ponderi goale și rămân
    cele configurate manual — niciodată o "optimizare" pe zgomot;
  - ponderile sunt limitate inferior, ca un model să nu fie eliminat complet
    pe baza unei perioade scurte nefavorabile (diversitatea ansamblului e
    ea însăși valoroasă);
  - calculul folosește DOAR orele utile și necontaminate de probleme de
    calitate, ca defecțiunile parcului să nu fie atribuite unui model meteo.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.data_quality import ore_excluse
from app.core.istoric_productie import citeste_productie_df

ZILE_FEREASTRA = 30
MIN_ORE_NECESARE = 72
PONDERE_MINIMA = 0.1        # niciun model nu coboară sub 10% din ponderea egală
NIVEL_INCREDERE_MINIM = 0.5  # amestec cu ponderile egale: 50% adaptiv, 50% egal


def ponderi_din_performanta(repo, parc, modele_disponibile: list[str]) -> dict[str, float]:
    """Întoarce ponderi per model meteo, sau {} dacă nu există destule dovezi.

    Notă de onestitate metodologică: evaluăm modelele prin eroarea prognozei
    FINALE a rulărilor recente, nu re-rulând fizica pentru fiecare model în
    parte (ceea ce ar cere re-descărcarea arhivei). E o aproximare: măsoară
    cât de bine a mers ansamblul când un model a fost prezent, nu contribuția
    izolată a modelului. De-aceea rezultatul e amestecat cu ponderile egale
    și limitat inferior — îl folosim ca ajustare, nu ca adevăr absolut.
    """
    if len(modele_disponibile) <= 1:
        return {}

    productie = citeste_productie_df(repo, parc.id)
    if productie.empty:
        return {}

    excluse = ore_excluse(repo, parc.id)
    sfarsit = productie.index.max()
    inceput = sfarsit - pd.Timedelta(days=ZILE_FEREASTRA)

    # Căutarea se face pe un DICȚIONAR, nu pe DataFrame: bucla de mai jos atinge
    # zeci de mii de ore, iar `productie.loc[ts, ...]` / `ts in productie.index`
    # costă fiecare o căutare pandas completă (măsurat: peste jumătate din
    # durata unei rulări de prognoză se ducea aici, la 50 de parcuri).
    productie_pe_ora: dict[pd.Timestamp, float] = {
        ts: float(v) for ts, v in productie["productie_kwh"].items()
    }
    excluse_set = set(excluse)

    erori: dict[str, list[float]] = {m: [] for m in modele_disponibile}
    # Fereastra de analiză e [inceput, sfarsit]; rulările emise complet în afara
    # ei nu conțin ore utile, deci nici nu se citesc (contează la fiecare rulare
    # zilnică, pentru fiecare parc din portofoliu).
    from app import setari as setari_mod
    marja = pd.Timedelta(days=int(setari_mod.get(repo, "zile_orizont_maxim")) + 1)
    for rulare in repo.itereaza_rulari(parc.id, limita=120,
                                        declansata_dupa=(inceput - marja).isoformat(),
                                        declansata_inainte=sfarsit.isoformat()):
        for ora in rulare.ore:
            if ora.putere_finala_kw is None or not ora.surse_meteo_folosite:
                continue
            ts = pd.Timestamp(ora.timestamp_utc)
            if ts < inceput or ts > sfarsit or ts in excluse_set:
                continue
            reala = productie_pe_ora.get(ts)
            if reala is None:
                continue
            eroare = abs(ora.putere_finala_kw - reala)
            for model in ora.surse_meteo_folosite:
                if model in erori:
                    erori[model].append(eroare)

    medii = {m: float(np.mean(v)) for m, v in erori.items() if len(v) >= MIN_ORE_NECESARE}
    if len(medii) < 2:
        return {}

    inverse = {m: 1.0 / max(e, 1e-6) for m, e in medii.items()}
    total = sum(inverse.values())
    adaptive = {m: v / total for m, v in inverse.items()}

    # amestec cu ponderile egale + limită inferioară
    egal = 1.0 / len(modele_disponibile)
    rezultat = {}
    for m in modele_disponibile:
        p = adaptive.get(m, egal)
        p = NIVEL_INCREDERE_MINIM * p + (1 - NIVEL_INCREDERE_MINIM) * egal
        rezultat[m] = max(p, PONDERE_MINIMA * egal)

    total_final = sum(rezultat.values())
    return {m: v / total_final for m, v in rezultat.items()}
