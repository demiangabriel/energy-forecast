# Sinteza cercetării: ce am comparat, ce am adoptat, ce am respins

Documentul rezumă în română cele trei direcții de cercetare (platforme comerciale, proiecte
open-source, literatură științifică) și — mai important — **decizia luată pentru fiecare
constatare**, cu motivul ei. Rapoartele integrale, cu surse și linkuri, sunt în `docs/cercetare/`.

---

## 1. Unde se situa platforma față de piață

Concluzia comună a celor trei direcții de cercetare a fost aceeași, și e utilă tocmai pentru că e
neașteptată:

> **Lanțul fizic era deja competitiv. Lipsurile nu erau în fizică, ci în felul în care se măsura
> rezultatul, în igiena datelor de intrare și în alinierea la ceasul pieței.**

Modelarea fizică (pvlib cu transpoziție anizotropă, IAM fizic, model termic SAPM, limitare pe suma
puterilor DC, corecție IEC de densitate) e la nivelul sau peste ce documentează public mai mulți
furnizori comerciali. În schimb:

- nu exista niciun **scor de skill** — deci nu se putea spune dacă modelul aduce ceva peste o regulă
  simplă;
- indicatorii se calculau într-un mod care îi făcea **sistematic mai buni decât realitatea**;
- orele de **avarie și limitare de putere** intrau în antrenare, unde modelul învăța defecțiuni;
- livrarea era **doar orară**, deși piața decontează la 15 minute;
- nu exista **prognoză probabilistică**, prezentă la toți concurenții serioși.

---

## 2. Ce oferă piața (și ce am preluat)

Furnizorii comparați: Solcast, Solargis, Steadysun, ENFOR, energy & meteo systems, Meteologica, DNV,
UL Solutions, plus jucătorii adiacenți de analiză a activelor (Enlitia, Kaiserwetter, GreenPowerMonitor).

| Ce oferă piața | Decizia noastră |
|---|---|
| Cuantile P10/P50/P90 în livrarea standard | **Adoptat.** Șapte niveluri, antrenate cu pierdere pinball, plus calibrare conformă. |
| Scor de skill față de o referință, ca indicator contractual | **Adoptat.** Referința e combinația convexă persistență/climatologie, în spațiul indicelui de senin. |
| Normalizare declarată (capacitate vs. producție medie) | **Adoptat.** Ambele, etichetate explicit, în interfață și în raportul Excel. |
| Tratarea explicită a limitărilor de putere (curtailment) | **Adoptat** parțial: detecție euristică, excludere din antrenare și din indicatori, raportare separată. *Nerealizat:* prognoza „producție tehnic posibilă" ca livrabil distinct. |
| Detecția de indisponibilitate / subperformanță | **Adoptat.** E și o constatare vandabilă către client, nu doar igienă de date. |
| Ponderi de ansamblu adaptate performanței (meta-prognoză) | **Adoptat**, cu prudență: amestec 50/50 cu ponderile egale și limită inferioară per model. |
| Rezoluție de 15 minute | **Adoptat.** Comutabil per parc. |
| Portofoliu agregat | **Adoptat** la nivel de energie. Incertitudinea agregată corect (corelații între parcuri) rămâne pentru mai târziu — însumarea naivă a benzilor ar fi greșită. |
| Nowcasting din satelit (0-6 h) | **Respins deocamdată.** Necesar doar dacă se vinde intraday; e un proiect în sine (ingestie EUMETSAT), nu o funcționalitate. |
| Portal de autoservire pentru clienți | **Respins deliberat.** Modelul de business e livrarea prin raport; nu e o lipsă, e o alegere. |

### Ceasul pieței din România (constrângere de operare)

Piața pentru ziua următoare (OPCOM/PZU) se închide la **11:00**; notificările fizice către
Transelectrica se depun până la **16:30** în ziua precedentă; decontarea e la **15 minute**, iar
cuplarea europeană a trecut la intervale de 15 minute din **octombrie 2025**.

