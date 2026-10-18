# HCW Air Separation Unit (ASU): A Simplified Process, Dynamics and DCS Reference for a Demo Simulator

> **Status (October 2026).** This document was the design basis for the HCW ASU1 simulator. ASU1 is now
> built, and in a few places the build went further than or differs from the original proposal: oxygen by
> internal compression instead of a GOX compressor, a nitrogen compressor, pumped backups, 19 controllers
> instead of ~12, 61 tags instead of ~35. §0 summarises the as-built plant, and *As built* notes in the
> sections below mark each difference. The research itself (values, literature, sources) is unchanged.
> The authoritative tag list is the generated interface inventory, [inventory/tags.csv](inventory/tags.csv).

**Bottom line:** For the demo, model HCW's Linde-type ASU as 6 process blocks (compression → front-end purification → main heat exchanger + expander → double column → product delivery/storage, plus utilities), about 12 PID loops and about 35 OPC UA tags as a starting point, using first-order lags, integrators and a few lookup maps. You do not need rigorous thermodynamics. A nominal plant of about 1,000 t/d oxygen draws roughly 10–14 MW of electricity and feeds three plausible Verbund consumers: an ethylene oxide plant, an acetylene/syngas partial-oxidation plant, and the site-wide nitrogen network.

## TL;DR
- **Process in 6 blocks.** Air is filtered and compressed to about 5–6 bar, dried and decarbonated in switching molecular-sieve beds (2–12 h online), and cooled to about −175 °C in a brazed-aluminium heat exchanger. An expander supplies the refrigeration. The air is then separated in a Linde double column: the HP column runs at about 5–5.5 bar and the LP column at about 1.3 bar, linked by the condenser/reboiler. Outputs are GOX at 99.5%+, GAN with ppm-level O2, and LOX/LIN to tanks with vaporizer backup.
- **Simulator approach.** Use fast first-order lags for compressor flow and pressure (seconds), integrators for liquid levels (minutes), slow lags for purity (tens of minutes; argon takes hours) and a very slow lag for cold-box temperature (hours to days). Superimpose a 4-hour sawtooth for mole-sieve switching. Conventional ASUs ramp only about 0.25 %/min; Linde and Air Liquide have shown 5–8 %/min with special control. That gap is the natural story for APC/MPC.
- **Site context and KPI.** Linde Engineering data for conventional ASUs making >99.5% oxygen give a specific power of 0.33–0.35 kWh/Nm³ (222–245 kWh/t, depending on gas-company vs vendor convention). Typical installed plants run 0.4–0.6 kWh/Nm³. The main air compressor dominates both power use and the predictive-maintenance asset list. Consumers to model are an EO/EG plant (about 0.75–1.1 t O2 per t EO), a BASF-style acetylene partial-oxidation plant (about 3,400 Nm³ O2 per t acetylene) and the site N2 grid for inerting and the tank farm.

---

## 0. ASU1 as built (status October 2026)

**System architecture.** Two containers, joined the way a real plant is:
- **Plant:** the process model in real time (0.5 s step), acting as the remote I/O rack. It exposes raw instrument values (`FT-101`, float32 register pairs, no units) over **Modbus TCP**, with fixed register addresses per process area (area *n* → registers *n*×100…).
- **DCS:** scans the plant every second, maps the raw points to controller tags with units, ranges, modes and alarms, runs the control layer, and stores everything in a built-in **SQLite historian** (exception reporting, 0.25 % deadband, 14 days). It publishes that modelled view over **OPC UA** (`Objects/HCW/Haltenberg/ASU1/<Block>/<Tag>`, HistoryRead from the historian), as §9 recommended.
- **IT side:** Node-RED subscribes over OPC UA and writes 15-minute time-weighted averages to CSV.
- **Instructor station:** fault scenarios live only in the plant, so the DCS and every data consumer see only the process response.

**Process as built (6 blocks + energy):**

