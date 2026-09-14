"""
Implementarea locală a Repository, peste SQLite — folosită implicit
(STORAGE_BACKEND=sqlite), conform R-1601 ("aplicația trebuie să ruleze
inițial LOCAL, pe un singur calculator, fără infrastructură de server").
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Iterator, Optional

from app.models import (CalitateOra, Client, Parc, ProductieOrara, Rulare, RulareSumar,
                         IncercareEmail, ModelMLInfo)
from app.storage.db import conecteaza
from app.storage.repository import Repository
from app.storage.serialize import (din_dict, parc_to_dict, parc_from_dict,
                                     rulare_to_dict, rulare_from_dict)
from dataclasses import asdict


class SqliteRepository(Repository):
    def __init__(self, cale: Path):
        self._conn = conecteaza(cale)
        self._lock = threading.Lock()  # scrieri seriale — aplicație mono-utilizator (R-201)

    # --- Clienți ---
    def listeaza_clienti(self) -> list[Client]:
        rows = self._conn.execute("SELECT id, nume FROM clienti ORDER BY nume").fetchall()
        return [Client(id=r["id"], nume=r["nume"]) for r in rows]

    def salveaza_client(self, client: Client) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO clienti(id, nume) VALUES(?, ?) "
                "ON CONFLICT(id) DO UPDATE SET nume=excluded.nume",
                (client.id, client.nume),
            )
            self._conn.commit()

    # --- Parcuri ---
    def listeaza_parcuri(self) -> list[Parc]:
        rows = self._conn.execute("SELECT date_json FROM parcuri ORDER BY cod").fetchall()
        return [parc_from_dict(json.loads(r["date_json"])) for r in rows]

    def get_parc(self, parc_id: str) -> Optional[Parc]:
        row = self._conn.execute("SELECT date_json FROM parcuri WHERE id=?", (parc_id,)).fetchone()
        return parc_from_dict(json.loads(row["date_json"])) if row else None

    def salveaza_parc(self, parc: Parc) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO parcuri(id, cod, client_id, tehnologie, activ, date_json) "
                "VALUES(?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET cod=excluded.cod, client_id=excluded.client_id, "
                "tehnologie=excluded.tehnologie, activ=excluded.activ, date_json=excluded.date_json",
                (parc.id, parc.cod, parc.client_id, parc.tehnologie.value, int(parc.activ),
                 json.dumps(parc_to_dict(parc), ensure_ascii=False)),
            )
            self._conn.commit()

    def sterge_parc(self, parc_id: str) -> None:
        """Ștergere în cascadă — vezi motivația din Repository.sterge_parc.
        Fișierele modelelor ML de pe disc sunt șterse de apelant (routes),
        pentru că repository-ul nu trebuie să știe despre sistemul de fișiere."""
        with self._lock:
            for tabel in ("productie_orara", "rulari", "incercari_email",
                           "modele_ml", "meteo_cache", "calitate_productie"):
                self._conn.execute(f"DELETE FROM {tabel} WHERE parc_id=?", (parc_id,))
            self._conn.execute("DELETE FROM parcuri WHERE id=?", (parc_id,))
            self._conn.commit()

    # --- Producție reală ---
    def salveaza_productie(self, randuri: list[ProductieOrara]) -> int:
        with self._lock:
            self._conn.executemany(
                "INSERT INTO productie_orara(parc_id, timestamp_utc, energie_kwh) VALUES(?, ?, ?) "
                "ON CONFLICT(parc_id, timestamp_utc) DO UPDATE SET energie_kwh=excluded.energie_kwh",
                [(r.parc_id, r.timestamp_utc, r.energie_kwh) for r in randuri],
            )
            self._conn.commit()
        return len(randuri)

    def listeaza_productie(self, parc_id: str, inceput_utc: str, sfarsit_utc: str) -> list[ProductieOrara]:
        rows = self._conn.execute(
            "SELECT parc_id, timestamp_utc, energie_kwh FROM productie_orara "
            "WHERE parc_id=? AND timestamp_utc BETWEEN ? AND ? ORDER BY timestamp_utc",
            (parc_id, inceput_utc, sfarsit_utc),
        ).fetchall()
        return [ProductieOrara(parc_id=r["parc_id"], timestamp_utc=r["timestamp_utc"],
                                energie_kwh=r["energie_kwh"]) for r in rows]

    def numar_ore_productie(self, parc_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM productie_orara WHERE parc_id=?", (parc_id,)
        ).fetchone()
        return int(row["n"])

    def sterge_productie_interval(self, parc_id: str, inceput_utc: str, sfarsit_utc: str) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM productie_orara WHERE parc_id=? AND timestamp_utc BETWEEN ? AND ?",
                (parc_id, inceput_utc, sfarsit_utc),
            )
            n = cursor.rowcount
            # verdictele de calitate ale acestor ore ar rămâne orfane, altfel
            self._conn.execute(
                "DELETE FROM calitate_productie WHERE parc_id=? AND timestamp_utc BETWEEN ? AND ?",
                (parc_id, inceput_utc, sfarsit_utc),
            )
            self._conn.commit()
        return max(0, int(n))

    # --- Calitatea producției măsurate ---
    def salveaza_calitate_productie(self, randuri: list[CalitateOra]) -> int:
        if not randuri:
            return 0
        with self._lock:
            self._conn.executemany(
                "INSERT INTO calitate_productie(parc_id, timestamp_utc, steag, detaliu) "
                "VALUES(?, ?, ?, ?) "
                "ON CONFLICT(parc_id, timestamp_utc) DO UPDATE SET "
                "steag=excluded.steag, detaliu=excluded.detaliu",
                [(r.parc_id, r.timestamp_utc, r.steag, r.detaliu) for r in randuri],
            )
            self._conn.commit()
        return len(randuri)

    def listeaza_calitate_productie(self, parc_id: str, inceput_utc: Optional[str] = None,
                                     sfarsit_utc: Optional[str] = None) -> list[CalitateOra]:
        if inceput_utc and sfarsit_utc:
            rows = self._conn.execute(
                "SELECT parc_id, timestamp_utc, steag, detaliu FROM calitate_productie "
                "WHERE parc_id=? AND timestamp_utc BETWEEN ? AND ? ORDER BY timestamp_utc",
                (parc_id, inceput_utc, sfarsit_utc),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT parc_id, timestamp_utc, steag, detaliu FROM calitate_productie "
                "WHERE parc_id=? ORDER BY timestamp_utc", (parc_id,),
            ).fetchall()
        return [CalitateOra(parc_id=r["parc_id"], timestamp_utc=r["timestamp_utc"],
                             steag=r["steag"], detaliu=r["detaliu"]) for r in rows]

    def sterge_calitate_productie(self, parc_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM calitate_productie WHERE parc_id=?", (parc_id,))
            self._conn.commit()

    # --- Rulări ---
    def salveaza_rulare(self, rulare: Rulare) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO rulari(id, parc_id, declansata_la, stare, date_json) VALUES(?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET stare=excluded.stare, date_json=excluded.date_json",
                (rulare.id, rulare.parc_id, rulare.declansata_la, rulare.stare,
                 json.dumps(rulare_to_dict(rulare), ensure_ascii=False)),
            )
            self._conn.commit()

    def get_rulare(self, rulare_id: str) -> Optional[Rulare]:
        row = self._conn.execute("SELECT date_json FROM rulari WHERE id=?", (rulare_id,)).fetchone()
        return rulare_from_dict(json.loads(row["date_json"])) if row else None

    def ultima_rulare_reusita(self, parc_id: str) -> Optional[Rulare]:
        row = self._conn.execute(
            "SELECT date_json FROM rulari WHERE parc_id=? AND stare IN ('reusita','partiala') "
            "ORDER BY declansata_la DESC LIMIT 1",
            (parc_id,),
        ).fetchone()
        return rulare_from_dict(json.loads(row["date_json"])) if row else None

    def itereaza_rulari(self, parc_id: str, limita: int = 30, *,
                         declansata_dupa: Optional[str] = None,
                         declansata_inainte: Optional[str] = None) -> Iterator[Rulare]:
        """Vezi Repository.itereaza_rulari. Cursorul SQLite e deja leneș, deci
        e de ajuns să NU facem fetchall(): fiecare rând se citește și se
        deserializează abia când apelantul cere următoarea rulare."""
        conditii = ["parc_id=?"]
        parametri: list = [parc_id]
        if declansata_dupa:
            conditii.append("declansata_la >= ?")
            parametri.append(declansata_dupa)
        if declansata_inainte:
            conditii.append("declansata_la <= ?")
            parametri.append(declansata_inainte)
        parametri.append(limita)
        cursor = self._conn.execute(
            f"SELECT date_json FROM rulari WHERE {' AND '.join(conditii)} "
            "ORDER BY declansata_la DESC LIMIT ?",
            tuple(parametri),
        )
        for row in cursor:
            yield rulare_from_dict(json.loads(row["date_json"]))

    def sterge_rulari_mai_vechi_de(self, inainte_de_utc: str) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM rulari WHERE declansata_la < ?", (inainte_de_utc,))
            n = cursor.rowcount
            self._conn.commit()
        # VACUUM chiar eliberează spațiul pe disc; fără el fișierul rămâne la
        # dimensiunea de vârf, iar operatorul n-ar vedea niciun efect.
        self._conn.execute("VACUUM")
        return max(0, int(n))

    def sterge_meteo_cache_mai_vechi_de(self, inainte_de_data: str) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM meteo_cache WHERE data < ?", (inainte_de_data,))
            n = cursor.rowcount
            self._conn.commit()
        return max(0, int(n))

    def listeaza_rulari_sumar(self, parc_id: Optional[str] = None,
                               limita: int = 30) -> list[RulareSumar]:
        """Antetele se extrag cu funcțiile JSON native ale SQLite (json_extract
        / json_array_length), nu prin json.loads în Python: motorul citește
        câmpurile cerute direct din textul JSON, iar lista uriașă de ore nu
        ajunge niciodată în memoria procesului."""
        unde = "WHERE parc_id=?" if parc_id else ""
        parametri = (parc_id, limita) if parc_id else (limita,)
        rows = self._conn.execute(
            "SELECT id, parc_id, declansata_la, stare, "
            "       json_extract(date_json, '$.tip') AS tip, "
            "       json_extract(date_json, '$.mesaj') AS mesaj, "
            "       json_extract(date_json, '$.durata_secunde') AS durata_secunde, "
            "       json_extract(date_json, '$.orizont_zile') AS orizont_zile, "
            "       json_extract(date_json, '$.rezolutie_minute') AS rezolutie_minute, "
            "       json_array_length(date_json, '$.ore') AS numar_ore, "
            "       json_extract(date_json, '$.surse_meteo_raspuns') AS surse_json, "
            "       json_extract(date_json, '$.ponderi_folosite') AS ponderi_json "
            f"FROM rulari {unde} ORDER BY declansata_la DESC LIMIT ?",
            parametri,
        ).fetchall()
        return [
            RulareSumar(
                id=r["id"], parc_id=r["parc_id"], declansata_la=r["declansata_la"],
                tip=r["tip"] or "", stare=r["stare"], mesaj=r["mesaj"],
                durata_secunde=r["durata_secunde"],
                orizont_zile=r["orizont_zile"] if r["orizont_zile"] is not None else 3,
                rezolutie_minute=r["rezolutie_minute"] if r["rezolutie_minute"] is not None else 60,
                numar_ore=r["numar_ore"] or 0,
                surse_meteo_raspuns=json.loads(r["surse_json"]) if r["surse_json"] else {},
                ponderi_folosite=json.loads(r["ponderi_json"]) if r["ponderi_json"] else {},
            )
            for r in rows
        ]

    # --- Email ---
    def salveaza_incercare_email(self, incercare: IncercareEmail) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO incercari_email(id, parc_id, moment, date_json) VALUES(?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET date_json=excluded.date_json",
                (incercare.id, incercare.parc_id, incercare.moment,
                 json.dumps(asdict(incercare), ensure_ascii=False)),
            )
            self._conn.commit()

    def listeaza_incercari_email(self, parc_id: Optional[str] = None, limita: int = 100) -> list[IncercareEmail]:
        if parc_id:
            rows = self._conn.execute(
                "SELECT date_json FROM incercari_email WHERE parc_id=? ORDER BY moment DESC LIMIT ?",
                (parc_id, limita),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT date_json FROM incercari_email ORDER BY moment DESC LIMIT ?", (limita,)
            ).fetchall()
        return [din_dict(IncercareEmail, json.loads(r["date_json"])) for r in rows]

    # --- Modele ML ---
    def salveaza_model_ml(self, model: ModelMLInfo) -> None:
        with self._lock:
            if model.activ:
                # doar un singur model activ per parc (R-605)
                self._conn.execute(
                    "UPDATE modele_ml SET activ=0, date_json=json_set(date_json,'$.activ',0) "
                    "WHERE parc_id=? AND activ=1", (model.parc_id,),
                )
            self._conn.execute(
                "INSERT INTO modele_ml(id, parc_id, activ, antrenat_la, date_json) VALUES(?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET activ=excluded.activ, date_json=excluded.date_json",
                (model.id, model.parc_id, int(model.activ), model.antrenat_la,
                 json.dumps(asdict(model), ensure_ascii=False)),
            )
            self._conn.commit()

    def model_ml_activ(self, parc_id: str) -> Optional[ModelMLInfo]:
        row = self._conn.execute(
            "SELECT date_json FROM modele_ml WHERE parc_id=? AND activ=1 ORDER BY antrenat_la DESC LIMIT 1",
            (parc_id,),
        ).fetchone()
        return din_dict(ModelMLInfo, json.loads(row["date_json"])) if row else None

    def listeaza_modele_ml(self, parc_id: str) -> list[ModelMLInfo]:
        rows = self._conn.execute(
            "SELECT date_json FROM modele_ml WHERE parc_id=? ORDER BY antrenat_la DESC", (parc_id,)
        ).fetchall()
        return [din_dict(ModelMLInfo, json.loads(r["date_json"])) for r in rows]

    # --- Setări ---
    def get_setare(self, cheie: str, implicit=None):
        row = self._conn.execute("SELECT valoare_json FROM setari WHERE cheie=?", (cheie,)).fetchone()
        return json.loads(row["valoare_json"]) if row else implicit

    def set_setare(self, cheie: str, valoare) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO setari(cheie, valoare_json) VALUES(?, ?) "
                "ON CONFLICT(cheie) DO UPDATE SET valoare_json=excluded.valoare_json",
                (cheie, json.dumps(valoare, ensure_ascii=False)),
            )
            self._conn.commit()

    def toate_setarile(self) -> dict:
        rows = self._conn.execute("SELECT cheie, valoare_json FROM setari").fetchall()
        return {r["cheie"]: json.loads(r["valoare_json"]) for r in rows}

    # --- Cache meteo ---
    def get_meteo_cache(self, parc_id: str, sursa: str, data: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT payload_json FROM meteo_cache WHERE parc_id=? AND sursa=? AND data=?",
            (parc_id, sursa, data),
        ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def set_meteo_cache(self, parc_id: str, sursa: str, data: str, payload: dict) -> None:
        from datetime import datetime, timezone
        with self._lock:
            self._conn.execute(
                "INSERT INTO meteo_cache(parc_id, sursa, data, fetched_at, payload_json) VALUES(?, ?, ?, ?, ?) "
                "ON CONFLICT(parc_id, sursa, data) DO UPDATE SET fetched_at=excluded.fetched_at, "
                "payload_json=excluded.payload_json",
                (parc_id, sursa, data, datetime.now(timezone.utc).isoformat(),
                 json.dumps(payload, ensure_ascii=False)),
            )
            self._conn.commit()
