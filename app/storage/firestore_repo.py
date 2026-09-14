"""
Implementarea Repository peste Google Firestore — folosită DOAR când
STORAGE_BACKEND=firestore (etapa 2 a proiectului, secțiunea 16 / R-1602).

Structura de colecții e o oglindă directă a tabelelor SQLite din db.py, ca
migrarea să fie strict o schimbare de "unde se scrie", nu de "ce se scrie":

    clienti/{client_id}                          -> {nume}
    parcuri/{parc_id}                             -> Parc serializat (parc_to_dict)
    parcuri/{parc_id}/productie_zilnica/{zi_utc}  -> {ore: {ts_utc: energie_kwh, ...}}  (R-xxx: împachetare compactă, vezi firestore_packing.py)
    parcuri/{parc_id}/calitate_zilnica/{zi_utc}   -> {ore: {ts_utc: {steag, detaliu}, ...}}
    rulari/{rulare_id}                            -> Rulare serializată (parc_id, declansata_la, stare, ...)
    incercari_email/{id}                          -> IncercareEmail serializată
    modele_ml/{id}                                -> ModelMLInfo serializată
    setari/{cheie}                                -> {valoare: ...}
    meteo_cache/{parc_id}__{sursa}__{data}        -> payload brut (deja pe zi)

`productie` și `calitate` NU se mai scriu un document per oră — un an de
istoric orar ar însemna ~8760 documente, deci ~8760 scrieri la un singur
import istoric și tot atâtea citiri la fiecare interogare pe an întreg,
suficient să epuizeze cota gratuită (20k scrieri/zi, 50k citiri/zi) dintr-un
singur import de câțiva ani. Se grupează în schimb pe ZIUA calendaristică
UTC — un document per zi — reducând documentele/citirile/scrierile de
~24×. Detaliile împachetării (pure, testate exhaustiv fără Firestore) sunt
în `firestore_packing.py`.

Nu e testată LIVE în acest mediu (nu există credențiale Firestore aici) —
dar respectă fidel interfața Repository, deci odată instalat pachetul
`google-cloud-firestore` și setate FIRESTORE_PROJECT_ID /
GOOGLE_APPLICATION_CREDENTIALS în .env, restul aplicației funcționează
neschimbat (get_repository() în storage/__init__.py face comutarea).
"""
from __future__ import annotations

from typing import Optional

from app.config import MEDIU
from app.models import (CalitateOra, Client, Parc, ProductieOrara, Rulare, RulareSumar,
                         IncercareEmail, ModelMLInfo)
from app.storage.firestore_packing import (despacheteaza_calitate, despacheteaza_productie,
                                            filtreaza_interval, grupeaza_calitate_pe_zi,
                                            grupeaza_productie_pe_zi, zile_din_interval)
from app.storage.repository import Repository
from app.storage.serialize import (din_dict, parc_to_dict, parc_from_dict,
                                     rulare_to_dict, rulare_from_dict)
from dataclasses import asdict


# Firestore refuză orice document peste 1 MiB — o limită TARE, nu o
# recomandare. O rulare de 16 zile la pas de 15 minute (1536 de intrări)
# ocupă deja ~0,81 MiB (măsurat), iar avertismentele de calitate scrise pe
# fiecare oră pot împinge documentul peste limită. Fără verificarea de mai
# jos, operatorul ar primi direct eroarea criptică a Firestore, la sfârșitul
# unei rulări care a durat minute întregi, fără să știe ce s-o schimbe.
LIMITA_DOCUMENT_FIRESTORE = 1_048_576
PRAG_AVERTISMENT_DOCUMENT = int(LIMITA_DOCUMENT_FIRESTORE * 0.8)


def _verifica_marime_document(date_: dict, descriere: str, numar_ore: int) -> None:
    import json
    import warnings

    marime = len(json.dumps(date_, ensure_ascii=False, default=str).encode("utf-8"))
    if marime >= LIMITA_DOCUMENT_FIRESTORE:
        raise ValueError(
            f"{descriere} nu încape într-un document Firestore: {marime / 1024:.0f} KiB, "
            f"peste limita tare de 1 MiB ({numar_ore} intrări de prognoză). "
            f"Reduceți orizontul rulării sau folosiți pas de 60 de minute în loc de 15 "
            f"pentru acest parc (Modul tehnic → configurarea parcului)."
        )
    if marime >= PRAG_AVERTISMENT_DOCUMENT:
        warnings.warn(
            f"{descriere} ocupă {marime / 1024:.0f} KiB, aproape de limita Firestore de 1 MiB "
            f"({numar_ore} intrări). La un orizont mai mare sau mai multe avertismente de "
            f"calitate, salvarea va începe să eșueze.",
            RuntimeWarning, stacklevel=2,
        )


