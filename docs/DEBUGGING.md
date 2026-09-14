# Audit complet al aplicației — probleme găsite și corectate

Documentul acoperă auditul linie cu linie al întregii aplicații: motoarele fizice, stratul ML,
persistența, interfața web și scripturile. Fiecare problemă are: unde era, ce efect avea în
practică, de ce nu se vedea, cum a fost corectată și ce test o împiedică să reapară.

Împărțirea pe severități e după **efectul asupra cifrelor livrate clientului**, nu după cât de
greu a fost de găsit.

---

## Rezumat

| # | Problemă | Severitate | Efect practic |
|---|---|---|---|
| A1 | Ponderile ansamblului meteo nu se renormalizau când un model avea valori lipsă | **Critică** | Producție subestimată tăcut cu până la ~33% la orizonturi lungi |
| A2 | Degradarea anuală arunca excepție pe orice parc cu dată de punere în funcțiune | **Critică** | Prognoza eșua complet pentru acele parcuri |
| A3 | Formulele de acuratețe din raportul lunar trimiteau spre celula greșită | **Critică** | nMAE și bias apăreau GOALE în raportul livrat clientului |
| A4 | Biasul din raport amesteca două mulțimi diferite de rânduri | Majoră | Bias eronat când prognoza și realizatul nu se suprapun perfect |
| A5 | Metodologia de verificare (nopți incluse, orizonturi amestecate, fără referință) | Majoră | Acuratețea părea sistematic mai bună decât era |
| A6 | Rezultatul antrenării ML era calculat și aruncat | Majoră | Operatorul nu afla niciodată dacă modelul a fost activat |
| A7 | Numărul de ore importate era calculat și aruncat | Medie | Import „finalizat" fără nicio cifră |
| A8 | Ștergerea unui parc nu ștergea datele lui | Majoră | Date orfane permanente: producție, rulări, modele, fișiere pe disc |
| A9 | Calibrarea geometriei media eroarea peste nopți | Majoră | Semnal de calibrare diluat de sute de ore fără informație |
| A10 | Calibrarea verifica pragul de ore înainte de intersecția cu datele meteo | Medie | Putea rula pe 100 de ore crezând că are 5000 |
| A11 | Analiza de sensibilitate calcula fiecare perturbație de două ori | Medie | Timp de execuție dublu, pe un an de date orare |
| A12 | Salvarea setărilor nu valida nimic | Medie | O literă greșită → pagină de eroare 500, formular pierdut |
| A13 | Rute fără verificare de existență | Medie | Linkuri vechi → eroare 500 în loc de redirecționare |
| A14 | Starea „abatere" se declanșa pe zile acoperite parțial | Medie | Alarme false în portofoliu |
| A15 | Serializarea nu tolera câmpuri necunoscute/lipsă | Majoră (latentă) | Baza de date devenea necitibilă la orice schimbare de schemă |
| A16 | Comparații de interval pe text ISO cu format inconsistent | Mică | Înregistrarea de la limita intervalului putea fi omisă |
| A17 | Reîncercarea cererilor meteo trata erorile permanente ca tranzitorii | Mică | ~31 s pierduți înainte de a raporta un id de model greșit |
| A18 | O sursă meteo picată anula toată rularea | Medie | Fragilitate inutilă înaintea unui termen de piață |
| A19 | Controalele de calitate presupuneau pas orar | Medie (la 15 min) | Energia zilnică ar fi ieșit de 4× |
| B1 | *(introdusă în v2, prinsă de teste)* Filtru Hampel pe putere brută PV | Majoră | 337 ore normale marcate fals ca extreme |
| B2 | *(introdusă în v2, prinsă de teste)* Presiunea lipsea din maparea eoliană | Medie | Corecția de densitate ar fi căzut pe atmosfera standard |
| B3 | *(introdusă în v2, prinsă de teste)* Două etichete identice în raport | Mică | „nMAE (%)" de două ori, fără să se știe la ce se raportează |
| C1-C6 | Cod mort, importuri nefolosite, f-string fără substituții | Cosmetică | Zgomot la citire |

