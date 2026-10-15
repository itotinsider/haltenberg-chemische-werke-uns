# HCW Air Separation Unit (ASU): A Simplified Process, Dynamics and DCS Reference for a Demo Simulator

**Bottom line:** For the demo, model HCW's Linde-type ASU as 6 process blocks (compression → front-end purification → main heat exchanger + expander → double column → product delivery/storage, plus utilities), about 12 PID loops and about 35 OPC UA tags, using first-order lags, integrators and a few lookup maps. You do not need rigorous thermodynamics. A nominal plant of about 1,000 t/d oxygen draws roughly 10–14 MW of electricity and feeds three plausible Verbund consumers: an ethylene oxide plant, an acetylene/syngas partial-oxidation plant, and the site-wide nitrogen network.

## TL;DR
- **Process in 6 blocks.** Air is filtered and compressed to about 5–6 bar, dried and decarbonated in switching molecular-sieve beds (2–12 h online), and cooled to about −175 °C in a brazed-aluminium heat exchanger. An expander supplies the refrigeration. The air is then separated in a Linde double column: the HP column runs at about 5–5.5 bar and the LP column at about 1.3 bar, linked by the condenser/reboiler. Outputs are GOX at 99.5%+, GAN with ppm-level O2, and LOX/LIN to tanks with vaporizer backup.
- **Simulator approach.** Use fast first-order lags for compressor flow and pressure (seconds), integrators for liquid levels (minutes), slow lags for purity (tens of minutes; argon takes hours) and a very slow lag for cold-box temperature (hours to days). Superimpose a 4-hour sawtooth for mole-sieve switching. Conventional ASUs ramp only about 0.25 %/min; Linde and Air Liquide have shown 5–8 %/min with special control. That gap is the natural story for APC/MPC.
- **Site context and KPI.** Linde Engineering data for conventional ASUs making >99.5% oxygen give a specific power of 0.33–0.35 kWh/Nm³ (222–245 kWh/t, depending on gas-company vs vendor convention). Typical installed plants run 0.4–0.6 kWh/Nm³. The main air compressor dominates both power use and the predictive-maintenance asset list. Consumers to model are an EO/EG plant (about 0.75–1.1 t O2 per t EO), a BASF-style acetylene partial-oxidation plant (about 3,400 Nm³ O2 per t acetylene) and the site N2 grid for inerting and the tank farm.

---

## 1. Reference context: ASUs on German Verbund sites

- Linde built the first ASU in 1902 and introduced the double-column rectification system in 1910. Its plants range from a few hundred t/d up to 5,500 t/d of oxygen, and its customized plants cover 1,000 t/d (30,000 Nm³/h) to 5,500 t/d (165,000 Nm³/h) of O2.\[1\]
- **BASF Ludwigshafen** "relies on various air separation units (ASU) from Linde". The oldest was built more than 50 years ago, has a footprint of almost 100 m², and stands 50 m tall. Linde PLANTSERV revamped its coldbox in a little over four months after BASF saw falling performance, problems with operational measurements, and ice on the coldbox shell. Linde's Jan Segatz notes that on large chemical complexes ASUs are "generally located directly on the customer site".\[2\]
- **Over-the-fence supply is common.** Air Liquide announced in February 2021 that it would invest around €40 million in a new ASU to supply oxygen and nitrogen to BASF Schwarzheide. dpa-AFX reported in September 2023 that the plant had gone on stream. Nippon Gases runs an ASU at CHEMPARK Dormagen that feeds its own O2/N2 pipeline network.
- **HCW framing for the demo.** HCW owns and operates the ASU itself, which is plausible for a centralized, OT-heavy company. If you prefer the over-the-fence model, have the gas company's remote operations centre (see §5) as a second data consumer. This fits the IT/OT data-ownership storyline.

**Nominal HCW demo plant (assumption, for consistency):** 1,000 t/d GOX ≈ 29,000 Nm³/h O2 at 99.5%. The air feed is about 5× the oxygen product, so about 145,000 Nm³/h of air.\[3\]

---

## 2. Simplified process flow (6 blocks)

