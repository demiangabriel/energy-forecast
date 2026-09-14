# Modelele matematice ale platformei

Toate modelele folosite, cu formula, motivul alegerii, sursa din literatură și locul din cod unde
sunt implementate. Documentul e scris ca să poată fi verificat de cineva care nu a scris codul.

Convenții valabile peste tot:
- **toate calculele interne sunt în UTC** (R-511); conversia la ora locală se face doar la afișare
  și la trimiterea raportului;
- **eticheta unui interval e ÎNCEPUTUL lui** (R-512): ora 12:00 acoperă 12:00-13:00 — convenția
  piețelor de energie (PZU/OPCOM, ENTSO-E) și a contoarelor, aplicată identic la import, în
  prognoză, în rapoarte și la export;
  - datele Open-Meteo se REETICHETEAZĂ la citire: radiația e livrată ca *„average of the
    preceding hour"*, deci valoarea de la 12:00 aparține intervalului care începe la 11:00. Temperatura, vântul și
    presiunea sunt însă valori **instantanee** la eticheta respectivă, nu medii ale orei
    precedente; le tratăm ca medii, aproximare acceptată fiindcă variază lent și intră în model
    prin corecții secundare (temperatura celulei, densitatea aerului), nu prin geometria solară;
  - la **import** nu se aplică nicio deplasare: fișierul și baza de date folosesc aceeași
    convenție. Dacă exportul dvs. etichetează prin finalul intervalului, trebuie mutat cu un
    pas înapoi înainte de încărcare;
  - la **export**, formatul se alege la fiecare descărcare, din `app/core/momente.py`; implicit
    e ISO 8601 cu decalaj local, adică exact ce acceptă importul — fișierele produse de platformă
    se pot reimporta fără conversie;
- **poziția solară se evaluează la mijlocul intervalului** (12:30 pentru eticheta 12:00) — altfel
  apare o eroare sistematică dimineața și seara, de semn opus;
- pierderile procentuale se compun **multiplicativ**, niciodată aditiv.

---

## 1. Lanțul fotovoltaic

### 1.1 Poziția solară
`pvlib.solarposition.get_solarposition` (algoritm NREL SPA), evaluat la mijlocul intervalului.
→ `app/core/solar_position.py`

### 1.2 Componentele radiației și închiderea lor fizică

Sursa meteo livrează GHI, DNI și DHI ca trei câmpuri separate, produse prin post-procesări diferite;
ele **nu** respectă exact relația fizică de închidere:

$$\text{GHI} = \text{DHI} + \text{DNI}\cdot\cos\theta_z$$

Platforma poate impune închiderea, recalculând componenta directă din GHI și DHI (ambele mai fiabile
decât DNI în modelele numerice), cu limitare la constanta solară și la zero, și cu renunțare sub 3°
elevație, unde împărțirea devine instabilă.

Opțional, DNI/DHI pot fi **re-derivate din GHI** cu un model de separare: Erbs, DIRINT, DIRINDEX sau
Boland/BRL (toate în pvlib). Alegerea e per parc, pentru că literatura arată clar că niciun model nu
domină universal.

> **De ce e opțional, nu impus:** Mayer & Gróf (2021) au testat toate combinațiile de 9 modele de
> separare × 10 de transpoziție × 5 termice × 4 PV × 3 de invertor pe 16 centrale și au găsit o
> dispersie totală de doar ~13% în MAE pe tot spațiul de alegeri. În schimb, post-procesarea
> statistică la nivel de PUTERE (stratul ML) aduce ~48% (Horat et al. 2025). Ordinea priorităților
> e clară: întâi stratul statistic, apoi rafinamentele de separare.

→ `app/core/weather_client.py::asigura_inchidere_radiatie`, `app/core/pv_model.py::_aplica_separare`

### 1.3 Transpoziția pe planul modulului

Implicit **Perez–Driesse** (formularea continuă a lui Driesse, 2023, a modelului Perez 1990), cu
Perez clasic, Hay–Davies și izotrop ca alternative selectabile.

