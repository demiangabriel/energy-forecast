# Cercetare 1 — Platforme comerciale de prognoză: peisaj competitiv și analiză de lacune

> Raport de cercetare produs în cadrul sesiunii de îmbunătățire a platformei.
> Sursele sunt citate cu URL la finalul fiecărei secțiuni. Textul e păstrat în engleză
> (limba surselor), cu o sinteză în română în `docs/SINTEZA-CERCETARE.md`.

## 1. What the market actually offers

### 1.1 Comparison table

| Provider | Horizons | Update freq. | Probabilistic | Published accuracy & normalization | Owner inputs required | Notable extras |
|---|---|---|---|---|---|---|
| **Solcast** | −7d history → +14d | 5–15 min; 5/15/60-min resolution | Yes — P10/P50/P90 in API | MAPE + nRMSE. **PV power normalized by AC capacity**; irradiance by *mean observed irradiance*. Benchmarks vs GFS and **smart persistence** | Tiered: rooftop = location+capacity; Advanced = ~20 params; Premium = SCADA history | Satellite (1–2 km) nowcast; **dynamic snow-cover & soiling factors**; ingests SCADA availability/curtailment tags; portfolio aggregation (2,000+ sites/150 GW) |
| **Solargis** | Nowcast (~3 h, CMV satellite) → 14 d | CMV 15 min (Europe); NWP every 6 h; 5-min→daily resolution | Not advertised as quantiles (IFS Ensemble used internally) | Bias / MAD / RMSD, **divided by installed capacity** for PVOUT. Day-ahead hourly: bias 1.08 %, MAD 6.27 %, RMSD 10.49 %; hour-ahead MAD 4.89 %, RMSD 8.66 % | **None** — explicitly "does not require real production data for calibration" | 4 NWP models (HRRR, GFS, ICON/ICON-EU, IFS + IFS-ENS); SFTP or API |
| **Steadysun** | 0–15 d | 4×/day long-range; up to 8×/day short-term; **5–15 min** satellite nowcast | Yes — quantile distributions | No universal figures published; sells **hindcast service** so clients benchmark on their own history pre-contract | PV: tilt, azimuth, DC/AC, tracker, module tech. Wind: power curve, hub height, capacity, layout | Auto-calibration of loss parameters from measurements (Advance+/Expert+); portfolio auto-aggregation groups; REST/SFTP, JSON/CSV/XML |
| **ENFOR** (DTU spinoff) | Minutes → ~2 weeks | **Event-driven** — recomputes on every new NWP *or* new production datapoint | Yes — dedicated quantile/uncertainty-band module for trading | "State-of-the-art" claims, no numbers | SCADA real-time or offline production; design power curve accepted as cold start | **Self-learning/self-calibrating** (adapts to PV aging, seasonality); availability & curtailment schedules ingested; dedicated **curtailment lost-production module**; portfolio grouping with combined power curve |
| **energy & meteo systems** (Previento) | 5 min → 15 d | Not published | Situation-dependent uncertainty per forecast | No numbers | Real-time measurements for optimization | **Meta-forecast: performance-weighted model blending based on historical skill**; **two curtailment variants** (with grid-operator curtailment vs. technically-possible feed-in); shadow-flicker/night-shutdown modelling; portfolio→market level |
| **Meteologica** | up to 14 d | 4×/day | Ensemble & probabilistic for trading | None public | "Low data requirements"; calibrates on generation **and availability** data | xTraders web portal with built-in performance analysis |
| **DNV Forecaster** | Operational service, 3rd-party data integration | — | — | — | — | Consultancy-backed; independent validation heritage |
| **UL Solutions / UL Renewables** | Minutes → weeks | — | Extreme-event probability + **MW-loss scenarios** | None public | Works with **or without** on-site measurement | **Curtailment hindcast** ("how much could have been produced"); extreme-weather alerting |
| **Renewables.ninja** | N/A | N/A | No | N/A | Location only | **Not an operational forecaster** — MERRA-2/SARAH reanalysis simulation for research. Useful only as a free validation/backfill baseline |
| **Enlitia / Kaiserwetter / GreenPowerMonitor** | N/A (analytics, not NWP) | — | — | — | Full SCADA | Adjacent segment: underperformance detection, data standardization, asset-performance KPIs. They *consume* forecasts rather than produce them |