| # | Block | What it does (1–3 sentences) | Key equipment |
|---|---|---|---|
| 1 | **Air intake and main air compressor (MAC)** | Ambient air passes a two-stage intake filter and is compressed in a multi-stage centrifugal (or axial) compressor to about 5–6 bar(a), within the 4–10 atm range typical across ASUs.\[4\] It is then cooled back to near ambient in a direct-contact aftercooler. Capacity is controlled by inlet guide vanes, and an antisurge recycle/blow-off valve is mandatory.\[5\] | Filter house, MAC, intercoolers, DCAC, cooling-water/chilled-water loop |
| 2 | **Front-end purification (PPU / mole sieve)** | Two adsorber vessels with alumina and 13X molecular sieve remove H2O, CO2, N2O and most hydrocarbons, including all acetylene, so nothing freezes or accumulates in the cold box. One bed is online while the other is heated and then cooled (TSA regeneration) with dry waste nitrogen. Online times are typically 2–12 h.\[5\] | 2 adsorbers, regeneration heater, switching valves, dust filter, CO2 analyzer |
| 3 | **Main heat exchanger (MHE) + expansion turbine** | In a brazed-aluminium plate-fin exchanger, clean air is cooled to about −172 to −176 °C (near its dew point) against the cold product and waste streams. A side stream is expanded in a turbo-expander, which produces most of the plant's refrigeration (over 90% in typical designs) and makes up for heat leak and liquid production.\[6\] The expander usually drives a booster compressor.\[7\]\[8\] | BAHX core(s), turbo-expander/booster, (optional) booster air compressor |
| 4 | **Linde double column** | HP column (about 5–5.5 bar): air separates into N2-rich vapour at the top and "rich liquid" (about 35–40% O2) at the bottom. The top N2 condenses in the **condenser/reboiler**, which in turn boils LOX in the LP column sump; this is the heat coupling that makes the double column work. The LP column (about 1.2–1.4 bar) receives the rich liquid and LIN reflux and produces pure O2 at the bottom and N2/waste N2 at the top.\[9\]\[10\]\[11\] | HP column, LP column, main condenser/reboiler, subcooler, reflux valves |
| 5 | **Products: GOX/GAN to site, LOX/LIN/LAR to tanks** | GOX and GAN are warmed back to ambient in the MHE and sent to the site pipeline grid, compressed if needed. Alternatively, LOX is pumped to pressure and vaporized in the MHE ("internal compression", a Linde innovation from 1978). A small liquid draw fills the LOX/LIN tanks. An optional crude/pure argon column side-draw from the LP column produces LAR.\[1\]\[5\] | Product compressors or LOX pumps, LOX/LIN/LAR tanks, backup vaporizers |
| 6 | **Backup and storage** | Cryogenic tanks plus ambient-air or water-bath vaporizers keep site supply going during ASU trips.\[12\] This is the buffer that decouples the slow ASU from fast-moving consumers. | Flat-bottom tanks, vaporizers, pressure-control station |

**Optional block:** argon column (crude + pure). For the demo, treat it as a single side-draw flow with a purity value that settles slowly over hours.

---

## 3. Key process variables and typical values (nominal ~1,000 t/d O2 plant)

| Variable | Typical value / range | Notes |
|---|---|---|
| Air feed flow | ~145,000 Nm³/h (≈5× O2 product) | Ratio stated in Air Products/IGCC patent literature\[3\] |
| MAC discharge pressure | 5.5–6.5 bar(a) | 4–10 atm across ASU types (CGA P-8 / AIGA 056)\[5\] |
| PPU cycle | 4 h online / 4 h regen (demo); 2–12 h real range | TSA; PSA versions switch in 5–30 min\[5\] |
| PPU outlet CO2 | <0.1 ppm normal; alarm 1 ppm; trip >10 ppm | CGA P-8 / AIGA 056 |
| MHE cold-end air temperature | −172 to −176 °C | Air enters HP column near dew point |
| MHE warm-end ΔT | 1.5–4 K | Rising ΔT indicates fouling or imbalance\[10\] |
| HP column top pressure | 5.0–5.5 bar(a) (range 5.0–6.5) | Linde patent example: 5.3 bar HP / 1.35 bar LP\[11\] |
| LP column top pressure | 1.2–1.4 bar(a) | Range 1.1–1.5 bar\[13\] |
| HP column bottom (rich liquid) | ~35–40% O2, ≈ −173 °C | |
| LP sump (LOX) temperature | ≈ −180 °C at ~1.4 bar | O2 boiling point is −183 °C at 1 atm |
| HP column top (N2) temperature | ≈ −177 °C at ~5.3 bar | Condenses against LOX boiling about 1–2 K colder |
| GOX purity | 99.5–99.8% O2 | Main quality spec |
| GAN purity | 1–10 ppm O2 (high-purity N2) | Waste N2 ~1–2% O2 |
| LOX/LIN liquid make | 2–5% of O2 product | Limited by refrigeration |
| Reboiler (LP sump) level | 60–80% of range; must keep reboiler submerged | Safety-critical |
| HP sump level | 40–60% | Level control sets rich-liquid flow to LP |
| Turbo-expander speed | ~10,000–60,000 rpm (size-dependent) | Small units run at 52,000–60,000 rpm; large ones run slower\[8\] |
| Expander inlet / outlet T | ~−110 °C / ~−175 °C | |
| LOX purge from reboiler | ≥0.1–0.2% of O2 product (continuous) | Hydrocarbon flush; pumped-LOX designs get this automatically\[5\] |
| Total hydrocarbons in LOX | Alarm ~250 ppm; limit ~450–500 ppm CH4-equivalent | Vendor-specific\[14\]\[15\] |
| Specific power | 0.33–0.35 kWh/Nm³ O2 (vendor best) to 0.4–0.6 kWh/Nm³ (typical) | ≈245–420 kWh/t\[16\]\[17\]\[18\] |
| Total electrical load | ~10–14 MW | MAC ≈ 80–90% of it\[19\] |