Perez e implicitul industriei (PVsyst, SAM) și include termenul de strălucire a orizontului, pe care
Hay–Davies nu îl are. Varianta Driesse elimină discontinuitățile de la granițele intervalelor de
claritate ale formulării originale — relevant aici, pentru că rezultatul e prelucrat statistic mai
departe, iar o discontinuitate în intrare devine o structură falsă pe care modelul ML ar încerca să
o învețe.

Radiația reflectată de sol folosește **albedo variabil**: valoarea configurată (implicit 0,20) și
comutare automată pe albedo de zăpadă (implicit 0,65) cât timp sursa meteo raportează strat de zăpadă.

Referințe: Perez et al. (1990), *Solar Energy* 44(5), 271-289; Driesse et al. (2023), *Solar Energy*;
Gracia & Huld (2013), JRC EUR 26075 EN.
→ `app/core/pv_model.py::_putere_dc_subansamblu`, `::_albedo_serie`

### 1.4 Unghiul de incidență
Pierderea prin reflexie la incidență razantă, modelul fizic standard (`pvlib.iam.physical`),
aplicat componentei directe.

### 1.5 Temperatura celulei

Două modele selectabile:

**SAPM** (implicit) — arhetipuri de montaj cu parametri ficși:
$$T_{cel} = T_{aer} + \frac{POA}{1000}\cdot e^{a + b\cdot v} \cdot 1000 + \frac{POA}{1000}\cdot\Delta T$$

**Faiman** — forma standardizată IEC 61853-2, cu doi parametri:
$$T_{cel} = T_{aer} + \frac{POA}{U_0 + U_1\cdot v}$$

Faiman e oferit nu pentru că ar fi intrinsec mai bun (alegerea modelului termic contribuie cu sub 2%
din dispersia totală a lanțului), ci pentru că cei doi parametri **pot fi calibrați din producția
reală a parcului**.

Referință: Faiman (2008), *Prog. Photovolt.* 16(4), 307-315.
→ `app/core/pv_model.py::_temperatura_celula`

### 1.6 Puterea DC
`pvlib.pvsystem.pvwatts_dc`:
$$P_{dc} = P_{dc0}\cdot\frac{POA}{1000}\cdot\left[1 + \gamma\cdot(T_{cel} - 25)\right]$$
cu γ = −(coeficient de temperatură)/100, implicit după tehnologia panoului.

### 1.7 Invertorul: randament și limitare

Randamentul poate fi **constant** (implicit) sau dat printr-o **curbă în funcție de încărcare**.
Curba contează la încărcări mici, unde pierderile de mers în gol sunt ~fixe și randamentul real
scade — adică exact în orele de dimineață și seară, unde un randament constant supraestimează.

Limitarea (clipping) se aplică, implicit, pe **suma** puterilor DC ale tuturor subansamblurilor,
pentru că ele împart același invertor (R-412). A limita fiecare subansamblu la puterea AC totală și
apoi a însuma supraestimează producția cu 4-6%.

Dacă parcul are **invertoare separate** per subansamblu, se declară explicit
(`clipping_per_subansamblu`): fiecare subansamblu primește partea lui proporțională din puterea AC și
se limitează separat — ceea ce dă un rezultat **mai mic** decât varianta cu invertor comun, pentru că
fiecare invertor e mai mic. Cele trei situații sunt distincte și testate separat.

### 1.8 Pierderi de sistem

Compunere multiplicativă: murdărire, neuniformitate, cablaj/conexiuni, LID, toleranța plăcuței,
indisponibilitate.

**Murdărirea** poate fi fixă (procent) sau **dinamică** (model Kimber): acumulare pe zilele uscate,
spălare la ploile peste prag. Când e dinamică, ea **înlocuiește** procentul fix, nu se adună la el.
→ `pvlib.soiling.kimber`

**Zăpada** (model NREL/Marion): modulul se acoperă la ninsoare și zăpada alunecă atunci când radiația
pe plan și înclinarea o permit; pierderea de putere e proporțională cu numărul de șiruri acoperite.
Contribuția anuală e mică, dar evită erori de 50-100% în zilele de iarnă — erori pe care stratul ML
nu le poate anticipa fără variabila de zăpadă.
Referință: Marion et al. (2013), *Solar Energy* 97, 112-121. → `pvlib.snow.coverage_nrel` + `dc_loss_nrel`