Consecința practică, documentată în aplicație: rularea zilnică trebuie să se încheie până la
**09:00-10:00**, altfel prognoza nu mai poate fi folosită pentru ziua următoare. (Cifrele provin din
procedurile publice; revizia documentului Transelectrica consultat era din 2021 — merită reverificată
înainte de a fi tratată ca obligație contractuală.)

---

## 3. Open-source: ce am luat, ce am lăsat

| Proiect | Decizie |
|---|---|
| **pvlib** | **Folosit mult mai mult.** Adăugate: model de zăpadă (`snow.coverage_nrel`), murdărire dinamică (`soiling.kimber`), transpoziție Perez-Driesse, temperatură Faiman, modele de separare a radiației, cer senin Ineichen cu turbiditate Linke. |
| **Solar Forecast Arbiter** | **Metodologia preluată, dependența nu.** Am portat definițiile de referință și metricile în `verification.py`. Proiectul e în întreținere minimă și ar fi adus un întreg stoc de API/raportare nefolosit. |
| **pvanalytics / RdTools** | **Ideile preluate, reimplementate.** Verificările de calitate (valori blocate, detecție de zi, outlieri, disponibilitate) sunt ~200 de linii proprii, testabile direct, fără două dependențe în plus. |
| **Soluțiile câștigătoare GEFCom2014** | **Adoptate:** gradient boosting cu pierdere pinball pentru cuantile; variabile decalate în timp; direcția vântului ca pereche sin/cos. |
| **Analog Ensemble** | **Amânat.** Merită ca al doilea post-procesor, sub aceeași procedură de promovare. ~150 de linii. |
| **MAPIE (predicție conformă)** | **Ideea preluată, dependența nu.** Calibrarea conformă split, pe fereastră cronologică, e ~40 de linii în cazul nostru (un model per parc, set de calibrare natural). |
| **windpowerlib** | **Neadoptat ca dependență**, dar folosit ca verificare conceptuală: de acolo vine ideea curbei de eficiență a parcului în funcție de viteză, în locul unui procent constant de siaj. |
| **PyWake / FLORIS / foxes** | **Respinse pentru rulare online** (cost de calcul disproporționat). Recomandate pentru generarea offline a unui tabel de eficiență per parc. |
| **PVNet / DGMR (deep learning)** | **Respinse.** Necesită ingestie satelitară, GPU și date la scară de teraocteți; ținta lor (agregat regional) e diferită de a noastră (parc individual). |
| **darts / sktime / statsforecast** | **Respinse ca framework.** O migrare ar aduce eleganță de API, nu acuratețe; liniile de bază sunt ~15 linii fiecare. |
| **`pvlib.ModelChain`** | **Respins.** Lanțul nostru are semantici intenționat diferite (limitare pe sumă, stivă proprie de pierderi, poziție solară la mijlocul intervalului) — `ModelChain` s-ar lupta cu toate trei. |

---

## 4. Literatura: cele mai utile constatări

**Post-procesarea la nivel de PUTERE bate tot restul.** Mayer & Yang (2024) și Horat et al. (2025)
arată că post-procesarea statistică a puterii aduce ~48-57% îmbunătățire, în timp ce post-procesarea
radiației înainte de conversie aduce ~7% — și devine aproape redundantă dacă puterea e oricum
post-procesată. Arhitectura noastră (LightGBM pe reziduul fizic) e **exact** configurația pe care o
recomandă literatura. De aceea efortul a mers în a-i da modelului **date mai curate și intrări mai
bogate**, nu în a rescrie lanțul fizic.

**Alegerea modelelor fizice contează surprinzător de puțin.** Mayer & Gróf (2021) au testat toate
combinațiile posibile (9 × 10 × 5 × 4 × 3 modele) pe 16 centrale: dispersia totală pe tot spațiul de
alegeri e ~13% în MAE. Concluzia operațională: nu merită obsesia pentru cel mai bun model de separare
a radiației înainte de a rezolva stratul statistic și curățenia datelor.

**Verificarea are un consens explicit.** Yang et al. (2020), un articol cu 20 de autori, contrazice
practica uzuală în trei puncte, toate adoptate aici: normalizare la capacitate (nu la medie) pentru
comparabilitate; excluderea obligatorie a orelor de noapte; raportarea unui scor de skill față de
combinația optimă persistență/climatologie în spațiul indicelui de senin.