| # | Block | Equipment in ASU1 |
|---|---|---|
| 1 | Air intake and compression | Filter, MAC with IGV (FIC-101), discharge-pressure override (PIC-102), antisurge (UIC-103), DCAC; **K-102 booster air compressor** (~44 kNm³/h to 60 bar(a)) for internal compression |
| 2 | Front-end purification | Mole-sieve beds A/B, regeneration heater E-204 (TIC-204), sequence KS-201 (4 h per bed, compressible to 5 min for demos) |
| 3 | MHE + expander | E-301 main heat exchanger, X-303 turbo-expander (FIC-303) |
| 4 | Double column | HP and LP columns, E-404 main condenser/reboiler, LIN reflux cascaded from O2-in-GAN (AIC-602 → FIC-405) |
| 5 | Products | **Internal compression:** P-501 LOX pump draws liquid oxygen from the LP sump and pumps it to ~33 bar(g); it boils in E-301 against the booster air and enters the 30 bar(g) GOX header. **K-503 GAN compressor** lifts HP-column nitrogen to the 8 bar(g) site grid |
| 6 | Backup and storage | LOX and LIN tanks; backup pumps **P-505 / P-506**, kept cold on recirculation, feed vaporizers **E-507 / E-508**. Split range on both header-pressure controllers, with feed-forward handover on a trip |
| 7 | Energy | KPI-701 specific power = (MAC + booster + GAN compressor power) ÷ GOX ≈ 0.53–0.55 kWh/Nm³ |

**Size of the control layer:** 61 tags, 19 PI controllers (incl. cascade, split range, override, cross-limiting ratio), 7 interlocks with parent/child relations, the KS-201 sequence, state-based alarms with priorities 1–3. Lists: §5.1, §5.2, §5.4.

**Not built:** the argon column, APC/MPC (§5.3) and OPC UA writes, OPC UA Alarms & Conditions, tank-truck loadouts, the business systems of §6 (LIMS, ERP, maintenance). Only the mole-sieve cycle can be time-compressed; everything else runs in real time, as §4.4 describes.

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

**As built.** Block 5 uses the internal-compression variant: LOX pump P-501 plus a motor-driven booster air compressor K-102 (not expander-driven), a single pump with no standby. GAN goes to the site through compressor K-503. Block 6 has pumped backups: P-505 / P-506 lift tank liquid to header pressure for the vaporizers E-507 / E-508, so they survive an ASU trip. The argon column is not built.

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

**As built: product-side values added by internal compression and the GAN compressor** (steady state in the simulator):

| Variable | ASU1 value | Tag |
|---|---|---|
| LOX pump discharge | ~33 bar(g) | PI-511 |
| GOX header pressure | 30 bar(g) (LL 27) | PIC-502 |
| Booster air flow / pressure / power | ~44 kNm³/h, 60 bar(a), 4.2 MW | FIC-111, PI-112, JI-113 |
| Booster air ÷ pumped LOX ratio | 1.52 (pinch below ~0.95) | FFI-111 |
| GOX temperature leaving the MHE | ~10 °C (trip at −25 °C, carbon-steel header) | TI-512 |
| GAN compressor flow / discharge / power | 25 kNm³/h, 8.4 bar(g), 0.64 MW | FIC-503, PI-513, JI-514 |
| GAN header pressure | 8 bar(g) | PIC-504 |
| Main condenser ΔT | ~2.6 K | TDI-410 |
| Total hydrocarbons in LOX | ~7–8 ppm (the pumped LOX flushes the sump continuously) | AI-603 |
| Specific power incl. booster and GAN compressor | 0.53–0.55 kWh/Nm³ | KPI-701 |

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

**As built.** All seven are instructor scenarios, plus nine more: acetylene burner trip, N2 purge campaign, MAC bearing degradation, regeneration heater fault, booster motor trip, LOX pump cavitation, GAN compressor trip, LOX backup pump trip, and MAC impeller fouling (the efficiency drift of §9). Each has a tested signature (first-out, alarms, effect) in `tests/test_behaviour.py`.

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