### 1.9 Degradarea și limita de injecție
Degradare liniară din data punerii în funcțiune; limitare finală la puterea de injecție avizată,
dacă e declarată. Ordinea e cea cerută explicit: clipping → pierderi → degradare → limită de injecție.

### 1.10 Umbrirea de orizont
Model simplu, intenționat (R-414): sub un unghi de elevație declarat, pe direcția declarată (est /
vest / ambele), componenta directă e blocată geometric. NU se modelează un profil complet de orizont.

---

## 2. Lanțul eolian

### 2.1 Extrapolarea la înălțimea butucului

Implicit **legea puterii cu doi parametri**, cu exponentul dedus exact din perechea 10 m / 100 m:

$$\alpha = \frac{\ln(u_{100}/u_{10})}{\ln(100/10)}, \qquad u(z) = u_{100}\cdot\left(\frac{z}{100}\right)^{\alpha}$$

cu α limitat la [0,05; 0,60]. Trece exact prin ambele puncte cunoscute, e monoton și nu are modurile
de eșec ale rezolvării prin bisecție a rugozității.

Profilul **logaritmic** rămâne disponibil:
$$u(z) = u_{10}\cdot\frac{\ln(z/z_0)}{\ln(10/z_0)}$$
cu z₀ dedus prin bisecție din raportul u₁₀/u₁₀₀, sau 0,10 m (teren agricol deschis) când lipsește
viteza la 100 m.

> Forma teoretic corectă ar include corecția de stabilitate Monin–Obukhov, dar aceasta necesită
> fluxul de căldură, indisponibil din sursa meteo, iar teoria însăși se degradează în stratificare
> stabilă peste ~100 m. Soluția practică: exponentul α e transmis ca **intrare** modelului ML, care
> învață efectul rezidual al stabilității (α mare ↔ atmosferă stabilă, tipic noaptea).

### 2.2 Densitatea aerului (IEC 61400-12-1, cu umiditate)

$$\rho = \frac{1}{T}\left[\frac{p}{R_0} - \varphi\, p_w\left(\frac{1}{R_0} - \frac{1}{R_w}\right)\right],
\qquad p_w = 2{,}05\cdot10^{-5}\,e^{0{,}0631846\,T}$$

cu R₀ = 287,05 și R_w = 461,5 J·kg⁻¹·K⁻¹. Folosirea densității standard în locul celei locale poate
deplasa energia anuală cu ~5%.
Referință: Floors & Nielsen (2019), *Energies* 12(11), 2038.

### 2.3 Corecția de densitate: pe viteză sau pe putere

IEC face distincția explicit:
- **reglaj pitch** (turbinele moderne): se corectează **viteza**, $u_{ech} = u\cdot(\rho/\rho_0)^{1/3}$ —
  ceea ce păstrează corect platoul de la puterea nominală;
- **reglaj stall**: se corectează **puterea**, $P = (\rho/\rho_0)\cdot P_0(u)$.

Tipul de reglaj e configurabil per parc.

### 2.4 Netezirea curbei de putere cu turbulența

O curbă de putere aplicată pe **media** vitezei din interval greșește sistematic: lângă viteza de
pornire curba e convexă (media puterilor > puterea mediei → subestimare), lângă cea nominală e concavă
(→ supraestimare). Corecția e convoluția cu distribuția vitezelor din interval:

$$P_{ef}(u) = \int P(v)\,\mathcal{N}(v;\,u,\,\sigma = TI\cdot u)\,dv$$

evaluată prin cuadratură Gauss–Hermite cu 9 noduri (practic exactă pentru o curbă netedă, cost
neglijabil). Intensitatea turbulenței e declarată sau estimată din rugozitate: $TI \approx 1/\ln(z_{hub}/z_0)$.

Referință: PCWG — *Turbulence Renormalisation*; MEASNET TI normalization.