### 1.2 Industry standard for verification and contractual KPIs

The authoritative reference is the **IEA Wind Task 36/51 Recommended Practice for the Implementation of
Renewable Energy Forecasting Solutions, Part 3 (Evaluation)**. Its core positions:

- **No single metric.** Choose metrics matching the user's cost function: MAE where imbalance cost is
  linear, RMSE where large errors are disproportionately costly (German TSOs prefer RMSE), bias to expose
  systematic error — bias is explicitly named as the detector of **unannounced curtailment**.
- **Normalization by installed capacity** is the recommended default for nMAE/nRMSE. GET.transform notes
  the practical split: **TSOs normalize by installed capacity, traders by mean output**. Normalizing by
  *actual* is discouraged (blows up at low output; for PV it is meaningless at night).
- **Skill score vs. a reference is expected.** Persistence is the canonical reference — "comparing 'not
  having a forecast' to having one." Climatology and the *previous evaluation period* are also
  recommended, the latter specifically for long-term contracts.
- **Trial design must be representative, significant, relevant**: minimum **90–120 consecutive days** to
  cover weather autocorrelation and seasonal modes; **bootstrap resampling or t-tests** to confirm
  differences aren't noise; **disaggregation by lead time, time of day, and power level**.
- **Curtailment must be removed** from the evaluation set unless it is itself a forecast target.
- **Missing data**: if any competing forecast is missing for a period, exclude that period **for all**
  forecasts. Log forecast delivery times strictly.
- **Contract KPI structures** in use: improvement over persistence; improvement vs. previous period
  (moving target); fixed target achievement; categorized error bands with differential weights. The RP
  warns that penalizing *missed deliveries* poorly creates an incentive to simply not deliver on hard days.

**Typical European accuracy levels** (GET.transform, 2024): single-site day-ahead wind MAE ≈ **9–10 %** of
installed capacity, with a **7–19 %** spread driven by terrain complexity; single-site RMSE **10–20 %**;
regional aggregates **5–10 %** RMSE. Solargis's published day-ahead PV figures (MAD 6.27 %, RMSD 10.49 %
of installed capacity) are a fair benchmark for utility-scale PV.

### 1.3 Romanian market timing (constrains the run schedule)

- **PZU / day-ahead (OPCOM operational procedure):** order submission closes — **gate closure 11:00 CET**;
  results published **11:40 CET**; physical notifications referenced at **14:30 CET**. Decoupling fallback:
  gate reopens after 12:35, results by 13:30.
- **Physical notifications to Transelectrica (PO 133 NF, rev. 26.01.2021):** PRE submit NF via **DAMAS**
  (markets.transelectrica.ro) in **ENTSO-E ESS v3r1** format by **16:30 on D-1**. Intraday modification:
  production/consumption units up to **50 minutes** before the settlement interval; block exchanges up to
  15 minutes before. **Wind and solar are exempt from justifying availability modifications.**
- **Settlement granularity: 15 minutes.** Additionally **SDAC moved to a 15-minute MTU on 30 September
  2025** (delivery from 1 Oct 2025) — **96 intervals/day**.
- **Intraday (SIDC continuous):** opens **15:00 CET on D-1** for the next delivery day, closes **60
  minutes** before delivery. Three IDA auction sessions (two on D-1, one on D).

> ⚠️ The 14:30 (OPCOM) vs 16:30 (Transelectrica) figures refer to different process steps and the PO 133
> revision is from 2021 — verify the current revision before hard-coding either.

