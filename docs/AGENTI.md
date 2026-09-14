# Agenți AI pentru această platformă — unde ajută cu adevărat

Notă de onestitate de la început: un agent e util acolo unde **verificarea e
ieftină și munca e repetitivă**. E riscant acolo unde greșeala e tăcută și
costisitoare. Platforma asta are ambele feluri de sarcini, așa că ideile de mai
jos sunt ordonate după raportul câștig/risc, nu după cât de impresionant sună.

---

## Categoria 1 — Descoperirea erorilor (cel mai bun raport câștig/risc)

Aici agentul propune, iar **testele decid**. Dacă propunerea e greșită, suita o
respinge; costul unei greșeli e aproape zero.

### 1.1 Vânătoarea de regresii de performanță, automat

Aveți deja `scripts/benchmark_scalare.py`. Un agent poate:

- să-l ruleze după fiecare set de modificări;
- să compare cu ultima măsurătoare salvată;
- să investigheze singur, cu `cProfile`, orice operație care s-a încetinit cu
  peste ~20%, și să vă spună exact ce funcție a crescut.

Exact așa au fost găsite cele două probleme majore din v8: pagina de portofoliu
(12,5 s) și ponderile adaptive (jumătate din durata unei rulări). Niciuna nu era
vizibilă la 3 parcuri de test — au apărut doar la 50.

**Cum cereți:** „Rulează benchmark-ul de scalare, compară cu ultimul rezultat
din docs/, și dacă ceva s-a încetinit cu peste 20%, profilează și spune-mi ce
funcție e de vină."

### 1.2 Teste pentru cazurile de la margine, generate sistematic

Punctele slabe ale unei platforme de prognoză sunt aproape întotdeauna aceleași
și se pot enumera: schimbarea orei (primăvara și toamna), ani bisecți, parcuri
la longitudini extreme, zile polare, serii cu goluri, valori negative, un singur
punct de date, producție zero o săptămână întreagă, un model meteo care răspunde
pe jumătate din orizont.

Un agent poate genera metodic teste pentru fiecare combinație și vă poate arăta
doar pe cele care pică. Dumneavoastră decideți apoi care „picare" e un bug real
și care e o așteptare greșită a testului.

**Cum cereți:** „Scrie teste pentru comportamentul la tranziția de oră de vară,
pentru fiecare din: import CSV, granița de zi la prognoză, gruparea pe zi în
rapoarte, calculul de energie zilnică. Arată-mi doar ce pică."

### 1.3 Verificarea încrucișată a fizicii cu o implementare independentă

Motorul fotovoltaic e cel mai greu de verificat prin simplă citire a codului. Un
agent poate scrie, separat, o implementare naivă și independentă (direct din
pvlib, fără straturile dumneavoastră) și poate compara rezultatele pe sute de
configurații generate aleator. Nu contează că versiunea naivă e mai lentă —
contează că e scrisă *altfel*. Acolo unde cele două diverg peste o toleranță,
aveți un candidat serios de bug.

Asta a funcționat deja o dată în acest proiect: comparația cu scriptul extern de
calibrare a scos la iveală confundarea scării cu geometria.

### 1.4 Citirea critică a avertismentelor, nu doar producerea lor

Odată pe lună, un agent poate lua jurnalul de rulări și avertismentele
controlului de calitate și poate căuta *tipare*: un avertisment care apare la
toate parcurile e aproape sigur un prag prost calibrat, nu 50 de probleme reale.
Un avertisment care apare doar la un parc, dar în fiecare zi, e o problemă de
configurare a acelui parc.

Distincția asta e exact ce s-a găsit la avertismentul „serie meteo suspect de
constantă": se declanșa aproape la fiecare prognoză PV, pentru că nu excludea
orele de noapte. Un avertisment care apare mereu nu mai e un avertisment — e
zgomot care ascunde problemele reale.

---

## Categoria 2 — Munca repetitivă de întreținere

### 2.1 Triajul zilnic al rulărilor

În loc să deschideți jurnalul în fiecare dimineață, un agent programat poate
citi rezultatul rulării de noapte și să vă trimită un rezumat de trei rânduri:
câte parcuri au mers, care au eșuat și de ce, ce s-a schimbat față de ieri. Vă
scrie doar când chiar e ceva de făcut.

Important: agentul citește și raportează, **nu repară singur** prognozele. O
prognoză „reparată" automat e o prognoză în care nu mai aveți încredere.

### 2.2 Pregătirea configurării unui parc nou

Adăugarea unui parc înseamnă zeci de câmpuri, dintre care multe se pot deduce
din fișa tehnică a instalației. Un agent poate citi fișa (PDF, Excel, e-mail de
la client), poate completa un draft de configurare și poate marca explicit ce a
dedus și ce a ghicit. Dumneavoastră verificați doar câmpurile marcate.

