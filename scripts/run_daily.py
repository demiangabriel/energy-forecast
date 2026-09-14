#!/usr/bin/env python3
"""
Punct de intrare pentru rularea zilnică a întregului portofoliu (R-901).

Rulare locală manuală:
    python scripts/run_daily.py

Rulare locală PROGRAMATĂ (secțiunea 9): adăugați acest script în
Programator de sarcini (Windows) sau crontab (Linux/Mac) la ora dorită —
vezi README.md, secțiunea "Rulare automată zilnică", pentru pașii exacți.

Rulare din cloud, gratuit, chiar și cu acest calculator oprit (R-902):
vezi .github/workflows/daily-forecast.yml — pregătit pentru momentul mutării
pe server (secțiunea 16 / R-1602); scripts/verifica_ora_si_ruleaza.py e
varianta folosită de acel workflow (gestionează singură ora de vară/iarnă,
R-903).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.daily_run import ruleaza_portofoliu  # noqa: E402
from app.storage import get_repository  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Rulează prognoza pentru tot portofoliul.")
    parser.add_argument("--fara-email", action="store_true", help="Nu trimite emailuri (doar calculează).")
    parser.add_argument("--zile", type=int, default=None, help="Orizont de zile (implicit: setarea din aplicație).")
    parser.add_argument("--tip", default="programata", choices=["programata", "manuala"])
    args = parser.parse_args()

    repo = get_repository()
    rezultate = ruleaza_portofoliu(repo, tip=args.tip, zile_viitor=args.zile,
                                    trimite_email=not args.fara_email)

    print(f"Rulare portofoliu — {len(rezultate)} parc(uri) procesate:")
    for r in rezultate:
        linie = f"  [{r['stare']:>8}] {r.get('parc_cod', '?'):>10} {r.get('parc_nume', '')}"
        if "email" in r:
            linie += f" | email: {r['email']}"
        if r.get("eroare"):
            linie += f" | EROARE: {r['eroare']}"
        print(linie)

    if any(r["stare"] == "esuata" for r in rezultate):
        sys.exit(1)


if __name__ == "__main__":
    main()