### 2.5 Curba de putere
Curba exactă a producătorului (interpolare liniară) sau, în lipsa ei, aproximarea cu trei viteze:
rampă cubică între pornire și nominal (puterea aerodinamică crește cu v³ înainte de limitare),
platou la nominal până la oprire, zero în rest.

### 2.6 Siajul

Implicit **dependent de viteză**: pierderea declarată de operator e valoarea **maximă**, atinsă în
zona de sarcină parțială (unde coeficientul de tracțiune e mare), și scade spre zero peste viteza
nominală, unde turbinele din față extrag puțin din flux. Un procent constant greșește în ambele cozi.

Modelul de referință pentru un calcul geometric complet e Jensen/Park —
$\Delta U/U = (1-\sqrt{1-C_T})/(1+2kx/D)^2$, k ≈ 0,075 onshore — dar necesită amplasamentul turbinelor,
pe care platforma nu îl cere. Recomandarea pentru viitor: tabel de eficiență precalculat offline
(PyWake/foxes) per parc.
Referință: Barthelmie et al. (2010), *J. Atmos. Ocean. Technol.* 27(8), 1302-1317.

---

## 3. Combinarea surselor meteo

Media ponderată, cu ponderi **renormalizate pe fiecare oră** în funcție de modelele disponibile
(vezi `docs/DEBUGGING.md`, A1). Dispersia între modele (abaterea standard) e folosită ca proxy al
incertitudinii și e transmisă ca intrare modelului ML.

**Ponderi adaptive (opțional).** Ponderile pot fi deduse din performanța recentă a fiecărui model pe
parcul respectiv: pondere invers proporțională cu eroarea medie, amestecată 50/50 cu ponderile egale
și limitată inferior, ca un model să nu fie eliminat pe baza unei perioade scurte nefavorabile —
diversitatea ansamblului e ea însăși valoroasă. → `app/core/ponderi_adaptive.py`

---

## 4. Referința de cer senin și indicele de senin

Radiația de cer senin: **Ineichen–Perez** cu climatologia de turbiditate Linke (inclusă offline în
pvlib). Puterea de cer senin se obține trecând această radiație prin **exact același lanț fizic** ca
prognoza reală — deci ține cont de orientare, pierderi, degradare, umbrire.

$$k_t^* = \frac{P_{măsurat}}{P_{cer\ senin}}$$

Indicele de senin e scara naturală a producției fotovoltaice: elimină variația deterministă
(zi/noapte, anotimp, geometrie) și lasă exact ce nu știe fizica — norii. E folosit în trei locuri:
ca țintă normalizată a modelului ML, ca bază a persistenței inteligente, și ca spațiu în care se
aplică filtrul de outlieri și interpolarea la 15 minute.
→ `app/core/clearsky.py`

---

## 5. Stratul de învățare automată

### 5.1 Ce învață
**Reziduul normalizat**, nu producția:

$$y = \frac{P_{măsurat} - P_{fizic}}{S}, \qquad
S = \begin{cases} P_{cer\ senin} & \text{fotovoltaic} \\ P_{instalat} & \text{eolian}\end{cases}$$

Normalizarea e importantă: pe scara kW bruți, arborii cheltuiesc capacitate pe variația deterministă
zi/noapte-anotimp; pe scara indicelui rămâne de învățat exact eroarea de nebulozitate.

### 5.2 Algoritm
LightGBM (gradient boosting pe arbori), 300 de runde, 31 de frunze, rată de învățare 0,05, minimum
20 de observații per frunză.

### 5.3 Intrări
Fracția de încărcare a invertorului (obligatorie, R-604); ora și ziua anului codificate ciclic
(sin/cos); zenitul și azimutul solar sau viteza echivalentă la butuc; dispersia între modelele meteo;
acoperirea cu nori pe straturi; umiditatea; direcția vântului ca pereche sin/cos (mărime circulară);
exponentul de forfecare; densitatea aerului; indicele de senin prognozat; factorul de zăpadă.

**Context temporal:** fiecare variabilă informativă primește și valorile decalate cu −3 h, −1 h, +1 h,
+3 h și diferența față de ora precedentă. Acestea corectează erorile de **sincronizare** ale modelului
numeric (norii ajung cu o oră mai devreme decât prezis) — o clasă de erori sistematice invizibilă
pentru un model care vede doar ora curentă.

