#!/usr/bin/env python3
"""
Wrapper folosit STRICT de rularea din cloud (.github/workflows/daily-forecast.yml
— R-902). Declanșatorul programat lucrează în UTC și nu poate ști singur când
e "ora 08:00 a României" de-a lungul anului (ora de vară/iarnă se schimbă de
două ori pe an). Soluția (R-903): workflow-ul pornește de DOUĂ ori pe zi
(acoperind ambele offset-uri UTC posibile), iar acest script verifică
EXPLICIT ora locală a României înainte de a rula orice calcul — dacă nu
suntem în fereastra corectă, iese fără să facă nimic. Rezultă o singură
rulare reală pe zi, la ora corectă, fără nicio intervenție manuală.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import setari as setari_mod  # noqa: E402
from app.core.daily_run import ruleaza_portofoliu  # noqa: E402
from app.storage import get_repository  # noqa: E402

# Trebuie să acopere intervalul dintre cele două declanșări cron din workflow
# (o oră) — altfel se pot pierde ferestre sau se poate rula de două ori.
FEREASTRA_MINUTE = 55


def in_fereastra_locala(ora_configurata: str, fus_orar: str = "Europe/Bucharest") -> bool:
    acum = datetime.now(ZoneInfo(fus_orar))
    ora_h, ora_m = (int(x) for x in ora_configurata.split(":"))
    minute_configurate = ora_h * 60 + ora_m
    minute_acum = acum.hour * 60 + acum.minute
    return 0 <= (minute_acum - minute_configurate) < FEREASTRA_MINUTE


def main() -> None:
    repo = get_repository()
    ora_configurata = setari_mod.get(repo, "ora_rulare_zilnica_locala")
    if not in_fereastra_locala(ora_configurata):
        print(f"Nu suntem în fereastra orei configurate ({ora_configurata}, ora României) — nu rulez nimic.")
        return
    rezultate = ruleaza_portofoliu(repo, tip="programata", trimite_email=True)
    for r in rezultate:
        print(r)


if __name__ == "__main__":
    main()
