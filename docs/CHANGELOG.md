# Jurnal de modificări

## v12 — corecții de aliniere temporală, tipografie pentru date, acțiuni grupate

### Verificarea fusului orar era greșită metodologic

Raportul „amiaza solară e la ora X" putea indica un decalaj de două ore acolo
unde nu exista niciunul. Cauza: se compara ORA ÎNTREAGĂ a vârfului mediu de
producție — mediat peste tot istoricul, deci amestecând ore de vară cu ore de
iarnă — cu amiaza solară a UNEI singure zile „reprezentative". Ecuația timpului
mută amiaza solară cu ±16 minute peste an, ora de vară cu încă o oră întreagă,
iar trunchierea la ora întreagă adăuga până la 60 de minute de eroare.

Metoda nouă compară, pentru FIECARE zi, centrul de greutate energetic al
producției cu amiaza solară a acelei zile, apoi ia mediana. Pe o zi senină
curba de radiație e simetrică față de amiaza solară, deci centrul cade exact pe
ea. Verificat pe date sintetice: zero fals pozitive pe orice fus (Bucharest,
Brussels, UTC), iarna și vara, pe istoric de peste un an; decalaje reale de
1, 2 și 3 ore detectate exact. Mesajul raportează acum decalajul în minute, nu
o oră întreagă, și e independent de fusul declarat.

### Cache-ul de reanaliză păstra date în convenția veche

Trecerea la „eticheta = începutul intervalului" (v10) a schimbat felul în care
se interpretează datele meteo, dar intrările deja salvate în `meteo_cache`
rămâneau în convenția veche și erau citite nemodificate. Amestecate cu date
proaspete, dădeau o serie incoerentă — cel mai vizibil la calibrarea
geometriei, care deduce înclinarea și azimutul tocmai din forma curbei zilnice.
Cheia de cache e acum versionată (`reanaliza_v2`), deci intrările vechi nu mai
sunt citite niciodată.

### Calibrarea semnalează datele nealiniate în loc să compenseze tăcut

Calibrarea deduce geometria din FORMA curbei zilnice. Dacă seria măsurată e
decalată în timp (fus de import greșit, ore din importuri succesive cu setări
diferite), sondajul compensează rotind azimutul — întoarce o geometrie care
„potrivește" datele fără să descrie parcul, și rezultatul pare aberant fără să
spună de ce. Rezultatul include acum decalajul măsurat față de amiaza solară;
peste 25 de minute, pagina avertizează explicit că geometria nu e de luat în
serios până nu se corectează alinierea.

Verificat end-to-end, cu JSON brut în convenția reală Open-Meteo trecut prin
tot lanțul de parsare: calibrarea recuperează exact geometria adevărată
(30°/180°) și factorul de scalare real (0,75).

### Prognoza pe 16 zile eșua

Motorul cere 2 zile de marjă peste orizont, deci la orizont maxim ajungea să
ceară 18 zile — peste limita Open-Meteo de 16, respinsă cu 400. Limita se
impune acum în clientul meteo, unde e o proprietate a furnizorului.

### Tipografie după recomandările Datawrapper

Aplicate din ghidul de fonturi pentru vizualizarea datelor: Source Sans 3
(sans-serif de lățime normală, dintre cele recomandate acolo), cu Roboto și
stiva de sistem ca rezervă — aplicația rulează local și poate fi offline, caz
în care pagina arată în continuare bine. Cifre LINING și TABULARE peste tot
unde apar numere, ca valorile să se alinieze pe coloane. Greutate normală în
text, bold doar la titluri. Majusculele au dispărut din antetele de tabel și
din titlurile de secțiune (textul în propoziție se citește mai repede);
au rămas doar pe etichete foarte scurte, cu spațiere între litere. Nimic sub
12px.

### Aliniere și structură

- **Bug de layout**: câmpul „Nume client nou" se afișa cu eticheta lipită de
  input. Cauza — `style.display = 'block'` călca peste `display: flex` al
  containerului; comutarea se face acum prin atributul `hidden`.
- Toate controalele de formular au aceeași înălțime ca butoanele, deci
  rândurile mixte (buton + select) se aliniază.
- **Acțiunile parcului, grupate pe scop** (Acțiuni / Analiză / Date și model)
  în loc de paisprezece butoane identice la rând, unde nimic nu spunea care e
  acțiunea principală.
- Verificat automat, cu browser real, pe cinci pagini: nicio suprapunere între
  etichete, câmpuri, butoane și titluri.


## v11 — estimare pe lună calendaristică, analiza antrenării, factorul de scalare aplicabil

### Estimarea lunară, pe lună calendaristică

Pagina răspunde acum la două întrebări diferite, alese explicit:

- **lună calendaristică** (implicit) — octombrie, noiembrie…, forma folosită la
  planificare și la raportarea către client;
- **următoarele N zile de mâine** — ce urmează, indiferent unde cade granița lunii.

Pentru luna curentă, zilele deja încheiate arată **producția reală măsurată**,
nu o estimare: o zi trecută nu se mai ghicește, se știe. Indicatorul principal
ia pe fiecare zi cifra cu cea mai mare încredere (măsurat > NWP > climatologie)
și le însumează o singură dată — celelalte trei totaluri rămân pe surse
separate, ca să nu se adune între ele.

### Analiză după antrenarea ML

Cifrele existau deja, dar ajungeau la operator într-o frază densă, afișată o
clipă ca mesaj. Există acum un ecran propriu (`/parcuri/{id}/antrenare`), care
rămâne accesibil și mai târziu: verdictul (activat / respins) cu motivul lui,
cifrele care l-au decis, datele folosite și observații — semnalate doar când e
cazul (skill negativ și ce anume de verificat, prea multe ore excluse de
controlul de calitate). Un model respins e explicat ca atare: versiunea veche
continuă să lucreze, nimic nu s-a stricat.

### Factorul de scalare: dialog de ajustare, nu aplicare automată

Butonul „Aplică pe configurația curentă" scrie, ca și până acum, **doar
înclinarea și azimutul**. Factorul de scalare NU se distribuie automat peste
pierderi — cerință explicită a operatorului, ca procentele să nu se poată
dubla cu cele deja configurate.