Aceleași intrări se construiesc identic la antrenare și la predicție, dintr-un singur cadru comun —
divergența între cele două e cauza clasică a modelelor care „merg la antrenare și nu merg în producție".

### 5.4 Excluderi
Orele de noapte (la fotovoltaic) și orele marcate de controlul de calitate (indisponibilitate,
limitare de putere, valori blocate, outlieri, producție noaptea) sunt eliminate din antrenare.
Altfel modelul învață defecțiunile parcului și le împrăștie în toate prognozele viitoare.

### 5.5 Promovare (R-605)
Împărțire **cronologică** 85/15. Modelul nou devine activ doar dacă are un **scor de skill** mai bun
decât cel activ, evaluat pe același set de validare (cu MAE ca departajare când skill-ul nu se poate
calcula). Altfel rămâne salvat, dar inactiv — mecanismul de revenire automată.

### 5.6 Cuantile și calibrare conformă
Șapte modele suplimentare, antrenate cu funcția de pierdere **pinball** la nivelurile
0,05 / 0,1 / 0,25 / 0,5 / 0,75 / 0,9 / 0,95:

$$PL_\tau(q,y) = \begin{cases}\tau\,(y-q) & y \ge q\\ (1-\tau)\,(q-y) & y < q\end{cases}$$

Cuantilele antrenate independent se pot **încrucișa** (P90 sub P50); se sortează pe fiecare oră.

Peste ele se aplică o **calibrare conformă** pe o fereastră de calibrare nevăzută la antrenare: se
măsoară cât de des realizarea a căzut în afara benzii și banda se lărgește exact cu cât trebuie ca
acoperirea observată să atingă nivelul promis. Fără acest pas, cuantilele dintr-un model de regresie
sunt sistematic prea înguste.

Referințe: Landry et al. (2016) și Nagy et al. (2016), *Int. J. Forecasting* 32(3) — soluțiile
câștigătoare GEFCom2014; Gneiting & Raftery (2007), *JASA* 102(477).

---

## 6. Verificarea

### 6.1 Indicatori
$$nMAE = \frac{\frac{1}{N}\sum|f_i - o_i|}{P_{ref}}\cdot 100, \qquad
nRMSE = \frac{\sqrt{\frac{1}{N}\sum(f_i-o_i)^2}}{P_{ref}}\cdot 100, \qquad
bias = \frac{\frac{1}{N}\sum(f_i-o_i)}{P_{ref}}\cdot 100$$

Baza de normalizare $P_{ref}$ e **declarată explicit**: implicit puterea instalată (convenția
operatorilor de sistem, recomandarea IEA Wind Task 36/51), cu raportarea paralelă la producția medie
(convenția traderilor). Un „nMAE" fără bază declarată nu e interpretabil.

### 6.2 Filtre obligatorii
Orele de noapte se exclud la fotovoltaic (echivalentul practic al filtrului θz < 85° recomandat de
Yang et al. 2020, dar exprimat prin puterea de cer senin a parcului, deci ține cont și de orientare).
Orele cu probleme de calitate se exclud și se raportează separat.

### 6.3 Prognoze de referință
- **persistență**: $P(t+h) = P(t)$;
- **persistență inteligentă** (solar): $P(t+h) = k_t^*(t)\cdot P_{cer\ senin}(t+h)$ — se persistă
  cerul, nu puterea;
- **climatologie**: media istorică a indicelui;
- **combinația convexă optimă**: $r = \rho_h\cdot\text{persistență} + (1-\rho_h)\cdot\text{climatologie}$,
  cu ρ_h autocorelația la decalajul h. Pe procese staționare, eroarea ei nu depășește pe cea a
  celei mai bune componente — deci e cea mai severă referință „fără model" rezonabilă.

### 6.4 Scorul de skill
$$s = 1 - \frac{RMSE(\text{prognoză})}{RMSE(\text{referință})}$$
+1 = perfect, 0 = exact cât referința, negativ = mai rău decât a nu avea model.