**As built** (`plant/model.py`), following this recipe with these choices:
- **Purities:** a static map of O2 draw ratio, reflux ratio, GAN ratio, reboiler level and condenser ΔT, then dead time and a slow lag: GOX purity 240 s dead time and τ 600 s, O2 in GAN 120 s and τ 300 s.
- **PPU:** bed loading grows with load and inlet water (TI-108) and is removed only while the regeneration gas is above 120 °C. CO2 breakthrough is `0.03 + 10·exp((x−1)/0.06)` ppm in the bed loading *x*, about 0.15 ppm at the end of a normal cycle: the sawtooth.
- **Reboiler level couples two balances:** oxygen (available O2 − GOX − LOX) and refrigeration (liquid made − LOX − LIN draws). That is why an expander trip slowly drains the sump, and a stopped LOX pump floods it in ~10 min.
- **Internal compression:** LOX flow = pump run × valve × capacity, reduced by cavitation (low sump or a scenario). The LOX boils in E-301 only with enough booster air; below a ratio of 0.95 the GOX leaves the MHE cold.
- **Headers:** integrators against consumer-demand random walks, with a site-grid hold-up (the GAN grid is ~70 m³), fed by the product flow plus the backup vaporizers (20 s lag).
- **Only the PPU time-compresses**, for demos; everything else runs in real time.

---

## 5. Control strategy for a simple DCS

### 5.1 Basic PID loops

The research proposed about 12 basic loops: MAC air flow as the master load setpoint, a discharge-pressure override, antisurge (mandatory on all axial/centrifugal machines\[5\]), the PPU sequence with its regeneration temperature, expander flow, LP pressure on the waste-N2 vent, HP sump and reboiler levels, LIN reflux cascaded from N2 purity, and GOX/GAN header pressure with backup-vaporizer split range. Purity loops are slow, typically tuned with minutes of integral time. HP column pressure is mostly a result of air flow and often only indicated.

**As built: 19 PI controllers** (velocity-form PI, modes MAN / AUTO / CAS). Internal compression and the GAN compressor added the booster ratio, the load limit and override on the LOX pump, and a second split range. The KS-201 PPU sequence is a separate step program.

| Controller | Manipulates | Role |
|---|---|---|
| FIC-101 MAC air flow | FV-101 (IGV) | Master load setpoint, ramped (1.45 kNm³/h per min ≈ 1 %/min) |
| PIC-102 MAC discharge pressure | — | Override: its output limits FIC-101 (low select) |
| UIC-103 MAC antisurge | UV-103 (recycle) | Keeps surge margin |
| FIC-111 booster air flow | FV-111 | Ratio to the pumped LOX, cross-limited (leads on increase) |
| TIC-204 regeneration gas temperature | TV-204 (heater) | AUTO at 180 °C in the HEAT step of KS-201, otherwise MAN 0 |
| FIC-303 expander flow | FV-303 (nozzles) | Sets refrigeration |
| PIC-402 LP column pressure | PV-402 (waste N2 vent) | HP pressure (PI-401) is indicated only |
| LIC-403 HP sump level | LV-403 (rich liquid to LP) | |
| LIC-404 reboiler level | LV-404 | Critical level |
| FIC-405 LIN reflux | FV-405 | Slave of AIC-602 |
| FIC-409 LIN to storage | FV-409 | |
| FIC-501 GOX (pumped LOX) | FV-501 (P-501) | Slave of PIC-502; capped by the booster air (cross-limit) and by the load limit FY-501 (0.208 × air) |
| LIC-415 reboiler low-level override | — | Limits FIC-501 below 45 % level |
| PIC-502 GOX header pressure | — | Split range: FIC-501 (OP 0–80 %), backup FIC-507 (80–100 %) |
| FIC-503 GAN compressor flow | FV-503 (K-503 IGV) | Slave of PIC-504 |
| PIC-504 GAN header pressure | — | Split range: FIC-503 (0–80 %), backup FIC-508 (80–100 %) |
| AIC-602 O2 in GAN | — | Master of FIC-405 (reflux) |
| FIC-507 LOX backup vaporizer | FV-507 (P-505 / E-507) | GOX header backup |
| FIC-508 LIN backup vaporizer | FV-508 (P-506 / E-508) | GAN header backup |

There is no GOX purity *controller*: GOX purity (AI-601) is held by the load limit FY-501, which keeps the oxygen draw within what the air brings in. On a product trip (I-500, I-503) the header master moves straight to where the backup vaporizer replaces the lost flow (feed-forward handover), and hands back smoothly after the restart.