class FirestoreRepository(Repository):
    def __init__(self):
        try:
            from google.cloud import firestore  # import întârziat — pachet opțional
        except ImportError as exc:
            raise RuntimeError(
                "STORAGE_BACKEND=firestore cere pachetul 'google-cloud-firestore' "
                "(pip install google-cloud-firestore) și FIRESTORE_PROJECT_ID setat în .env."
            ) from exc
        self._db = firestore.Client(project=MEDIU.firestore_project_id or None)

    # --- Clienți ---
    def listeaza_clienti(self) -> list[Client]:
        docs = self._db.collection("clienti").stream()
        return [Client(id=d.id, nume=d.to_dict().get("nume", "")) for d in docs]

    def salveaza_client(self, client: Client) -> None:
        self._db.collection("clienti").document(client.id).set({"nume": client.nume})

    # --- Parcuri ---
    def listeaza_parcuri(self) -> list[Parc]:
        docs = self._db.collection("parcuri").stream()
        return [parc_from_dict(d.to_dict()) for d in docs]

    def get_parc(self, parc_id: str) -> Optional[Parc]:
        doc = self._db.collection("parcuri").document(parc_id).get()
        return parc_from_dict(doc.to_dict()) if doc.exists else None

    def salveaza_parc(self, parc: Parc) -> None:
        self._db.collection("parcuri").document(parc.id).set(parc_to_dict(parc))

    def _sterge_documente(self, query) -> None:
        """Firestore nu are ștergere în cascadă — subcolecțiile și documentele
        legate trebuie parcurse explicit, în loturi."""
        batch = self._db.batch()
        n = 0
        for doc in query.stream():
            batch.delete(doc.reference)
            n += 1
            if n % 400 == 0:
                batch.commit()
                batch = self._db.batch()
        if n % 400 != 0 or n == 0:
            batch.commit()

    def sterge_parc(self, parc_id: str) -> None:
        """Ștergere în cascadă (vezi Repository.sterge_parc)."""
        parc_ref = self._db.collection("parcuri").document(parc_id)
        self._sterge_documente(parc_ref.collection("productie_zilnica"))
        self._sterge_documente(parc_ref.collection("calitate_zilnica"))
        for colectie in ("rulari", "incercari_email", "modele_ml"):
            self._sterge_documente(self._db.collection(colectie).where("parc_id", "==", parc_id))
        self._sterge_documente(
            self._db.collection("meteo_cache").where("parc_id", "==", parc_id))
        parc_ref.delete()

    # --- Producție reală ---
    # Un document PER ZI CALENDARISTICĂ UTC, nu per oră — vezi firestore_packing.py
    # pentru motivul (cota gratuită Firestore) și pentru funcțiile pure de
    # împachetare/despachetare, testate exhaustiv fără Firestore.
    def salveaza_productie(self, randuri: list[ProductieOrara]) -> int:
        if not randuri:
            return 0
        parc_id = randuri[0].parc_id
        parc_ref = self._db.collection("parcuri").document(parc_id)
        grupuri = grupeaza_productie_pe_zi(randuri)
        batch = self._db.batch()
        n = 0
        for zi, ore in grupuri.items():
            ref = parc_ref.collection("productie_zilnica").document(zi)
            # merge=True combină harta "ore" cu cea existentă cheie-cu-cheie
            # (nu suprascrie orele deja salvate ale aceleiași zile care nu
            # sunt în acest lot — un import parțial/repetat al aceleiași zile
            # nu pierde ore scrise anterior).
            batch.set(ref, {"ore": ore}, merge=True)
            n += 1
            if n % 400 == 0:   # limita Firestore per batch e 500
                batch.commit()
                batch = self._db.batch()
        batch.commit()
        return len(randuri)

    def listeaza_productie(self, parc_id: str, inceput_utc: str, sfarsit_utc: str) -> list[ProductieOrara]:
        col = self._db.collection("parcuri").document(parc_id).collection("productie_zilnica")
        randuri: list[ProductieOrara] = []
        for zi in zile_din_interval(inceput_utc, sfarsit_utc):
            doc = col.document(zi).get()
            if doc.exists:
                randuri.extend(despacheteaza_productie(parc_id, doc.to_dict()))
        return filtreaza_interval(randuri, inceput_utc, sfarsit_utc)

    def numar_ore_productie(self, parc_id: str) -> int:
        col = self._db.collection("parcuri").document(parc_id).collection("productie_zilnica")
        total = 0
        for doc in col.stream():
            total += len((doc.to_dict() or {}).get("ore") or {})
        return total

    def sterge_productie_interval(self, parc_id: str, inceput_utc: str, sfarsit_utc: str) -> int:
        """Documentele sunt PACHETE PE ZI (`firestore_packing.py`) — un
        interval poate cere ștergerea unei ZI ÎNTREGI sau doar a unor ore
        dintr-o zi de la margine; se citește, se scot cheile din interval și
        se rescrie documentul (sau se șterge, dacă rămâne fără nicio oră)."""
        parc_ref = self._db.collection("parcuri").document(parc_id)
        n = 0
        for colectie_nume in ("productie_zilnica", "calitate_zilnica"):
            col = parc_ref.collection(colectie_nume)
            for zi in zile_din_interval(inceput_utc, sfarsit_utc):
                ref = col.document(zi)
                doc = ref.get()
                if not doc.exists:
                    continue
                ore = (doc.to_dict() or {}).get("ore") or {}
                ramase = {ts: v for ts, v in ore.items() if not (inceput_utc <= ts <= sfarsit_utc)}
                if colectie_nume == "productie_zilnica":
                    n += len(ore) - len(ramase)
                if ramase:
                    ref.set({"ore": ramase})
                else:
                    ref.delete()
        return n

    # --- Calitatea producției măsurate ---
    def salveaza_calitate_productie(self, randuri: list[CalitateOra]) -> int:
        if not randuri:
            return 0
        parc_id = randuri[0].parc_id
        parc_ref = self._db.collection("parcuri").document(parc_id)
        grupuri = grupeaza_calitate_pe_zi(randuri)
        batch = self._db.batch()
        n = 0
        for zi, ore in grupuri.items():
            ref = parc_ref.collection("calitate_zilnica").document(zi)
            batch.set(ref, {"ore": ore}, merge=True)
            n += 1
            if n % 400 == 0:
                batch.commit()
                batch = self._db.batch()
        batch.commit()
        return len(randuri)

    def listeaza_calitate_productie(self, parc_id: str, inceput_utc: Optional[str] = None,
                                     sfarsit_utc: Optional[str] = None) -> list[CalitateOra]:
        col = self._db.collection("parcuri").document(parc_id).collection("calitate_zilnica")
        if inceput_utc and sfarsit_utc:
            randuri: list[CalitateOra] = []
            for zi in zile_din_interval(inceput_utc, sfarsit_utc):
                doc = col.document(zi).get()
                if doc.exists:
                    randuri.extend(despacheteaza_calitate(parc_id, doc.to_dict()))
            return filtreaza_interval(randuri, inceput_utc, sfarsit_utc)
        randuri = []
        for doc in col.stream():
            randuri.extend(despacheteaza_calitate(parc_id, doc.to_dict()))
        return sorted(randuri, key=lambda r: r.timestamp_utc)

    def sterge_calitate_productie(self, parc_id: str) -> None:
        self._sterge_documente(
            self._db.collection("parcuri").document(parc_id).collection("calitate_zilnica"))

    # --- Rulări ---
    def salveaza_rulare(self, rulare: Rulare) -> None:
        date_ = rulare_to_dict(rulare)
        _verifica_marime_document(date_, f"rularea {rulare.id}", len(rulare.ore))
        self._db.collection("rulari").document(rulare.id).set(date_)

    def get_rulare(self, rulare_id: str) -> Optional[Rulare]:
        doc = self._db.collection("rulari").document(rulare_id).get()
        return rulare_from_dict(doc.to_dict()) if doc.exists else None

    def ultima_rulare_reusita(self, parc_id: str) -> Optional[Rulare]:
        docs = list(self._db.collection("rulari").where("parc_id", "==", parc_id)
                    .where("stare", "in", ["reusita", "partiala"])
                    .order_by("declansata_la", direction="DESCENDING").limit(1).stream())
        return rulare_from_dict(docs[0].to_dict()) if docs else None

    def itereaza_rulari(self, parc_id: str, limita: int = 30, *,
                         declansata_dupa: Optional[str] = None,
                         declansata_inainte: Optional[str] = None):
        """Pe Firestore, filtrarea pe interval NU e doar o optimizare de viteză:
        se plătește per DOCUMENT CITIT, iar o pagină care caută prognoza unei
        singure zile citea altfel zeci de rulări întregi degeaba. `stream()`
        e deja leneș (aduce documentele în loturi, pe măsură ce se consumă),
        deci un `break` timpuriu chiar oprește citirile."""
        q = self._db.collection("rulari").where("parc_id", "==", parc_id)
        if declansata_dupa:
            q = q.where("declansata_la", ">=", declansata_dupa)
        if declansata_inainte:
            q = q.where("declansata_la", "<=", declansata_inainte)
        docs = q.order_by("declansata_la", direction="DESCENDING").limit(limita).stream()
        for d in docs:
            yield rulare_from_dict(d.to_dict())

    def sterge_rulari_mai_vechi_de(self, inainte_de_utc: str) -> int:
        col = self._db.collection("rulari").where("declansata_la", "<", inainte_de_utc)
        n = sum(1 for _ in col.select([]).stream())
        self._sterge_documente(col)
        return n

    def sterge_meteo_cache_mai_vechi_de(self, inainte_de_data: str) -> int:
        col = self._db.collection("meteo_cache").where("data", "<", inainte_de_data)
        n = sum(1 for _ in col.select([]).stream())
        self._sterge_documente(col)
        return n

    def listeaza_rulari_sumar(self, parc_id: Optional[str] = None,
                               limita: int = 30) -> list[RulareSumar]:
        """Firestore facturează documentul citit întreg, indiferent câte câmpuri
        folosim — `select()` reduce doar traficul și deserializarea, nu costul.
        Câștigul real rămâne că nu se mai construiesc obiectele `OraPrognoza`."""
        q = self._db.collection("rulari")
        if parc_id:
            q = q.where("parc_id", "==", parc_id)
        campuri = ["parc_id", "declansata_la", "tip", "stare", "mesaj", "durata_secunde",
                    "orizont_zile", "rezolutie_minute", "surse_meteo_raspuns", "ponderi_folosite"]
        docs = q.select(campuri).order_by("declansata_la", direction="DESCENDING") \
            .limit(limita).stream()
        rezultat = []
        for d in docs:
            date_ = d.to_dict() or {}
            rezultat.append(RulareSumar(
                id=d.id, parc_id=date_.get("parc_id", ""),
                declansata_la=date_.get("declansata_la", ""), tip=date_.get("tip", ""),
                stare=date_.get("stare", ""), mesaj=date_.get("mesaj"),
                durata_secunde=date_.get("durata_secunde"),
                orizont_zile=date_.get("orizont_zile") or 3,
                rezolutie_minute=date_.get("rezolutie_minute") or 60,
                # `select()` nu aduce lista de ore, deci numărul lor nu e
                # disponibil aici fără o citire completă — rămâne 0, iar
                # interfața afișează „—" în loc de o cifră inventată.
                numar_ore=0,
                surse_meteo_raspuns=date_.get("surse_meteo_raspuns") or {},
                ponderi_folosite=date_.get("ponderi_folosite") or {},
            ))
        return rezultat

    # --- Email ---
    def salveaza_incercare_email(self, incercare: IncercareEmail) -> None:
        self._db.collection("incercari_email").document(incercare.id).set(asdict(incercare))

    def listeaza_incercari_email(self, parc_id: Optional[str] = None, limita: int = 100) -> list[IncercareEmail]:
        q = self._db.collection("incercari_email")
        if parc_id:
            q = q.where("parc_id", "==", parc_id)
        docs = q.order_by("moment", direction="DESCENDING").limit(limita).stream()
        return [din_dict(IncercareEmail, d.to_dict()) for d in docs]

    # --- Modele ML ---
    def salveaza_model_ml(self, model: ModelMLInfo) -> None:
        if model.activ:
            actuale = self._db.collection("modele_ml").where("parc_id", "==", model.parc_id) \
                .where("activ", "==", True).stream()
            for d in actuale:
                d.reference.update({"activ": False})
        self._db.collection("modele_ml").document(model.id).set(asdict(model))

    def model_ml_activ(self, parc_id: str) -> Optional[ModelMLInfo]:
        docs = list(self._db.collection("modele_ml").where("parc_id", "==", parc_id)
                    .where("activ", "==", True).limit(1).stream())
        return din_dict(ModelMLInfo, docs[0].to_dict()) if docs else None

    def listeaza_modele_ml(self, parc_id: str) -> list[ModelMLInfo]:
        docs = self._db.collection("modele_ml").where("parc_id", "==", parc_id) \
            .order_by("antrenat_la", direction="DESCENDING").stream()
        return [din_dict(ModelMLInfo, d.to_dict()) for d in docs]

    # --- Setări ---
    def get_setare(self, cheie: str, implicit=None):
        doc = self._db.collection("setari").document(cheie).get()
        return doc.to_dict().get("valoare") if doc.exists else implicit

    def set_setare(self, cheie: str, valoare) -> None:
        self._db.collection("setari").document(cheie).set({"valoare": valoare})

    def toate_setarile(self) -> dict:
        return {d.id: d.to_dict().get("valoare") for d in self._db.collection("setari").stream()}

    # --- Cache meteo ---
    def get_meteo_cache(self, parc_id: str, sursa: str, data: str) -> Optional[dict]:
        doc_id = f"{parc_id}__{sursa}__{data}"
        doc = self._db.collection("meteo_cache").document(doc_id).get()
        return doc.to_dict().get("payload") if doc.exists else None

    def set_meteo_cache(self, parc_id: str, sursa: str, data: str, payload: dict) -> None:
        from datetime import datetime, timezone
        doc_id = f"{parc_id}__{sursa}__{data}"
        self._db.collection("meteo_cache").document(doc_id).set({
            "payload": payload, "fetched_at": datetime.now(timezone.utc).isoformat(),
        })