**Interpretation for the demo:** only about six numbers drive everything else. These are air flow, MAC pressure, HP and LP pressures, the LIN reflux split, and the expander flow. Make these the "handles", and make purities, levels and power the visible consequences.

---

## 4. Process dynamics for a simple simulator

### 4.1 Fast vs slow (suggested demo time constants)

| Variable group | Response | Suggested model |
|---|---|---|
| MAC flow/pressure, valve positions | Seconds (5–30 s) | First-order lag, τ ≈ 10 s |
| Expander speed | Seconds | First-order lag on flow, τ ≈ 5 s |
| HP/LP column pressures | 1–5 min | First-order lag (gas capacitance) |
| Sump and reboiler levels | Integrating (minutes to tens of minutes) | dL/dt = (in − out)/area |
| O2/N2 product purity | 15–60 min | First-order lag + dead time (5–10 min) on a static purity map |
| Argon purity | Hours | τ ≈ 2–4 h |
| Cold-box / MHE metal temperature | Hours to days | τ ≈ 6–24 h; energy balance on lumped thermal mass |
| Mole-sieve breakthrough | Periodic (2–12 h cycle) | Sawtooth CO2 + switching transients |

### 4.2 Key couplings to reproduce
- **Air flow → everything.** O2 product ≈ air × 0.2095 × recovery (95–99%). If GOX withdrawal exceeds what the air supports, purity collapses.
- **Reflux split vs purity.** More LIN to the LP top gives purer GAN and LP-top N2. Too little reflux lets O2 slip into the N2; too much pushes N2 into the LOX.
- **Pressure ↔ temperature.** Column temperatures follow saturation pressure. HP pressure must stay high enough for HP-top N2 to condense against LP-sump LOX. If the condenser/reboiler ΔT closes, the column "loses its heat pump".
- **Refrigeration balance.** Expander flow + liquid draw + heat leak sets the slow drift of the liquid inventory. Drawing more LOX/LIN without more expander duty drains the sump levels over hours.
- **Inverse response.** Feed-effluent heat exchange in the MHE can make cold-end temperatures respond the wrong way at first.\[20\] This is a nice optional "gotcha" for an MPC story.

### 4.3 Disturbances and upsets to script
1. **Ambient temperature swing** (day/night, summer). Hot, humid air lowers MAC capacity and raises the water load on the PPU, since air's water content roughly doubles for every 10 °C rise. The result is earlier CO2 breakthrough.\[5\]
2. **MAC trip.** Air flow drops to zero within seconds. Column pressures decay over minutes, and backup vaporizers must start to hold site supply.
3. **Consumer demand swing** (EO plant step, acetylene burner trip). GOX header pressure moves first, then the ASU load controller responds slowly.
4. **Mole-sieve breakthrough** (incomplete regeneration, heater fault). CO2 at the PPU outlet rises, giving an alarm at 1 ppm and a trip at 10 ppm.\[5\]
5. **MHE fouling/icing** (moisture or CO2 slip). Warm-end ΔT and pressure drop creep up over weeks, which makes a good PdM/efficiency-drift signal. The BASF case shows the related failure of ice forming on the coldbox shell from insulation loss.\[2\]
6. **Expander trip.** Refrigeration is lost, and over hours the levels fall and liquid production must be cut.
7. **Hydrocarbon event** (nearby flare, haze). Total hydrocarbons in LOX rise. The response is to maximise the LOX purge and, if the limit is exceeded, shut down.\[5\]

### 4.4 Start-up, shutdown and load change
- **Cold start** (after a trip of hours to a few days): the cold box is still cold, and full purity returns in roughly 6–24 h.\[21\]
- **Warm start** (after derime or maintenance): the whole cold box must be cooled down, which takes typically 1–3 days. The literature puts start-up times "on the order of 20 hours or more".\[22\]
- **Turndown:** typically down to 60–70% of design, and some designs reach 50%.\[3\]\[23\] Conventional load change runs at about 0.25 %/min. Air Liquide reported "fast load change" above 5 %/min. Linde reported 8 %/min between 75% and 100% load "without appreciable fluctuation of oxygen purity". Linde's FLEXASU plant in Vejle has operated since August 2020. Linde's execution project manager Frank Stumpf-Marquardt says its components were "designed and certified to withstand around 10,000 pressure load changes during their 25-year service lifetime", over a pressure range of 70 to 6 bar.
- **How operators change load:** they ramp the air flow setpoint, pre-move the reflux and expander setpoints with feed-forward, keep the LP pressure constant, and watch purity. APC/MPC automates exactly this.

