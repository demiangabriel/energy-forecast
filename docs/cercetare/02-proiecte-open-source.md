# Cercetare 2 — Peisajul open-source (GitHub): ce merită adoptat

> Raport de cercetare. Sinteza în română: `docs/SINTEZA-CERCETARE.md`.

Scope: what is genuinely additive to the existing Python/FastAPI/SQLite platform
(Open-Meteo multi-model NWP → pvlib PV physics / hand-written wind physics → LightGBM residual
correction → deterministic output).

## 1. Project inventory

| Project | URL | License | Maturity / activity | Relevance |
|---|---|---|---|---|
| **pvlib-python** | https://github.com/pvlib/pvlib-python | BSD-3 | 1.7k★, very active, JOSS-published | **Critical** — already a dependency, but heavily under-used |
| **Solar Forecast Arbiter core** | https://github.com/SolarArbiter/solarforecastarbiter-core | MIT | 45★, low activity (DOE funding ended) — treat as a *specification*, not a dependency | **Critical** — verification & reference-forecast methodology |
| **pvanalytics** | https://github.com/pvlib/pvanalytics | MIT | 139★, v0.2.2 (Nov 2024), pvlib-org maintained | **High** — QC on measured production |
| **RdTools (NREL)** | https://github.com/NREL/rdtools | MIT | 186★, active, Py3.10+ | **High** — soiling, degradation, availability/outage detection |
| **windpowerlib** | https://github.com/wind-python/windpowerlib | MIT | 391★, v0.2.2 (Feb 2024), moderate | **High** — validates/replaces hand-written wind chain |
| **OpenSTEF (LF Energy)** | https://github.com/OpenSTEF/openstef | MPL-2.0 | 148★, v4.0.1 (May 2026), active | **High** — closest comparable whole product |
| **MAPIE** | https://github.com/scikit-learn-contrib/MAPIE | BSD-3 | 1.6k★, active | **High** — conformal prediction intervals |
| **Quartz Solar Forecast (OCF)** | https://github.com/openclimatefix/open-source-quartz-solar-forecast | MIT | 154★, active | **Medium** — validates GBT approach; feature list reusable |
| **Analog Ensemble (PAnEn/PyAnEn)** | https://github.com/Weiming-Hu/AnalogsEnsemble | MIT | 18★, C++/R, dormant | **Medium** — algorithm worth reimplementing (~150 LOC) |
| **mqe-forecast (Greenlytics)** | https://github.com/greenlytics/mqe-forecast | MIT | 4★, dormant | **Medium** — multi-quantile GBDT reference impl. on GEFCom2014 |
| **Saint-Drenan power-curve model** | https://github.com/YvesMSaintDrenan/WT_PowerCurveModel | ⚠️ unspecified | 22★, dormant | **Medium** — parametric power curve when OEM curve unknown |
| **foxes (Fraunhofer IWES)** | https://github.com/FraunhoferIWES/foxes | MIT | 39★, active | **Medium** — offline wake table only |
| **PyWake (DTU)** | GitLab (GitHub mirror deprecated) | MIT | Active | **Medium** — same, offline only |
| **FLORIS (NREL)** | https://github.com/NREL/floris | BSD-3 | 300★, active | **Low** — controls/AEP oriented |
| **darts** | https://github.com/unit8co/darts | Apache-2.0 | 9.5k★, very active | **Low-Medium** — conformal models worth reading, not importing |
| **statsforecast** | https://github.com/Nixtla/statsforecast | Apache-2.0 | 4.9k★ | **Low** — baselines are trivial to hand-roll |
| **PVNet (OCF)** | https://github.com/openclimatefix/PVNet | MIT | 57★, active | **Low** — architecture inspiration only |
| **skillful_nowcasting (DGMR)** | https://github.com/openclimatefix/skillful_nowcasting | MIT | 295★ | **Low** — out of scale |
| **scikit-garden** | https://github.com/scikit-garden/scikit-garden | BSD | **abandoned since 2017** | **None** |

## 2. Prioritized adoptable techniques

### P0 — Adopt now, high value / low effort

**2.1 Skill-score-based champion/challenger, not raw MAE.**
Source: Solar Forecast Arbiter `metrics.deterministic` + `.reference_forecasts.persistence`.
The meaningful quantity is `forecast_skill = 1 − MSE_fx / MSE_ref` against an explicit **reference
forecast**, plus normalized metrics (normalized by AC capacity). Implement three reference forecasts:
`persistence_scalar` (last observed value), `persistence_interval` (mean over matching interval), and
**`persistence_scalar_index`** — clear-sky-index persistence for PV: persist `k_c = P_meas / P_clearsky`
rather than power. A physical+ML model that cannot beat clear-sky-index persistence at 1–6 h is not adding
value. **Difficulty: low.** Port the metric formulas; do *not* take the dependency (maintenance mode, heavy).