### 5.2 Interlocks and trips (demo set)
- MAC: antisurge, high-high vibration, low lube-oil pressure, high bearing temperature, motor overload.\[5\]
- PPU: CO2 >1 ppm alarm; trip if >10 ppm or if beds cannot be switched within 30 min of the alarm; high air outlet temperature trip (protects the cold box); valve-position/pressure-equalisation permissives.\[5\]
- Expander: overspeed (loss of loading), low lube-oil pressure, high vibration, abnormal bearing temperature.\[5\]
- Column/reboiler: low reboiler level (submergence), high HP-column level, high total hydrocarbons / acetylene in LP sump liquid, low LOX purge flow.\[5\]
- Products: high O2 in GAN → divert to vent; low suction pressure on product compressors (protects LP column from vacuum).\[5\]
- Acetylene: a PPU removes all of it, but if it enters the cold box it "precipitates out as a solid at concentrations as low as 4 ppm to 6 ppm". Operators therefore alarm well below this, at about 0.3–0.5 ppm in the reboiler sump.\[5\]\[24\]\[25\]

**As built: 7 interlocks.** Each latches, records a first-out, and can only be reset when no cause is active. A cause marked with equipment is armed only after that machine has run for its start-up bypass time, so a stopped plant can always be reset. Any machine whose run feedback drops while commanded also trips its own interlock (motor protection).

| Interlock | Stops | Causes | Parent |
|---|---|---|---|
| I-100 ASU shutdown | MAC (and through its children, everything except the backups) | VI-105 HH, TI-106 HH, PI-107 LL, AI-202 HH (CO2 10 ppm), AI-603 HH, AI-604 HH, LIC-404 LL or HH | — |
| I-300 expander trip | X-303 | SI-304 HH (overspeed), VI-305 HH, TI-306 HH | I-100 |
| I-110 booster trip | K-102 | VI-114 HH, PI-112 HH | I-100 |
| I-500 LOX pump trip | P-501 | TI-512 LL (cold GOX into the carbon-steel header), PI-511 LL (cavitation) | I-100, I-110 |
| I-503 GAN compressor trip | K-503 | VI-515 HH, PI-513 HH | I-100 |
| I-505 LOX backup pump trip | P-505 | LI-505 LL | none: the backup must survive an ASU trip |
| I-506 LIN backup pump trip | P-506 | LI-506 LL | none |

Start permissives: the expander and booster need the MAC running; P-501 needs the booster running and LIC-404 ≥ 40 %; K-503 needs the MAC running and HP pressure ≥ 4 bar(a); the backup pumps need tank level above LL. "Low LOX purge flow" and "high O2 in GAN → vent" were not implemented: with pumped LOX the sump is flushed continuously.

### 5.3 Role of APC/MPC
- **Why:** ASUs are strongly coupled, nonlinear, slow and power-hungry. MPC handles multivariable load changes (air, expander split, reflux, drain) within purity constraints and minimises kWh/Nm³.\[26\]\[27\]
- **Evidence:** Linde-linked academic work with RWTH Aachen (Mitsos group) and TUM includes economic NMPC under fluctuating electricity prices, Koopman/ML reduced models that enable real-time NMPC "at an average CPU time decrease by 98%", and a pressure-driven digital twin. Its manipulated variables are exactly the demo handles: air feed, turbine split, reflux fraction and reboiler drain.\[6\]\[27\]\[28\]\[29\]\[30\] Industrial two-layer MPC for automatic load change has been deployed on Chinese ASUs.\[26\]
- **Industry operating model:** Air Liquide opened its Saint-Priest remote operation centre in January 2017 under the €20 million Connect project. It remotely manages production for 22 units in France and is "capable of stopping or restarting a site remotely". It adapts production to customer demand and uses big data for predictive maintenance by "identifying the weak signals that precede a malfunction". Air Liquide uses AVEVA PRiSM for its SIO.Predict analytics.\[31\]
- **Demo suggestion:** expose an "APC ON/OFF" switch. With it OFF, the operator ramps load at 0.25 %/min by hand. With it ON, the MPC ramps at 3–5 %/min with less purity overshoot and lower kWh/Nm³.
- **As built:** not yet. OPC UA is read-only; writable setpoints for an external APC agent are the obvious next step. The air-flow ramp defaults to about 1 %/min.

