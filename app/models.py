"""
Modele de date pentru platforma de prognoză.

De ce dataclass-uri simple și nu un ORM: cerințele R-307 și R-1403 spun explicit
că logica aplicației trebuie să rămână inspectabilă și modificabilă direct de
operator/programator, fără framework-uri sau abstractizări care ascund ce se
întâmplă. Un ORM ar ascunde exact structura pe care operatorul trebuie să o
poată citi. De aceea conversia dataclass <-> rând SQL / document Firestore se
face explicit, în cod simplu, în app/storage/.

Toate câmpurile "opțional" din Anexele B/C au valori implicite rezonabile aici;
câmpurile "important" nu au valoare implicită (trebuie completate de operator).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Enumerări — folosim string-enum ca să fie lizibile direct în baza de date
# și în formulare, fără tabele de traducere separate.
# ---------------------------------------------------------------------------

class Tehnologie(str, Enum):
    FOTOVOLTAIC = "fotovoltaic"
    EOLIAN = "eolian"


class TipAsezarePV(str, Enum):
    FIX = "fixed"
    EST_VEST = "east_west"
    TRACKER = "tracker"


class TehnologiePanou(str, Enum):
    MONOCRYSTALLINE = "monocrystalline"
    POLYCRYSTALLINE = "polycrystalline"
    PERC_TOPCON_HJT = "PERC_TOPCon_HJT"
    THIN_FILM = "thin_film"
    BIFACIAL = "bifacial"


# Coeficienți de temperatură impliciți (%/°C, valoare pozitivă = pierdere pe grad
# peste 25°C), folosiți doar dacă operatorul nu completează unul propriu (R-413).
# Valori tipice din fișele tehnice ale producătorilor — ordin de mărime industrial.
COEFICIENT_TEMPERATURA_IMPLICIT = {
    TehnologiePanou.MONOCRYSTALLINE: 0.38,
    TehnologiePanou.POLYCRYSTALLINE: 0.40,
    TehnologiePanou.PERC_TOPCON_HJT: 0.34,
    TehnologiePanou.THIN_FILM: 0.25,
    TehnologiePanou.BIFACIAL: 0.35,
}


class ModelTermicMontaj(str, Enum):
    """Valorile corespund exact cheilor din
    pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS['sapm'] — folosite direct
    în core/pv_model.py, fără traducere suplimentară."""
    DESCHIS_STICLA_STICLA = "open_rack_glass_glass"
    STICLA_POLIMER = "open_rack_glass_polymer"
    APROPIAT_ACOPERIS = "insulated_back_glass_polymer"


class DirectieUmbrire(str, Enum):
    EST = "est"
    VEST = "vest"
    AMBELE = "ambele"


class StareParc(str, Enum):
    """Vezi R-303 — starea afișată pe cardul de portofoliu."""
    MOD_UMBRA = "mod_umbra"
    LIVRAT = "livrat"
    ABATERE = "abatere"
    DOAR_FIZIC = "doar_fizic"
    FARA_DATE = "fara_date"


# ---------------------------------------------------------------------------
# Configurație tehnologie
# ---------------------------------------------------------------------------

@dataclass
class Subansamblu:
    """Un subansamblu / orientare al unui parc PV — R-412: un parc poate avea
    mai multe subansambluri, fiecare cu propria înclinare/azimut, dar toate
    împart ACELAȘI invertor -> clipping-ul se aplică pe SUMA puterilor DC."""
    nume: str
    putere_dc_kwp: float
    inclinare_grade: Optional[float] = None   # None = necunoscut, poate fi calibrat (secț. 7)
    azimut_grade: Optional[float] = None      # convenție pvlib: 180 = sud
    tip_asezare: TipAsezarePV = TipAsezarePV.FIX


class ModelTranspozitie(str, Enum):
    """Modelul de transpoziție a radiației pe planul înclinat al modulului.

    `perez` e implicitul industriei (PVsyst/SAM) — varianta `perez-driesse`
    e formularea continuă a lui Driesse (2023), fără discontinuitățile de la
    granițele intervalelor ε ale formulării originale, recomandată când
    rezultatul e prelucrat mai departe statistic (cazul nostru: corector ML).
    Vezi docs/cercetare/03-literatura-stiintifica.md, secțiunea 2.
    """
    PEREZ_DRIESSE = "perez-driesse"
    PEREZ = "perez"
    HAY_DAVIES = "haydavies"
    IZOTROP = "isotropic"


class ModelTemperaturaCelula(str, Enum):
    """SAPM: arhetipuri de montaj (parametri ficși). Faiman: doi parametri
    (U0, U1), forma standard IEC 61853-2, care POATE fi calibrată din
    producția reală a parcului — de-asta e oferită ca alternativă."""
    SAPM = "sapm"
    FAIMAN = "faiman"


class ModSeparareRadiatie(str, Enum):
    """De unde vin DNI și DHI: direct din modelul meteo (`nwp`) sau
    re-derivate din GHI cu un model de separare. DNI-ul NWP e în general
    câmpul radiativ cel mai slab, deci merită testat per parc."""
    NWP = "nwp"
    ERBS = "erbs"
    DIRINT = "dirint"
    DIRINDEX = "dirindex"
    BOLAND = "boland"


@dataclass
class ConfigPV:
    putere_dc_kwp: float                                  # important — suma tuturor panourilor
    putere_ac_kw: float                                   # important — limita de vârf a parcului
    subansambluri: list[Subansamblu] = field(default_factory=list)
    randament_invertor: float = 0.97                      # opțional, implicit 0,96-0,98
    limita_injectie_kw: Optional[float] = None             # opțional — doar dacă avizul o impune
    tehnologie_panou: TehnologiePanou = TehnologiePanou.MONOCRYSTALLINE
    coeficient_temperatura_pct_per_c: Optional[float] = None  # None => se ia implicitul tehnologiei
    model_termic_montaj: ModelTermicMontaj = ModelTermicMontaj.DESCHIS_STICLA_STICLA
    degradare_anuala_pct: float = 0.6                      # opțional, 0.4-0.8%/an tipic
    data_punerii_in_functiune: Optional[str] = None        # ISO date — folosită pt. degradare
    # Pierderi de sistem — se compun MULTIPLICATIV, niciodată aditiv (R-413, glosar).
    pierdere_murdarire_pct: float = 2.0
    pierdere_neuniformitate_pct: float = 1.0
    pierdere_cablaj_conexiuni_pct: float = 1.5
    pierdere_lid_pct: float = 1.0
    pierdere_placuta_pct: float = 1.0
    pierdere_indisponibilitate_pct: float = 1.0
    # Pierdere REZIDUALĂ, nemodelată: partea din diferența față de producția
    # măsurată pe care calibrarea o găsește, dar pe care nu o poate atribui unei
    # cauze fizice anume (murdărire? indisponibilitate? degradare peste cea
    # declarată? subdimensionare a plăcuței?). Ține separat tocmai ca să rămână
    # onestă: un câmp numit „rezidual" spune că e o corecție empirică, în timp
    # ce umflarea unui câmp fizic ar pretinde o explicație pe care n-o avem.
    # Implicit 0 — se completează DOAR dacă operatorul alege explicit să aplice
    # factorul de scalare rezultat din calibrare.
    pierdere_nemodelata_pct: float = 0.0
    # Umbrire de orizont — DOUĂ câmpuri simple (R-414), NU profil complet de orizont.
    umbrire_orizont_grade: float = 0.0
    umbrire_orizont_directie: DirectieUmbrire = DirectieUmbrire.AMBELE

    # ----------------------------------------------------------------- v2
    # Toate câmpurile de mai jos au implicituri care reproduc comportamentul
    # anterior sau îl îmbunătățesc fără configurare — parcurile existente
    # continuă să funcționeze neschimbat. Vezi docs/MODELE.md.
    model_transpozitie: ModelTranspozitie = ModelTranspozitie.PEREZ_DRIESSE
    albedo: float = 0.20                                   # reflexia solului (iarbă/pământ ~0,2)
    albedo_zapada: float = 0.65                            # înlocuiește albedo cât timp e strat de zăpadă
    model_temperatura: ModelTemperaturaCelula = ModelTemperaturaCelula.SAPM
    faiman_u0: float = 25.0                                # W/m2/K — coeficient convecție liberă
    faiman_u1: float = 6.84                                # W/m2/K/(m/s) — coeficient convecție forțată
    # Curbă de randament al invertorului: [[fracție_încărcare, randament], ...].
    # Dacă lipsește, se folosește `randament_invertor` constant (comportamentul vechi).
    curba_randament_invertor: Optional[list[list[float]]] = None
    # Murdărire dinamică (Kimber): acumulare pe zi uscată, resetată de ploaie.
    # Dacă e activă, ÎNLOCUIEȘTE `pierdere_murdarire_pct` (nu se adună).
    soiling_dinamic: bool = False
    soiling_rata_zi_pct: float = 0.1                        # %/zi de acumulare în perioade uscate
    soiling_prag_ploaie_mm: float = 5.0                     # ploaia peste acest prag spală panourile
    pierderi_zapada: bool = True                            # model Marion/NREL de acoperire cu zăpadă
    mod_separare_radiatie: ModSeparareRadiatie = ModSeparareRadiatie.NWP
    # Topologia invertoarelor: implicit TOATE subansamblurile împart același
    # invertor (R-412) -> clipping pe SUMA puterilor DC. Dacă fiecare
    # subansamblu are invertorul/MPPT-ul lui, clipping-ul se aplică separat.
    clipping_per_subansamblu: bool = False

    def coeficient_temperatura(self) -> float:
        if self.coeficient_temperatura_pct_per_c is not None:
            return self.coeficient_temperatura_pct_per_c
        return COEFICIENT_TEMPERATURA_IMPLICIT[self.tehnologie_panou]

    def factor_pierderi_sistem(self) -> float:
        """Compunere multiplicativă a pierderilor de sistem (fără umbrire/degradare,
        care se aplică separat în lanțul de calcul)."""
        pierderi = [
            self.pierdere_murdarire_pct, self.pierdere_neuniformitate_pct,
            self.pierdere_cablaj_conexiuni_pct, self.pierdere_lid_pct,
            self.pierdere_placuta_pct, self.pierdere_indisponibilitate_pct,
            self.pierdere_nemodelata_pct,
        ]
        factor = 1.0
        for p in pierderi:
            factor *= (1 - p / 100.0)
        return factor


class ProfilVertical(str, Enum):
    """Cum se extrapolează viteza vântului de la înălțimile măsurate la butuc.

    `power_law` (legea puterii, cu exponentul α dedus exact din perechea
    10 m / 100 m) trece exact prin ambele puncte cunoscute și e monoton —
    fără modurile de eșec ale rezolvării prin bisecție a lui z0.
    `log_law` păstrează comportamentul anterior (profil logaritmic).
    """
    POWER_LAW = "power_law"
    LOG_LAW = "log_law"


class ReglajTurbina(str, Enum):
    """IEC 61400-12: la turbinele cu reglaj pitch (practic toate cele moderne)
    corecția de densitate se aplică VITEZEI; la cele cu reglaj stall, PUTERII."""
    PITCH = "pitch"
    STALL = "stall"


@dataclass
class ConfigEolian:
    numar_turbine: int                                     # important
    putere_nominala_kw: float                               # important — per turbină
    inaltime_butuc_m: float                                 # important
    # Curba de putere: listă de (viteză m/s, putere kW). Dacă lipsește, se
    # aproximează din cele 3 viteze simple (R-422).
    curba_putere: Optional[list[list[float]]] = None
    viteza_pornire_ms: Optional[float] = None
    viteza_nominala_ms: Optional[float] = None
    viteza_oprire_ms: Optional[float] = None
    pierdere_siaj_pct: float = 8.0                          # opțional, implicit ~8%
    pierdere_sistem_pct: float = 2.0

    # ----------------------------------------------------------------- v2
    profil_vertical: ProfilVertical = ProfilVertical.POWER_LAW
    reglaj: ReglajTurbina = ReglajTurbina.PITCH
    # Netezirea curbei de putere cu intensitatea turbulenței: o curbă de putere
    # aplicată pe media orară a vitezei supraestimează lângă pornire și
    # subestimează lângă nominal (curba e convexă, apoi concavă).
    netezire_turbulenta: bool = True
    intensitate_turbulenta: Optional[float] = None           # None => estimată din rugozitate
    # Pierderea de siaj depinde de viteză (maximă între pornire și nominal,
    # ~0 peste nominal, unde C_T scade) — un procent constant e eronat în ambele cozi.
    wake_dependent_de_viteza: bool = True
    diametru_rotor_m: Optional[float] = None                 # opțional, pentru documentare/siaj


# ---------------------------------------------------------------------------
# Client și parc
# ---------------------------------------------------------------------------

@dataclass
class Client:
    id: str
    nume: str


@dataclass
class Parc:
    id: str
    cod: str                       # identificator unic, scurt — nu se schimbă după creare
    nume: str
    client_id: str
    tehnologie: Tehnologie
    latitudine: float
    longitudine: float
    altitudine_m: float = 0.0
    fus_orar: str = "Europe/Bucharest"     # orice fus orar IANA valid — vezi app/core/timezones.py
    mod_umbra: bool = True                  # R-406 — ÎNTOTDEAUNA True la creare
    activ: bool = True
    config_pv: Optional[ConfigPV] = None
    config_eolian: Optional[ConfigEolian] = None
    # R-620: împrumutul corectorului ML de la alt parc — explicit, manual, gol implicit.
    parc_imprumut_corector_id: Optional[str] = None
    destinatari_email: list[str] = field(default_factory=list)   # R-1002
    surse_meteo: list[str] = field(default_factory=lambda: ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"])
    ponderi_meteo: dict[str, float] = field(default_factory=dict)  # gol => pondere egală
    creat_la: str = field(default_factory=_now_iso)

    # ----------------------------------------------------------------- v2
    # Rezoluția de livrare. Piața din România decontează la 15 minute, iar
    # cuplarea zilei următoare (SDAC) a trecut la MTU de 15 min din 10/2025.
    rezolutie_minute: int = 60
    # Ponderi de ansamblu deduse automat din performanța recentă per model
    # (meta-prognoză). Dacă e activă, `ponderi_meteo` e recalculată periodic.
    ponderi_automate: bool = True
    # Produce și benzi de incertitudine (P10/P50/P90), nu doar valoarea centrală.
    produce_cuantile: bool = True
    # Putere contractată / limită de racordare, folosită ca bază de normalizare
    # când diferă de puterea instalată. None => se folosește puterea instalată.
    putere_referinta_kw: Optional[float] = None

    # ----------------------------------------------------------------- v3
    # Fusul de INTERPRETARE a importului CSV (Anexa A) poate diferi de cel
    # care guvernează prognozele (`fus_orar` — granița de zi și afișarea).
    # Motiv tipic: exportul din SCADA/contor foloseste o convenție orară
    # fixă (fără oră de vară/iarnă), diferită de fusul IANA ales pentru
    # prognoze. Gol ("") => se folosește `fus_orar`, ca înainte — parcurile
    # existente nu-și schimbă comportamentul. Vezi `app/core/timezones.py:
    # fus_orar_pentru_import`.
    fus_orar_import: str = ""



# ---------------------------------------------------------------------------
# Producție reală importată (Anexa A) — sursa unică de adevăr pt. antrenare/comparații
# ---------------------------------------------------------------------------

@dataclass
class ProductieOrara:
    parc_id: str
    timestamp_utc: str     # ISO 8601, oră FINALĂ a intervalului (R-512)
    energie_kwh: float


class SteagCalitate(str, Enum):
    """Verdictul controlului de calitate pe o oră de producție REALĂ măsurată.

    Orele care nu sunt `OK` nu intră în antrenarea corectorului ML și sunt
    raportate separat în verificare — altfel modelul învață "uneori producția
    scade fără motiv meteorologic" și împrăștie asta în toate prognozele.
    """
    OK = "ok"
    NOAPTE_NENUL = "noapte_nenul"       # producție noaptea -> decalaj de metering / semn inversat
    VALOARE_BLOCATA = "valoare_blocata"  # aceeași valoare multe ore la rând -> senzor înghețat
    INDISPONIBILITATE = "indisponibilitate"  # producție mult sub așteptarea fizică, susținut
    LIMITARE = "limitare"                # curtailment: plafon plat sub așteptarea fizică
    VALOARE_EXTREMA = "valoare_extrema"  # outlier robust (Hampel)
    PESTE_INSTALATA = "peste_instalata"  # imposibil fizic


@dataclass
class CalitateOra:
    """Un verdict de calitate pentru o oră de producție măsurată."""
    parc_id: str
    timestamp_utc: str
    steag: str                       # una din valorile SteagCalitate
    detaliu: Optional[str] = None


# ---------------------------------------------------------------------------
# Prognoză
# ---------------------------------------------------------------------------

@dataclass
class OraPrognoza:
    """O oră de prognoză, cu tot lanțul de calcul păstrat pt. trasabilitate (R-1201)."""
    timestamp_utc: str
    # intrări meteo (deja combinate din ansamblu)
    ghi_wm2: Optional[float] = None
    dni_wm2: Optional[float] = None
    dhi_wm2: Optional[float] = None
    temp_aer_c: Optional[float] = None
    viteza_vant_ms: Optional[float] = None
    dispersie_meteo: Optional[float] = None      # R-502 — proxy incertitudine
    surse_meteo_folosite: list[str] = field(default_factory=list)
    # rezultate intermediare lanț fizic (pt. trasabilitate)
    putere_dc_kw: Optional[float] = None
    putere_ac_fizic_kw: Optional[float] = None    # rezultatul modelului fizic (înainte de ML)
    fractie_incarcare: Optional[float] = None
    # corecție ML
    corectie_ml_kw: Optional[float] = None
    model_folosit: Optional[str] = None            # "propriu" / "imprumutat:<parc_id>" / None (fizică pură)
    putere_finala_kw: Optional[float] = None        # ce se raportează efectiv
    # avertismente controale de calitate (R-1503)
    avertismente: list[str] = field(default_factory=list)
    blocat: bool = False

    # ----------------------------------------------------------------- v2
    # Benzi de incertitudine: {"0.1": kW, "0.5": kW, "0.9": kW, ...}.
    # Cheile sunt nivelurile de probabilitate ca text (JSON nu are chei float).
    cuantile_kw: dict[str, float] = field(default_factory=dict)
    # Ora de referință "cer senin" — baza pentru indicele de senin (kt*) și
    # pentru prognozele de referință folosite la scorul de skill.
    putere_clearsky_kw: Optional[float] = None
    # Câte ore în avans a fost emisă această valoare (ora prognozată minus
    # momentul rulării). Verificarea corectă cere separarea pe orizont.
    ore_in_avans: Optional[float] = None


@dataclass
class Rulare:
    """O rulare (programată sau manuală) pentru un parc — R-1510."""
    id: str
    parc_id: str
    declansata_la: str
    tip: str                        # "programata" / "manuala"
    stare: str = "in_progres"       # in_progres / reusita / partiala / esuata
    durata_secunde: Optional[float] = None
    surse_meteo_raspuns: dict = field(default_factory=dict)
    mesaj: Optional[str] = None
    ore: list[OraPrognoza] = field(default_factory=list)
    orizont_zile: int = 3
    # ----------------------------------------------------------------- v2
    rezolutie_minute: int = 60
    ponderi_folosite: dict[str, float] = field(default_factory=dict)  # ponderile efective per model meteo


@dataclass
class RulareSumar:
    """Antetul unei rulări, FĂRĂ orele de prognoză.

    Majoritatea ecranelor care listează rulări (jurnalul, „Rulări recente”
    din detaliul parcului, diagnosticul surselor meteo) au nevoie doar de
    metadate — dar citirea unei `Rulare` complete deserializează și toate
    orele ei. La un portofoliu de 50 de parcuri asta însemna sute de mii de
    obiecte `OraPrognoza` construite ca să se afișeze un tabel cu cinci
    coloane (măsurat: ~96% din timpul paginii de portofoliu,
    scripts/benchmark_scalare.py). `numar_ore` păstrează totuși informația
    „câte ore are rularea”, care se vede în interfață."""
    id: str
    parc_id: str
    declansata_la: str
    tip: str
    stare: str
    mesaj: Optional[str] = None
    durata_secunde: Optional[float] = None
    orizont_zile: int = 3
    rezolutie_minute: int = 60
    numar_ore: int = 0
    surse_meteo_raspuns: dict = field(default_factory=dict)
    ponderi_folosite: dict[str, float] = field(default_factory=dict)


@dataclass
class IncercareEmail:
    """Jurnal de trimitere email — R-1008, R-1511."""
    id: str
    parc_id: str
    rulare_id: str
    catre: list[str]
    moment: str
    rezultat: str      # "reusit" / "esuat" / "sarit"
    motiv: Optional[str] = None


@dataclass
class ModelMLInfo:
    """Metadatele unui model de corecție ML antrenat pt. un parc — R-601-606."""
    id: str
    parc_id: str
    antrenat_la: str
    activ: bool
    mae_validare_kw: float
    mae_model_anterior_kw: Optional[float]
    numar_ore_antrenare: int
    cale_fisier: str
    note: Optional[str] = None

    # ----------------------------------------------------------------- v2
    # Scorul de skill față de prognoza de referință (persistență inteligentă /
    # climatologie) — criteriul REAL de promovare, nu MAE brut (R-605 reinterpretat
    # conform Yang et al. 2020). Pozitiv = mai bun decât "a nu avea prognoză".
    skill_vs_referinta: Optional[float] = None
    skill_model_anterior: Optional[float] = None
    rmse_validare_kw: Optional[float] = None
    # Modele de cuantile antrenate odată cu cel central: {"0.1": cale, ...}
    cai_cuantile: dict[str, str] = field(default_factory=dict)
    # Lățimea de calibrare conformă (kW) per nivel, dedusă pe fereastra de calibrare.
    corectie_conforma_kw: dict[str, float] = field(default_factory=dict)
    # Ținta pe care s-a antrenat: "reziduu_kw" (vechi) sau "reziduu_normalizat".
    tinta_antrenare: str = "reziduu_kw"
    numar_ore_excluse_qc: int = 0