### 4.5 Recommended modelling recipe (demo-grade)
- **Compressor:** P_dis = f(IGV, speed) as a static map, followed by a first-order lag. Power = k × air_flow × ln(P_dis/P_in) / η, with η drifting down slowly to simulate fouling.
- **PPU:** two-state machine (A online / B regen) with timers. CO2_out = base + breakthrough term that grows exponentially near end-of-cycle (scaled by humidity). Add a short pressure dip and temperature bump at each switchover.
- **Cold box:** one lumped thermal mass, dT/dt = (Q_refrigeration − Q_leak − Q_liquid_draw)/C.
- **Columns:** HP and LP pressures as first-order lags on (vapour in − vapour out). Purities as a static logistic map of (reflux ratio, O2 draw/air ratio) passed through first-order lag + dead time.
- **Levels:** pure integrators with PID level control.
- **Tanks:** integrator: level += (liquid make − vaporizer send-out). Vaporizers start automatically when GOX header pressure is low.
- **Noise:** add small Gaussian noise plus slow random walks on ambient temperature and demand. These make the historian trends look real.

---

## 5. Control strategy for a simple DCS

### 5.1 Basic PID loops (~12)
1. **MAC air flow (FIC-101)** → IGV position. Master load setpoint.
2. **MAC discharge pressure (PIC-102)** → overrides/limits flow.
3. **MAC antisurge (UIC-103)** → recycle/blow-off valve. Mandatory on all axial/centrifugal machines.\[5\]
4. **PPU sequence (KS-201)** → timer-based switching valves, with interlocks on bed pressure equalisation.
5. **Regeneration temperature (TIC-202)** → heater.
6. **Expander flow/speed (FIC-301 / SIC-302)** → inlet nozzles (IGV). Sets refrigeration.
7. **HP column pressure (PIC-401)** → mostly a result of air flow; often only indicated.
8. **LP column pressure (PIC-402)** → waste N2 vent valve.
9. **HP sump level (LIC-403)** → rich-liquid valve to LP column.
10. **Reboiler/LP sump level (LIC-404)** → GOX draw or LOX draw (critical).
11. **LIN reflux to LP (FIC-405)** → reflux valve, cascaded from N2 purity (AIC-602).
12. **GOX/GAN delivery pressure (PIC-501/502)** → product valve or compressor, with backup vaporizer split-range.

Purity cascades: GOX purity (AIC-601) trims the O2 draw-to-air ratio, and N2 purity (AIC-602) trims LIN reflux. Both are slow loops, typically tuned with minutes of integral time.

### 5.2 Interlocks and trips (demo set)
- MAC: antisurge, high-high vibration, low lube-oil pressure, high bearing temperature, motor overload.\[5\]
- PPU: CO2 >1 ppm alarm; trip if >10 ppm or if beds cannot be switched within 30 min of the alarm; high air outlet temperature trip (protects the cold box); valve-position/pressure-equalisation permissives.\[5\]
- Expander: overspeed (loss of loading), low lube-oil pressure, high vibration, abnormal bearing temperature.\[5\]
- Column/reboiler: low reboiler level (submergence), high HP-column level, high total hydrocarbons / acetylene in LP sump liquid, low LOX purge flow.\[5\]
- Products: high O2 in GAN → divert to vent; low suction pressure on product compressors (protects LP column from vacuum).\[5\]
- Acetylene: a PPU removes all of it, but if it enters the cold box it "precipitates out as a solid at concentrations as low as 4 ppm to 6 ppm". Operators therefore alarm well below this, at about 0.3–0.5 ppm in the reboiler sump.\[5\]\[24\]\[25\]

### 5.3 Role of APC/MPC
- **Why:** ASUs are strongly coupled, nonlinear, slow and power-hungry. MPC handles multivariable load changes (air, expander split, reflux, drain) within purity constraints and minimises kWh/Nm³.\[26\]\[27\]
- **Evidence:** Linde-linked academic work with RWTH Aachen (Mitsos group) and TUM includes economic NMPC under fluctuating electricity prices, Koopman/ML reduced models that enable real-time NMPC "at an average CPU time decrease by 98%", and a pressure-driven digital twin. Its manipulated variables are exactly the demo handles: air feed, turbine split, reflux fraction and reboiler drain.\[6\]\[27\]\[28\]\[29\]\[30\] Industrial two-layer MPC for automatic load change has been deployed on Chinese ASUs.\[26\]
- **Industry operating model:** Air Liquide opened its Saint-Priest remote operation centre in January 2017 under the €20 million Connect project. It remotely manages production for 22 units in France and is "capable of stopping or restarting a site remotely". It adapts production to customer demand and uses big data for predictive maintenance by "identifying the weak signals that precede a malfunction". Air Liquide uses AVEVA PRiSM for its SIO.Predict analytics.\[31\]
- **Demo suggestion:** expose an "APC ON/OFF" switch. With it OFF, the operator ramps load at 0.25 %/min by hand. With it ON, the MPC ramps at 3–5 %/min with less purity overshoot and lower kWh/Nm³.

### 5.4 Suggested tag list (OPC UA namespace e.g. `HCW.ASU1.<Tag>`)