În loc de asta, pagina de calibrare deschide un **dialog de ajustare**: fiecare
pierdere (murdărire, neuniformitate, cablaj, LID, plăcuță, indisponibilitate,
rest neatribuit) plus rata de degradare, cu **totalul cumulat recalculat la
fiecare tastă** și distanța până la factorul țintă, semnalizată pe culori.
Operatorul ajustează până ajunge la țintă și salvează.

De ce manual și nu automat: factorul adună la un loc pierderi de sistem și
degradare, iar repartizarea lor pe cauze fizice e o judecată despre parcul real
(e murdărire? indisponibilitate? plăcuță supraevaluată?), nu un calcul.
Totalul afișat folosește exact aceeași formulă multiplicativă ca modelul fizic,
deci nimic nu se poate dubla pe ascuns — suma reală e mereu pe ecran. Câmpul
`pierdere_nemodelata_pct` există ca linie separată „rest neatribuit", pentru
partea pe care operatorul nu o poate atribui unei cauze anume, dar se
completează DOAR de mână.

### Interfață

- buton **„Înapoi"** pe fiecare pagină în afară de portofoliu, care folosește
  istoricul browserului (același ecran se deschide din locuri diferite, deci o
  destinație fixă ar trimite adesea greșit) și cade pe o destinație firească
  atunci când pagina a fost deschisă direct.

### Pachet complet de instalare

`INSTALARE.md` — pornire de la zero, configurare, actualizarea unei instalări
existente, Docker, rulare zilnică. Arhiva de instalare NU conține baza de date
și nici modelele antrenate, intenționat: dezarhivarea peste o instalare
existentă ar fi șters datele reale.


## v10 — eticheta = ÎNCEPUTUL intervalului, peste tot; orizont NWP extins; filtru pe tehnologie

### Convenția de interval, schimbată în toată platforma

R-512 devine: **eticheta unui interval e începutul lui** — ora 12:00 înseamnă
energia produsă între 12:00 și 13:00. E convenția piețelor de energie
(PZU/OPCOM, ENTSO-E) și a contoarelor, deci cea pe care o vorbesc datele care
intră în platformă. Se aplică la import, în prognoză, în rapoarte și la export,
fără nicio excepție și fără nimic de configurat.

Schimbarea atinge tot lanțul, iar semnul greșit undeva ar fi însemnat exact
eroarea de o oră pe care convenția o previne:

- poziția solară se evaluează la mijlocul intervalului, acum **după** etichetă
  (12:00 → 12:30). Pasul se DEDUCE din serie în loc să fie presupus o oră —
  ceea ce repară și o eroare latentă la rezoluție de 15 minute, unde jumătatea
  de oră fixă cădea cu totul în intervalul următor;
- datele Open-Meteo se reetichetează la citire: radiația e publicată ca „media
  orei precedente", deci valoarea de la 12:00 aparține intervalului care începe
  la 11:00. Temperatura și vântul sunt instantanee la etichetă și rămân pe loc;
- reeșantionarea la 15 minute generează acum 12:00, 12:15, 12:30, 12:45 în loc
  de 11:15…12:00.

Testul care păzește totul (`tests/test_conventie_interval.py`) nu verifică
etichete, ci **fizica**: intervalul cu producția maximă trebuie să rămână cel
care conține amiaza solară. Reetichetarea mută numele rândurilor, nu realitatea.

Setarea per parc introdusă în v9 a fost eliminată: o singură convenție, peste
tot, înseamnă că nu mai există nimic de ghicit sau de configurat greșit.
Avertismentul R-1104 nu mai sugerează convenția de etichetare drept cauză
posibilă a unui decalaj de o oră, fiindcă nu mai poate fi.

### Prognoză pe următoarea lună: NWP pe mai multe zile

Pagina arăta doar 3 zile de NWP, deși modelele meteo acoperă până la 16. Două
cauze, ambele rezolvate:

- se privea doar ULTIMA rulare, iar rularea zilnică salvează doar orizontul
  implicit (3 zile) — o rulare manuală mai lungă, făcută mai devreme, era
  ignorată. Acum NWP se adună din toate rulările recente, cu convenția „cea mai
  recentă prognoză disponibilă pentru fiecare zi";
- buton nou, **„Calculează NWP pe 16 zile"**, care cere explicit orizontul
  maxim. Rularea zilnică rămâne scurtă (mult mai ieftină ca trafic, suficientă
  pentru raportul zilnic), iar restul orizontului se descarcă atunci când chiar
  e nevoie de el.

Corectată și o eroare de scalare pe această pagină: la rezoluție de 15 minute
energia zilnică NWP era de patru ori prea mare (suma puterilor nu e energie
fără înmulțirea cu pasul).

### Graficul „Reală vs. prognoză" începea cu o zi din luna următoare

Rândurile se ordonau după textul afișat, în format `zz.ll.aaaa` — iar
lexicografic „01.09" vine înaintea lui „29.08", pentru că „0" < „2". Graficul
începea, deci, cu o zi din septembrie și sărea apoi înapoi în august.
Ordonarea se face acum pe momentul real, nu pe eticheta citibilă.

### Interfață

