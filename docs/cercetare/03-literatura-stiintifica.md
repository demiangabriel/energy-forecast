# Cercetare 3 — Literatura științifică: modele matematice validate și aplicabilitatea lor

> Raport de cercetare, cu formule și referințe DOI. Sinteza în română: `docs/SINTEZA-CERCETARE.md`.

## 1. Irradiance separation / decomposition

**State of the art.** Gueymard & Ruiz-Arias validated ~140 separation models against 1-min data worldwide;
the best performers are quasi-universal multi-predictor models, while Erbs/Orgill-Hollands class models carry
large conditional biases ([Gueymard & Ruiz-Arias, 2016, *Solar Energy* 128, 1–30](https://doi.org/10.1016/j.solener.2015.10.010)).
The current reference is [Ruiz-Arias & Gueymard, 2024, *RSER* 202, 114678](https://doi.org/10.1016/j.rser.2024.114678):
GISPLIT reduces mean-site RMSE of the best benchmark by ≈11–17 % (DNI) and ≈17–25 % (DHI), concentrated under
variable skies. Below GISPLIT: Engerer2 ([Bright & Engerer, 2019, *JRSE* 11(3), 033701](https://doi.org/10.1063/1.5097014)),
Yang's hybrid/cascade family ([Yang, 2021, *JRSE* 13(5), 056101](https://doi.org/10.1063/5.0059554)) and DIRINT;
BRL ([Ridley, Boland & Lauret, 2010, *Renewable Energy* 35(2), 478–483](https://doi.org/10.1016/j.renene.2009.07.018))
is competitive and cheap.

**Critical caveat.** All of that literature separates *measured* GHI. Ours is an NWP forecast: (a) the
separation model's own error is second-order vs. the NWP GHI error, and (b) NWP-native DNI is known to be
substantially worse than NWP GHI, because DNI error scales roughly as 1/(1−k) with cloud errors. Crucially,
Mayer & Gróf tested all combinations of 9 separation × 10 transposition × 5 temperature × 4 PV × 3 inverter
models on 16 Hungarian plants and found a total spread of only **13 % in MAE / 12 % in RMSE / 23–33 % in skill
score** across the *entire* chain ([Mayer & Gróf, 2021, *Applied Energy* 283, 116239](https://doi.org/10.1016/j.apenergy.2020.116239)).

**Recommendation.** Treat separation as a configurable, benchmarked switch
(`nwp_native | engerer2 | dirint | dirindex | boland`), selected per plant on the own holdout. Add **closure
enforcement** regardless of mode: `DNI = (GHI − DHI)/cos θz`, clipped to extraterrestrial DNI and to zero, so
the three components are always physically consistent (Open-Meteo's three fields come from different
post-processing paths and do not close). Prefer DIRINDEX over DIRINT when a clear-sky GHI series exists.

**Library.** `pvlib.irradiance.dirint / dirindex / erbs / boland / disc`. Engerer2 is not in pvlib
([JamieMBright/Engerer2-separation-model](https://github.com/JamieMBright/Engerer2-separation-model)); GISPLIT
via [jararias/splitting_models](https://github.com/jararias/splitting_models).

**Expected gain.** 0–3 % relative nMAE, site-dependent; the *variance reduction* from closure enforcement is
real and free. **Difficulty:** Low (switch + closure), Medium (Engerer2/GISPLIT port).

## 2. Transposition models

**State of the art.** The JRC validated 14 transposition models over European data and concluded explicitly
that "there is not a particular model that outperforms the rest for all the various scenarios considered"
([Gracia & Huld, 2013, JRC EUR 26075 EN](https://publications.europa.eu/resource/cellar/4ef8c4e1-4397-4e27-8487-448786327f27.0001.01/DOC_1)).
Clear-sky relative RMSD of good anisotropic models ≈4 %; bright overcast degrades to 22–23 % for *all* of them.
Perez et al. (1990) remains the industry default ([*Solar Energy* 44(5), 271–289](https://doi.org/10.1016/0038-092X(90)90055-H))
and is what PVsyst/SAM use; Hay-Davies is systematically slightly worse under partly cloudy skies because it
lacks the horizon-brightening term.

**Recommendation.** Default to **Perez**, keeping Hay-Davies selectable; prefer the **Driesse continuous
formulation** (`perez-driesse`), which removes discontinuities at ε-bin boundaries and is the better default
for a chain that is ML-corrected downstream ([Driesse et al., 2023, *Solar Energy*](https://www.sciencedirect.com/science/article/pii/S0038092X23007272)).
Two higher-value fixes alongside: (i) **time-varying albedo** — monthly climatology plus a snow-triggered
override (albedo → 0.6–0.8 when snow depth > 0); (ii) if bifacial, `pvlib.bifacial.infinite_sheds` rather than
a flat bifacial gain %.

**Expected gain.** Perez vs Hay-Davies ~1–2 % rel. RMSE on POA; albedo up to 1–3 % of annual energy at
high-tilt/snowy sites; infinite sheds 2–5 % on bifacial plants. **Difficulty:** Low / Low-Med / Medium.

## 3. Post-processing / MOS for NWP-to-power

**State of the art.** Mayer & Yang found that "post-processing GHI only leads to marginal, if that can be
quantified at all, benefits, **so long as the PV power forecasts are to be post-processed**"
([2024, *Applied Energy* 371, 123681](https://doi.org/10.1016/j.apenergy.2024.123681)). Horat, Klerings &
Lerch quantified the asymmetry: post-processing at the power stage gave ≈57 % CRPS reduction, GHI alone ≈7 %;
deterministic MAE improved ≈48 % ([2025, *Adv. Atmos. Sci.*](https://doi.org/10.1007/s00376-024-4219-2)). They
also found **hour-of-day structure is the single most valuable feature**, and that bias correction in the
morning/afternoon ramps dominates the gain. Mayer & Yang's hybridization review confirms ML-on-top-of-physics
beats both pure ML and pure physics ([*RSER* 168, 2022](https://doi.org/10.1016/j.rser.2022.112772)).

**Recommendation.** The current architecture (LightGBM on the physical residual, post-processing at the power
stage) *is* the configuration the literature endorses. Improve it in four ways:

1. **Normalize the target.** Train on the clear-sky-index residual:
   `target = (P_meas − P_phys) / P_clearsky_phys` for PV (clear-sky chain via `pvlib.clearsky.ineichen`,
   floored to avoid blow-up near sunrise); rated power for wind. Removes the deterministic diurnal/seasonal
   scale so trees spend capacity on cloud errors.
2. **Add documented high-value features:** lagged/leading NWP (t−3h, t−1h, t+1h, t+3h GHI and cloud cover);
   **spatial averaging** over a 3×3 stencil plus its std (cloud-variability index — cf.
   [Focken et al., 2002, *JWEIA* 90(3), 231–246](https://doi.org/10.1016/S0167-6105(01)00222-7));
   temporal differencing (Δ over ±1 h); total/low/mid/high cloud cover; RH at 700 hPa; per-model raw values.
3. **Analog ensemble as a second, cheap post-processor**
   ([Delle Monache et al., 2013, *MWR* 141(10), 3498–3516](https://doi.org/10.1175/MWR-D-12-00281.1);
   [Alessandrini et al., 2015, *Applied Energy* 157, 95–110](https://doi.org/10.1016/j.apenergy.2015.08.011)).
4. **Keep the chronological-holdout promotion gate, but change the metric** to RMSE-skill-score against a
   proper reference, and require the gain to hold in ≥3 of 4 seasons.

**Expected gain.** Feature expansion + kt* normalization: **10–25 % relative nMAE reduction**.
**Difficulty:** Low-Medium (features, normalization), Medium (AnEn).

## 4. Probabilistic forecasting

**State of the art.** Quantile-loss gradient boosting won or placed top in GEFCom2014 for wind and solar
([Landry et al., 2016, *IJF* 32(3), 1061–1066](https://doi.org/10.1016/j.ijforecast.2016.02.002);
[Nagy et al., 2016, *IJF* 32(3)](https://doi.org/10.1016/j.ijforecast.2015.11.013);
[Hong et al., 2016, *IJF* 32(3), 896–913](https://robjhyndman.com/papers/gefcom2014.pdf)). Verification
standards: [Lauret, David & Pinson, 2019, *Solar Energy* 194, 254–271](https://doi.org/10.1016/j.solener.2019.10.041).

**Recommendation.** Train LightGBM with `objective='quantile'` at τ ∈ {0.05, 0.1, 0.2 … 0.9, 0.95} on the same
normalized-residual target, add back to the physical forecast, **sort the quantiles** to remove crossing, and
wrap in **split conformal prediction** on a rolling calibration window.

**Formulas (implement exactly).**

- Pinball / quantile loss: `PL_τ(q, y) = τ·(y − q)` if `y ≥ q`, else `(1 − τ)·(q − y)`
- CRPS from a quantile set: `CRPS ≈ (2/M) · Σ_m PL_{τ_m}(q_{τ_m}, y)` for M equally spaced τ on (0,1),
  since `CRPS = 2∫₀¹ PL_τ dτ` ([Gneiting & Raftery, 2007, *JASA* 102(477), 359–378](https://doi.org/10.1198/016214506000001437))
- Reliability: for each τ, empirical coverage `ĉ_τ = (1/N)Σ 1{y_i ≤ q̂_{τ,i}}`; plot vs τ.
- Sharpness: mean interval width `(1/N)Σ (q̂_{0.9,i} − q̂_{0.1,i})`, reported **only** alongside reliability.
- Conformal: on a calibration set `s_i = |y_i − P̂50_i|`; `Q̂ =` the `⌈(n+1)(1−α)⌉`-th smallest `s_i`;
  interval `= P̂50 ± Q̂`. Normalized scores `s_i = |y_i − P̂50_i| / σ̂_i` give adaptive width.

**Expected gain.** Not nMAE — a new product. Typical: PICP within ±3 % of nominal after conformalization vs
10–20 % under-coverage for raw quantile regression. **Difficulty:** Medium.

## 5. Verification metric standards

[Yang et al., 2020, *Solar Energy* 210, 20–37](https://doi.org/10.1016/j.solener.2020.04.019) — a 20-author
consensus paper — is the standard, and it contradicts common practice in three ways:

1. **Do not use mean-normalized RMSE/MAE for cross-site comparison.** Normalize by **installed AC capacity**
   (`nMAE = MAE / P_AC,nom`). If mean-normalized values are also reported, the normalization constant must be
   tabulated. (Wind industry practice concurs: [Piotrowski et al., 2022, *Energies* 15(24), 9657](https://doi.org/10.3390/en15249657).)
2. **Exclude night-time always**, using a solar zenith filter of **θz < 85°**. Including zeros inflates
   apparent skill and is the most common source of unbelievably good published PV nMAE.
3. **Report a skill score against a proper reference:** `s = 1 − RMSE(f, x) / RMSE(r, x)` where the reference
   `r` is the **optimal convex combination of climatology and persistence in clear-sky-index space**, with
   weight equal to the lag-h autocorrelation ρ_h: `r = ρ_h·kt*(t) + (1−ρ_h)·k̄t*`, converted back to power by
   multiplying by the clear-sky power. Guaranteed `RMSE_cp ≤ min(RMSE_c, RMSE_p)`. For intra-day, smart
   persistence `kt*(t+h) = kt*(t)` is the right benchmark; for day-ahead the convex combination collapses
   toward climatology, which is the correct and much harder bar.

**Typical values.** Day-ahead, normalized by installed capacity: single **wind** farms 10–20 % RMSE (≥18 % in
complex terrain); single **PV** plants ~6–9 % MAE (plant-operator forecasts ~9 %, specialist providers ~6 %)
— [GET.transform, 2024](https://www.get-transform.eu/wp-content/uploads/2024/01/GET.transform-Brief_VRE-Forecasting-Solar-Wind.pdf).
Aggregation buys accuracy: spatial smoothing reduces error roughly as `1/√N` up to decorrelation distance.

**Curtailment and outages.** Compute and report three metric sets: (a) **all hours** (commercial/settlement
truth), (b) **available hours only**, excluding flagged curtailment/outage/derating — the only fair measure of
*model* skill, and (c) **daylight-only** for PV. Never train the residual model on curtailed or outage hours.
Detect curtailment as sustained flat-topped power below the physical expectation with `P/P_phys < 0.9` and low
variance; treat it as a mask, not a target.

**Difficulty:** Low. Accuracy gain: zero — but this is what stops shipping a model that is *worse* while the
dashboard says it improved.

## 6. Wind specifics

- **Profile.** A bisection-derived z₀ from paired 10/100 m speeds is a neutral-stability fit that silently
  absorbs stability into z₀. The theoretically correct form is `u(z) = (u*/κ)·[ln(z/z₀) − ψ_m(z/L)]`, but MOST
  breaks down in stable stratification above ~100 m ([WES 5, 391, 2020](https://doi.org/10.5194/wes-5-391-2020))
  and no heat flux is available. **Better and cheaper:** the **two-level power law**, exact through the two
  known points and monotone: `α = ln(u₁₀₀/u₁₀)/ln(100/10)`, `u(z) = u₁₀₀·(z/100)^α`, α clipped to [0.05, 0.6].
  Then let ML learn residual stability by adding **α, the 10 m/100 m ratio, and a stability proxy** as features.
- **Air density with humidity (IEC 61400-12-1):**
  `ρ = (1/T)·[P/R₀ − φ·P_w·(1/R₀ − 1/R_w)]`, `R₀ = 287.05`, `R_w = 461.5 J kg⁻¹K⁻¹`,
  `P_w = 0.0000205·exp(0.0631846·T)`. Neglecting humidity raises density MAE from ~0.185 % to ~0.203 %; using
  standard 1.225 kg m⁻³ instead of local density produced **+4.74 % AEP error**
  ([Floors & Nielsen, 2019, *Energies* 12(11), 2038](https://doi.org/10.3390/en12112038)).
- **Density correction: speed vs power.** IEC distinguishes **pitch-regulated** (virtually all modern
  machines) → correct the **speed**, `u = u₀·(ρ/ρ₀)^(1/3)` (leaves the rated plateau flat) vs
  **stall-regulated** → correct the **power**, `P = (ρ/ρ₀)·P₀(u)`. The current implementation is correct for
  pitch machines — make it configurable by regulation type.
- **Turbulence smoothing (highest-value wind change).** A power curve applied to an hourly *mean* speed
  systematically over-predicts near cut-in and under-predicts near rated, because the curve is convex then
  concave. Correct by convolving with the within-interval speed distribution:
  `P_eff(u) = ∫ P(v)·N(v; u, σ=TI·u) dv`, or the cheap 2nd-order form `P_eff(u) ≈ P(u) + ½·(TI·u)²·P''(u)`
  ([PCWG turbulence renormalisation](https://pcwg.org/consensus/Consensus%20Analysis%20Turbulence%20Renormalisation%20Documentation.pdf)).
  Estimate `TI ≈ 1/ln(z_hub/z₀)` if unmeasured (0.10–0.16 typical onshore).
- **Wakes with only turbine count and rated power.** Jensen/Park is the right fidelity:
  `ΔU/U = (1 − √(1 − C_T)) / (1 + 2k·x/D)²`, `k = 0.075` onshore. Without a layout, use a
  **direction-independent empirical array efficiency** or at least make wake loss **speed-dependent** — wake
  loss peaks between cut-in and rated (C_T ≈ 0.8) and collapses above rated. A constant % is the single
  biggest structural error in the wind chain
  ([Barthelmie et al., 2010, *JTECH* 27(8), 1302–1317](https://doi.org/10.1175/2010JTECHA1398.1)).

**Expected gain.** TI smoothing 2–5 % rel. RMSE; speed-dependent wake 1–3 %; humidity-corrected density
0.5–1 %; power law vs log law ~0 but removes failure modes.

## 7. PV specifics worth adding

- **Cell temperature.** **Faiman** ([2008, *Prog. Photovolt.* 16(4), 307–315](https://doi.org/10.1002/pip.813))
  has two parameters (U₀, U₁), is the IEC 61853-2 standard form, and is **fittable from own production data**.
  Temperature-model choice is a minor contributor (<2 % of the 13 % chain spread) — switch because Faiman is
  *fittable*, not because it is intrinsically better. `pvlib.temperature.faiman`. Gain 0.5–1.5 %, Low.
- **Inverter efficiency.** Replacing a constant efficiency with a load-dependent curve matters most at low
  irradiance — exactly the morning/evening hours where bias is largest. `pvlib.inverter.adr` (Driesse) or
  `pvlib.inverter.sandia`. Gain 0.5–2 % annual, more on low-light hours. Low.
- **Snow (high value at this latitude).** Marion's coverage model
  ([2013, *Solar Energy* 97, 112–121](https://doi.org/10.1016/j.solener.2013.07.029)); `pvlib.snow.coverage_nrel`
  + `pvlib.snow.dc_loss_nrel`; Open-Meteo provides `snowfall` and `snow_depth`. Negligible annually, but removes
  50–100 % errors on 5–20 days/year. Low-Medium.
- **Soiling.** `pvlib.soiling.kimber` (dry-period accumulation with rain reset) / `soiling.hsu` (PM-driven).
  In temperate Europe, Kimber with a 5 mm reset and ~0.1 %/day accumulation is adequate. Gain 0.3–1 %. Low.
- **Spectral correction.** `pvlib.spectrum.spectral_factor_firstsolar`; for c-Si in temperate Europe the annual
  effect is ≈ ±1 % with a seasonal signature the ML layer already partly absorbs. <1 %. Low.
- **Clipping topology.** If sub-arrays sit on *different* inverters/MPPTs, clipping must be applied per
  inverter, not to the total. (In this platform the spec states sub-arrays share one inverter, so the summed
  clipping is correct — but the topology should be explicit and configurable.)

## 8. Data-quality / measured-production QC

**State of the art.** NREL/Sandia's **PVAnalytics**
([Perry, Meyers & Anderson, 2022, NREL/CP-5K00-83824](https://docs.nrel.gov/docs/fy22osti/83824.pdf)) packages
QCRad limits, stale-value and interpolation detection, clipping detection, daytime inference, and **automated
time-shift detection** via changepoint analysis. Rule sets: QCPV
([Killinger et al., 2017, *Solar Energy* 143, 120–131](https://doi.org/10.1016/j.solener.2016.12.053));
[Livera et al., 2021, *Prog. Photovolt.* 29](https://doi.org/10.1002/pip.3349).

**Implement in this order, as a mandatory gate before ML training:**

1. **Timestamp/timezone shift** — cross-correlate measured power against modelled clear-sky power and find the
   lag maximizing correlation; a persistent ±1 h lag is a DST bug. **Highest-value check in the report** — an
   undetected 1 h shift makes every downstream model worse and is invisible in nMAE aggregates.
2. **Night-time non-zero** — flag `P > 0.01·P_nom` when θz > 95°. Metering offset or reversed sign.
3. **Stuck/stale values.**
4. **Clipping detection** — keep in physical validation but down-weight in residual training.
5. **Outage/curtailment** — `P_meas / P_phys < 0.9` sustained ≥2 h with low variance. Exclude from training;
   report separately.
6. **Outliers** — Hampel filter (robust, better than z-score here).
7. **Orientation sanity** — verify configured tilt/azimuth against the data.
8. **Data shifts** — capacity expansions and meter replacements poison a long training window.

**Expected gain.** Indirect but large: cleaning timestamp shifts and outage hours typically improves the
residual model by **5–15 % relative**, and it is the precondition for every other item being measurable.

## Prioritized ranking — top 10 by (expected gain) / (implementation cost)

| # | Change | Area | Expected gain | Cost |
|---|--------|------|---------------|------|
| 1 | **Data-quality gate before ML training** (timestamp-shift first, then night-nonzero, stale, clipping, outage flags) | §8 | 5–15 % rel. on the ML layer | Low |
| 2 | **Correct verification**: capacity-normalized, θz<85° daylight filter, RMSE skill score vs. convex climatology-persistence reference; curtailment-excluded metric set | §5 | 0 % accuracy, 100 % of the ability to *know* accuracy | Low |
| 3 | **Expand ML feature set**: kt*-normalized target, lagged/leading NWP, spatial mean/std, temporal differencing, cloud layers, RH700, per-model raw values | §3 | 10–25 % rel. nMAE | Low-Med |
| 4 | **Explicit inverter/clipping topology** (per-MPPT vs summed) | §7 | Removes a systematic bias on multi-orientation plants | Low |
| 5 | **Turbulence-intensity power-curve smoothing** | §6 | 2–5 % rel. RMSE (wind) | Low-Med |
| 6 | **Quantile LightGBM (9 levels) + split conformal → P10/P50/P90**, verified with pinball, CRPS, reliability, sharpness | §4 | New product; ±3 % coverage | Med |
| 7 | **Snow-cover loss + snow-triggered albedo** | §7, §2 | Removes 50–100 % errors on 5–20 winter days | Low-Med |
| 8 | **Perez-Driesse transposition + time-varying albedo**; separation-mode switch with closure | §1, §2 | 1–3 % rel. | Low |
| 9 | **Wind: two-level power law, humidity-corrected IEC density, speed-dependent wake, α as ML feature** | §6 | 2–4 % rel. combined | Low-Med |
| 10 | **ADR/Sandia inverter efficiency + Faiman temperature fitted to own data** | §7 | 1–3 % rel., concentrated in low-light hours | Low |

**Deliberately *not* recommended:** (a) chasing GISPLIT/Engerer2 — the whole model-chain choice space is worth
~13 % MAE while the ML layer alone is worth ~48 %; (b) post-processing GHI before conversion — Mayer & Yang
(2024) show it adds essentially nothing once power is post-processed.

---

### References (principal)

Alessandrini et al. (2015) *Applied Energy* 157, 95–110 · Bright & Engerer (2019) *JRSE* 11(3), 033701,
doi:10.1063/1.5097014 · Delle Monache et al. (2013) *MWR* 141(10), 3498–3516, doi:10.1175/MWR-D-12-00281.1 ·
Faiman (2008) *Prog. Photovolt.* 16(4), 307–315, doi:10.1002/pip.813 · Floors & Nielsen (2019) *Energies*
12(11), 2038, doi:10.3390/en12112038 · Focken et al. (2002) *JWEIA* 90(3), 231–246 · Gneiting & Raftery (2007)
*JASA* 102(477), 359–378, doi:10.1198/016214506000001437 · Gracia & Huld (2013) JRC EUR 26075 EN ·
Gueymard & Ruiz-Arias (2016) *Solar Energy* 128, 1–30, doi:10.1016/j.solener.2015.10.010 · Hong et al. (2016)
*IJF* 32(3), 896–913 · Horat, Klerings & Lerch (2025) *Adv. Atmos. Sci.*, doi:10.1007/s00376-024-4219-2 ·
Killinger et al. (2017) *Solar Energy* 143, 120–131 · Landry et al. (2016) *IJF* 32(3), 1061–1066 ·
Lauret, David & Pinson (2019) *Solar Energy* 194, 254–271 · Livera et al. (2021) *Prog. Photovolt.* 29,
doi:10.1002/pip.3349 · Marion et al. (2013) *Solar Energy* 97, 112–121, doi:10.1016/j.solener.2013.07.029 ·
Mayer & Gróf (2021) *Applied Energy* 283, 116239, doi:10.1016/j.apenergy.2020.116239 · Mayer & Yang (2022)
*RSER* 168, doi:10.1016/j.rser.2022.112772 · Mayer & Yang (2024) *Applied Energy* 371, 123681,
doi:10.1016/j.apenergy.2024.123681 · Perez et al. (1990) *Solar Energy* 44(5), 271–289 ·
Perry, Meyers & Anderson (2022) NREL/CP-5K00-83824 · Piotrowski et al. (2022) *Energies* 15(24), 9657 ·
Ridley, Boland & Lauret (2010) *Renewable Energy* 35(2), 478–483 · Ruiz-Arias & Gueymard (2024) *RSER* 202,
114678 · Yang et al. (2020) *Solar Energy* 210, 20–37, doi:10.1016/j.solener.2020.04.019 · IEC 61400-12-1:2022 ·
IEA Wind Task 36/51 Recommended Practices (2022).