| Tag | Description | Unit | Range | Normal |
|---|---|---|---|---|
| FIC-101 | MAC air flow | kNm³/h | 0–180 | 145 |
| PIC-102 | MAC discharge pressure | bar(a) | 0–8 | 5.8 |
| UIC-103 | MAC antisurge controller output | % | 0–100 | 0–5 |
| JI-104 | MAC motor power | MW | 0–15 | 10.5 |
| VI-105 | MAC bearing vibration (DE) | mm/s | 0–25 | 2.5 |
| TI-106 | MAC bearing temperature | °C | 0–120 | 75 |
| PI-107 | MAC lube-oil pressure | bar(g) | 0–4 | 2.0 |
| TI-108 | Air temperature after DCAC | °C | 0–40 | 12 |
| TI-109 | Ambient air temperature | °C | −20–45 | 15 |
| KS-201 | PPU active bed (A/B) + step | enum | — | A-ONLINE |
| AI-202 | CO2 at PPU outlet | ppm | 0–20 | 0.05 |
| PDI-203 | PPU bed differential pressure | mbar | 0–200 | 60 |
| TIC-204 | Regeneration gas temperature | °C | 0–250 | 180 (heat step) |
| ZI-205 | Switching valve stroke time (last) | s | 0–60 | 12 |
| TI-301 | MHE cold-end air temperature | °C | −200–40 | −174 |
| TDI-302 | MHE warm-end ΔT | K | 0–15 | 3 |
| FIC-303 | Expander flow | kNm³/h | 0–30 | 18 |
| SI-304 | Expander speed | rpm | 0–40,000 | 25,000 |
| VI-305 | Expander vibration | µm | 0–100 | 15 |
| TI-306 | Expander bearing temperature | °C | 0–120 | 60 |
| PIC-401 | HP column top pressure | bar(a) | 0–8 | 5.3 |
| PIC-402 | LP column top pressure | bar(a) | 0–3 | 1.35 |
| LIC-403 | HP column sump level | % | 0–100 | 50 |
| LIC-404 | Reboiler / LP sump level | % | 0–100 | 70 |
| FIC-405 | LIN reflux to LP column | kNm³/h | 0–60 | 40 |
| TI-406 | LP sump (LOX) temperature | °C | −200–−150 | −180 |
| FIC-501 | GOX product flow | kNm³/h | 0–35 | 29 |
| PIC-502 | GOX header pressure | bar(g) | 0–40 | 30 |
| FIC-503 | GAN product flow | kNm³/h | 0–60 | 25 |
| PIC-504 | GAN header pressure | bar(g) | 0–12 | 8 |
| LI-505 | LOX tank level | % | 0–100 | 65 |
| LI-506 | LIN tank level | % | 0–100 | 60 |
| FIC-507 | Backup vaporizer send-out | kNm³/h | 0–30 | 0 |
| AIC-601 | GOX purity | % O2 | 95–100 | 99.6 |
| AIC-602 | O2 in GAN | ppm | 0–100 | 3 |
| AI-603 | Total hydrocarbons in LOX | ppm CH4-eq | 0–600 | 40 |
| AI-604 | Acetylene in LOX | ppm | 0–2 | <0.05 |
| KPI-701 | Specific power | kWh/Nm³ O2 | 0–1 | 0.36 |

Note: SI-304 is set for a large-plant expander (~25,000 rpm). Change it if you prefer a small high-speed unit.

---

## 6. Assets and condition-monitoring signals (PdM demo)

| Asset | Typical failure modes | Condition signals |
|---|---|---|
| Main air compressor (+ gearbox, motor) | Bearing wear, imbalance/misalignment, impeller fouling/erosion, intercooler fouling, surge events, lube-oil degradation\[32\]\[33\] | Shaft vibration (proximity probes, mandatory), bearing temperature, lube-oil pressure/temperature/filter ΔP, oil analysis, polytropic efficiency drift, motor current, surge-margin events\[5\] |
| Booster air compressor | As MAC | As MAC |
| Turbo-expander / booster | Loss of loading → overspeed, bearing failure, oil ingress to process, ice/CO2 fouling of nozzles, seal-gas loss\[5\] | Speed, vibration, bearing temperatures, seal-gas ΔP, isentropic efficiency (ΔT vs ΔP) |
| Mole-sieve adsorbers + switching valves | Adsorbent ageing/dusting, incomplete regeneration, valve leakage or slow stroke, heater failure | CO2 breakthrough time trend, bed ΔP, valve stroke times, regen outlet temperature peak, heater power |
| Cold box (MHE, columns, piping) | Insulation degradation (cold spots/ice on casing), BAHX fouling or leaks, flooding\[2\] | Warm-end ΔT, ΔP across MHE, casing thermography, perlite settling, product purity drift |
| LOX/LIN pumps | Cavitation (low NPSH), seal wear, bearing failure\[5\] | Vibration, discharge pressure stability, motor current, seal-purge flow |
| Vaporizers | Icing (ambient), water-bath heater faults | Outlet temperature, ΔP, bath temperature |
| Cooling tower / DCAC | Fouling, fan/bearing faults, DCAC level-control failure (water carry-over into the PPU)\[5\] | Approach temperature, fan vibration, DCAC level, PPU inlet humidity |