**Implication:** the daily run must complete and deliver **before ~09:00–10:00 CET** to be usable for the
11:00 gate, and the forecast should span **D+1 00:00–24:00 local, at 15-minute resolution**. A second useful
cycle sits before **16:30 D-1** (NF submission) and a third intraday, given the 50-minute re-notification
window.

## 2. Prioritized gaps

### Tier A — Table stakes we lack

**A1. Calibrated probabilistic output (P10/P50/P90).** *Difficulty: Low–Medium.*
Every serious competitor ships quantiles. Inter-model standard deviation is an *uncorrelated proxy*, not a
calibrated interval — it systematically under-disperses. Fix: train LightGBM with `objective='quantile'` at
α = 0.1/0.5/0.9 on the residual, then validate with **pinball loss + PICP/reliability diagram**. Highest
value-to-effort item.

**A2. Skill score against a reference forecast.** *Difficulty: Low.*
`SS = 1 − MSE_forecast / MSE_reference`. Use **smart persistence** for PV (persist clear-sky index, not
power) and plain persistence + climatology for wind. Without this, a client cannot tell whether the product
beats doing nothing — and IEA RP treats it as the baseline contractual KPI.

**A3. Explicit, dual normalization convention in the monthly report.** *Difficulty: Low.*
Publish nMAE/nRMSE **both** normalized by installed capacity (TSO convention, IEA-recommended) and by mean
production (trader convention), and for PV state whether night hours are included. An unlabelled "nMAE" is
not defensible in a commercial dispute.

**A4. Curtailment-aware training and a dual forecast.** *Difficulty: Medium.*
If residual ML trains on curtailed actuals, it learns a **permanent negative bias**. Minimum viable:
heuristic curtailment detection (flat-topped output below expected, sustained clipping at non-inverter
levels), flag those intervals, **exclude from ML training and from KPI computation**, and report "lost
production" as a client-facing value-add.

**A5. Outage / underperformance detection.** *Difficulty: Low–Medium.*
Actual ≪ physical-model expectation with good irradiance ⇒ flag. Cleans the training set, protects the
reported nMAE from being blamed for the client's broken inverter, and is a saleable finding.

**A6. Proper verification harness (rolling backtest).** *Difficulty: Medium.*
Champion/challenger on one chronological holdout is not verification. Needed: ≥90–120 day rolling backtest,
**disaggregated by lead time, hour of day, and power bin**, with bootstrap confidence intervals.

**A7. 15-minute resolution.** *Difficulty: Medium.*
Romanian settlement and now SDAC are quarter-hourly. Hourly-only output is behind the market. pvlib handles
sub-hourly natively; the work is NWP temporal interpolation (interpolate irradiance via clear-sky index, not
raw GHI) and storage/report volume (×4).

**A8. Delivery SLA discipline and fallback chain.** *Difficulty: Low.*
Sole dependence on Open-Meteo is a single point of failure ahead of an 11:00 gate. Need: per-model fetch
retry, graceful degradation to remaining models with re-normalized weights, last-good-forecast fallback,
**logged forecast issue time**, and an alert if delivery would miss the deadline.

### Tier B — High-value differentiators

**B1. Second and third daily run cycles.** *Difficulty: Medium.* Align to real deadlines: ~08:00 CET
(pre-gate), ~15:00 CET (pre-NF), and optionally hourly intraday for the 50-min re-notification window.

**B2. Adaptive, performance-weighted ensemble.** *Difficulty: Low–Medium.* Replace fixed model weights with
weights fitted on trailing skill **per site and per lead time** — exactly emsys's "meta-forecast" selling
point, and cheap given per-model values are already stored.

**B3. Snow and soiling modelling.** *Difficulty: Low–Medium.* pvlib provides `pvlib.snow.coverage_nrel` and
the HSU/Kimber soiling models; inputs (precipitation, snowfall, temperature) are already available from
Open-Meteo.

**B4. Commercial hindcast / trial product.** *Difficulty: Low–Medium.* Steadysun sells hindcasts explicitly
as a pre-sales instrument. For a report-selling operator this is directly revenue-generating.

