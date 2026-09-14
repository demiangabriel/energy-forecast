# Limitările aplicației

Acest document răspunde explicit la întrebarea „unde nu scalează aplicația și
de ce" — mai ales legat de volumul de date. Fiecare limitare de mai jos e
verificată direct în cod (nu presupusă), cu fișierul și motivul exact.
Scopul nu e să sperie, ci să fie clar dinainte, nu descoperit la producție.

## 1. SQLite — un singur scriitor, mono-proces

`SqliteRepository` (`app/storage/sqlite_repo.py`) serializează TOATE
scrierile printr-un `threading.Lock()` (comentariul din cod: „scrieri
seriale — aplicație mono-utilizator, R-201"). E o alegere deliberată pentru
faza inițială (R-1601: „aplicația trebuie să ruleze inițial LOCAL, pe un
singur calculator, fără infrastructură de server"), nu un accident — dar
înseamnă concret:

- Două rulări de prognoză simultane pe parcuri diferite NU scriu efectiv în
  paralel — a doua așteaptă lock-ul eliberat de prima. Pentru câteva zeci de
  parcuri, întârzierea e nesemnificativă (milisecunde per scriere); pentru
  câteva sute, rularea zilnică secvențială (vezi §3) devine gâtul de sticlă
  mai devreme decât lock-ul.
- Fișierul `data/prognoza.db` crește nemărginit — nu există arhivare sau
  ștergere automată a datelor orare vechi. Interogările pe cheie
  (`parc_id`, `timestamp_utc`) rămân rapide (indexate — vezi §7), dar
  fișierul însuși (și orice copie de rezervă completă a lui, §6) crește
  proporțional cu anii de istoric × numărul de parcuri.
- Nu există control de concurență pentru mai mulți operatori simultani —
  aplicația presupune UN operator (R-201). Două persoane care editează
  configurația aceluiași parc în același timp se pot suprascrie reciproc.

## 2. Firestore — cotă gratuită, chiar și după împachetarea compactă

Migrarea la Firestore (`app/storage/firestore_repo.py`) rezolvă problema de
mono-proces de mai sus, dar introduce alta: cota gratuită e de 50.000 de
citiri și 20.000 de scrieri pe zi. Producția reală și verdictele de calitate
sunt împachetate acum UN DOCUMENT PER ZI (nu per oră —
`app/storage/firestore_packing.py`), o reducere de ~24×, dar rămân limitări
reale:

- Un import istoric dintr-o singură lovitură, pentru un portofoliu mare
  (ex. 50 de parcuri × 3 ani de istoric = ~55.000 de documente-zi), tot
  poate depăși cota zilnică de scrieri — recomandarea practică e importul
  eșalonat pe mai multe zile pentru portofolii mari, sau upgrade la planul
  plătit (Blaze) al Firestore.
- Fiecare document Firestore e limitat la 1 MiB. La rezoluție de 15 minute
  (secțiunea 8 din documentație), o zi înseamnă 96 de intrări — încă foarte
  sub limită — dar un document-zi cu rezoluție mai fină sau cu câmpuri
  suplimentare adăugate în viitor ar trebui verificat din nou față de
  această limită.
- `FirestoreRepository` rămâne netestabil LIVE în acest mediu de dezvoltare
  (fără credențiale) — testat doar prin funcțiile pure de (de)împachetare,
  offline. Comportamentul exact sub sarcină reală (latențe de rețea, retry
  Firestore) nu poate fi verificat aici.

## 3. Rularea zilnică — secvențială, nu paralelă

`app/core/daily_run.py` parcurge parcurile unul câte unul
(`for parc in repo.listeaza_parcuri(): ...`), izolat (un parc care eșuează
nu blochează restul — R-1603), dar STRICT secvențial: nicio paralelizare
între cererile către sursele meteo, inferența ML sau scrierea în bază.
Pentru câteva zeci de parcuri, rularea completă durează secunde-minute;
pentru câteva sute, timpul crește liniar și poate depăși fereastra utilă
(documentația §1: piața PZU se închide la 11:00, recomandarea e ca rularea
să se încheie până la 09:00-10:00). Paralelizarea (ex. `asyncio.gather` pe
cererile meteo, care sunt I/O-bound) e o îmbunătățire posibilă, netratată
încă.

## 4. Orizontul de prognoză NWP — maximum 16 zile

Setarea `zile_orizont_maxim` (`app/config.py`) e limitată implicit la 16
zile — „limită practică peste care prognozele meteo nu mai sunt fiabile"
(R-906). Modelele meteo numerice (ECMWF IFS, GFS, ICON) folosite de
Open-Meteo nu emit prognoze utile peste acest orizont; nu e o limitare a
aplicației, ci a fizicii prognozei meteo în sine. O „zonă de prognoză pentru
următoarea lună" (cerută separat) nu poate fi o prognoză meteorologică
propriu-zisă dincolo de ~16 zile — trebuie să fie explicit o estimare pe
bază de climatologie/cer senin, cu incertitudine mult mai mare, etichetată
clar ca atare, nu amestecată vizual cu prognoza NWP de scurtă durată.

## 5. Antrenarea modelului ML — prag minim de date, un model per parc

`antreneaza_model` (`app/core/ml_corrector.py`) refuză explicit antrenarea
(R-606) sub `prag_min_ore_antrenare_ml` (implicit 1500 ore utile, ~6 luni de
istoric la fotovoltaic, mai puțin la eolian care nu are „ore de noapte").
Un parc nou, fără istoric suficient, funcționează doar pe modelul fizic
(fără corecție ML) până acumulează destule date — corect prin design, dar
înseamnă că acuratețea unui parc nou e mai slabă în primele luni. Fiecare
parc are propriul model (fără învățare încrucișată automată între parcuri,
în afara mecanismului explicit de „împrumut" de corector de la alt parc,
setat manual). Nu există antrenare incrementală — fiecare rulare de
antrenare reface modelul de la zero pe tot istoricul disponibil, ceea ce
înseamnă timp de antrenare crescător odată cu anii de istoric acumulați.

## 6. Copia de rezervă completă — totul în memorie, dintr-o singură lovitură

`backup_complet_json` (`app/core/backup.py`) încarcă în memorie ȘI
serializează într-un singur fișier JSON toată producția, toate rulările și
toate verdictele de calitate ale TUTUROR parcurilor, dintr-o singură
cerere HTTP sincronă. Pentru un portofoliu mic-mediu, cu ani de istoric, e
practic instant; pentru un portofoliu foarte mare (sute de parcuri, ani de
istoric orar/15-minute), fișierul rezultat poate ajunge la sute de MB, iar
generarea lui poate dura suficient de mult încât să atingă limite de
timeout HTTP. Nu există (încă) o variantă paginată/pe eșantion de perioadă
pentru copia completă — doar „tot" sau „doar configurația parcurilor"
(copia mai mică, xlsx/csv).

## 7. Rapoartele Excel — generate integral în memorie

`genereaza_raport_lunar` (`app/core/reports.py`) construiește foaia „Date
orare" rând cu rând, cu `openpyxl`, integral în memorie, înainte de a
trimite fișierul. Pentru o LUNĂ (maximum ~2976 de rânduri la rezoluție de
15 minute), timpul e neglijabil. Nu există un raport „pe toată durata de
viață a parcului" generat la fel — ar crește liniar cu numărul de luni și
ar trebui, la un portofoliu vechi de mulți ani, regândit ca export
streaming, nu construit integral în memorie.

## 8. Sursele meteo externe — fără cache pentru prognoza LIVE, fără limită proprie de rată

Reanaliza istorică (ERA5, folosită la pagina „Reală vs. prognoză — meteo",
R-701) e cache-uită pe zi în `meteo_cache`
(`obtine_meteo_istoric_real_cache`) — o cerere repetată pentru aceeași zi
nu mai atinge rețeaua. Prognoza meteo LIVE (`obtine_prognoza_meteo`),
folosită de fiecare rulare de prognoză, NU e cache-uită — fiecare rulare
(manuală sau zilnică) interoghează din nou sursele externe, cu 4 reîncercări
automate la eșec (`_cerere_cu_reincercare`, timeout 30s/cerere) dar fără
limitare de rată proprie. Aplicația nu impune un plafon de cereri pe zi —
dacă mai mulți operatori rulează prognoze manuale frecvent pe multe parcuri,
limita reală devine cea impusă de furnizorul extern (Open-Meteo), nu de
aplicație. O întrerupere a rețelei sau a furnizorului extern la momentul
rulării zilnice blochează prognoza pentru acea zi, pentru parcurile afectate
(rularea altor parcuri nu e afectată — R-1603). Codul are deja documentat
(dar nu impus automat) un prag orientativ: peste **60 de parcuri active**
(`PRAG_ACTIVE_ABONAMENT_RECOMANDAT`, `app/core/weather_client.py`), volumul
de cereri al unei rulări zilnice pe portofoliu riscă limitele de rată ale
API-ului gratuit Open-Meteo — recomandarea e un abonament plătit peste acest
prag.

## 9. Fără autentificare — o singură zonă de încredere

Nu există un strat de autentificare/autorizare în aplicație — R-201
presupune un singur operator de încredere, iar Modul tehnic e explicit
„fără restricții" (R-305) pe orice câmp din orice model de date, inclusiv
funcțiile de restaurare din copie de rezervă adăugate recent. Dacă
aplicația ajunge accesibilă de mai mulți utilizatori sau expusă în rețea
mai largă, ar avea nevoie de un strat separat de autentificare — netratat
în această versiune.

## 10. Afișarea în interfață — tabele plafonate, nu date brute nelimitate

Paginile de comparație (Reală vs. prognoză, atât pentru producție cât și
pentru meteo) plafonează tabelul afișat la 500 de rânduri, ca protecție
practică a interfeței pentru intervale foarte lungi — datele complete rămân
disponibile prin export CSV/raport, doar afișarea directă în pagină e
limitată.

---

*Acest document se actualizează pe măsură ce apar limitări noi sau se
rezolvă cele existente — vezi și `docs/CHANGELOG.md` pentru istoricul
schimbărilor legate de ele (ex. împachetarea Firestore, §2, era mult mai
restrictivă înainte de v5).*