### 6.5 Verificare probabilistică
CRPS aproximat din setul de cuantile prin identitatea $CRPS = 2\int_0^1 PL_\tau\,d\tau$; acoperire
empirică per nivel (fiabilitate); lățimea medie a intervalului (ascuțime) — raportată **doar**
împreună cu fiabilitatea, pentru că un interval îngust necalibrat e mai rău decât inutil.

### 6.6 Defalcare pe orizont
Indicatorii se raportează separat pe 0-6 h, 6-24 h, 24-48 h și 48+ h. Amestecul orizonturilor
flatează rezultatul, pentru că rulările recente sunt sistematic mai precise.

Referințe: Yang et al. (2020), *Solar Energy* 210, 20-37; Lauret, David & Pinson (2019),
*Solar Energy* 194, 254-271; IEA Wind Task 36/51 Recommended Practice, Part 3.

---

## 7. Controlul calității datelor măsurate

| Verificare | Metodă | De ce contează |
|---|---|---|
| Decalaj de timp | corelație încrucișată cu puterea de cer senin, ±4 h | un decalaj de 1-3 h e invizibil în indicatorii agregați, dar strică sistematic calibrarea, antrenarea și toți indicatorii |
| Producție noaptea | > 1% din instalată când referința de cer senin e zero | decalaj de contorizare sau semn inversat |
| Peste puterea instalată | > 1,05 × instalată | imposibil fizic; de obicei eroare de unitate |
| Valori blocate | ≥ 6 valori identice consecutive, nenule | senzor/legătură de date înghețată |
| Indisponibilitate | < 25% din așteptarea fizică, ≥ 2 h susținut | avarie, lucrări |
| Limitare de putere | < 90% din așteptare, platou cu coeficient de variație < 2%, ≥ 3 h | semnătura curtailment-ului |
| Valori extreme | filtru Hampel (mediană + MAD) **pe indicele de senin**, prag absolut suplimentar | robust la outlierii înșiși |

Filtrul Hampel: outlier dacă $|x - \tilde{x}_{local}| > k\cdot 1{,}4826\cdot MAD_{local}$.
Aplicarea lui pe indicele de senin, nu pe kW bruți, e esențială — vezi `docs/DEBUGGING.md`, B1.

Datele brute **nu** se modifică niciodată (R-611): rezultatul e un set separat de verdicte,
recalculabil oricând.

Referință: Perry, Meyers & Anderson (2022), NREL/CP-5K00-83824 (PVAnalytics).

---

## 8. Rezoluția de 15 minute

Modelele meteo livrează date orare; decontarea se face la 15 minute. Radiația **nu** se interpolează
liniar (forma ei în interiorul orei e puternic neliniară, mai ales la răsărit/apus, iar interpolarea
liniară deplasează energia). Se interpolează **indicele de senin** — o mărime lentă, aproape liniară —
și se reconstruiește radiația înmulțind cu radiația de cer senin calculată direct la pasul fin, unde
geometria solară e exactă prin construcție.

Restul variabilelor se interpolează liniar; cele cumulative (precipitații, ninsoare) se împart la
numărul de sub-intervale.

Atenție la unități: la pas sub-orar, **suma puterilor nu mai este energie** — o confuzie care ar da
o eroare de 4×. Conversia e explicită (`energie_din_putere`).

---

## 9. Controalele de calitate ale prognozei (înainte de livrare)

| # | Verificare | Tip |
|---|---|---|
| 1 | putere negativă sau peste puterea instalată (+2% marjă numerică) | **blocant** |
| 2 | producție solară semnificativă noaptea (zenit ≥ 95°) | **blocant** |
| 3 | serie meteo suspect de constantă | avertisment |
| 4 | salt brusc față de rularea precedentă, aceeași oră | avertisment |
| 5 | energie zilnică peste un plafon fizic de factor de capacitate | avertisment |

O oră blocată nu se livrează; rularea devine „parțial reușită". Filozofia (R-1503): o livrare de ~99%
e mai bună decât o „perfecțiune" care trimite uneori o cifră greșită fără să spună.