**Data-placement note (brief):** process and machine tags live in the DCS and the OT historians (high-frequency vibration sometimes sits in a separate machine-protection system such as a Bently-type rack, which is a classic IIoT-landing debate). Lab cross-checks of O2/N2 purity and periodic GC hydrocarbon analyses of LOX sit in LIMS. Delivered product volumes per consumer, electricity (MWh) and maintenance orders sit in ERP. The PdM initiative needs all three systems joined on a common asset model (e.g., tag → equipment → functional location). That join is where the IT/OT data-model ownership question becomes concrete.

---

## 7. Energy KPI

- **Benchmarks:** for >99.5% O2 from a conventional ASU, Linde Engineering data (Beysel, 1st Oxyfuel Conference, Cottbus) give 0.35 kWh/Nm³ (245 kWh/t) in the vendor convention and 0.33 kWh/Nm³ (222 kWh/t) in the gas-company convention, and about 0.25 kWh/Nm³ for energy-optimized 95% O2 plants. Typical installed plants run 0.4–0.6 kWh/Nm³.\[16\]\[18\] A study of three operating ASUs measured 0.464–0.639 kWh/Nm³ against a 0.28 kWh/Nm³ benchmark.\[34\]
- **Where the power goes:** the MAC plus booster account for about 86% of energy in one case study,\[35\] and MAC power makes up about 70–90% of ASU operating cost.\[36\]
- **Trend:** Linde reports a 15% drop in average ASU power consumption over the last 10 years.\[1\]
- **Demo KPI:** KPI-701 = JI-104 (+ booster + product compression) ÷ FIC-501. Show it worsening with MAC efficiency drift, PPU over-regeneration, high liquid make and part load. This is the bridge between operations, energy and PdM.

---

## 8. Consumer plants on the HCW site (Part 2)