**B5. Portfolio-level aggregated forecast with correlation-aware uncertainty.** *Difficulty: Medium.*
Aggregate nMAE is materially lower than the mean of individual nMAEs (5–10 % vs 10–20 %). Naive summation of
P10s/P90s is wrong — aggregate quantiles from the joint distribution or empirically from backtested residual
correlations.

**B6. A written KPI/contract definition pack.** *Difficulty: Low (documentation only).* Specify: metric,
normalization base, exclusions (curtailment, outages, comms failures), evaluation window length, reference
forecast, and what happens on missed delivery.

### Tier C — Nice-to-have

**C1. Satellite nowcasting (CMV, 0–6 h).** *Difficulty: High.* Only pays if selling intraday. Defer.
**C2. Ramp/event metrics (POD, FAR, CSI).** *Difficulty: Low–Medium.* IEA-recommended for high-wind
shutdown and ramp events.
**C3. Wind storm-cutout hysteresis and icing.** *Difficulty: Medium.* Straight power-curve interpolation
mishandles cut-out/cut-back-in hysteresis, producing the largest single wind errors.
**C4. Notification-format export.** *Difficulty: Low.* A DAMAS/ENTSO-E ESS-shaped CSV for clients inside a PRE.
**C5. Client-facing self-service portal.** *Difficulty: High, low priority.* The business model deliberately
does not have one. Not a gap — a positioning choice.

### Summary judgement

The physical modelling chain is genuinely competitive. The gaps are **not in the physics**; they are in
**statistical framing (A1–A3, A6), data hygiene around curtailment/outages (A4–A5), and market-clock
alignment (A7, B1)**.

---

**Sources:** [Solcast accuracy](https://solcast.com/forecast-accuracy) ·
[Solcast PV models](https://solcast.com/energy/pv-power-forecast-models) ·
[Solcast market/grid](https://solcast.com/energy/market-portfolio-grid-forecast-models) ·
[Solargis forecasting](https://kb.solargis.com/docs/solar-power-forecasting) ·
[Solargis validation](https://kb.solargis.com/docs/validation-of-forecast-data) ·
[Steadysun FAQ](https://steady-sun.com/faq/) · [ENFOR SolarFor](https://enfor.dk/services/solarfor/) ·
[ENFOR WindFor](https://enfor.dk/services/windfor/) ·
[energy & meteo systems](https://www.emsys-renewables.com/products/power_forecasts/wind-power-forecasts.php) ·
[Meteologica](https://www.meteologica.com/services.html) ·
[DNV Forecaster](https://www.dnv.com/energy/services/forecaster/our-services/) ·
[UL Renewables forecasting](https://ul-renewables.com/forecasting/) ·
[Renewables.ninja](https://www.renewables.ninja/) ·
[IEA Wind Task 36 RP Part 3](https://iea-wind.org/wp-content/uploads/2021/04/IEAWIND-TASK36_Recommended-Practice_Part3_20190228_v2-1.pdf) ·
[GET.transform VRE forecasting brief](https://www.get-transform.eu/wp-content/uploads/2024/01/GET.transform-Brief_VRE-Forecasting-Solar-Wind.pdf) ·
[OPCOM PZU procedure](https://www.opcom.ro/opcom/uploads/doc/PZU/1_PO%20Functionare%20PZU_final.pdf) ·
[OPCOM intraday/SIDC](https://www.opcom.ro/tranzactii-produse/en/162) ·
[Transelectrica PO 133 NF](https://www.transelectrica.ro/documents/10179/5837055/PO+133+NF_26.01.2021_publicare.pdf/a4c79a58-3cf4-47f6-8b04-b03fc204df22) ·
[ENTSO-E SIDC](https://www.entsoe.eu/network_codes/cacm/implementation/sidc/) ·
[SDAC 15-min MTU](https://www.methodia.com/blog/sdac-15-minute-eu-day-ahead-power-market)