**Ce am adoptat din partea eoliană:** legea puterii cu exponent dedus din două niveluri (în locul
rezolvării prin bisecție a rugozității); densitatea aerului cu umiditate (IEC 61400-12-1); netezirea
curbei de putere cu intensitatea turbulenței (2-5% din RMSE); siaj dependent de viteză.

**Ce am adoptat din partea fotovoltaică:** zăpadă (Marion), murdărire dinamică (Kimber), Perez-Driesse,
albedo variabil cu comutare pe zăpadă, temperatură Faiman calibrabilă, curbă de randament al
invertorului.

---

## 5. Clasamentul final: valoare / efort

Ordinea în care au fost implementate, cu justificarea din cercetare:

| # | Schimbare | Câștig estimat | Stare |
|---|---|---|---|
| 1 | Control de calitate înainte de antrenare | 5-15% relativ pe stratul ML | **Implementat** |
| 2 | Verificare corectă (normalizare, ore utile, skill) | 0% acuratețe, 100% din capacitatea de a ști | **Implementat** |
| 3 | Intrări ML extinse + țintă normalizată | 10-25% relativ | **Implementat** |
| 4 | Topologia invertoarelor explicită | elimină un bias sistematic | **Implementat** |
| 5 | Netezire cu turbulența (eolian) | 2-5% RMSE | **Implementat** |
| 6 | Cuantile + calibrare conformă | produs nou | **Implementat** |
| 7 | Zăpadă + albedo de zăpadă | elimină erori de 50-100% în 5-20 zile/an | **Implementat** |
| 8 | Perez-Driesse + albedo variabil | 1-3% relativ | **Implementat** |
| 9 | Eolian: profil, densitate cu umiditate, siaj variabil | 2-4% relativ | **Implementat** |
| 10 | Curbă de randament al invertorului | 1-3%, concentrat la lumină slabă | **Implementat** |
| — | Rezoluție 15 minute | aliniere la piață | **Implementat** |
| — | Ponderi adaptive de ansamblu | diferențiator comercial | **Implementat** |

---

## 6. Recomandări pentru pașii următori (neimplementate deliberat)

Ordonate după raportul valoare/efort, cu motivul amânării:

1. **API-ul de ansamblu Open-Meteo** (51 de membri ECMWF ENS). Ar înlocui dispersia dintre modele —
   un proxy necalibrat — cu o distribuție fizic consistentă, propagată prin curba de putere neliniară.
   *Amânat pentru că* e muncă de infrastructură (interogări, cache, cost de calcul × N membri), nu de
   modelare, iar cuantilele antrenate acoperă deja nevoia comercială de benzi.

2. **Backtesting rulant, pe 90-120 de zile, cu intervale de încredere prin bootstrap.** Verificarea
   actuală măsoară corect, dar pe o singură fereastră. Recomandarea IEA e evaluarea pe minimum 90 de
   zile consecutive, cu test statistic pentru a distinge diferențele reale de zgomot.

3. **Al doilea și al treilea ciclu zilnic**, aliniate la 11:00 (închiderea pieței) și 16:30
   (notificări), plus actualizări intraday.

4. **Model separat pentru orizont scurt (0-6 h)**, cu producția măsurată recentă ca intrare. Atenție:
   aceste variabile NU trebuie adăugate modelului pentru ziua următoare — la momentul rulării nu
   există, iar folosirea lor la antrenare ar produce o scurgere de informație din viitor.

5. **Incertitudinea agregată pe portofoliu**, din corelațiile empirice ale reziduurilor între parcuri.

6. **Analog Ensemble** ca al doilea post-procesor, sub aceeași procedură de promovare.

7. **Tabel de siaj precalculat** (PyWake/foxes) per parc, dacă apar amplasamente cunoscute.

8. **Export în formatul de notificare** (ENTSO-E ESS / DAMAS) pentru clienții care fac parte dintr-un PRE.