### 5.4 Tag list (as built)

The research suggested ~35 tags. ASU1 has 61, listed here with their steady-state value in the simulator. NodeIds are `ns=2;s=HCW.ASU1.<Tag>.PV` (and `.SP`, `.OP` for controllers) in namespace `urn:haltenberg:hcw:asu1:dcs`. This table is a snapshot: the authoritative list, with Modbus addresses and OPC UA nodes per tag, is the generated [inventory/tags.csv](inventory/tags.csv).

| Tag | Description | Unit | Range | Normal | Alarms (limit, priority) |
|---|---|---|---|---|---|
| FIC-101 | MAC air flow (load) | kNm³/h | 0 … 180 | 145 | L 120 P2, H 165 P2 |
| PIC-102 | MAC discharge pressure (override) | bar(a) | 0 … 8 | 5.75 | H 6.4 P2, HH 6.8 P1 |
| FI-103 | MAC compressor (suction) flow | kNm³/h | 0 … 200 | 145 |  |
| UIC-103 | MAC antisurge (surge margin) | % | −20 … 80 | 37.9 | L 5 P2 |
| JI-104 | MAC motor power | MW | 0 … 15 | 10.6 | H 12.5 P2 |
| VI-105 | MAC bearing vibration (DE) | mm/s | 0 … 25 | 2.53 | H 7.1 P2, HH 11.0 P1 |
| TI-106 | MAC bearing temperature | °C | 0 … 120 | 74.9 | H 95 P2, HH 105 P1 |
| PI-107 | MAC lube-oil pressure | bar(g) | 0 … 4 | 2.01 | L 1.5 P2, LL 1.0 P1 |
| TI-108 | Air temperature after DCAC | °C | 0 … 40 | 12.0 | H 20 P3 |
| TI-109 | Ambient air temperature | °C | −20 … 45 | 18.8 | H 32 P3 |
| FIC-111 | Booster air flow (ratio to pumped LOX) | kNm³/h | 0 … 60 | 44.0 | L 20 P2 |
| FFI-111 | Booster air / pumped LOX ratio (FIC-111 ÷ FIC-501) | – | 0 … 3 | 1.52 | L 1.4 P2 |
| PI-112 | Booster discharge pressure | bar(a) | 0 … 80 | 60.2 | H 66 P2, HH 70 P1 |
| JI-113 | Booster motor power | MW | 0 … 6 | 4.22 | H 5.0 P2 |
| VI-114 | Booster bearing vibration | mm/s | 0 … 25 | 2.09 | H 7.1 P2, HH 11.0 P1 |
| AI-202 | CO2 at PPU outlet | ppm | 0 … 20 | 0.04 | H 1.0 P2, HH 10.0 P1 |
| PDI-203 | PPU bed differential pressure | mbar | 0 … 200 | 60.4 | H 100 P2 |
| TIC-204 | Regeneration gas temperature | °C | 0 … 250 | 15 (180 in HEAT) |  |
| ZI-205 | Switching valve stroke time (last) | s | 0 … 60 | 12.0 | H 20 P2 |
| TI-301 | MHE cold-end air temperature | °C | −200 … 40 | −174 | H −170 P2 |
| TDI-302 | MHE warm-end ΔT | K | 0 … 15 | 2.99 | H 5.0 P2, HH 7.0 P1 |
| FIC-303 | Expander flow (refrigeration) | kNm³/h | 0 … 30 | 18.0 | L 12 P2 |
| SI-304 | Expander speed | rpm | 0 … 40000 | 24,974 | H 28000 P2, HH 30000 P1 |
| VI-305 | Expander vibration | µm | 0 … 100 | 14.9 | H 40 P2, HH 60 P1 |
| TI-306 | Expander bearing temperature | °C | 0 … 120 | 60.0 | H 85 P2, HH 95 P1 |
| PI-401 | HP column top pressure | bar(a) | 0 … 8 | 5.30 | L 4.8 P2, H 5.8 P2 |
| PIC-402 | LP column top pressure | bar(a) | 0 … 3 | 1.35 | L 1.25 P2, H 1.45 P2 |
| LIC-403 | HP column sump level | % | 0 … 100 | 49.6 | L 30 P2, H 70 P2, HH 85 P1 |
| LIC-404 | Reboiler / LP sump level | % | 0 … 100 | 70.0 | L 55 P2, LL 35 P1, H 85 P2, HH 95 P1 |
| FIC-405 | LIN reflux to LP column | kNm³/h | 0 … 60 | 39.8 |  |
| TI-406 | LP sump (LOX) temperature | °C | −200 … −150 | −180 |  |
| TI-407 | HP column top (N2) temperature | °C | −200 … −150 | −178 |  |
| TDI-410 | Main condenser ΔT (TI-407 − TI-406) | K | −15 … 10 | 2.61 | L 1.0 P2 |
| TY-411 | N2 condensing temperature at HP pressure (PI-401) | °C | −200 … −150 | −178 |  |
| TY-412 | O2 boiling temperature at LP pressure (PIC-402) | °C | −200 … −150 | −180 |  |
| TY-413 | N2 boiling temperature at LP pressure (LIN after FV-405) | °C | −200 … −150 | −193 |  |
| FI-408 | LOX draw to storage | kNm³/h | 0 … 3 | 0.81 |  |
| FIC-409 | LIN draw to storage | kNm³/h | 0 … 2 | 0.58 |  |
| AI-603 | Total hydrocarbons in LOX | ppm CH4 | 0 … 600 | 6.86 | H 250 P2, HH 450 P1 |
| AI-604 | Acetylene in LOX | ppm | 0 … 2 | 0.02 | H 0.3 P2, HH 1.0 P1 |
| FIC-501 | GOX product flow (pumped LOX, P-501) | kNm³/h | 0 … 35 | 28.9 |  |
| FY-501 | GOX load limit (0.208 × FIC-101 − FI-408 + sump surplus allowance) | kNm³/h | 0 … 35 | 29.4 |  |
| LIC-415 | Reboiler low-level override (limits P-501) | % | 0 … 100 | 70.0 |  |
| PI-511 | LOX pump P-501 discharge pressure | bar(g) | 0 … 50 | 33.3 | L 25 P2, LL 20 P1 |
| TI-512 | GOX temperature leaving E-301 (to carbon-steel header) | °C | −200 … 50 | 9.50 | L −5 P2, LL −25 P1 |
| PIC-502 | GOX header pressure (split range GOX / vaporizer) | bar(g) | 0 … 40 | 30.0 | LL 27 P1, L 28.5 P2, H 33 P2 |
| FIC-503 | GAN product flow (K-503 compressor) | kNm³/h | 0 … 60 | 25.0 |  |
| PIC-504 | GAN header pressure (split range GAN / LIN vaporizer) | bar(g) | 0 … 12 | 7.99 | LL 6.5 P1, L 7.3 P2, H 9.0 P2 |
| PI-513 | GAN compressor K-503 discharge pressure | bar(g) | 0 … 15 | 8.39 | H 10.0 P2, HH 11.0 P1 |
| JI-514 | GAN compressor K-503 motor power | MW | 0 … 2 | 0.64 | H 1.3 P2 |
| VI-515 | GAN compressor K-503 vibration | mm/s | 0 … 25 | 1.75 | H 7.1 P2, HH 11.0 P1 |
| AI-601 | GOX purity | % O2 | 95 … 100 | 99.6 | L 99.5 P2, LL 99.0 P1 |
| AIC-602 | O2 in GAN (purity → reflux) | ppm | 0 … 100 | 2.93 | H 10 P2, HH 50 P1 |
| LI-505 | LOX tank level | % | 0 … 100 | 65.0 | LL 10 P1, L 20 P2, H 90 P2 |
| LI-506 | LIN tank level | % | 0 … 100 | 60.0 | LL 10 P1, L 20 P2, H 90 P2 |
| FIC-507 | LOX backup vaporizer E-507 send-out (P-505) | kNm³/h | 0 … 30 | 0.00 | H 0.5 P3 |
| PI-516 | LOX backup pump P-505 discharge pressure | bar(g) | 0 … 50 | 35.6 | L 25 P2 |
| FIC-508 | LIN backup vaporizer E-508 send-out (P-506) | kNm³/h | 0 … 45 | 0.00 | H 0.5 P3 |
| PI-517 | LIN backup pump P-506 discharge pressure | bar(g) | 0 … 20 | 12.0 | L 7.0 P2 |
| KPI-701 | Specific power ((JI-104 + JI-113 + JI-514) ÷ FIC-501) | kWh/Nm³ | 0 … 1 | 0.53 | H 0.6 P3 |