- **Filtru pe tehnologie în portofoliu**: toate / fotovoltaic / eolian, cu
  numărul de active pe fiecare. Filtrele sunt linkuri cu URL propriu (se pot
  pune la favorite, butonul „înapoi" funcționează), iar indicatorii de sus
  urmează filtrul, ca să nu stea un total pe tot portofoliul deasupra unui
  tabel filtrat.
- Rândul de acțiuni: toate controalele au aceeași înălțime, iar butonul
  „Descarcă prognoza (CSV)" formează împreună cu selectorul de format un
  singur control lipit, nu două elemente nimerite alături.
- Tabelele: cifre cu lățime fixă (se citesc pe coloane) și evidențierea
  discretă a rândului de sub cursor.

## v9 — un singur format de timestamp, peste tot; convenția de interval, declarată

### Formatul timestamp, armonizat în toată platforma

Până acum fiecare capăt avea convenția lui. Cea mai clară dovadă că era o
problemă, nu o chestiune de stil: **exportul CSV scria coloana de timp fără
decalaj de fus orar, deci un fișier produs de platformă nu trecea prin importul
platformei** — iar orele din nopțile schimbării orei rămâneau ambigue exact ca
înainte. Există acum un test care verifică drumul dus-întors.

Un singur loc definește formatele (`app/core/momente.py`), folosit de exportul
CSV și de raportul lunar Excel:

| format | exemplu | când |
|---|---|---|
| `iso_local` (implicit) | `2026-06-01T12:00:00+03:00` | ora locală cu decalaj — neambiguu, reimportabil |
| `iso_utc` | `2026-06-01T09:00:00+00:00` | pentru sisteme care lucrează în UTC |
| `local_simplu` | `2026-06-01 12:00` | formatul vechi, ambiguu — doar dacă sistemul din aval cere asta |

Alegerea se face **individual la fiecare descărcare**: un selector lângă
„Descarcă prognoza (CSV)" și unul în pagina de rapoarte lunare. Un format
necunoscut în URL cade pe cel implicit, nu produce eroare. Coloana
`timestamp_utc` rămâne prezentă în ambele fișiere, ca sursă de adevăr,
indiferent de alegere.

Efect vizibil în raportul lunar pe octombrie: cele două ore locale „03:00" din
noaptea tranziției apar acum ca valori distincte (`+03:00` și `+02:00`), în loc
să fie scrise identic.

### Ce format are vremea (și de ce contează)

Open-Meteo e interogat mereu cu `timezone=UTC`, iar etichetele întoarse sunt
ISO **fără** decalaj (`2026-06-01T12:00`) — singura convenție implicită rămasă
în lanț. Se verifică acum explicit `utc_offset_seconds` din răspuns: dacă nu e
0, cererea se oprește cu o eroare clară, în loc să interpreteze ca UTC o serie
locală și să deplaseze tăcut toată producția cu 2-3 ore.

Convenția de interval e confirmată din documentația Open-Meteo: radiația e
„average of the **preceding** hour", adică eticheta e FINALUL intervalului —
exact convenția internă a platformei (R-512). Temperatura și vântul sunt însă
valori instantanee la eticheta respectivă, nu medii ale orei precedente; e o
aproximare acceptată (ambele variază lent și intră prin corecții secundare),
documentată acum explicit.

### Convenția de interval la import: declarată, nu ghicită

Decalajul explicit rezolvă „care oră locală", dar nu și a doua întrebare:
`2026-06-01T12:00:00+03:00` e un moment fără dubiu, însă energia raportată
acolo s-a produs **înainte** sau **după** el? Ghicit greșit, rezultatul e exact
același decalaj de o oră.

- Câmp nou per parc, `eticheta_ora_import`: „finalul intervalului" (implicit,
  comportamentul dinainte) sau „începutul intervalului", cum scriu multe
  exporturi SCADA/contor. La import, valorile se mută automat cu un pas, iar
  mutarea e anunțată explicit în raportul de analiză.
- **Avertismentul R-1104 nu mai trimite direct la fusul orar** când decalajul e
  de exact o oră. Menționează acum ambele cauze posibile și avertizează că
  schimbarea fusului ar ascunde simptomul introducând o eroare de fus care nu
  exista — capcana în care era ușor de căzut cu mesajul vechi.

## v8 — scalare măsurată la 50 de parcuri, format de import fără ambiguități, retenție

### Format de import obligatoriu: ora cu decalaj de fus orar explicit

Formatul de bază al fișierelor de producție devine ISO 8601 **cu decalaj**
(`2025-03-30T01:00:00+02:00`). Motivul e cel care a produs, în practică,
avertismentele raportate de operator: o oră scrisă fără decalaj nu identifică un
moment în timp. În noaptea trecerii la ora de iarnă, ora locală 03:00 se petrece
de două ori; la trecerea la ora de vară nu există deloc. Aceleași cifre pot
însemna două momente diferite sau niciunul — iar diferența e exact o oră de
producție atribuită greșit.

- Cu decalaj în fișier dispar cele trei simptome clasice: „timestamp duplicat"
  la trecerea la ora de iarnă, „interval neregulat" la trecerea la ora de vară,
  și vârful de producție decalat cu o oră față de amiaza solară. Fusul de import
  declarat pe parc nu mai poate strica nimic — fișierul îl poartă cu el.
- Se acceptă `T` sau spațiu între dată și oră, și sufixul `Z` pentru UTC.
- Duplicatele se judecă acum pe MOMENTUL absolut, nu pe text: două scrieri
  diferite ale aceleiași ore reale sunt semnalate, iar două texte identice ca
  cifre dar cu decalaje diferite sunt recunoscute ca ore distincte.
- **Formatul vechi** (oră locală fără decalaj) rămâne importabil, dar numai după
  o bifă explicită în pagina de import. În acel mod, orele imposibile sau
  ambigue de la schimbarea orei sunt acum **respinse una câte una**, cu
  instrucțiunea de corectare — înainte erau rezolvate tăcut, alegând prima
  apariție (`fold=0`), adică ghicind.

### Anduranță: platforma, măsurată la 50 de parcuri