| # | Plant | Consumes | Rough demand (demo-scaled) | Why it fits a Verbund site |
|---|---|---|---|---|
| 1 | **Ethylene oxide / ethylene glycol (EO/EG)** | GOX (99.5%) at ~20–30 bar\[37\] | 0.75–1.1 t O2 per t EO (EU JRC BREF data). A 200 kt/yr EO plant needs about 500 t/d O2, about half the ASU.\[38\] | Takes ethylene from the site cracker, feeds surfactants/glycols/amines downstream, and exports reaction steam to the grid. BASF runs EO in Ludwigshafen and Antwerp with a combined 845,000 t/yr. Very sensitive to O2 supply interruptions, so it is the obvious "VIP" consumer.\[39\]\[40\] |
| 2 | **Acetylene + syngas by partial oxidation (BASF Sachsse-Bartholomé type)** | GOX | About 3,400 Nm³ O2 per t acetylene (≈4.9 t O2/t, per Ullmann's). A 30 kt/yr unit needs about 400 t/d O2.\[41\] | This is a real Ludwigshafen archetype. BASF's site page describes its new 90,000 t/yr acetylene plant (on stream October 2020, replacing a 1964 plant) as made "from natural gas and oxygen" and states that "around 20 plants in BASF's Production Verbund process acetylene and its derivatives". The by-product syngas feeds further Verbund chemistry (e.g., methanol or oxo).\[42\]\[43\]\[44\] Burner trips create sharp O2 demand steps, a good disturbance for the simulator. |
| 3 | **Site nitrogen grid: inerting, blanketing, purging + central tank farm** | GAN (low-pressure network); LIN for large or peak jobs | Base load ~5,000–15,000 Nm³/h, with spikes during turnarounds. Linde cites 1,000–12,000 Nm³/h for large inerting operations.\[45\] | Every plant on a chemical site uses N2 for tank blanketing, reactor and line purging, pigging, and catalyst handling.\[46\]\[47\] Demand is many small, spiky consumers, so it suits a header-pressure loop with LIN backup. |
| 4 (optional) | **Ammonia from imported/electrolytic H2, or argon users** | GAN (0.82 t N2/t NH3 stoichiometric); LAR | A 300 kt/yr NH3 loop needs about 670 t/d N2. Argon is small (workshops, welding, specialty inerting). | Classic natural-gas ammonia takes its N2 from process air, so an ASU-fed ammonia plant only makes sense for a "green ammonia" storyline. Ludwigshafen's ammonia capacity was reported at 880,000 t/yr in 2022, and BASF has since idled part of it.\[48\]\[49\] |

**Plausibility check:** EO (about 500 t/d) plus acetylene POX (about 400 t/d) is about 900 t/d O2, which matches a 1,000 t/d ASU with about 10% margin. The ASU's GAN covers the site N2 grid. This gives a self-consistent mass balance for the demo.

---

## 9. Recommendations for the build

1. **Start with 6 blocks and about 35 tags.** Resist adding the argon column until the core loop runs well.
2. **Model three time scales explicitly** (seconds, minutes/hours, hours/days). This is what makes the trends feel like an ASU, not a generic tank.
3. **Script four scenarios:** EO demand step, MAC trip with vaporizer backup, mole-sieve breakthrough, and slow MAC efficiency drift for PdM. These map one-to-one onto operations, safety, data and AI storylines.
4. **Publish via OPC UA with an ISA-style hierarchy** (Site/Area/Unit/Equipment/Tag) and engineering units in metadata. Then demonstrate that "an OPC UA connector" alone does not answer who owns the asset model, the LIMS join, or the ERP volume reconciliation.

## 10. Caveats

- All numbers are **representative**, not design data. Real values depend on cycle (GOX vs pumped-LOX), vendor and product slate. Time constants in §4 are demo assumptions informed by the literature, not measured plant data.
- Hydrocarbon and acetylene limits vary by vendor and reboiler type (EIGA Doc 65 / AIGA 035).\[24\]\[50\] Treat the demo thresholds as illustrative.
- Consumer demand figures are scaled for a fictional site. The BASF figures (acetylene 90 kt/yr, EO 845 kt/yr combined with Antwerp, ammonia 880 kt/yr in 2022) come from BASF releases and bank/press reports and may have changed since.\[40\]\[48\]\[51\]\[52\]
- Expander speeds vary widely with machine size. The 52,000–60,000 rpm figures come from small-unit studies.\[8\]

## Sources

1. [Air Separation Plants](https://www.linde-engineering.com/products-and-services/process-plants/air-separation-plants)
2. [Ageing Coldbox Gets a Makeover](https://www.linde-engineering.com/products-and-services/success-stories/2024/ageing-coldbox-gets-a-makeover)
3. [Air separation unit feed flow control in an IGCC power generation system](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/5983668)
4. [Integrating an air separation unit into an oxygenate-to-olefins reaction system](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/7479468)
5. [SAFE PRACTICES GUIDE FOR CRYOGENIC AIR SEPARATION PLANTS](https://asiaiga.org/uploaded_docs/AIGA_056_14%20Safe%20practices%20guide%20for%20cryogenic%20air%20separation%20plants.pdf)
6. [End-to-End Reinforcement Learning of Koopman Models for eNMPC of an Air Separation Unit](https://arxiv.org/pdf/2511.04522)
7. [Enhancing refrigeration capacity of turbo expander by means of multipoint design optimization - ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0140700719303846)
8. [Off-design performance analysis of cryogenic turbo-expander based on mathematic prediction and experiment research - ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1359431117323864)
9. [Analysis of Air Separation Equipment and Cryogenic Air Separation Process - Minnuo](https://minnuogas.com/analysis-of-air-separation-equipment-and-cryogenic-air-separation-process/)
10. [Cryogenic Air Separation for Oxygen Production](https://www.shengerhk.com/en/cryogenic-air-separation-for-oxygen-production/)
11. [Method and device for the low-temperature separation of air at variable energy consumption](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10458702)
12. [Modular Air Separation Plants](https://www.linde-engineering.com/products-and-services/process-plants/air-separation-plants/modular-air-separation-plants)
13. [Method and apparatus for the low-temperature fractionation of air](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/5669237)
14. [Refinery hydrogen from petcoke gasification - the lightest of gases from the heaviest of residues](https://www.hydrocarbonengineering.com/special-reports/11062020/refinery-hydrogen-from-petcoke-gasification--the-lightest-of-gases-from-the-heaviest-of-residues/)
15. [(PDF) Safe design and operation of a Cryogenic Air Separation Unit](https://www.researchgate.net/publication/227599641_Safe_design_and_operation_of_a_Cryogenic_Air_Separation_Unit)
16. [A novel low-energy, low-cost air separation unit integrating heat pump recovery and off-peak cold storage: An energy, exergy, and economic performance analysis - ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0360544225030051)
17. [Enhanced Cryogenic Air Separation Enhanced Cryogenic Air Separation](https://publications.ieaghg.org/docs/oxyfuel/OCC1/Plenary%201/Beysel_ASU_1stOxyfuel%20Cottbus.pdf)
18. [Techno-economic analysis of low-cost air separation unit with peak-shaving](https://indico.cern.ch/event/1296489/contributions/5881380/attachments/2901636/5090532/06%20-%20Zhikang%20Wang-Tue-Or6.pdf)
19. [Air Separation Unit Explained: Engineering Principles and Industrial Gas Production - EPCLand](https://epcland.com/air-separation-unit/)
20. [Abstract: Rigorous Dynamic Modeling and Model Predictive Control of Air Separation Unit as Part of IGCC Power Plant (2009 Annual Meeting)](https://aiche.confex.com/aiche/2009/webprogram/Paper170446.html)
21. [Improving Agility of Cryogenic Air Separation Plants](https://www.researchgate.net/publication/231375235_Improving_Agility_of_Cryogenic_Air_Separation_Plants)
22. [(740d) Optimal Start-up of Cryogenic Air Separation Units](https://proceedings.aiche.org/conferences/aiche-annual-meeting/2019/proceeding/paper/740d-optimal-start-cryogenic-air-separation-units)
23. [System and method for operating an integrated gas turbine and cryogenic air separation plant under turndown conditions](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/5501078)
24. [SAFE OPERATION OF REBOILERS/CONDENSERS IN AIR SEPARATION UNITS](https://asiaiga.org/uploaded_docs/AIGA%20035_06%20Safe%20operation%20of%20reboilers_condensers%20in%20ASUs_%20reformat%20Apr%2011.pdf)
25. [Comparisons of thermodynamic and economic performances of cryogenic air separation plants designed for external and internal compression of oxygen - ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1359431119313286)
26. [Automatic load change coordinated control of air separation units - ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0967066118301370)
27. [Economic Nonlinear Model Predictive Control for Flexible Operation of Air Separation Units - ScienceDirect](https://www.sciencedirect.com/science/article/pii/S240589631832682X)
28. [\[2309.05386\] Data-Driven Model Reduction and Nonlinear Model Predictive Control of an Air Separation Unit by Applied Koopman Theory](https://arxiv.org/abs/2309.05386)
29. [Data-Driven Model Reduction and Nonlinear Model Predictive Control of an Air Separation Unit by Applied Koopman Theory](https://arxiv.org/pdf/2309.05386)
30. [Flexible Operation of Air Separation Units\<link href='#cben202100023-note-0001'\> \</link\> - Klein - 2021 - ChemBioEng Reviews - Wiley Online Library](https://onlinelibrary.wiley.com/doi/full/10.1002/cben.202100023)
31. [Air Liquide’s Internal Digital Revolution](https://www.automationworld.com/home/article/13319060/air-liquides-internal-digital-revolution)
32. [An Overview of Air Separation Machine Compressors: Standards, Grades, and Mechanical Performance](https://www.alibaba.com/product-insights/air-separation-machine-compressors.html)
33. [How to ID predictive maintenance strategies for compressed air systems - Plant Engineering](https://www.plantengineering.com/how-to-id-predictive-maintenance-strategies-for-compressed-air-systems/)
34. [Oxygen Specific Power Consumption Comparison for Air Separation Units](https://engj.org/index.php/ej/article/view/400)
35. [Energy Saving Opportunities in an Air Separation Process](https://docs.lib.purdue.edu/cgi/viewcontent.cgi?article=2130&context=iracc)
36. [Development of Two-Phase Dense Fluid Expander](https://www.netl.doe.gov/sites/default/files/2017-11/20170320_1400C_Presentation_FE0026186_AirProducts.pdf)
37. [Ethylene Oxide Production from Ethylene (Pure Oxygen as Oxidizer) Report](https://cdn.intratec.us/docs/reports/previews/eo-e11a-b.pdf)
38. <https://www.pbl.nl/sites/default/files/downloads/pbl-2020-decarbonisation-options-for-large-volume-organic-chemicals-production-shell-moerdijk_3483.pdf>
39. [BASF pursues a capacity expansion of the integrated ethylene oxide complex at its Verbund site in Antwerp](https://www.basf.com/global/en/media/news-releases/2018/07/p-18-273)
40. [BASF to expand integrated ethylene oxide and derivatives complex at its Verbund site in Antwerp](https://www.basf.com/global/en/media/news-releases/2019/09/p-19-336)
41. <https://www.ugr.es/~tep028/pqi/descargas/Industria%20quimica%20organica/tema_3/acetileno_a01_097.pdf>
42. [Process for the manufacture of acetylene by partial oxidation of hydrocarbons](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/8017823)
43. <https://cdn.intratec.us/docs/reports/previews/acetylene-e11a-b.pdf>
44. [The Acetylene Plant](https://www.basf.com/global/en/who-we-are/organization/locations/europe/german-sites/ludwigshafen/the-site/production/the-production-verbund/new_acetylene_facility)
45. [Inerting in the chemical industry.](https://static.prd.echannel.linde.com/wcsstore/DE_REC_Industrial_Gas_Store/Assets/anwendungen/White-paper-Inerting-chemical-industry-Englisch.PDF)
46. [Nitrogen Purging and Inerting](https://assets.linde.com/-/media/global/usa/linde-us/literature/chemicals/p-40-4567-nitrogen-purging-and-inerting_linde-final---x.pdf)
47. [PSA Nitrogen for Chemical Plants](https://mentiseng.com/en/psa-nitrogen-chemical-petrochemical-tank-blanketing-inerting-purging)
48. [BASF’s Antwerp, US ammonia output could offset potential shutdown in Germany - bank](https://www.icis.com/explore/resources/news/2022/06/28/10779322/basf-s-antwerp-us-ammonia-output-could-offset-potential-shutdown-in-germany-bank/)
49. [BASF Taps International Process Plants to Offload Ludwigshafen Assets](https://www.chemicalprocessing.com/industrynews/news/55037920/basf-taps-international-process-plants-to-offload-ludwigshafen-assets)
50. [SAFE OPERATION OF REBOILERS/CONDENSERS IN AIR SEPARATION UNITS Doc 65/20](https://www.eiga.eu/uploads/documents/DOC065.pdf)
51. [The new Acetylene Plant](https://www.basf.com/no/en/who-we-are/organization/locations/europe/german-sites/ludwigshafen/production/the-production-verbund/new_acetylene_facility)
52. [The new BASF Acetylene Plant in Ludwigshafen](https://chemicalparks.eu/news/new-basf-acetylene-plant-ludwigshafen)