SI-304 is set for a large-plant expander (~25,000 rpm). Calculated tags (FFI, TDI-410, TY, FY, KPI) are computed by the DCS, not measured; KPI-701 and FFI-111 are *not applicable* (OPC UA `BadOutOfService`) while no GOX is produced.

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

**As built.** Condition signals exist in the DCS for the MAC (VI-105, TI-106, PI-107, JI-104), the booster (VI-114, JI-113), the expander (SI-304, VI-305, TI-306), the GAN compressor (VI-515, JI-514), the mole sieve (PDI-203, ZI-205, TIC-204), the MHE (TDI-302) and the LOX pump (PI-511, whose unsteady pressure shows cavitation). Instructor scenarios produce the matching failure patterns (bearing wear, impeller fouling, cavitation, MHE fouling, heater fault). LIMS, ERP and maintenance systems are not built.

---

## 7. Energy KPI

- **Benchmarks:** for >99.5% O2 from a conventional ASU, Linde Engineering data (Beysel, 1st Oxyfuel Conference, Cottbus) give 0.35 kWh/Nm³ (245 kWh/t) in the vendor convention and 0.33 kWh/Nm³ (222 kWh/t) in the gas-company convention, and about 0.25 kWh/Nm³ for energy-optimized 95% O2 plants. Typical installed plants run 0.4–0.6 kWh/Nm³.\[16\]\[18\] A study of three operating ASUs measured 0.464–0.639 kWh/Nm³ against a 0.28 kWh/Nm³ benchmark.\[34\]
- **Where the power goes:** the MAC plus booster account for about 86% of energy in one case study,\[35\] and MAC power makes up about 70–90% of ASU operating cost.\[36\]
- **Trend:** Linde reports a 15% drop in average ASU power consumption over the last 10 years.\[1\]
- **Demo KPI:** KPI-701 = JI-104 (+ booster + product compression) ÷ FIC-501. Show it worsening with MAC efficiency drift, PPU over-regeneration, high liquid make and part load. This is the bridge between operations, energy and PdM.
- **As built:** KPI-701 = (JI-104 + JI-113 + JI-514) ÷ FIC-501, i.e. MAC + booster + GAN compressor. Normal ≈ 0.53–0.55 kWh/Nm³, inside the typical installed range, with a P3 alarm at 0.60. It is computed only while P-501 delivers at least 10 kNm³/h; otherwise it is *not applicable* rather than a meaningless large number.