Presupunerea R-430 („sub 50 de parcuri") nu fusese niciodată verificată. S-a
adăugat `scripts/benchmark_scalare.py`, care construiește un portofoliu sintetic
realist (parcuri, ani de producție, zeci de rulări arhivate) și cronometrează
exact operațiile pe care le face operatorul. Rezultatele au contrazis intuiția.

- **Pagina de portofoliu: 12,5 s → 0,50 s.** Cauza, găsită prin profilare: 96%
  din timp se construiau obiecte `OraPrognoza` — 222.650 dintre ele — ca să se
  afișeze un tabel cu 50 de rânduri de metadate. `listeaza_rulari` deserializa
  toate orele fiecărei rulări, inclusiv ale celor pe care bucla nici nu le
  atingea.
- **Repository, două metode noi**: `itereaza_rulari(...)` (iterator leneș, cu
  filtrare pe interval chiar în interogare — un `break` timpuriu chiar oprește
  citirea) și `listeaza_rulari_sumar(...)` (doar antetul, fără ore; pe SQLite
  extras cu funcțiile JSON native, deci lista de ore nu ajunge niciodată în
  memoria procesului). Pe Firestore, filtrarea pe interval reduce direct
  numărul de documente citite, adică factura.
- **Jurnalul general** cerea rulările parc cu parc — 50 de interogări, fiecare
  aducând rulări complete, ca apoi să fie tăiate la primele 150. Acum e o
  singură interogare.
- **Durata unei rulări de prognoză: 0,58 s → 0,35 s per parc.** Peste jumătate
  se consuma în `ponderi_adaptive`, într-o căutare `DataFrame.loc` repetată de
  zeci de mii de ori într-o buclă Python; înlocuită cu un dicționar construit o
  singură dată.
- `dataclasses.fields()` se recalcula pentru fiecare obiect citit din bază —
  memorat acum per clasă.

### Disjunctor pentru sursa meteo

Măsurat: cu 50 de parcuri și Open-Meteo căzut, rularea zilnică încerca **~800 de
cereri HTTP și aștepta cumulat ~50 de minute**, ca apoi să nu producă nimic —
fiecare parc relua de la zero scara completă de reîncercări pentru o sursă
despre care se știa deja, de la parcul precedent, că nu răspunde.

După trei eșecuri TEMPORARE consecutive (rețea, timeout, 5xx, 429 repetat),
cererile următoare eșuează imediat, fără rețea, până la expirarea unei răciri de
10 minute, cu un mesaj care spune explicit că se reia automat. Erorile permanente
(4xx — de obicei un id de model greșit pe un parc) NU declanșează disjunctorul:
altfel un singur parc prost configurat ar bloca prognozele întregului portofoliu.

### Întreținere și retenție (Modul tehnic)

Arhiva de rulări crește, la 50 de parcuri, cu ordinul unui gigabyte pe an —
adică exact cota gratuită de stocare Firestore, atinsă în aproximativ un an.
Cache-ul meteo nu se golea niciodată singur.

Ecran nou, *Întreținere & retenție*: arată cât ocupă datele și permite ștergerea
rulărilor mai vechi de N zile și golirea cache-ului meteo. Se șterge doar
istoricul „ce am prognozat atunci" — producția reală importată, modelele ML și
configurarea parcurilor rămân neatinse. Retenția sub 30 de zile e refuzată
(ar strica verificarea acurateței și ponderile adaptive). Operația e explicită,
niciodată automată — la fel ca principiul R-611.

De asemenea, salvarea pe Firestore verifică acum mărimea documentului înainte de
scriere: o rulare de 16 zile la pas de 15 minute ocupă ~0,81 MiB, la 81% dintr-o
limită TARE de 1 MiB. Peste prag se dă un mesaj clar, cu soluția (orizont mai
mic sau pas de 60 de minute), în locul erorii criptice a Firestore la finalul
unei rulări de câteva minute.

### Documentație

- `docs/GHID_ACTUALIZARE.md` — cum se actualizează platforma în siguranță, cum
  se adaugă o funcție nouă, harta codului, regulile de casă și capcanele de
  performanță de evitat.
- `docs/AGENTI.md` — unde ajută cu adevărat agenții AI în operarea acestei
  platforme, și unde nu trebuie folosiți.


## v7 — curățare producție dintr-un interval, editare rapidă înclinare/azimut, „Tabel recent” pe 3 zile, MWh peste tot, NWP/climatologie în paralel

### Curățarea unui import cu fus orar greșit (raport operator: avertismente DST la import CSV)

- **Diagnostic**: avertismentele de import („timestamp duplicat” la schimbarea orei, „6 intervale neregulate”,
  „vârful de producție cade la 11:00, nu la 12:00 solar”) NU sunt un bug al platformei — arată corect că
  fișierul sursă (export SCADA/contor) folosește un offset orar FIX, care nu urmează trecerea la ora de
  vară/iarnă din România, în timp ce fusul declarat al parcului o urmează. Cel mai probabil offset real:
  UTC+2 (EET) tot anul, nu Europe/Bucharest.
- **Unealtă nouă**: „Curățare producție importată dintr-un interval” (zona tehnică, configurarea parcului) —
  șterge producția reală ȘI verdictele de calitate asociate dintr-un interval de date ales, interpretat în
  fusul de IMPORT al parcului (nu fusul de prognoză), ca să corespundă exact orelor greșit importate.
  Necesară pentru că re-importul, singur, NU corectează retroactiv orele deja scrise cu fusul greșit —
  cheia de upsert e timestamp-ul UTC, deci un re-import după fixarea fusului ar ADĂUGA ore noi alături de
  cele vechi, nu le-ar înlocui. Implementată în ambele depozite (SQLite: `DELETE` direct; Firestore:
  citire-modificare-scriere pe documentele zilnice pe care le afectează parțial).
- **Pasul recomandat pentru un parc cu acest simptom**: (1) setați `fus_orar_import` corect (ex. `UTC` sau
  `Etc/GMT-2`, dacă exportul e cu offset fix EET) din pagina de import; (2) folosiți unealta de curățare de
  mai sus pentru a șterge intervalul afectat de importul vechi; (3) reîncărcați același fișier CSV — de
  această dată se va converti corect, fără avertismente de fus orar sau timestamp-uri duplicate la
  schimbarea orei.

### Cerințe operator

- **„Tabel recent” arată acum 3 zile complete, cu totalul zilei** (`app/web/vizualizare.py:tabel_recent_zile`),
  nu primele 24 de INTRĂRI din ultima rulare — la rezoluție sub-orară (ex. 15 minute) asta însemna doar
  6 ore dintr-o singură zi, nu 3 zile. Gruparea e pe zi CALENDARISTICĂ LOCALĂ (fusul parcului) și scalează
  corect energia zilei după pasul de rezoluție.
- **Valorile de energie afișate în MW/MWh cu trei zecimale, generalizat**: „Prognoză pentru următoarea lună”
  și „Analiză de sensibilitate” (zona tehnică) foloseau formatare manuală în kWh — trecute pe filtrul comun
  `| mw`, la fel ca restul aplicației.
- **„Prognoză pentru următoarea lună” arată NWP și climatologia în PARALEL, pe fiecare zi** — nu mai
  combinate într-un singur rând care alege o singură sursă. Climatologia se calculează acum pentru
  TOATE zilele din interval (nu doar cele necoperite de NWP), ca operatorul să poată compara direct cele
  două estimări chiar și pe zilele apropiate, unde NWP e disponibilă.
- **Editare rapidă a înclinării/azimutului, în zona tehnică**: raportul „nu mai pot modifica înclinarea și
  azimutul” s-a dovedit un bug de parsare — formularul accepta strict punct decimal (`float()`), fără
  toleranță la virgulă zecimală („30,5”, tastatură românească), spre deosebire de convenția deja existentă
  în `validare.py`. O virgulă zecimală făcea salvarea să eșueze COMPLET, silențios din perspectiva
  operatorului. Corectat, plus câmpuri rapide de înclinare/azimut afișate direct (fără textarea
  semnalizată prin punct-virgulă) când parcul are un singur subansamblu. Corectat și un bug latent conex:
  o înclinare de 0° (montaj plat, valoare fizic validă) se serializa greșit ca „gol”.

## v6 — decuplare scală/geometrie la calibrare, fus orar de import separat, avertismente ML clarificate

### Corecții raportate de operator

- **Calibrarea geometriei nu mai confundă scara cu forma.** Comparat metodologic cu un script extern
  de calibrare furnizat de operator (dintr-un alt proiect, `pvforecast`) — care funcționa bine — s-a
  găsit o diferență reală: sondajul de înclinare/azimut evalua fiecare candidat CU pierderile de sistem
  și degradarea configurate pe parc încă active în model. O pierdere/degradare configurată greșit
  (frecvent, mai ales la parcuri noi) deplasează magnitudinea TUTUROR geometriilor testate la fel — iar
  sondajul ajungea să aleagă geometria după cine se apropie ca MAGNITUDINE, nu după forma reală a curbei
  de producție. Corectat (`app/core/calibration.py`): fiecare geometrie testată e evaluată cu pierderile
  și degradarea SCOASE din model, înlocuite de un factor de scalare potrivit separat, prin cele mai mici
  pătrate — exact decuplarea folosită de scriptul extern. Rezultatul afișează acum și acest factor de
  scalare optim, procentul cumulat de pierdere pe care-l reprezintă și, dacă parcul are dată de punere
  în funcțiune, o defalcare estimată între cât explică degradarea anuală configurată și cât rămân
  probabil pierderi de sistem neconfigurate corect. Test dedicat: cu pierderi/degradare configurate
  DELIBERAT greșit (mult peste factorul real), calibrarea tot recuperează geometria adevărată.
- **Fus orar de import separat de fusul de prognoză, per parc** (`Parc.fus_orar_import`,
  `app/core/timezones.py:fus_orar_pentru_import`): exportul din SCADA/contor poate folosi o convenție
  orară diferită de fusul IANA ales pentru prognoze (frecvent: offset fix, fără oră de vară/iarnă).
  Până acum un singur câmp (`fus_orar`) guverna ambele — o corecție a fusului de import deplasa și
  granița de zi/afișarea prognozelor deja calculate, sau invers, un parc cu convenții diferite nu putea
  fi configurat corect deloc. Acum `fus_orar_import` e opțional (gol = identic cu `fus_orar`, comportament
  neschimbat pentru toate parcurile existente) și se poate declara separat la creare, din zona tehnică,
  sau din butonul rapid de pe pagina de import — care acum corectează STRICT interpretarea importului,
  nu mai atinge fusul de prognoză.
- **Avertismentele confuze de după antrenarea ML, investigate și corectate la sursă** (nu doar
  reformulate): două probleme distincte găsite prin reproducere controlată, cu date sintetice.
  (1) „Serie meteo suspect de constantă" (`app/core/quality_gates.py`) se declanșa aproape la FIECARE
  prognoză PV: GHI = 0 constant pe multe ore de noapte e fizică normală, nu o sursă meteo blocată/înghețată
  — verificarea nu excludea orele de noapte (zenit solar ≥ 95°), spre deosebire de verificarea vecină
  („producție noaptea"), care o face. Corectat: constanța nopții nu mai contorizează și nu mai declanșează
  avertismentul; o constanță reală, ÎN TIMPUL ZILEI, tot semnalată (regresie testată explicit).
  (2) Corectorul ML e antrenat STRICT pe ore de zi (`masca_ore_utile`), dar la inferență corecția lui se
  aplica până acum pe TOATE orele, inclusiv noaptea — o extrapolare necontrolată în afara a ceea ce a
  văzut vreodată la antrenare. Adăugată aceeași mască, defensiv, la inferență
  (`app/core/ml_corrector.py:gaseste_si_aplica_corectie`): corecția (și cuantilele P10/P90) sunt forțate
  la 0 pe orele de noapte, indiferent ce prezice modelul — testat cu un model construit deliberat să
  „vrea" o corecție mare constant, pe orice intrare. Cea mai probabilă cauză a simptomului raportat
  („28 din 72 ore blocate de controalele de calitate") rămâne însă mismatch-ul de fus orar de mai sus:
  producția istorică importată cu fusul greșit corupe exact relația oră-locală ↔ producție pe care
  corectorul o învață, iar blocarea „producție noaptea" (R-1503) e fizică, nu un bug al verificării —
  se rezolvă la sursă reimportând cu `fus_orar_import` corect și reantrenând modelul.

## v5 — meteo (diagnostic + comparație), interval personalizabil, împachetare Firestore, fusuri orare peste tot, copie de rezervă completă, prognoză pe o lună, analiza limitărilor

### Funcționalități noi

- **Analiza limitărilor aplicației** (`docs/LIMITARI.md`, rezumat în Documentație → secțiunea 10): analiză
  completă, verificată direct în cod (nu presupusă), a tot ce ține de volum de date și scalare — scrierea
  serială SQLite, cota Firestore chiar și după împachetare, rularea zilnică secvențială, orizontul NWP de
  maximum 16 zile, pragul minim de antrenare ML, costul copiei de rezervă complete în memorie, lipsa
  autentificării, pragul orientativ de 60 de parcuri active pentru abonament Open-Meteo plătit.
- **Zonă de prognoză pentru următoarea lună** (`/parcuri/{id}/prognoza-luna`,
  `app/core/forecast_climatologie.py` + `app/web/vizualizare.py:prognoza_luna_urmatoare`): combină, zi cu
  zi, prognoza NWP reală (cât timp o acoperă cea mai recentă rulare — practic ~16 zile) cu o estimare
  CLIMATOLOGICĂ pentru restul lunii — media producției fizice calculate din vremea REALĂ (reanaliză ERA5)
  din aceleași zile calendaristice ale până la 3 ani anteriori (metoda „an meteorologic tipic"). Cele două
  surse sunt etichetate distinct pe fiecare rând, niciodată amestecate ca și cum ar fi aceeași certitudine;
  rândurile climatologice arată și un interval min-max, nu doar o medie.

- **Diagnostic import meteo** (`/parcuri/{id}/diagnostic-meteo`, `app/web/vizualizare.py:diagnostic_meteo`):
  zonă de siguranță care analizează ultimele rulări și semnalează sursele meteo cu rată de răspuns sub
  80% — răspunde direct la „au fost importate corect datele meteo?”. O rulare fără NICIO încercare către
  o sursă (rulare eșuată înainte de a apela sursele) nu e confundată cu o sursă care chiar a eșuat.
- **Interval personalizabil la Reală vs. prognoză** (`/parcuri/{id}/comparatie`): formular cu dată de
  început/sfârșit, în locul unui număr fix de zile; capetele inversate sunt corectate automat.
- **Reală vs. prognoză pentru date METEO** (`/parcuri/{id}/comparatie-meteo`,
  `app/web/vizualizare.py:serie_comparatie_meteo`): compară GHI/temperatură/vânt prognozate cu
  reanaliza ERA5 (vremea REALĂ, distinctă de rulările istorice folosite la antrenare ML), cu MAE per
  variabilă. Reanaliza e cache-uită pe zi (`app/core/weather_client.py:obtine_meteo_istoric_real_cache`),
  punând în sfârșit la treabă tabela `meteo_cache` din schemă (exista, dar nu era apelată de nimic).
- **Ziua curentă ascunsă la rulare manuală de prognoză**: motorul de prognoză exclude deja corect ziua
  LOCALĂ curentă (verificat live) — problema raportată era doar de AFIȘARE (UTC brut într-un tabel,
  oră locală în graficul de deasupra). Corectat: toate afișările de timestamp din Panou (tabelul recent,
  verdictele de calitate) arată acum ora LOCALĂ a parcului, prin filtrul Jinja nou `| local(fus_orar)`.
- **Fusuri orare extinse la import** (`app/core/timezones.py`): ~30 de fusuri europene plus UTC,
  validare server-side ce acceptă ORICE fus IANA valid. Câmpul de fus orar al parcului (zona tehnică)
  guvernează explicit ATÂT interpretarea importului CÂT ȘI granița de zi/afișarea prognozelor — gestionat
  corect la ora de iarnă/vară prin `zoneinfo` (fără offset-uri fixe hardcodate nicăieri în cod).
- **Timestamp-uri consistente în rapoarte**: foaia „Date orare” a raportului lunar are acum o coloană
  cu ora LOCALĂ (fusul ales) ȘI o coloană separată cu timestamp-ul UTC brut (trasabilitate); fusul
  afișat e inclus explicit în foaia Sumar. La descărcarea unui raport, operatorul alege fusul de afișare
  dintr-un `<select>` (implicit fusul parcului); UTC brut rămâne mereu prezent, indiferent de alegere.
- **Împachetare compactă Firestore** (`app/storage/firestore_packing.py` + rescriere
  `FirestoreRepository`): producția reală și verdictele de calitate treceau la UN DOCUMENT PER ORĂ —
  epuiza cota gratuită (20k scrieri/50k citiri pe zi) la puțin peste 2 ani de istoric orar importat
  dintr-o dată. Regrupate pe UN DOCUMENT PER ZI CALENDARISTICĂ UTC (aceeași idee ca `meteo_cache`),
  reducere de ~24× a documentelor/citirilor/scrierilor. Funcțiile de (de)împachetare sunt pure și
  testate exhaustiv offline (`tests/test_firestore_packing.py`), fiindcă `FirestoreRepository` însuși
  rămâne netestabil live în acest mediu (fără credențiale).
- **Copie de rezervă completă & restaurare** (`app/core/backup.py`, zona tehnică —
  `/tehnic/rezerva`): pe lângă copia existentă a configurării parcurilor (xlsx/csv), acum există și
  restaurarea ei („adaugă automat toate parcurile" dintr-o copie descărcată anterior — potrivire după
  COD, aditivă), plus o copie de rezervă COMPLETĂ în JSON (parcuri, clienți, producție reală, verdicte
  de calitate, rulări/prognoze, metadate modele ML, setări) descărcabilă și restaurabilă la fel.
  Restaurarea e ÎNTOTDEAUNA ADITIVĂ (upsert după cod/ID) — nu există cale, din aceste funcții, să se
  piardă date deja existente în bază. Modelele ML antrenate (fișierele `.txt`) NU sunt incluse — doar
  metadatele lor; după restaurare pe alt mediu, modelele trebuie reantrenate.

### Consistență design

- Audit al claselor CSS folosite în toate șabloanele față de `style.css`: `.grid-3` era folosită
  (comparație meteo) fără să fie definită — adăugată. Normalizat marginile duplicate cu valori diferite
  pentru același efect vizual (14px vs. 16px după un card de rezultat) la valoarea majoritară (16px).
  Unificat stilizarea ad-hoc a rândurilor „blocate” dintr-un tabel (opacitate/culoare hardcodată în două
  șabloane diferite) într-o singură clasă nouă, `.rand-blocat`, pe același tipar ca `.pastila` (variabilă
  de culoare, nu valoare hardcodată). Aliniat structura cardurilor KPI dintr-un șablon izolat la tiparul
  folosit de restul (`grid grid-N` cu `card kpi`). Fontul era deja declarat o singură dată, global.

## v4 — fusuri orare extinse la import, verificare a curățării datelor pentru ML

### Funcționalități noi

- **Fusuri orare extinse** (`app/core/timezones.py`): lista disponibilă la crearea unui parc, la
  configurarea tehnică și la import nu se mai limitează la `Europe/Bucharest`/`Europe/Chisinau` — 
  acoperă acum ~30 de fusuri din regiunea europeană (Bulgaria, Ungaria, Serbia, Grecia, Turcia,
  Ucraina, Polonia, restul UE etc.), plus UTC. Validarea de pe server merge și mai departe: acceptă
  ORICE fus orar IANA valid, nu doar cele din lista afișată în `<select>` — platforma poate deservi
  parcuri din orice zonă, lista curată e doar comoditate de interfață. Zona tehnică (fără restricții,
  R-305) afișează și fusul curent al parcului chiar dacă nu e în listă.

### Verificare (fără schimbare de comportament)

- **Excluderea din antrenarea ML a orelor marcate de controlul de calitate era deja corect
  implementată** (`app/core/ml_corrector.py`, `antreneaza_model`): `cadru.drop(de_scos)` /
  `productie.drop(de_scos)` scot efectiv acele ore din setul de antrenare ÎNAINTE de împărțirea
  cronologică antrenare/validare — nu doar le numără pentru raportare. Verificat prin citirea directă
  a codului și întărit testul existent
  (`tests/test_ml_probabilistic.py::test_orele_marcate_de_calitate_sunt_excluse_din_antrenare`):
  acum „otrăvește" valorile orelor excluse cu producție de 20× puterea instalată și verifică că
  modelul tot antrenează un skill pozitiv — dacă orele otrăvite ar mai fi folosite la antrenare,
  testul ar eșua. Toate cele 6 categorii de probleme detectate (decalaj de fus, producție noaptea,
  valori blocate, indisponibilitate, limitare, valori extreme, peste puterea instalată) sunt deja în
  `STEAGURI_EXCLUSE_DIN_ANTRENARE` — nimic nu e doar semnalat fără să fie și exclus.
- **Mediul virtual de dezvoltare era rămas pe versiuni vechi** (pvlib 0.11.1, numpy 2.1.1 etc.),
  deși `requirements.txt` fusese trecut la versiuni nefixate (v3) — asta rupea 38 de teste care
  foloseau API-ul nou al pvlib (`effective_irradiance`, `solar_azimuth`). Corectat cu
  `pip install --upgrade -r requirements.txt`; suita completă (177 teste) trece din nou.

## v3 — corecții raportate de operator, unități de afișare, comparație completă

### Corecții

- **Importul istoric (Pasul 2) nu scria niciodată în bază.** `token` era declarat ca parametru
  simplu (`token: str = ""`), pe care FastAPI îl tratează ca parametru de QUERY, nu ca un câmp din
  corpul formularului — formularul HTML trimite `token` ca `application/x-www-form-urlencoded`, deci
  parametrul rămânea mereu gol, `cale.exists()` era mereu fals, iar operatorul vedea mereu „Nu s-a
  găsit fișierul încărcat”, indiferent cât de corect era fișierul. Aceeași clasă de bug depistată
  și corectată anterior la `reconstructie`/`calibrare/aplica` — de data asta scăpată la acea trecere.
  Corectat cu `token: str = Form(default="")`; reprodus și verificat cu un test end-to-end prin
  `TestClient` înainte și după corecție.
- **Compatibilitate Starlette/FastAPI cu semnătura nouă a `TemplateResponse`.** Versiunile mai noi
  cer `TemplateResponse(request, nume, context)` explicit; cele vechi (folosite peste tot în cod)
  acceptă `TemplateResponse(nume, context)` cu request-ul în context. Adaptor central în
  `app/web/deps.py` (`CompatJinja2Templates`) — restul rutelor nu se schimbă.

### Funcționalități noi

- **Rubrica „Reală vs. prognoză”** (`/parcuri/{id}/comparatie`): producție reală, prognoză fizică,
  corecție ML, prognoză finală și **dezechilibru** (|reală − finală|) alături, oră cu oră, cu grafic
  și export vizual pe perioadă configurabilă. `app/web/vizualizare.py:serie_comparatie_completa()`.
- **Afișare în MW/MWh, cu 3 zecimale**, pe Panou, în e-mailul către client și în raportul Excel lunar
  (`app/core/formatare.py`). Calculele interne rămân în kW/kWh; zona tehnică și formatul de import
  CSV (Anexa A) rămân neschimbate, în kW/kWh.
- **Fus orar explicit la import**: pagina de import afișează fusul declarat al parcului și permite
  schimbarea lui pe loc, înainte de a încărca fișierul. Verificat: toate sursele meteo (prognoză,
  arhivă de rulări precedente, reanaliză) cer și interpretează deja STRICT UTC — corelate din start.
- **SMTP mutabil din `.env` în Setări de operare** — deci în baza de date (Firestore, după migrare):
  `app/setari.py:smtp_config()`. Rularea din GitHub Actions poate trimite rapoarte fără niciun secret
  SMTP separat în GitHub, doar cu credențiala Firestore (care rămâne, inevitabil, în afara bazei de
  date — e nevoie de ea ca să ajungi la Firestore).
- **Copie de rezervă a configurării parcurilor**, descărcabilă din Panou, în Excel și CSV
  (`app/core/backup.py`, `/rezerva/parcuri.xlsx` / `.csv`).
- **`start.sh` / `start.bat`** — pornire cu un singur pas (mediu virtual, dependențe, `.env`, server),
  în locul secvenței manuale de 4 comenzi.

### Notă despre rularea zilnică din GitHub Actions

`.github/workflows/daily-forecast.yml` și `FirestoreRepository` existau deja, pregătite structural
complet (secțiunea 16) dintr-o sesiune anterioară — nu au fost reconstruite, doar verificate și
completate cu mecanismul de SMTP de mai sus. Rămân inactive practic până la migrarea efectivă pe
Firestore (`STORAGE_BACKEND=firestore` + credențiale) — vezi README, „Migrare la Firestore”.

## v2 — audit complet, cercetare aplicată, verificare metodologică

Sesiune dedicată depanării minuțioase a întregii aplicații, cercetării asupra platformelor
comerciale / proiectelor open-source / literaturii de specialitate, și implementării concluziilor.
Detaliile complete: `docs/DEBUGGING.md`, `docs/SINTEZA-CERCETARE.md`, `docs/MODELE.md`.

### Corecții (efect direct asupra cifrelor livrate)

- **Ponderile ansamblului meteo se renormalizează pe fiecare oră** în funcție de modelele
  disponibile. Înainte, o valoare lipsă însemna tăcut o medie mai mică cu ponderea acelui model —
  până la ~33% subestimare la orizonturi lungi, unde modelele cu orizont scurt lipsesc sistematic.
- **Degradarea anuală nu mai aruncă excepție.** Orice parc cu data punerii în funcțiune completată
  eșua la fiecare rulare (`Index` nu are metoda `.clip(min=...)`).
- **Raportul lunar livrează din nou indicatori.** Formulele de nMAE și bias indicau o celulă goală;
  `IFERROR` ascundea eroarea, iar raportul ajungea la client fără niciun indicator de acuratețe.
  Biasul se calculează acum dintr-o coloană explicită de eroare cu semn, pe rândurile cu ambele valori.
- **Ștergerea unui parc curăță toate datele lui** (producție, rulări, emailuri, modele, cache,
  verdicte de calitate) și fișierele modelelor de pe disc, în ambele implementări de persistență.
- **Serializarea tolerează evoluția schemei** — câmpuri necunoscute ignorate, câmpuri lipsă pe
  implicit, enum invalid pe implicit. Fără asta, orice câmp nou făcea baza de date necitibilă.
- **Rezultatele ajung la operator**: mesajul de antrenare (activat / neactivat + skill) și numărul
  de ore importate erau calculate și aruncate.
- **Calibrarea geometriei** compară doar orele cu radiație semnificativă, exclude orele cu probleme
  de calitate, verifică pragul după toate filtrele și refolosește poziția solară (identic ca
  rezultat, semnificativ mai rapid).
- **Analiza de sensibilitate** evaluează fiecare perturbație o singură dată (era de două ori).
- **Validare la salvarea setărilor**, cu erori pe câmp în loc de pagină de eroare 500.
- **Verificări de existență** pe rutele care primeau id-uri din linkuri vechi.
- **Starea „abatere"** nu se mai declanșează pe zile acoperite parțial de prognoză.
- **Cereri meteo**: eșec rapid la erorile permanente (4xx), reîncercare doar la cele tranzitorii;
  la eșecul cererii comune se încearcă model cu model, ca o sursă picată să nu anuleze rularea.

### Funcționalități noi

- **Verificare metodologică** (`app/core/verification.py`): prognoze de referință (persistență,
  persistență inteligentă pe indicele de senin, climatologie, combinația convexă optimă), scor de
  skill, normalizare dublă declarată, excluderea orelor de noapte și a celor cu probleme de calitate,
  defalcare pe orizont, verificare probabilistică (pinball, CRPS, acoperire, ascuțime).
  Pagină dedicată în interfață + foaie nouă în raportul lunar.
- **Control al calității producției măsurate** (`app/core/data_quality.py`): detectarea decalajului
  de fus orar prin corelație cu cerul senin, producție noaptea, valori blocate, indisponibilități,
  limitări de putere, valori extreme. Rulează automat după import; orele marcate sunt excluse din
  antrenare și raportate separat. Pagină dedicată în interfață.
- **Prognoză probabilistică**: cuantile P05-P95 antrenate cu pierdere pinball, sortate pentru a evita
  încrucișarea, calibrate conform pe o fereastră nevăzută la antrenare. Afișate ca bandă în grafic,
  în tabel, în exportul CSV și verificate pe pagina de acuratețe.
- **Rezoluție de 15 minute**, comutabilă per parc, cu interpolarea radiației prin indicele de senin.
- **Ponderi de ansamblu adaptive**, deduse din performanța recentă a fiecărui model pe parcul respectiv.
- **Export CSV** al prognozei curente, în ora locală, cu benzi și orizont.
- **Vedere de portofoliu agregată**: număr de parcuri, putere instalată, energie totală pentru mâine.

### Modele fizice adăugate

- Fotovoltaic: transpoziție **Perez-Driesse** (implicit), albedo configurabil cu comutare pe albedo
  de zăpadă, **pierderi din acoperirea cu zăpadă** (NREL/Marion), **murdărire dinamică** (Kimber),
  model termic **Faiman** (calibrabil), **curbă de randament al invertorului**, modele de separare a
  radiației, impunerea închiderii fizice GHI = DHI + DNI·cos θz, topologie de invertoare declarată.
- Eolian: **profil de tip lege a puterii** cu exponent dedus din 10 m/100 m, **densitate a aerului cu
  umiditate** (IEC 61400-12-1), corecție de densitate pe viteză sau pe putere după tipul de reglaj,
  **netezirea curbei de putere cu intensitatea turbulenței**, **siaj dependent de viteză**.
- ML: țintă normalizată la cerul senin, intrări extinse (decalaje temporale, diferențe, nori pe
  straturi, umiditate, direcția vântului ca sin/cos, exponent de forfecare, dispersie), excluderea
  orelor cu probleme de calitate, promovare pe scor de skill.

### Teste

De la 89 la **173 de teste**, toate offline și deterministe (~12 s). Module noi acoperite:
verificare, calitatea datelor, persistență, raport (referințe de celule), rezoluție sub-orară,
strat probabilistic, fizică PV v2.