Pași care merită automatizați aici: verificarea că lat/lon chiar cad pe
amplasamentul declarat, verificarea că puterea AC/DC are un raport plauzibil
(1,1–1,3), verificarea că înclinarea/azimutul sunt rezonabile pentru latitudine.

### 2.3 Curățarea fișierelor de import înainte de încărcare

Acum că formatul de bază cere decalaj de fus orar explicit, multe exporturi
SCADA vor avea nevoie de o conversie. E exact genul de muncă mecanică pe care un
agent o face bine și pe care o puteți verifica ieftin: numărul de linii trebuie
să rămână același, iar suma energiei trebuie să fie identică. Două verificări
simple prind aproape orice greșeală de conversie.

**Cum cereți:** „Convertește fișierul ăsta la formatul cu decalaj explicit,
știind că orele sunt ora României. Verifică apoi că numărul de linii și suma
kWh sunt neschimbate față de original."

### 2.4 Verificarea documentației față de cod

Documentația îmbătrânește tăcut. Un agent poate parcurge periodic `docs/` și
comentariile din cod și poate semnala afirmațiile care nu mai corespund:
praguri schimbate, fișiere redenumite, comportamente descrise care nu mai există.

---

## Categoria 3 — Analiză, cu prudență

### 3.1 Investigarea unui parc cu acuratețe slabă

Când un parc are nMAE mult mai prost decât restul, întrebările sunt mereu
aceleași: e geometria greșită? e fusul de import greșit? e o defecțiune reală a
parcului confundată cu eroare de prognoză? modelul ML e antrenat pe date
murdare? sursa meteo acoperă prost zona?

Un agent poate parcurge lista sistematic, cu datele la îndemână, și vă poate
aduce o ipoteză principală plus dovezile. Decizia rămâne a dumneavoastră —
dar cele două ore de căutat prin ecrane le-a făcut altcineva.

### 3.2 Ce NU merită dat pe mâna unui agent

**Alegerea pragurilor care blochează ore.** Un prag prea strict aruncă producție
bună; unul prea larg lasă să treacă erori. E o decizie de business (cât vă costă
o prognoză greșită față de o prognoză lipsă), nu una tehnică.

**Modificarea automată a fizicii ca să iasă indicatorii mai bine.** Asta e
supraajustare pe istoric, iar rezultatul arată excelent în rapoarte și prost în
realitate. Fizica se schimbă doar pe baza unui argument fizic.

**Ștergerea de date.** Retenția trebuie să rămână o apăsare de buton conștientă.
Un agent poate să vă *spună* că e momentul, niciodată să apese el.

**Trimiterea rapoartelor către clienți fără verificare.** Modul umbră există
tocmai pentru că un raport greșit trimis nu se mai poate retrage.

---

## Cum să lucrați practic cu un agent pe acest cod

**Dați-i contextul care contează, nu tot codul.** Fișierul potrivit plus
constrângerea reală bat un „uită-te în tot proiectul". De exemplu: „în
`vizualizare.py`, funcția asta trebuie să rămână corectă la rezoluție de 15
minute" e mai util decât o descriere lungă a platformei.

**Cereți-i să măsoare, nu să estimeze.** „Cât de încet e X?" primește un răspuns
plauzibil și posibil fals. „Rulează benchmark-ul și arată-mi cifrele" primește
un răspuns verificabil. Toate îmbunătățirile din v8 au pornit de la măsurători,
iar două dintre ele au contrazis intuiția.

**Cereți-i testul odată cu modificarea.** Un test care pică înainte și trece
după e dovada că modificarea face ce spune. Fără el, aveți doar o afirmație.

**Lăsați-l să spună „nu știu".** Dacă întrebați „e o problemă aici?", veți primi
adesea o problemă, inventată dacă e nevoie. Întrebați mai bine „e ceva în
neregulă aici, și dacă da, cum dovedești?".

**Verificați ce a făcut, nu ce spune că a făcut.** `git diff` și suita de teste
sunt singura sursă de adevăr. Rezumatul e doar intenția declarată.

---

## Un plan rezonabil de pornire

Dacă vreți să începeți cu ceva concret, în ordinea asta:

1. **Triajul zilnic al rulărilor** (2.1) — cel mai mic risc, cel mai clar
   câștig zilnic.
2. **Benchmark-ul de scalare la fiecare modificare** (1.1) — vă protejează de
   exact genul de problemă care apare doar la 50 de parcuri.
3. **Conversia fișierelor de import** (2.3) — muncă mecanică, verificare
   banală.
4. **Testele pentru cazurile de margine** (1.2) — investiție care se plătește
   la fiecare actualizare ulterioară.

Restul merită abia după ce primele patru vă sunt firești.