**2.2 Upgrade pvlib and adopt five unused modules.**
- `pvlib.clearsky.detect_clearsky()` (Reno–Hansen): label clear intervals in measured production — used as
  (a) a filter for tilt/azimuth calibration, (b) a boolean ML feature, (c) a QC gate.
- `pvlib.irradiance.get_sky_diffuse(model='perez')` / `perez_driesse`: replace Hay–Davies.
- `pvlib.soiling.hsu()` / `soiling.kimber()`: replace the static soiling term with a **rainfall-driven
  dynamic soiling state**.
- `pvlib.snow.coverage_nrel()` + `snow.loss_townsend()`: in a continental climate this is the largest single
  unmodelled winter error source; ML on residuals cannot learn it from the current feature set.
- `pvlib.iotools.get_era5()` — replaces hand-rolled ERA5 fetching in the calibration path.
Also new: the **Marion (2008) non-linear irradiance adjustment for `pvwatts_dc()`** — a direct low-light fix.

**2.3 Quantile LightGBM on the residual → P10/P50/P90.**
Train additional models with `objective='quantile', alpha=q`, same features and harness; add
`physical + residual_quantile(q)` and clamp to `[0, capacity]`. This is what won the GEFCom2014 wind and
solar tracks (Landry et al.; Nagy et al.). Sort quantiles post-hoc to remove crossing. Reference impl.:
**mqe-forecast**. Verify with `quantile_score`, `crps`, reliability diagram.

**2.4 Empirical residual quantiles binned by lead time and power level (OpenSTEF pattern).**
Bucket archived residuals by (horizon, predicted-power decile, day/night) and take empirical quantiles.
~40 lines, no new model, robust; a sanity floor for the quantile GBMs.

**2.5 Production-data QC from pvanalytics + RdTools.**
`clipping.geometric`, `quality.gaps` (stale/interpolated runs), `quality.data_shifts` (capacity step
changes), `features.daytime` (night-time non-zero), `rdtools.availability` (outage/curtailment).
**Critical:** outage/curtailment periods must be *excluded from residual ML training*, otherwise LightGBM
learns "sometimes production drops 60 % for no meteorological reason" and disperses that across all
forecasts.

### P1 — High value, moderate effort

**2.6 True NWP ensemble instead of inter-model std.**
Open-Meteo's **Ensemble API** exposes ECMWF IFS ENS (51 members), GFS ENS (31), ICON-EPS (40), GEM (21).
Running the physics chain over N members yields a *physically consistent* spread, including the
non-linearity of the wind power curve. Subsample to 10–15 members and only for horizons > 12 h. The ensemble
quantiles then become an *input feature* to the quantile LightGBM.

**2.7 Analog Ensemble over the own forecast archive.**
For a target (park, lead time), find the K historical forecasts most similar under the Delle Monache metric,
then take the *observed* productions of those K analogs as the ensemble. Simultaneously a bias correction
and a probabilistic forecast; needs no training; naturally lead-time-conditional. ~150 lines in NumPy.

**2.8 Wind chain: cross-validate against windpowerlib, adopt farm-efficiency curves.**
`wind_speed.logarithmic_profile`/`hellman`, `density.barometric`/`ideal_gas`,
`power_output.power_curve_density_correction`, plus the **OpenEnergy turbine database** with real OEM power
curves. `TurbineClusterModelChain` applies a **wind-farm efficiency curve** (efficiency vs wind speed)
instead of a constant wake loss — wake losses peak at mid wind speeds and vanish above rated.
⚠️ `WT_PowerCurveModel` has **no declared license** — reproduce the paper, do not vendor the code.

**2.9 Wake losses as a precomputed offline table (PyWake / foxes).**
Do not run a wake model online. Run once per park over a grid of (speed × direction × TI) and export a
static efficiency lookup. Even without a wake model, **add wind direction (sin/cos) as an ML feature
immediately.**

**2.10 Intraday blend: recent generation + clear-sky-index persistence.**
For 0–6 h horizons, recent measured generation dominates NWP skill. Add lagged measured production
(t−1h, t−2h, t−24h) to a **separate short-horizon model**, blending its weight to zero by ~6–8 h. Do not add
lags to the day-ahead model — they are unavailable at run time and will silently leak.

### P2 — Worth doing later

- **Conformal calibration of the quantile intervals** (MAPIE as a reference; implement split conformal on a
  rolling calibration window, ~60 lines).