---

## 8. Consumer plants on the HCW site (Part 2)

| # | Plant | Consumes | Rough demand (demo-scaled) | Why it fits a Verbund site |
|---|---|---|---|---|
| 1 | **Ethylene oxide / ethylene glycol (EO/EG)** | GOX (99.5%) at ~20–30 bar\[37\] | 0.75–1.1 t O2 per t EO (EU JRC BREF data). A 200 kt/yr EO plant needs about 500 t/d O2, about half the ASU.\[38\] | Takes ethylene from the site cracker, feeds surfactants/glycols/amines downstream, and exports reaction steam to the grid. BASF runs EO in Ludwigshafen and Antwerp with a combined 845,000 t/yr. Very sensitive to O2 supply interruptions, so it is the obvious "VIP" consumer.\[39\]\[40\] |
| 2 | **Acetylene + syngas by partial oxidation (BASF Sachsse-Bartholomé type)** | GOX | About 3,400 Nm³ O2 per t acetylene (≈4.9 t O2/t, per Ullmann's). A 30 kt/yr unit needs about 400 t/d O2.\[41\] | This is a real Ludwigshafen archetype. BASF's site page describes its new 90,000 t/yr acetylene plant (on stream October 2020, replacing a 1964 plant) as made "from natural gas and oxygen" and states that "around 20 plants in BASF's Production Verbund process acetylene and its derivatives". The by-product syngas feeds further Verbund chemistry (e.g., methanol or oxo).\[42\]\[43\]\[44\] Burner trips create sharp O2 demand steps, a good disturbance for the simulator. |
| 3 | **Site nitrogen grid: inerting, blanketing, purging + central tank farm** | GAN (low-pressure network); LIN for large or peak jobs | Base load ~5,000–15,000 Nm³/h, with spikes during turnarounds. Linde cites 1,000–12,000 Nm³/h for large inerting operations.\[45\] | Every plant on a chemical site uses N2 for tank blanketing, reactor and line purging, pigging, and catalyst handling.\[46\]\[47\] Demand is many small, spiky consumers, so it suits a header-pressure loop with LIN backup. |
| 4 (optional) | **Ammonia from imported/electrolytic H2, or argon users** | GAN (0.82 t N2/t NH3 stoichiometric); LAR | A 300 kt/yr NH3 loop needs about 670 t/d N2. Argon is small (workshops, welding, specialty inerting). | Classic natural-gas ammonia takes its N2 from process air, so an ASU-fed ammonia plant only makes sense for a "green ammonia" storyline. Ludwigshafen's ammonia capacity was reported at 880,000 t/yr in 2022, and BASF has since idled part of it.\[48\]\[49\] |

**Plausibility check:** EO (about 500 t/d) plus acetylene POX (about 400 t/d) is about 900 t/d O2, which matches a 1,000 t/d ASU with about 10% margin. The ASU's GAN covers the site N2 grid. This gives a self-consistent mass balance for the demo.

**As built.** The consumers are not modelled as plants: each is a demand random walk on the GOX or GAN header, plus instructor scenarios (EO demand step +20 %, acetylene burner trip, N2 purge campaign). The ammonia/argon option is not built.

---

## 9. Recommendations for the build

1. **Start with 6 blocks and about 35 tags.** Resist adding the argon column until the core loop runs well.
2. **Model three time scales explicitly** (seconds, minutes/hours, hours/days). This is what makes the trends feel like an ASU, not a generic tank.
3. **Script four scenarios:** EO demand step, MAC trip with vaporizer backup, mole-sieve breakthrough, and slow MAC efficiency drift for PdM. These map one-to-one onto operations, safety, data and AI storylines.
4. **Publish via OPC UA with an ISA-style hierarchy** (Site/Area/Unit/Equipment/Tag) and engineering units in metadata. Then demonstrate that "an OPC UA connector" alone does not answer who owns the asset model, the LIMS join, or the ERP volume reconciliation.

**Status:** 1 grew beyond 35 tags once internal compression and the backups were added (§0), still without argon. 2 and 3 are done, with 16 scenarios instead of four. 4 is done for the OPC UA side (`HCW/Haltenberg/ASU1/<Block>/<Tag>`, AnalogItems with EURange and units, HistoryRead); the LIMS/ERP join is still open.

## 10. Caveats

- All numbers are **representative**, not design data. Real values depend on cycle (GOX vs pumped-LOX), vendor and product slate. Time constants in §4 are demo assumptions informed by the literature, not measured plant data.
- Hydrocarbon and acetylene limits vary by vendor and reboiler type (EIGA Doc 65 / AIGA 035).\[24\]\[50\] Treat the demo thresholds as illustrative.
- Consumer demand figures are scaled for a fictional site. The BASF figures (acetylene 90 kt/yr, EO 845 kt/yr combined with Antwerp, ammonia 880 kt/yr in 2022) come from BASF releases and bank/press reports and may have changed since.\[40\]\[48\]\[51\]\[52\]
- Expander speeds vary widely with machine size. The 52,000–60,000 rpm figures come from small-unit studies.\[8\]
- **Simplifications in the built simulator:** no argon column; tanks only fill from the column and drain through the backups (no truck loadouts); vaporizers are assumed big enough (no outlet temperature); the mole-sieve bed feedbacks always follow their command. ASU1 is built for realistic-looking data and plausible cause and effect, not as a full-fidelity operator training simulator.

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