---

## A1. Ponderile ansamblului meteo nu se renormalizau — **cea mai gravă eroare tăcută**

**Unde:** `app/core/weather_client.py`, `combina_ansamblu`.

**Codul vechi:**
```python
serii = [pe_model[m][col] * p[m] for m in modele if col in pe_model[m].columns]
medii[col] = pd.concat(serii, axis=1).sum(axis=1, min_count=1)
```

Ponderile `p[m]` sunt normalizate o singură dată, la început, pe lista de modele care au răspuns.
Media ponderată corectă e `Σ(valoare·pondere) / Σ(pondere)`. Aici se calcula doar numărătorul,
presupunând implicit că numitorul e 1 — adevărat **numai dacă toate modelele au valoare la acea oră**.

**Efectul practic.** Când un model nu avea valoare la o oră, contribuția lui devenea zero, dar
ponderea lui rămânea „cheltuită". Cu trei modele egale și unul lipsă, media ieșea cu ~33% mai mică.

Cazul nu e rar — e **sistematic**: modelele numerice au orizonturi diferite (ICON ~7,5 zile, GFS 16,
ECMWF 15). La un orizont cerut de 10-16 zile, ultimele zile rămâneau acoperite de mai puține modele,
deci producția prognozată scădea artificial exact acolo. Nu apărea nicio eroare, niciun avertisment:
doar cifre mai mici.

**Corecția.** Ponderile se recalculează pe fiecare oră, în funcție de modelele disponibile la acea oră:

```python
disponibil = valori.notna()
ponderi_efective = disponibil.mul(ponderi_col, axis=1)
medie = (valori.fillna(0) * ponderi_efective).sum(axis=1) / ponderi_efective.sum(axis=1)
```

Suplimentar: dispersia cu un singur model disponibil e acum `0`, nu `NaN` (NaN s-ar fi propagat
în intrările modelului ML), iar `acoperire_per_model()` raportează în trasabilitate ce fracțiune
din orizont a acoperit fiecare sursă, ca acoperirea parțială să fie **vizibilă**, nu tăcută.

**Teste:** `tests/test_weather_client.py::test_REGRESIE_model_lipsa_pe_o_ora_nu_scade_media`,
`::test_dispersia_cu_un_singur_model_este_zero_nu_nan`, `::test_acoperire_per_model`.

---

## A2. Degradarea anuală arunca excepție — **prognoza eșua complet**

**Unde:** `app/core/pv_model.py`, `_factor_degradare`.

```python
ani = (momente - punere).total_seconds() / (365.25 * 24 * 3600)
ani = ani.clip(min=0)      # AttributeError: 'Index' object has no attribute 'clip'
```

`momente` e un `DatetimeIndex`; scăderea dă `TimedeltaIndex`, iar `.total_seconds()` întoarce un
`Index` de numere. Spre deosebire de `Series`, `Index` **nu are metoda `.clip(min=...)`**.

**Efectul practic.** Orice parc cu `data_punerii_in_functiune` completată arunca `AttributeError`
la **fiecare** rulare de prognoză. Parcul apărea permanent ca „eșuat".

**De ce nu s-a văzut înainte:** câmpul e opțional și nu e expus în formularul simplificat de
adăugare a unui parc — doar în configurarea completă din Modul tehnic. Toate testele manuale
anterioare folosiseră parcuri fără dată de punere în funcțiune.

**Corecția:** `ani = np.clip(ani, 0, None)`.

**Test:** `tests/test_pv_model.py::test_degradare_anuala_reduce_productia` (acum exercită explicit
calea cu dată de punere în funcțiune).

---

## A3 și A4. Raportul lunar livra indicatori goi sau greșiți

**Unde:** `app/core/reports.py`.

**A3 — referință de celulă greșită.** Etichetele se scriau în coloana A și valorile în coloana B,
dar formulele împărțeau la `C3`:

```python
("Eroare medie absolută normalizată — nMAE (%)",
 f"=IFERROR(AVERAGE('Date orare'!D2:D{n+1})/C3*100,\"\")"),
```

`C3` e goală → împărțire la zero → `IFERROR` prinde eroarea → celula rămâne **goală**.
Raportul lunar trimis clientului nu conținea, practic, niciun indicator de acuratețe — iar
`IFERROR`, pus ca protecție, e exact ce a ascuns problema.

**A4 — mulțimi diferite de rânduri.** Biasul era:

```
(SUM(prognoza) - SUM(realizat)) / COUNT(realizat) / capacitate
```

`SUM(prognoza)` include și orele fără realizat, dar se împarte la numărul de ore **cu** realizat.
Când cele două serii nu se suprapun perfect (situația normală la început de lună sau după o
întrerupere de date), biasul iese greșit, cu un semn care poate fi chiar inversat.

**Corecția.** O coloană explicită de **eroare cu semn**, definită prin formulă doar pe rândurile
cu ambele valori, și toți indicatorii calculați din ea. Referințele absolute (`$B$4`) indică baza
de normalizare. În plus, raportul are acum o foaie **Verificare** separată, cu indicatorii calculați
metodologic corect (vezi A5) și cu scorul de skill.

**Teste:** `tests/test_reports.py::test_REGRESIE_formulele_trimit_spre_celula_corecta` verifică
**structural** că fiecare formulă indică celula care chiar conține baza de normalizare — adică
exact clasa de eroare care a scăpat, nu doar instanța ei.

---

## A5. Metodologia de verificare făcea acuratețea să pară mai bună decât era

**Unde:** `app/web/vizualizare.py` (`indicatori_parc`), `app/core/reports.py`.

Trei probleme independente, toate în aceeași direcție — a flata rezultatul:

1. **Orele de noapte intrau în medie.** La un parc fotovoltaic, ~50% din ore au producție zero,
   pe care modelul o „prezice" perfect. Ele trag media erorii în jos fără să spună nimic despre
   calitatea prognozei. Practica standard (consensul Yang et al. 2020) e filtrarea la orele de zi.

2. **Orizonturile se amestecau.** Se lua „cea mai recentă prognoză disponibilă" pentru fiecare oră.
   O valoare emisă cu 2 ore înainte e mult mai precisă decât una emisă cu 36 de ore înainte;
   amestecul lor arată o acuratețe pe care livrarea reală (cu o zi înainte) nu o are.