- **GEFCom-winner feature engineering**: NWP **lead/lag features** (t−2…t+2), wind **u/v components**,
  `speed³`, speed at multiple heights, spatial neighbours, clear-sky index. Lead/lag features correct NWP
  *timing* errors.
- **pvlib separation models** (`disc/dirint/dirindex/erbs/boland`) — cheap A/B test; NWP DNI is often the
  weakest radiation field.
- **`pvlib.bifacial.infinite_sheds`** — only if a park has bifacial modules; then essential.

## 3. Do NOT adopt — and why

| Project / technique | Reason |
|---|---|
| **PVNet** | Multi-modal late-fusion NN over EUMETSAT SEVIRI + ICON-EU grids, GPU training, TB-scale storage; regional (GSP) target ≠ per-park target. |
| **skillful_nowcasting / DGMR** | GAN radar nowcasting, ~1 TB training set, radar-based — no benefit at day-ahead horizons. |
| **ocf_datapipes / nowcasting_dataset** | Superseded by `ocf-data-sampler`; effectively archived. |
| **scikit-garden** | Last release 2017, unmaintained. LightGBM's native quantile objective covers the need. |
| **FLORIS** | Steady-state, controls-oriented; nothing targets time-series forecasting. |
| **PyWake / foxes running online** | Orders of magnitude more compute than the rest of the chain for a correction a static lookup captures at ~99 % fidelity. |
| **darts / sktime as a framework** | Heavy (darts pulls PyTorch). One model family, one task — a framework migration buys API elegance, not accuracy. |
| **statsforecast** | Its value is fitting 1M series fast. Persistence baselines are ~15 lines. |
| **Rewriting the PV chain onto `pvlib.ModelChain`** | The chain has deliberate non-default semantics (clipping on the **sum** of sub-array DC, custom multiplicative loss stack, solar position at interval midpoint). Adopt pvlib *functions*, not its orchestration. |
| **Solar Forecast Arbiter as a runtime dependency** | Low activity; drags in an API client/reporting stack. Port the ~30 formulas. |
| **atlite** | Gridded reanalysis-driven energy-system modelling, not point-level operational forecasting. |
| **`pvlib.scaling.wvm`** | Sub-minute spatial smoothing across distributed fleets. Irrelevant at 15-min resolution for single parks. |
| **`pvlib` spectral models** | Sub-1 % annual effect, already absorbed by the loss stack and residual ML. |

## 4. Suggested sequencing

1. **Verification first** (2.1, 2.4) — nothing below can be ranked until skill vs. a reference is measurable.
2. **pvlib upgrade + clear-sky detection + snow + soiling** (2.2) — remove physical error before asking ML to absorb it.
3. **QC gates from pvanalytics/RdTools** (2.5) — clean the ML training target.
4. **Quantile LightGBM** (2.3) — ship the probabilistic product.
5. **Open-Meteo Ensemble API** (2.6) — the largest accuracy/uncertainty step change.
6. **Wind chain hardening** (2.8, 2.9) and **intraday blend** (2.10).
7. **AnEn and conformal calibration** as challengers under the harness from step 1.

---

**Sources:** [pvlib](https://github.com/pvlib/pvlib-python) ·
[solarforecastarbiter-core](https://github.com/SolarArbiter/solarforecastarbiter-core) ·
[pvanalytics](https://github.com/pvlib/pvanalytics) · [RdTools](https://github.com/NREL/rdtools) ·
[windpowerlib](https://github.com/wind-python/windpowerlib) · [OpenSTEF](https://github.com/OpenSTEF/openstef) ·
[MAPIE](https://github.com/scikit-learn-contrib/MAPIE) ·
[Quartz Solar Forecast](https://github.com/openclimatefix/open-source-quartz-solar-forecast) ·
[PVNet](https://github.com/openclimatefix/PVNet) ·
[AnalogsEnsemble](https://github.com/Weiming-Hu/AnalogsEnsemble) ·
[mqe-forecast](https://github.com/greenlytics/mqe-forecast) ·
[WT_PowerCurveModel](https://github.com/YvesMSaintDrenan/WT_PowerCurveModel) ·
[foxes](https://github.com/FraunhoferIWES/foxes) · [FLORIS](https://github.com/NREL/floris) ·
[darts](https://github.com/unit8co/darts) · [statsforecast](https://github.com/Nixtla/statsforecast) ·
[awesome-energy-models](https://github.com/rebase-energy/awesome-energy-models) ·
[Open-Meteo Ensemble API](https://open-meteo.com/en/docs/ensemble-api)
