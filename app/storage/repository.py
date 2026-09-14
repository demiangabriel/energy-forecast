"""
Interfața de persistență — ABC pe care o implementează atât SqliteRepository
(folosit local, acum) cât și FirestoreRepository (folosit după mutarea pe
server, secțiunea 16). Toată logica de business (core/, web/) vorbește DOAR
cu această interfață, niciodată direct cu SQL sau cu SDK-ul Firestore — așa
se face mutarea ulterioară pe Firestore fără să se rescrie restul aplicației.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator, Optional

from app.models import (
    CalitateOra, Client, Parc, ProductieOrara, Rulare, RulareSumar, IncercareEmail, ModelMLInfo,
)


class Repository(ABC):
    # --- Clienți ---
    @abstractmethod
    def listeaza_clienti(self) -> list[Client]: ...

    @abstractmethod
    def salveaza_client(self, client: Client) -> None: ...

    # --- Parcuri ---
    @abstractmethod
    def listeaza_parcuri(self) -> list[Parc]: ...

    @abstractmethod
    def get_parc(self, parc_id: str) -> Optional[Parc]: ...

    @abstractmethod
    def salveaza_parc(self, parc: Parc) -> None: ...

    @abstractmethod
    def sterge_parc(self, parc_id: str) -> None:
        """Șterge parcul ȘI toate datele lui derivate (producție importată,
        rulări, încercări de email, modele ML, cache meteo, steaguri de
        calitate). Fără cascadă, baza de date ar acumula date orfane care
        apar în jurnal fără parc și cresc dimensiunea la nesfârșit."""

    # --- Producție reală (istoric importat) ---
    @abstractmethod
    def salveaza_productie(self, randuri: list[ProductieOrara]) -> int:
        """Upsert. Returnează numărul de rânduri scrise."""

    @abstractmethod
    def listeaza_productie(self, parc_id: str, inceput_utc: str, sfarsit_utc: str) -> list[ProductieOrara]: ...

    @abstractmethod
    def numar_ore_productie(self, parc_id: str) -> int: ...

    @abstractmethod
    def sterge_productie_interval(self, parc_id: str, inceput_utc: str, sfarsit_utc: str) -> int:
        """Șterge producția reală importată (ȘI verdictele de calitate
        asociate — ar rămâne orfane, referindu-se la ore care nu mai există)
        dintr-un interval UTC [inceput, sfarsit], capete incluse. Pentru
        curățarea unui import greșit (ex. fus orar greșit la o parte din
        istoric) înainte de o reimportare curată — altfel reimportarea, fiind
        un upsert după `timestamp_utc`, ar ADĂUGA orele corect convertite pe
        lângă cele vechi, greșit convertite (timestamp-uri diferite), nu le-ar
        înlocui. Returnează numărul de ore de producție șterse."""

    # --- Calitatea producției măsurate (v2) ---
    @abstractmethod
    def salveaza_calitate_productie(self, randuri: list[CalitateOra]) -> int:
        """Upsert al verdictelor de calitate. Înlocuiește verdictele anterioare
        pentru aceleași ore (re-rularea analizei e idempotentă)."""

    @abstractmethod
    def listeaza_calitate_productie(self, parc_id: str,
                                     inceput_utc: Optional[str] = None,
                                     sfarsit_utc: Optional[str] = None) -> list[CalitateOra]: ...

    @abstractmethod
    def sterge_calitate_productie(self, parc_id: str) -> None: ...

    # --- Rulări / prognoze ---
    @abstractmethod
    def salveaza_rulare(self, rulare: Rulare) -> None: ...

    @abstractmethod
    def get_rulare(self, rulare_id: str) -> Optional[Rulare]: ...

    @abstractmethod
    def ultima_rulare_reusita(self, parc_id: str) -> Optional[Rulare]: ...

    @abstractmethod
    def itereaza_rulari(self, parc_id: str, limita: int = 30, *,
                         declansata_dupa: Optional[str] = None,
                         declansata_inainte: Optional[str] = None) -> Iterator[Rulare]:
        """Rulările complete (cu ore), de la cea mai nouă la cea mai veche, ca
        ITERATOR — fiecare rulare se deserializează abia când e consumată.

        Distincția contează: tiparul obișnuit în aplicație e „parcurge rulările
        de la cea mai nouă până găsesc ora/ziua căutată, apoi break”. Cu o
        listă gata construită, TOATE rulările erau deserializate înainte ca
        bucla să înceapă — inclusiv cele pe care `break` nu le atingea
        niciodată.

        `declansata_dupa`/`declansata_inainte` (ISO UTC) restrâng căutarea în
        chiar interogarea bazei de date. Pentru un ecran care privește un
        interval de timp, rulările emise complet în afara lui nu pot conține
        ore utile, iar filtrarea la sursă e singura care reduce și costul pe
        Firestore (unde se plătește per document citit)."""

    def listeaza_rulari(self, parc_id: str, limita: int = 30, *,
                         declansata_dupa: Optional[str] = None,
                         declansata_inainte: Optional[str] = None) -> list[Rulare]:
        """Varianta „materializată” a lui `itereaza_rulari`, pentru apelanții
        care chiar au nevoie de toate rulările deodată (ex. copia de rezervă)."""
        return list(self.itereaza_rulari(parc_id, limita, declansata_dupa=declansata_dupa,
                                          declansata_inainte=declansata_inainte))

    @abstractmethod
    def sterge_rulari_mai_vechi_de(self, inainte_de_utc: str) -> int:
        """Șterge arhiva de rulări declanșate ÎNAINTE de momentul dat și
        întoarce câte au fost șterse.

        De ce e nevoie: fiecare rulare păstrează toate orele ei de prognoză,
        iar rularea zilnică lasă una pe zi, per parc. La 50 de parcuri asta
        înseamnă ~1 GB pe an — exact cota gratuită de stocare Firestore, atinsă
        în aproximativ un an de funcționare normală (măsurat cu
        scripts/benchmark_scalare.py).

        Ce se pierde: doar istoricul „ce am prognozat atunci". Producția reală
        importată, modelele ML antrenate și configurarea parcurilor NU sunt
        atinse. Verificarea acurateței și ponderile adaptive privesc oricum
        ferestre recente, deci o retenție de ordinul unui an nu le afectează."""

    @abstractmethod
    def sterge_meteo_cache_mai_vechi_de(self, inainte_de_data: str) -> int:
        """Golește cache-ul de reanaliză pentru zilele dinaintea datei date
        (AAAA-LL-ZZ). Cache-ul crește nelimitat altfel: fiecare zi vizualizată
        în „Reală vs. prognoză (meteo)" rămâne salvată definitiv. E doar un
        cache — ștergerea lui nu pierde nimic, doar face ca o re-vizualizare a
        acelorași zile să ceară din nou datele de la sursă."""

    @abstractmethod
    def listeaza_rulari_sumar(self, parc_id: Optional[str] = None,
                               limita: int = 30) -> list[RulareSumar]:
        """Doar ANTETUL rulărilor (fără ore) — vezi `RulareSumar` pentru
        motivație. `parc_id=None` întoarce rulările tuturor parcurilor,
        amestecate și ordonate descrescător după moment: jurnalul general le
        cerea altfel parc cu parc, adică zeci de interogări separate în locul
        uneia singure."""

    # --- Email ---
    @abstractmethod
    def salveaza_incercare_email(self, incercare: IncercareEmail) -> None: ...

    @abstractmethod
    def listeaza_incercari_email(self, parc_id: Optional[str] = None, limita: int = 100) -> list[IncercareEmail]: ...

    # --- Modele ML ---
    @abstractmethod
    def salveaza_model_ml(self, model: ModelMLInfo) -> None: ...

    @abstractmethod
    def model_ml_activ(self, parc_id: str) -> Optional[ModelMLInfo]: ...

    @abstractmethod
    def listeaza_modele_ml(self, parc_id: str) -> list[ModelMLInfo]: ...

    # --- Setări de operare (R-1401) ---
    @abstractmethod
    def get_setare(self, cheie: str, implicit=None): ...

    @abstractmethod
    def set_setare(self, cheie: str, valoare) -> None: ...

    @abstractmethod
    def toate_setarile(self) -> dict: ...

    # --- Cache meteo (evită refetch inutil, R-1605) ---
    @abstractmethod
    def get_meteo_cache(self, parc_id: str, sursa: str, data: str) -> Optional[dict]: ...

    @abstractmethod
    def set_meteo_cache(self, parc_id: str, sursa: str, data: str, payload: dict) -> None: ...