3. **Nu exista nicio referință.** Un nMAE de 6% e bun sau prost? Fără comparație cu o regulă simplă
   („mâine cerul va fi ca azi"), cifra nu se poate interpreta — și nu se poate ști dacă tot lanțul
   fizic + ML aduce ceva peste persistență.

**Corecția.** Un modul nou, `app/core/verification.py`, care implementează:
- filtrul de ore utile, exprimat prin puterea de cer senin a parcului (ține cont și de orientare,
  și de umbrirea de orizont, nu doar de unghiul solar);
- excluderea orelor marcate de controlul de calitate (avarii, limitări) — ele măsoară starea
  parcului, nu calitatea modelului;
- prognoze de referință explicite: persistență, persistență **inteligentă** (se persistă indicele
  de senin, nu puterea), climatologie și combinația convexă optimă;
- scorul de skill `s = 1 − RMSE(prognoză)/RMSE(referință)`;
- indicatori separați pe orizont (0-6 h, 6-24 h, 24-48 h, 48+ h);
- ambele convenții de normalizare, declarate explicit;
- verificare probabilistică: pinball, CRPS, acoperire empirică, ascuțime.

**Teste:** `tests/test_verification.py` (14 teste), inclusiv verificarea că includerea nopților
chiar face indicatorul artificial de bun și că un model mai slab decât referința primește skill negativ.

---

## A6 și A7. Rezultate calculate și aruncate

**Unde:** `app/web/routes_panou.py`.

```python
info = antreneaza_model(...)
mesaj = ("Model ACTIVAT — " if info.activ else "Model antrenat, dar NU a fost activat...") + info.note
...
return RedirectResponse(f"/parcuri/{parc_id}?actiune=antrenare" + (f"&eroare={eroare}" if eroare else ""))
```

Variabila `mesaj` nu era folosită nicăieri. Operatorul apăsa „Antrenează model ML", aștepta, și
primea un mesaj generic — fără să afle dacă modelul a fost activat, cât de bun e, sau de ce nu a fost
activat. Exact informația care justifică existența mecanismului campion/provocator (R-605) era produsă
și aruncată. Identic la import: numărul de ore importate se calcula și se pierdea.

**Corecția.** Mesajele ajung în interfață (parametru de interogare, afișat pe pagina parcului), iar
lista ultimelor modele antrenate — cu stare activ/inactiv, MAE, skill și note — e afișată permanent
pe pagina parcului. După import se rulează automat și controlul de calitate, iar rezumatul lui intră
în același mesaj.

---

## A8. Ștergerea unui parc lăsa datele în urmă

**Unde:** `app/storage/sqlite_repo.py`, `app/storage/firestore_repo.py`, `app/web/routes_tehnic.py`.

`sterge_parc` executa doar `DELETE FROM parcuri WHERE id=?`. Rămâneau: orele de producție importate,
toate rulările, încercările de email, modelele ML (în bază **și** ca fișiere pe disc) și cache-ul meteo.

**Efectul practic.** Jurnalul afișa rulări ale unor parcuri care nu mai există (căderea pe id brut),
baza de date creștea la nesfârșit, iar fișierele modelelor rămâneau orfane permanent.

**Corecția.** Ștergere în cascadă pe toate tabelele, în ambele implementări de persistență, plus
ștergerea folderului cu modelele parcului. Firestore nu are cascadă nativă, deci subcolecțiile se
parcurg explicit, în loturi.

**Teste:** `tests/test_storage.py::test_stergerea_parcului_curata_toate_datele_derivate` și
`::test_stergerea_unui_parc_nu_atinge_alt_parc`.

---

## A9 și A10. Calibrarea geometriei lucra pe un semnal diluat

**Unde:** `app/core/calibration.py`.

Eroarea medie se calcula peste **toate** orele comune, inclusiv nopțile. Pentru orice combinație de
înclinare/azimut, noaptea producția e zero — deci sute de ore identice, care nu deosebesc geometriile,
dar intră în medie și apropie artificial scorurile între ele. Rezoluția reală a căutării era mult mai
slabă decât părea.

Suplimentar (A10), pragul minim de ore se verifica pe producția totală, **înainte** de intersecția cu
datele meteo. Se putea intra în calibrare cu 5000 de ore de producție și doar 100 cu meteo corespondent.

**Corecția.** Se compară doar orele cu radiație semnificativă (GHI > 50 W/m²), se exclud orele marcate
de controlul de calitate, iar pragul se verifică **după** toate filtrele. Se raportează și RMSE și
biasul per combinație, plus configurația curentă, ca operatorul să vadă cât de departe e de optim.

**Bonus de performanță.** Poziția solară se calcula din nou pentru fiecare din cele ~100 de combinații,
deși depinde doar de timp și locație. Acum se calculează o dată și se transmite (`poz_solara=`).
Rezultatul e identic — verificat prin test — dar calibrarea e semnificativ mai rapidă.

---

## A11. Analiza de sensibilitate rula fiecare perturbație de două ori

**Unde:** `app/core/sensitivity.py`.

```python
"energie_anuala_modificata_kwh": round(_energie_anuala_pv(cfg, parc, meteo_pv), 0),
"variatie_pct": round((_energie_anuala_pv(cfg, parc, meteo_pv) - baza) / baza * 100, 2)
```

Același apel, de două ori, pentru fiecare din ~13 perturbații, pe un an întreg de date orare.
Timpul de execuție era exact dublu față de necesar, pe o operație pe care operatorul o așteaptă în
interfață. Corectat prin evaluare unică + poziție solară precalculată.

---

## A12 și A13. Erori de operare afișate ca pagină de eroare 500

**A12 — setările.** `setari_salveaza` apela direct `setari_mod.set(...)`, care face `int(valoare)`.
O valoare nenumerică (sau o oră scrisă „8:0") arunca `ValueError` → pagina generică de eroare.
Operatorul nu afla **ce câmp** era greșit și pierdea tot formularul.
Acum fiecare setare e validată separat, valorile corecte se salvează, iar cele greșite sunt
raportate pe câmp, cu formularul păstrat.

**A13 — rute fără verificare de existență.** `parc_config_salveaza`, `senzitivitate_*`,
`trasabilitate` foloseau direct rezultatul lui `get_parc()` / `get_rulare()`. Un link vechi (parc
șters) producea `AttributeError` → 500. Acum redirecționează curat.

---

## A14. Starea „abatere" se aprindea pe zile acoperite parțial

**Unde:** `app/web/vizualizare.py`, `rezumat_portofoliu_parc`.

Se lua prima rulare care conținea **măcar o oră** din ziua precedentă și se compara suma ei cu
producția reală a întregii zile. O rulare care acoperea 6 din 24 de ore arăta ca o subestimare de 75%
și marca parcul cu „abatere" — o alarmă falsă, exact pe ecranul de ansamblu unde operatorul se uită
primul. Acum se cere o acoperire de minimum 75% din zi, altfel comparația nu se face.

În plus, energia se calculează acum ținând cont de pasul seriei: la 15 minute, suma puterilor **nu**
e energie fără scalare.

---

## A15. Serializarea nu tolera evoluția schemei

**Unde:** `app/storage/serialize.py`.

`Parc(**d)` aruncă `TypeError` dacă dicționarul conține o cheie care nu e câmp al dataclass-ului.
Concret: dacă baza de date fusese scrisă de o versiune mai nouă (sau dacă un câmp e eliminat între
versiuni), parcurile devin necitibile — baza de date pare **coruptă**, deși nu e.

Problema era latentă, dar devenea sigură în momentul în care se adaugă orice câmp nou — adică exact
ce a urmat în v2 (peste 25 de câmpuri noi de configurare).

**Corecția.** Toate construcțiile din JSON trec prin `_doar_campuri_cunoscute`: cheile necunoscute se
ignoră, cele lipsă cad pe implicitele dataclass-ului, iar valorile de enum invalide cad pe implicit
în loc să arunce. Astfel parcurile create înainte de v2 funcționează neschimbat, iar o eventuală
revenire la o versiune anterioară nu distruge baza.

**Teste:** `tests/test_storage.py` — câmpuri din viitor, câmpuri lipsă, enum invalid, dus-întors JSON.

---

## A16 – A19. Probleme de robustețe

**A16 — comparații de interval pe text ISO.** Momentele sunt stocate ca text și comparate ca text
(`BETWEEN` în SQL). Comparația lexicografică e corectă doar dacă formatul e identic peste tot, dar
`datetime.now(timezone.utc).isoformat()` produce microsecunde, iar momentele salvate din pandas nu.
Lexicografic `'+' < '.'`, deci o graniță de început cu microsecunde exclude tăcut înregistrarea de la
exact acel moment. Corectat printr-un formator canonic (`app/core/momente.py`).

**A17 — reîncercări inutile.** `_cerere_cu_reincercare` prindea `httpx.HTTPError`, care include și
erorile de stare HTTP. Un id de model greșit (400) era reîncercat de 5 ori, cu așteptare progresivă:
~31 de secunde până la un mesaj de eroare care se cunoștea de la prima încercare. Acum erorile 4xx
(în afară de 429) sunt raportate imediat, cu textul explicativ întors de sursă.

**A18 — o sursă picată anula tot.** Toate modelele se cereau într-o singură interogare; dacă aceasta
eșua, rularea se oprea. Acum, la eșecul cererii comune, se încearcă model cu model și se păstrează ce
răspunde — o singură sursă indisponibilă nu mai anulează livrarea.

**A19 — pasul seriei presupus orar.** La trecerea la 15 minute, două controale de calitate ar fi
greșit: energia zilnică (suma puterilor nu e energie la pas sub-orar → ar fi ieșit de 4× peste plafon,
declanșând avertismente pe fiecare oră) și pragul de „serie constantă", exprimat de operator în ore,
nu în pași. Ambele primesc acum pasul explicit.

---

## B1 – B3. Probleme introduse în v2 și prinse de teste

Le documentez la fel de explicit ca pe celelalte: sunt dovada că suita de teste își face treaba.

**B1 — filtrul Hampel aplicat pe putere brută.** Filtrul de outlieri presupune o serie aproximativ
staționară. Aplicat direct pe puterea fotovoltaică — care parcurge zilnic 0 → vârf → 0 — a marcat
**337 de ore normale** dintr-un set de 1464 (23%!) ca „valori extreme", eliminându-le din antrenare.
Corectat prin aplicarea filtrului pe **indicele de senin** (raportul față de producția de cer senin),
care nu are ciclu zi-noapte, plus un prag absolut minim. După corecție: 3 ore marcate în loc de 337,
iar setul de antrenare a crescut de la 396 la 680 de ore.

**B2 — presiunea lipsea din maparea eoliană.** La restructurarea modulului `meteo_mapping`, presiunea
la sol a ajuns în lista variabilelor suplimentare pentru fotovoltaic, dar a fost omisă din cea eoliană
— unde e chiar intrarea corecției de densitate a aerului. Ar fi căzut tăcut pe atmosfera standard.
Prins imediat la recitire, înainte de a rula.

**B3 — etichete duplicate în raport.** Foaia „Sumar" avea două rânduri etichetate identic
„Eroare medie absolută — nMAE (%)" — unul normalizat la capacitate, celălalt la producția medie.
Prins de un test care verifica referințele de celulă și a dat peste ambiguitate. Etichetele spun acum
explicit baza de normalizare, iar un test dedicat interzice etichetele duplicate.

---

## C. Curățenie

- Importuri nefolosite în `models.py`, `routes_tehnic.py`, `routes_panou.py`, `calibration.py`,
  `ml_corrector.py`, plus în teste.
- Variabilă locală nefolosită (`n` în `pv_model.py`, `tz` în `vizualizare.py`).
- `f"F2"` și `f"?actiune=antrenare"` — f-string-uri fără substituții.
- Cod mort în `routes_panou.py` (`if repo.get_parc.__self__ and ...`) — corectat anterior.

---

## Ce NU am schimbat, deliberat

- **Clipping-ul pe suma puterilor DC rămâne implicit** (R-412). O analiză din literatură îl semnala
  ca posibilă eroare, dar specificația e explicită: subansamblurile împart același invertor. Am
  adăugat în schimb o opțiune declarată (`clipping_per_subansamblu`) pentru topologia cu invertoare
  separate — topologia trebuie declarată, nu ghicită.
- **Trimiterea emailului nu are deduplicare pe zi.** „Un singur email pe zi" (R-1001) e asigurat
  structural: doar rularea programată trimite automat, iar ea rulează o dată pe zi. Trimiterea manuală
  rămâne o acțiune deliberată a operatorului; nu o blochez.
- **Nu am migrat pe `pvlib.ModelChain`.** Lanțul are semantici intenționat diferite de implicitele
  bibliotecii (clipping pe sumă, stivă proprie de pierderi, poziție solară la mijlocul intervalului).
  Folosim funcțiile pvlib, nu orchestrarea ei.

---

## Starea suitei de teste

| Înainte de audit | După |
|---|---|
| 89 de teste | **173 de teste** |
| Un test depindea de rețea | Toate rulează offline, determinist |
| ~70 s | ~12 s |

Module noi acoperite: verificare, calitatea datelor, persistență (cascadă + evoluția schemei),
raport (referințe de celule), rezoluție sub-orară, stratul probabilistic, fizica PV v2.
