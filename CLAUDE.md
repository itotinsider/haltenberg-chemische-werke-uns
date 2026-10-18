# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A demo plant for a fictional chemical site, **Haltenberg Chemische Werke (HCW)**. The first unit is **ASU1**, a Linde-type Air Separation Unit (~1,000 t/d GOX, 145 kNm³/h air). The process model, tag list and control strategy follow `linde air seperation research.md`; section references (§) below point into it.

It is the sibling of the Sweet Harmony Treats waffle-line simulator (`../sht-scada/sweet-harmony-treats-main`, "SHT"). The build philosophy is the same, with one deliberate architectural difference:

- **SHT**: one simulator publishes to an MQTT broker; HMIs poll its HTTP API.
- **HCW**: a simulated **DCS** with a **built-in historian** exposes its data over **OPC UA** (not MQTT). There is no broker. The plant and the DCS are separate containers joined by a raw **Modbus TCP** field link.

Keep this file current. It is the main knowledge carrier between sessions.

**Other docs to keep current:**
- **Research doc:** keeps the original research, plus §0 "ASU1 as built" and *As built* notes per section (loops §5.1, interlocks §5.2, tag table with normal values §5.4). Update them when equipment, loops, interlocks or tags change; the §5.4 table is a snapshot, `inventory/tags.csv` is authoritative.
- **The wiki (`wiki/*.md`):** the user documentation, for humans: getting started, the primer "Plant automation in brief" (glossary, tag naming, a trip walkthrough), the first tour (K-503 trip), process, architecture, displays, scenarios, data access, configuration, development. Update the page whose topic you change (new tag on a display, new scenario, new env var, changed restart procedure), and keep its examples working.
  - **Source in the repo, published by script:** `python tools/publish_wiki.py [--dry-run]` copies `wiki/*.md` and `docs/screenshots/*.png` (as `images/`) to `<repo>.wiki.git`, removes pages that no longer exist, commits and pushes. Edits made on GitHub are overwritten. **Automatic:** `.github/workflows/wiki.yml` runs it on every push to `main` that touches `wiki/**` or `docs/screenshots/**` (and by hand from the Actions tab), with the workflow's `GITHUB_TOKEN`. So a merged PR updates the wiki; never run the script from a feature branch.
  - **Links:** between pages by page name (`[Architecture](Architecture)`, `Process-behaviour#restart-after-a-trip`), to repo files by full GitHub URL, images as `images/<file>.png`. `test_wiki_links_resolve` checks pages and images.
  - **README** is kept short: pitch, quick start, architecture sketch, links. Detail goes to the wiki.

**Every component has a tested inventory of its interface, and every change is checked against it.** `inventory/` lists what each layer exposes to the others: Modbus map, DCS tags, OPC UA nodes, HTTP API, instructor scenarios. It can be trusted because the test suite proves it matches the code, and that the layers agree with each other. So:
- look up names and addresses there instead of guessing;
- before changing a name, check there who uses it;
- after a change, run the tests and regenerate it.

See "Tests and the interface inventory" below.

## Layout

```
ROADMAP.md               plans, ideas, claims and progress (read before starting work)
common/iomap.py          Modbus register map — single source of truth, copied into BOTH images
plant/model.py           process model (6 blocks) + instructor SCENARIOS — no I/O, no clock
plant/modbus_server.py   hand-written Modbus TCP server (FC1-6, 15, 16)
plant/main.py            real-time loop (0.5 s), register bank, instructor HTTP :8111
plant/web/instructor.html
dcs/config.py            the DCS "engineering database": blocks, tags, PID configs, alarm limits,
                         interlocks, line-up, PPU sequence steps. Everything else is generated from it.
dcs/control.py           PID / alarms / interlocks / equipment / KS-201 sequence — pure logic, no I/O
dcs/historian.py         SQLite historian (exception reporting) + event journal
dcs/opcua_server.py      asyncua server, address space generated from config, HistoryRead → historian
dcs/main.py              asyncio scan loop (pymodbus client), checkpointing, HTTP :8110
dcs/web/index.html       hub (the only page that isn't a persona HMI)
dcs/web/operator.html    operator console (overview mimic + L3 detail displays, tag list, interlocks, alarms/events, trends, the three trainers)
tools/offline_sim.py     plant model + control layer coupled directly, faster than real time (Sim class = test harness)
tools/publish_wiki.py    copies wiki/ + docs/screenshots/ to the GitHub wiki repo and pushes (by hand, after an OK)
tools/inventory.py       generates inventory/ from every layer's source of truth (needs asyncua: run in the tests image)
inventory/               GENERATED interface inventory: field/dcs/opcua/api/plant .json + tags.csv (README.md is hand-written)
wiki/                    the GitHub wiki's source: user documentation (Home.md, _Sidebar.md, one file per page)
docs/screenshots/        screenshots, shown in the wiki (as images/) and the README
tests/                   pytest: test_inventory, test_contracts, test_behaviour, test_e2e (+ Dockerfile for the runner)
nodered/Dockerfile       official Node-RED image + node-red-contrib-opcua (baked in, /data is a bind mount)
nodered_data/flows.json  the only tracked file in nodered_data/ (runtime state is git-ignored)
data/                    bind mount: historian.db, dcs_state.json (git-ignored)
exports/                 bind mount: CSVs Node-RED writes (git-ignored, .gitkeep tracked)
```

## Commands

```bash
docker compose up -d --build          # REQUIRED after any code/web change (files are copied into images)
# A second stack (another worktree/agent): a git-ignored .env with HCW_STACK and HCW_*_PORT, see docker-compose.yml
docker compose down
docker logs -f hcw-asu1-dcs           # / hcw-asu1-plant
curl -s localhost:8110/api/state | python3 -m json.tool | head -50
curl -s -X POST localhost:8111/api/scenarios/mac_motor_trip/start       # instructor scenario
curl -s -X POST -H 'Content-Type: application/json' -d '{"sp":145}' localhost:8110/api/loops/FIC-101
curl -s "localhost:8110/api/history?tags=FIC-101.PV,AI-601.PV&minutes=60&points=300"

# Tune/verify control logic offline (hours of plant time in seconds, no containers needed):
docker run --rm -v "$PWD":/w -w /w python:3.12-slim python tools/offline_sim.py \
  --hours 2 --every 300 --scenario eo_demand_step@600 --cmd sp:FIC-101=160@1800 \
  --cols FIC-101,AI-601,FIC-501,PIC-502,FIC-507
#   --cmd accepts lineup, ack, switch, reset:I-100, start:MAC, stop:EXP, sp:TAG=v, mode:TAG=AUTO,
#   op:TAG=v (MAN output), ratio:FIC-111=1.6,
#   cycle:900 (PPU time compression, applied to plant and DCS together)
curl -s -X POST -H 'Content-Type: application/json' -d '{"seconds":900}' localhost:8111/api/ppu_cycle
TWA_WINDOW_MINUTES=1 docker compose up -d nodered   # Node-RED CSV rows every minute (test), then recreate without

# Tests (compose profile "test": never started by `up`; mounts the working tree, no rebuild after code changes)
docker compose run --rm tests                                   # all, ~1 min
docker compose run --rm tests python -m pytest -m "not e2e"     # without the end-to-end processes
docker compose run --rm tests python tools/inventory.py         # regenerate inventory/ after an intended interface change
docker compose run --rm -e HCW_TEST_SEED=3 tests python -m pytest tests/test_behaviour.py   # another seed
```

**OPC UA client checks:** the DCS image has asyncua installed, so a throwaway client container on the compose network works:
```bash
docker run --rm --network <repo-folder>_default -v /path/to/script:/s --entrypoint python hcw/asu1-dcs:latest /s/client.py
# network = compose project (folder) name + _default, e.g. haltenberg-chemische-werke-uns_default
# connect to opc.tcp://dcs:4840/hcw/asu1/, namespace "urn:haltenberg:hcw:asu1:dcs"
```

**Screenshots** (headless Chrome; Playwright is not installed). `?tag=<TAG>` opens a faceplate, `?trend=A.PV,B.PV&range=<min>` preselects trend points and window (a RANGES value), `#overview|tags|ilk|alarms|trends` selects a tab and `#detail/<id>` (e.g. `#detail/C-401`) a detail display, so every view can be captured without clicking. Screenshots live in `docs/screenshots/` (the wiki shows them as `images/`); for a lively trend, compress the PPU cycle to 300 s for ~15 min first, then restore 14400:
```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars \
  --window-size=1600,1000 --virtual-time-budget=6000 --screenshot=out.png "http://localhost:8110/operator?tag=PIC-502#tags"
```

Verification is the test suite (below) plus, for new behaviour or graphics, the offline sim, the API, an OPC UA client and screenshots. **Shell gotcha:** in zsh, `echo =====` fails ("= not found"), and `"${2:-{}}"` mangles JSON bodies. Pass JSON literally.

## Architecture

**Two clocks, like a real plant.**
- The plant integrates in real time at a 0.5 s step on its own thread.
- The DCS samples it every 1 s over Modbus, asynchronously. Nothing is lock-stepped.
- The plant holds the last outputs if the DCS goes away, the way an I/O rack does. Its holding registers are seeded with the design line-up, so it sits at steady state before any DCS connects.
- A plant heartbeat (`HB-900`) lets the DCS detect a frozen rack; it then sets every PV to Bad quality.
- A DCS watchdog (`WD-900`) is shown on the instructor station.

**Modbus TCP is the "raw" layer.** This replaces SHT's `_RAW` MQTT tree as the teaching contrast:
- Instrument names (`FT-101`) and float32 register pairs: no units, no hierarchy, no quality.
- The DCS maps them to controller tags (`FIC-101`) with units, ranges, modes and alarms.
- Only that modelled view goes to OPC UA.
- **Fixed addresses per process area** (first digit of the tag number), written explicitly in `common/iomap.py`:
  - **Registers:** area *n* → *n*×100 … *n*×100+99 (FT-101 = IR 100, HB-900 = IR 900).
  - **Bits:** area *n* → *n*×10 …
  - **Adding a point:** it goes at the end of its area with the next free address. Never renumber; gaps are fine.
  - **Why:** until 2.5 addresses came from list position, so inserting a point (step 2b) shifted every later address. The single 104-register read would also have broken the 125-register limit once step 3 added points.
  - **Requests:** the DCS reads and writes one area per request (`iomap.AI_BLOCKS` / `AO_BLOCKS`), and the bits in one request.
  - **Limits:** `plant/modbus_server.py` enforces the spec's quantity limits (125 / 123 registers, 2000 / 1968 bits; exception 3), like a real device. pymodbus won't even send a bigger request.
  - **Enforced by** `test_modbus_layout`.

`plant/modbus_server.py` is hand-written on purpose. pymodbus 3.16 deprecated its whole server datastore API ahead of v4. The DCS uses the real pymodbus client, so every scan is an interop check.

**Process model (`plant/model.py`)** follows research §4.5. A few "handles" drive everything else:
- **Handles:** IGV→air flow, LP vent→LP pressure, reflux, expander flow, product draws.
- **Fast first-order lags** for compressor flow and pressure.
- **Integrators:**
  - Sump and tank levels.
  - Header pressures, against consumer demand random walks.
  - Cold-box refrigeration balance (`dt_coldbox`, hours).
- **Purities:** a static map of (O₂ draw ratio, reflux ratio, GAN ratio, reboiler level, condenser ΔT) → dead time → slow lag.
  - `imp_gox` has 240 s dead time and τ 600 s; `ppm_gan` has 120 s and τ 300 s.
- **PPU:**
  - Bed loading grows at `0.65/cycle × load × water(TT-108)`.
  - Regeneration removes loading only while `TT-204 > 120 °C`.
  - CO₂ = `0.03 + 10·exp((x−1)/0.06)`, giving ~0.15 ppm at end of cycle: the sawtooth.
- **Reboiler level couples two balances:** O₂ (`o2_avail − GOX − LOX`) and refrigeration (`p_liq − LOX − LIN`). That is why an expander trip slowly drains the sump.
  - **Hold-up** `LP_SUMP_GAIN` = 0.00185 %/(kNm³/h·s): a refrigeration deficit drains it over hours, a stopped LOX pump floods it in ~10 min. It was 0.00556 before internal compression (3× too small a sump).
- **Internal compression (oxygen product):**
  - P-501 pumps the GOX product as **liquid** out of the LP sump (no vapour cap any more). Flow = pump run × FV-501 × 40 × (1 − 0.8 × cavitation).
  - Cavitation comes from a low sump (< 25 %) or the `lox_pump_cavitation` scenario. That scenario only builds while the pump runs and re-cools in ~5 min when stopped.
  - The pumped LOX boils in E-301 only with enough booster air: ratio = booster ÷ (1.52 × LOX). Below 0.95 the GOX outlet (TT-512) drops, by up to 200 K. The time constant scales with the flow, so a stopped pump doesn't "cool" a dead line.
  - The K-102 booster takes clean air after the PPU, needs the MAC running, and surges below ~14 kNm³/h (VT-114). Its power uses the MAC's specific work: 4.2 MW at 44 kNm³/h, 60 bar(a).
  - Booster air with no LOX to condense against (`bst_excess`) costs 0.04 refrigeration per kNm³/h; in E-301 it warms the profile (see the heat balance below).
  - Hydrocarbons: every liquid leaving the sump flushes it (`flush = LOX + GOX`), so THC is ~8 ppm normally and only climbs while P-501 is down. Initial `thc` = 7.6.
- **Nitrogen product and backups (step 2b):**
  - K-503 GAN compressor: GAN flow = run × FV-503 (its IGV) × 60, capped at 0.30 × air; it can't run without HP-column pressure (`p_hp > 2.5`). Power from the MAC's specific work: 0.64 MW at 25 kNm³/h, 5.05 → 9.41 bar(a). Surge vibration below ~8 kNm³/h (VT-515). Latched motor trip like the MAC/booster. A stopped K-503 simply stops the draw; the column then vents that N₂ with the waste N₂ (PIC-402 opens).
  - **GAN header hold-up** `GAN_HEADER_GAIN` = 0.004 bar/s per kNm³/h (~70 m³ of site grid). It was 0.02 (~14 m³): losing the GAN supply emptied the header from 8 to 3 bar(g) in a minute.
  - Backup pumps P-505 / P-506 are "kept cold" (running on minimum-flow recirculation): send-out = pump run × valve × capacity (E-507 30, E-508 `LIN_VAP_CAPACITY` 40 kNm³/h), vaporizer lag 20 s. Discharge = header + shut-off head, falling with send-out; a tank below 5 % starves the suction. No pump, no send-out.
  - T-506 now has an outlet (`l506 += (lin − vap_n)`), T-505 as before.
  - **Purity draw ratio and sump surplus:**
    - **Credit:** the inventory drawdown (`gox + lox − o2_avail`) is credited while the sump is above normal: none at 65 %, full at 75 %. Liquid drained from a too-full sump (e.g. what piled up while P-501 was stopped) is surplus, not production.
    - **Without the credit:** every pump trip was followed by a fake purity crash to ~95 % while LIC-404 drained the surplus.
    - **What still counts:** a sustained over-draw (more GOX than the air makes) still hits purity.
  - **LIC-404 Kc = 12** (was 4): tripled with the sump hold-up, so the level loop is as fast as before and doesn't overshoot. Steady 4 h: AI-601 mean 99.67 %, sd 0.05 (the old model: 99.60 %, sd 0.12, regular dips below the 99.5 % L alarm).

- **E-301 heat balance (step 3, `Plant._mhe()`, `mhe_balance()`):** every stream's temperature at both ends, plus X-303 in/out.
  - **Assumptions (Willem: "simple, ideal conditions"):** ideal gases with one constant cp `CP_GAS` 1.30 kJ/(Nm³·K); constant heats of vaporisation (`L_O2_PUMPED`); no heat leak into E-301; a clean exchanger (`mhe_fouling` comes on top).
  - **Inlets** from upstream: air TT-108 + `PPU_HEAT`; booster TT-108 + `BOOSTER_COOLER_DT`; column streams at their boiling points (pressures); LOX = sump + `LOX_PUMP_HEAT`.
  - **Outlets** from design approaches: warm end `MHE_WARM_DT` (3 K, less at low load); cold end `MHE_COLD_END` + ½ `dt_coldbox`; expander draw at `MHE_SIDE_DRAW` of the air's cooling.
  - **Closure:** the residual (warm duty − cold duty) / total capacity = `mhe_shift`, lagged by `MHE_TAU` (600 s metal), clamped ±15 K, added to every outlet. If the warm end would cross (product warmer than the air in), the excess spills to the cold end. GOX (TI-512) is excluded: it keeps its pinch logic (I-500 is tuned on it).
  - **Calibrated:** `BOOSTER_LATENT` (~200 kJ/Nm³, the 60 bar booster air's pseudo-latent heat) is computed at import so the design point balances; `EXP_DT_PER_LN` likewise fits the expander to −110 → −178 °C at 85 %.
  - **Waste through E-301** = air × (1 − recovered O₂) − GAN − LIN, *not* `self.waste`: with P-501 stopped the O₂ piles up in the sump, it doesn't leave as cold gas. (`self.waste` still drives the LP pressure, unchanged.)
  - **Stopped streams** (`flow_lag`): a sensor drifts slowly (~30 min) to the metal around it; TDT-302 is clamped at 0.
  - **Effects:** expander stop → warm end pinches (TDI-302 ~0.2), TI-301 H after ~20 min; P-501 lost → cold end ~+10 K; fouling → TDI-302 H after ~70 min. The refrigeration balance (`p_liq`, `dt_coldbox`, sump levels) is unchanged.

Tuning facts that are easy to break:
- **GOX purity sensitivity:** the width is 0.020 on the draw ratio. At 0.010, normal demand wander alone pushed purity through the 99.0 % LL limit.
- **GAN ratio width:** 0.06, so routine N₂ purge jobs don't swing AIC-602.
- **Carry-over rate:** `ppu_water_carryover` adds an absolute `load/3000 s` loading rate, independent of the cycle. Tied to the cycle, a short-cycle demo tripped in 9 minutes with no chance to react.
- **Acetylene:** with no air (`load < 0.1`) it holds instead of integrating. Otherwise a stopped plant's frozen CO₂ reading drove AI-604 to its HH trip, and I-100 could never be reset.

**DCS control layer (`dcs/control.py`).** It is pure logic: `DCS.scan(ai, di, now, io_ok) → (ao, do)`. Callers hold `DCS.lock` around `scan()` and every command, because HTTP threads issue commands.
- **PID:** velocity-form PI. Error is in % of span, so Kc is dimensionless.
  - **Modes:** MAN / AUTO / CAS. In MAN the SP tracks the PV.
  - **SP ramp:** optional rate `sp_rate` (FIC-101 defaults to 1.45 kNm³/h per minute ≈ 1 %/min).
  - **Anti-windup** comes from clamping the integrated output.
- **Cascades** are declared on the master: `cascade: [{slave, op:[lo,hi], sp:[lo,hi|"sp_hi"]}]`.
  - **Split range:** PIC-502 GOX header → FIC-501 GOX (OP 0–80 %) and FIC-507 vaporizer (OP 80–100 %); PIC-504 GAN header → FIC-503 K-503 (0–80 %) and FIC-508 LIN vaporizer (80–100 %). That is how the backup vaporizers cut in. FIC-507/508 are tuned Kc 1.2 / Ti 20 (vaporizer lag 20 s); at the old Kc 0.3 / Ti 10 they took over a minute to deliver.
  - **Trip handover** (interlock key `handover: {master: slave}`, I-500 and I-503): on the trip, `_handover()` moves the master's OP straight to where the backup slave's SP equals the lost flow (feed-forward); the PI trims from there. No PI tuning could do it alone: either the header dipped to 3–5 bar(g) (GAN) / 23.8 bar(g) (GOX, below LL) or the vaporizer oscillated in the steep half of the split range.
  - **Handback** (the same pairs, reversed): when that slave goes back to CAS (line-up or mode change), `_handback()` re-initialises the master so the slave's SP = the total flow of all its slaves now. Without it the slave got whatever SP the master's OP implied (often full range) and the GAN header overshot to 9.4 bar(g).
  - **Bumpless return:** a master whose slaves are all out of CAS back-calculates its OP from the first slave's SP.
- **Override:** PIC-102 has `limits: "FIC-101"`. Its OP becomes FIC-101's output high limit (low select). External reset keeps PIC-102 at FIC-101.OP + 5 so it can take over without unwinding from 100 %.
  - LIC-415 (reboiler low-level override, SP 45 %, direct action) does the same on FIC-501. With pumped LOX, a header demand the column can't make (low air after a restart) would otherwise empty the sump; this was a gap in the old model too, hidden by the GOX vapour cap.
- **Cross-limiting ratio control** (burner air/fuel style), generic config keys:
  - `ratio_of` (FIC-111): in CAS, `rsp = ratio × max(partner PV, partner demand)`, so it leads on increase. The demand is capped at the partner's `sp_cap` (FY-501): before step 3 the booster ran for the uncapped demand, ~11 kNm³/h of air with no LOX to condense against at reduced load (found with the heat balance). The ratio is operator-settable within `ratio_lo..ratio_hi` (API field `ratio`, faceplate "Ratio").
  - `xlimit` (FIC-501): the SP is capped at `PV(xlimit) / (its ratio × 0.96)`, so it follows on increase. Status: `xlimited`, shown as `XLIM` / "CROSS-LIMIT".
  - **Why:** without it, a line-up after a restart let PIC-502 slam FIC-501 to full flow before the booster followed, and TI-512 tripped I-500.
  - **The 4 % margin** is wider than the flow noise (no flicker), and keeps the real ratio ≥ 1.46, above the 0.95 pinch.
- **Load limit** (generic key `sp_cap: "<tag>"`, used on FIC-501): the SP is capped at that tag's PV. `sp_lim` is the lower of the cross-limit and the cap; `lim_by` (snapshot) says which, shown as `XLIM` / "CROSS-LIMIT" or `CAP` / "LOAD LIMIT".
  - `FY-501` = `GOX_AIR_LIMIT` (0.208) × FIC-101 − FI-408 + `GOX_SUMP_ALLOWANCE` (0.5) × (LIC-404 − its SP, if above). The oxygen the air brings in, at the purity edge (~99.4 % if held there); the allowance lets P-501 draw down liquid that piled up while it was stopped (already separated, costs no purity).
  - **Why:** with pumped LOX nothing limited the draw any more. On the EO demand step PIC-502 drove FIC-501 to 31.5, the sump drained for hours and purity crashed to 94 % (a step 2a gap, found in 2b). Now the vaporizer covers the excess and purity sits at the spec edge until the air is raised. Restart at low air: ~99.2 % instead of 94 %.
  - 0.209 was too high (purity 98.9 % at reduced load: AIC-602 raises the reflux ratio when the site still takes full GAN). 0.205 would cap normal operation.
- **Scan order:** cascade masters, then overrides, then the rest.
- **Alarms (state-based, ISA-18.2-ish):**
  - H/HH/L/LL with a 1 % of span deadband, priorities 1–3.
  - Tags with `suppress: "<equipment key>"` (MAC, EXP, BAC, LOXP, GANC, LOXB, LINB) are not evaluated while that equipment isn't running. They clear silently.
  - Discrete alarms: `<interlock>.TRIP` (one per configured interlock, text from `trip_message`), `KS-201.*`, `IO.COMM`.
- **Interlocks:**
  - They latch and record a first-out. Reset is only allowed when no armed cause is active.
  - **Armed causes:** a cause armed with an equipment key (`"MAC"`, `"BAC"`, `"LOXP"`, `"GANC"`, `"LOXB"`, `"LINB"`) only counts once that equipment has run longer than its `bypass_s` (MAC 600 s, BAC/GANC 120 s, LOXP 60 s, backup pumps 30 s); otherwise a stopped plant could never be reset. `STARTUP_BYPASS_S` = the MAC's.
  - **Parents:** an interlock with `parents` trips when a parent trips, and can only be reset after it. I-100 → I-300, I-110 and I-503; I-110 → I-500. I-505 / I-506 (backup pumps, tank LL) have **no** parent on purpose: the backup must survive an ASU trip.
  - **Motor protection:** any equipment whose run feedback drops while commanded trips its own interlock.
  - **I-500** (LOX pump) trips on TI-512 LL −25 °C (cold oxygen into the carbon-steel header) or PI-511 LL 20 bar(g) (cavitation). Both are armed on LOXP.
  - **I-100** also trips on LIC-404 HH 95 %: a reboiler flooded because the LOX pump stays down. Single pump, no standby, by design choice.
  - While latched, `forces` hold loops in MAN at a fixed OP. An operator *stop* deliberately goes through the trip path, so it has to be reset like a trip.
- **Normal line-up** restores `LINEUP` modes, but skips loops on stopped equipment (in AUTO they would just wind up; this once made the expander overspeed-trip on restart). It reports the skipped loops. Equipment start sets a fixed start OP: FIC-101 40 %, FIC-303 30 %, FIC-111 30 %, FIC-501 20 %, FIC-503 30 %, FIC-507/508 0 %.
- **Start permissives:**
  - MAC: lube oil.
  - EXP: MAC running and PI-401.
  - BAC: MAC running.
  - LOXP: BAC running and LIC-404 ≥ 40 %.
  - GANC: MAC running and PI-401 ≥ 4 bar(a) (suction).
  - LOXB / LINB: tank level above its LL (10 %).
- **I/O map change gotcha:** after changing `common/iomap.py`, run `docker compose down` before `up -d --build`, and move the checkpoint aside.
  - **What happened in step 2.5:** `up --build` recreated the plant first, so the old DCS read the new map for a few seconds. It saw the run feedbacks drop, tripped I-110 / I-500 / I-503 on motor protection, and checkpointed the trips and stopped commands.
- **Checkpoint gotcha:** `data/dcs_state.json` restores each loop's mode, SP, OP, tuning and SP limits. After changing tuning or limits in `config.py`, move the checkpoint aside before testing, or the old values win.
- **KS-201 PPU sequence:** steps are fractions of `PPU_CYCLE_SECONDS`. HEAT drives TIC-204 in AUTO at 180 °C; other steps hold it in MAN at 0. Switch-over is permissive on STANDBY; while the MAC is stopped the operator may switch at any time.
  - **Time compression (cycle is runtime-adjustable, 300–14400 s):** the DCS owns the value.
  - **Owner:** `DCS.set_ppu_cycle()` rescales `online_time`/`step_time` so the sequence keeps its place, logs an event, and the cycle is checkpointed.
  - **Instructor change:** the instructor slider POSTs to the plant (`/api/ppu_cycle`). The plant applies it locally and pushes it to `DCS_URL/api/sequences/KS-201/cycle`; it retries every 5 s while `pending`.
  - **Otherwise:** the plant *adopts* the DCS value (`/api/config` → `ppu_cycle_s`), so after a plant restart the DCS checkpoint wins. `PPU_CYCLE_SECONDS` (one YAML anchor) is only the initial value.
  - **What scales:** bed loading, the regeneration gas time constant (`tau_regen = 600·cycle/14400`, min 15 s) and TIC-204 (flag `ppu_time=True`, its PI runs on `dt·14400/cycle`).
  - **Why TIC-204 too:** without it a 15-min cycle peaks at ~90 °C ("regeneration incomplete") and breaks through in ~35 min. Verified offline at 300 s and 900 s: peaks 187–191 °C, no breakthrough.
  - **Range limits:** `PPU_CYCLE_MIN/MAX` live in `dcs/config.py` AND `plant/main.py`.

**Historian (`dcs/historian.py`).**
- **Points:** `<TAG>.PV`, plus `.SP` and `.OP` for controllers.
- **Exception reporting:** deadband 0.25 % of span (0.1 % was ~29 samples/s, about 3 GB a month), heartbeat 60 s, retention 14 days.
- **Quality:** `GOOD` 0, `BAD` 1 (no I/O), `NOT_APPLICABLE` 2.
  - **Not applicable:** pass `historian.NA` as the value. `dcs/main.historian_values()` does that for a PV that is None while the I/O is fine, i.e. a calculation that doesn't apply right now.
  - **Storage:** both non-GOOD qualities are written once as a NULL row, so trends show gaps.
- **Events table:** the operator console's event journal.
- **Concurrency:** WAL mode, one connection per thread; the scan loop is the only writer.
- **Trend API:** `trend()` returns per-bucket min/max plus the last value before the window.

**OPC UA (`dcs/opcua_server.py`, asyncua 2.1).**
- **Address space:** generated from config: `Objects/HCW/Haltenberg/ASU1/<Block>/<Tag>/{PV,SP,OP,Mode,AlarmState}`.
- **NodeIds:** strings, `HCW.ASU1.<tag>.<attr>`, in namespace `urn:haltenberg:hcw:asu1:dcs`.
- **AnalogItems:** PV/SP/OP are instantiated from `AnalogItemType`, with EURange and EngineeringUnits. AccessLevel = CurrentRead|HistoryRead, Historizing = true.
- **HistoryRead** goes through `HistorianStorage`, a custom `HistoryStorageInterface` that reads the SQLite historian. There is no second copy of the history.
- **Quality:**
  - `BadNoCommunication` while the I/O link is down.
  - `BadOutOfService` for a value that is None while the I/O is fine (a calculation that doesn't apply now). This holds live and in HistoryRead (historian quality 2).
  - Before 2.5 every None showed as BadNoCommunication, which misleads data users.
- **asyncua 2.x gotcha:** the `DataValue` field is `StatusCode=`, not the 1.x `StatusCode_=`.
- **Read-only** in v1. Writable SPs, for an external APC agent (research §5.3 "APC ON/OFF"), are the obvious next step.

**Node-RED (`nodered` service).** It is an OPC UA client demo, the IT side of the story.
- **Image:** the official `nodered/node-red:4.1.4` plus `node-red-contrib-opcua@0.2.355`, installed in `/usr/src/node-red`. `/data` is the `./nodered_data` bind mount, so a palette install there wouldn't survive a fresh checkout. `FLOWS=flows.json` is pinned, as in SHT.
- **Flow:** `flows.json` was hand-written (no editor export). One tab, `OPC UA → 15-min TWA → CSV`:
  - **Subscribe:** inject → "Tags to subscribe" function → OpcUa-Client in `subscribe` mode with `msg.topic="multiple"`, `msg.payload=[{nodeId}]` (one subscription, auto-resubscribe on reconnect). The output is `msg.topic=nodeId`, `msg.payload=DataValue`.
  - **Average:** the "TWA per window" function uses sample-and-hold weighting by `sourceTimestamp`, left-clamped to the previous sample. Bad quality is excluded. A 5 s tick closes windows when nothing changes.
  - **Write:** the "CSV line" function (`fs` via the function node's `libs`) adds the header only if the file is new or empty. A file node appends the row.
  - **Window:** env `TWA_WINDOW_MINUTES` (default 15).
- **Endpoint:** `opc.tcp://dcs:4840/hcw/asu1/`, the compose service name, never localhost.
- **Known log noise:** "Encrypted credentials not found" and the node-opcua `[NODE-OPCUA-W27] … CVE-2023-46809` warning (we use SecurityPolicy None) are harmless.

**Trainers (step 4, `operator.html`, 🎓 Training).** Three trainers over the same plant, one per audience (Willem): 🎓 **Process** (`TRAIN`, the overview, plain language), ⚙️ **Engineering** (`TRAINERS.eng`, how the units work) and 🔌 **IT/OT** (`TRAINERS.ot`, from sensor to OPC UA; our core audience, so the first card on the hub).
- **One overlay card, no separate page** (Willem, after trying a side-by-side `/trainer` page: "I like the overlays, not the side screen"). The switch at the top of the card keeps the unit: Process step → topic via `DEEPER`, topic → Process step via its `process`; IT/OT follows the step's first watch tag.
- **Never opens by itself:** only 🎓 Training or `?training=1|eng|ot&topic=&step=&tag=`. The position (`hcw.train.pos`) is remembered for the button, on/off is not (bug: reopening the console used to land in a lesson).
- **State:** `TR {on, trainer, topic, step}`; `trSet()` validates, stores, and steers the console (`go(step.go)`, `select(step.sel)`). `renderTraining()` runs on every `render()` and after every `route()`: it moves `#train-card` to its host (`#mimic-wrap`, `#dmimic-wrap`, or the deep-dive panel's `.tf-side`; hidden on other tabs), rebuilds it only when the build key (trainer, topic, step, fig, hash, tab) changes, and otherwise only patches `[data-live]`. One delegated click handler on the card (`data-sw`, `data-topic`, `data-tr`, `data-fig`, `data-back`, `data-tag`).
- **Steps:** Process steps use `regions` + `marker` on the overview (`#train-layer`). Engineering/IT/OT steps steer the console (`go` display address, `hl` regions in that display's viewBox: overview 1520×672, detail 1520×630, `sel` faceplate) or open a **deep dive** (`fig`, or a `deep` button): `FIG[id].build(el)` once, `update(el)` every poll, in `#train-fig`, a fixed panel over the console with the card beside it. `drawHighlights()` redraws the `.hl-layer` when `buildDetail()` has replaced the SVG. If the user navigates away, the card offers "↺ Back to this step's display".
- **Live text:** `trainLive(i)` (Process) and each step's `live()` build "Right now in ASU1" from `ST`/`TAG` only. Never instructor scenarios or ground truth. The model's documented time constants (purity dead time ~4 min, lag ~10 min) may be *described*, as process knowledge.
- **IT/OT data path:** `GET /api/datapath?tag=` (`dcs/main.datapath()`), read-only from what the runtime already holds: the raw Modbus words of the last scan (`Runtime.wire`, set in `read_io`/`write_io`), the DCS tag and quality, the historian points (`historian.last`, `historian.count()` for the last hour) and the OPC UA NodeIds / browse paths / status. Calculated tags show `calc_from` = `config.CALC_INPUTS` (contract test: covers every CALC tag and PID with a calculated PV). Clicking any value on the console follows it (`select()` → `setTag()`).
- **Deep dives draw with the DCS's own constants:** `/api/config` carries `t_sat` (Clausius-Clapeyron, = `control.T_SAT_*`) and the historian settings come from `/api/historian`. The rich-liquid temperatures are an ideal-mixture (Raoult) estimate at 35 % O₂, labelled as such; no new calculated tags were needed.
- **Rendering:** the card sits next to the step's regions (left or right), top-right without regions or with `card: "right"` on the step (keeps the column visible on the reflux step), 620 px wide for topics with `wide: true` (the IT/OT ones, not tied to one piece of equipment); the data path runs L0 → L4, below the step text; stacked under the drawing below 900 px; its body scrolls. Teal (`--train`) is the training colour only.
- **Maintenance:** when you move mimic or detail-display elements, check the `regions`/`marker` (Process) and `hl` (C-401, E-301 steps) coordinates.

**Detail displays (ISA-101 L3, `operator.html`).**
- **Displays:** K-101 (MAC + DCAC), R-201 (mole sieve + KS-201 panel), K-102 (booster + ratio panel), E-301 (MHE + X-303; warm end on top, cold end at the bottom, a data block above each stream), C-401 (columns + E-404 + P-501), `products` (GOX/GAN headers, K-503, PIC-502 and PIC-504 split-range bars), T-505 (tanks, P-505/E-507, P-506/E-508).
  - `splitHtml(tag, x, y, w)` / `renderSplit(root, tag, …)` draw one bar per master; several per display are fine (selected by `data-split`).
- **Registry:** `DETAILS` holds one entry per display, in process order:
  - **Identity:** `title`, `short` (display-bar label), `block`, `aliases` (every equipment tag it covers) and the overview `hot` rects.
  - **Drawing:** its own `svg` markup, plus `placements`, `valves` (ids `dv-<valve>`) and `levels`.
  - **Optional:** `equip` (element id → equipment key), `status` (equipment/interlock lines that link to `#ilk`), `ladder` (boiling points), `build(d)` / `render(root, d)` hooks for display-specific graphics (`renderPPU`, `splitHtml`/`renderSplit`).
  - **Trends:** `minis` (`shared: true` = one scale for all points).
  - Data blocks, faceplate, banner and polling are shared with the overview.
- **Off-page connectors:** `opc(x, y, text, to, {w, left})` links to display `to`, so streams can be followed from display to display. `left` is an inbound arrow from the right.
- **Display bar** (`.dnav`, on the overview and on every detail display): lateral navigation; each button shows the highest active alarm among that display's placements.
- **Routing:** the hash is the display address (`#overview`, `#tags`, …, `#detail/<id or alias>`). In-app navigation goes through `go()`, which pushes history entries marked `{app: true}`; the first entry is marked `{root: true}`. The detail Back button uses `history.back()` only when the current entry is ours, otherwise it closes to the overview. Close and Esc always go to the overview.
- **Gotcha:** never declare a global function named `history` (or another `window` property) in the page script: it replaces `window.history`. The historian fetch is `fetchHistory()`.
- **C-401 column display:** calc tags `TY-411` (N₂ condensing at PI-401), `TY-412` (O₂ boiling at PIC-402) and `TY-413` (N₂ boiling at PIC-402) use the DCS's own Clausius-Clapeyron `t_sat()` in `control.py`, with the same constants as the plant model. So TI-407 ≈ TY-411 (pure N₂ at saturation), and TI-406 sits below TY-412 by the LOX impurity.

**Hidden faults.** The SHT rule carries over: scenarios live only in the plant (`SCENARIOS`, instructor HTTP), never in Modbus, the historian or OPC UA. Don't add a status point for them.

## Tests and the interface inventory

**Goal (Willem, Oct 2026).** As the model grows, the system must keep behaving consistently, realistically enough, and with stable, testable interfaces between the systems: plant ↔ Modbus ↔ DCS ↔ historian / OPC UA / HTTP ↔ HMIs, Node-RED, external clients. No tag may go missing, and behaviour may not change silently in one system and not another. The goal is *not* a perfect training simulator.

**Approach: one trusted inventory per component, checked by tests.**
- **Each component owns its interface in one source file**, its source of truth, and only *reads* names from the layer below it:

  | Component | Source of truth | Inventory file |
  |---|---|---|
  | Field I/O | `common/iomap.py` | `field.json` |
  | DCS | `dcs/config.py` | `dcs.json` |
  | OPC UA | generated from the config | `opcua.json` |
  | HTTP API / historian | `dcs/main.py` | `api.json` |
  | Plant / instructor | `plant/model.py` | `plant.json` |

  `tags.csv` joins one tag across all of them.
- **The inventory is generated, never edited.** It is trusted *because it is tested*:
  - `test_inventory`: committed inventory == code;
  - `test_contracts`: every component only uses names that exist in its source;
  - `test_e2e`: the running programs expose exactly what the inventory says.
- **Every change is checked against the inventory:**
  1. Before renaming or removing something, look up who uses it (`inventory/`, `tags.csv`, then grep the consumers: displays, training, Node-RED, docs).
  2. Make the change in the source of truth and in every consumer.
  3. Run `docker compose run --rm tests`. If `test_inventory` fails, read the diff: is *only* the intended interface change in it?
  4. Regenerate (`python tools/inventory.py` in the tests image) and commit the inventory with the change.
  5. Mention every added, renamed or removed name in the PR. Renames and removals break external clients.

**Method: four test layers, fastest first.**
1. **Interface** (`test_inventory`, `test_contracts`, ~10 s): structure only, no physics beyond one scan.
2. **Behaviour** (`test_behaviour`, ~30 s): plant model + DCS coupled in-process (`tools/offline_sim.Sim`), faster than real time, fixed seed.
   - **Cause and effect:** steady state, one signature per instructor scenario, restart procedures, permissives, trips.
   - **Bands, not traces:** limits are ranges, preferably the config's alarm limits, so retuning stays possible.
   - **Robust across seeds:** the limits must hold for any seed, not just the default.
3. **End to end** (`test_e2e`, ~20 s): the real `plant/main.py` and `dcs/main.py` as processes over real Modbus, checked from the outside (HTTP, raw Modbus, OPC UA browse / read / HistoryRead) against the committed inventory.
4. **Mutation check (manual, when the tests change):** break the code on purpose and confirm a test catches it.
   - **Done for nine typical breaks:** a tag removed, an OPC UA node renamed, a Modbus point inserted, an interlock cause with a missing limit, a data block missing, the handover removed, a ground-truth key renamed, a bad Node-RED NodeId, a scenario without a signature.
   - **How:** a scratch copy of the repo and `docker run -v <copy>:/w hcw/asu1-tests`.

**Inventory details:**
- **Sources of truth:** each layer keeps its own: `common/iomap.py`, `dcs/config.py`, `plant/model.py`, `dcs/main.py` / `dcs/opcua_server.py`.
- **Generator:** `tools/inventory.py` reads them all, and builds the OPC UA address space in-process exactly as the DCS does, without starting a server. It writes `inventory/*.json` with one record per line, so a changed tag is a one-line diff, plus `tags.csv` (one tag joined across layers).
- **Never edit inventory/ by hand.** After an intended interface change, regenerate it and commit it with the change.
- **Ownership table:** `inventory/README.md`.

**Four test files:**
- **`test_inventory`:** committed inventory == code. On a failure it prints the full diff (`pytest.fail(pytrace=False)`; a plain assert gets truncated).
- **`test_contracts`:** cross-references. It uses the `code` fixture (inventory generated from the code *now*), not the committed `inv`, so it doesn't depend on someone regenerating first. It checks:
  - the plant writes exactly the AI/DI and reads exactly the AO/coils;
  - every `io`/`out`/`cmd`/`fb` exists, and every field point is used or listed in `UNMAPPED_FIELD_POINTS`;
  - interlock causes name an existing **alarm limit** (`_limit_active()` silently returns False otherwise: the trip could never fire);
  - cascade / limits / ratio / xlimit / sp_cap / handover / LINEUP / PRIORITY_OVERRIDE references;
  - every tag has a value after one scan, and the historian points;
  - OPC UA units and ranges, and that hidden faults stay hidden;
  - every instrument tag named in operator.html, index.html, instructor.html, README, CLAUDE.md, the research doc and every wiki page exists (`DRAWING_ONLY` lists HV-116);
  - every DCS tag has a data block (`["TAG", x, y` placement);
  - console equipment and valve ids;
  - instructor ground-truth keys and Node-RED NodeIds;
  - the hub/README/wiki Architecture counts ("19 controllers", "7 interlocks (I-100 … I-506)");
  - wiki links between pages and to images resolve.
- **`test_behaviour`:** `Sim(seed, t0)`; `Sim.run(seconds, until=, win=)` returns a `Window` (min/max per tag, alarms seen, events). It covers:
  - steady state 2 h: no P1/P2 alarms (P3 FIC-507.H can occur: normal demand wander touches the FY-501 load limit), in spec, every tag live and in range;
  - repeatability;
  - `SIGNATURES`: one per instructor scenario (first-out regex, required alarms, an extra check). A new scenario fails `test_every_scenario_has_a_signature` until it has one;
  - the restart procedures (wiki: Process behaviour);
  - permissives, reset refused while a cause is active, ESD, I/O loss, checkpoint, PPU compression at 900 / 300 s;
  - per-GOX figures not applicable without production;
  - E-301: the balance recomputed from DCS tags closes within 2 % (steady and with the expander stopped), TDI-302 = TI-307 − TI-310, the expander cools 60–75 K; expander stop warms the cold end; at reduced air the booster follows the load limit.
- **`test_e2e`:** runs `plant/main.py` and `dcs/main.py` as processes on free ports (`PYTHONPATH=common`, temp `DATA_DIR`). It checks against the committed inventory:
  - the HTTP shapes, and `/api/datapath` (raw words = float = DCS PV, NodeId, a calculated tag's inputs);
  - the raw Modbus registers vs the DCS PVs;
  - an OPC UA browse (the same `walk()` as the generator), values Good and in range, HistoryRead;
  - a mode change via the API showing up on OPC UA;
  - after an ESD: KPI-701 `BadOutOfService` live and in history, while measured tags stay Good;
  - a raw FC4 read of 126 registers refused with exception 3;
  - last, killing the plant: Bad quality, `IO.COMM` (raised after 3 s), no new trips.

  `asyncua` `read_data_value()` raises on Bad unless `raise_on_bad_status=False`.

**Limits are bands, not traces, and robust across seeds.** The behaviour tests pass on seeds 1–44 except 37 (step 3).
- **Transmitter noise has its own random stream** (`Plant.noise`, step 3). Before, every `gauss()` in `measurements()` came from the global stream, so adding an instrument changed the demand wander and purge jobs of every seed. That is how step 3 found that the 2.5 "seeds 1–22" result held for those 22 draws only.
- **What the new draws exposed, fixed in step 3 (Willem's choice):**
  - Design O₂ demand sat on the GOX load limit (~29.2 vs FY-501 29.4, which is at the purity edge), so normal wander held GOX on the limit for up to 45 min in 2 h and purity dipped to 99.46 % (L) in "steady state". The "Other" O₂ consumers went 2.7 → 2.2 kNm³/h (average demand ~28.7).
  - The GAN handback band went 8.8 → 9.1 bar(g): purge jobs that end just at the handback add ~0.2 (without the handback: 9.4).
- **Seed 37, known:** in the second hour after a full MAC restart purity touches 99.49 % (L): a high EO-demand draw keeps GOX on FY-501. Same mechanism, rarer; more margin would mean less GOX (Willem chose 2.2).
- **Header limits come from config alarm limits.** On trips the backups keep both headers above **LL**, not L. When a site N₂ purge job coincides with a K-503 or MAC trip, the GAN header touches ~7.27 bar(g), below L (7.3), for under a minute. Without the handover it fell to 3–5.
- **The GAN header in steady state** is only checked against its alarm limits, because purge jobs move it.
- **Range tolerance:** 2 % of span for measured tags.

**Every tag stays inside its range (= OPC UA EURange) in every scenario.** There is no exception list. When the tests (2.5) found four calculated tags outside their range on trips, each was handled by its cause:
- **Artifacts of our calculation:**
  - KPI-701 / FFI-111 divided by a GOX flow coasting down to 3 kNm³/h, giving 5 kWh/Nm³ and a ratio of 12. They are now computed only while P-501 runs and FIC-501 ≥ `PER_GOX_MIN_FLOW` (10); otherwise None → `BadOutOfService`.
  - FY-501 had no limits (−0.03 with the MAC stopped, 39.8 with a flooded sump). It is now clamped to 0–35, with no control effect, because FIC-501's SP stops at 31.5.
- **Real physics, range too narrow:** TDI-410. After a shutdown the HP column depressurises and its N₂ saturates at −196 °C under the −183 °C LOX, a real −13 K. Range now −15…10.
- **Rule for the next one:** widen the range if it is a real measurement; make it "not applicable" if it is a calculation without meaning at that moment.

**Accepted simplification:** ZS-201A/B (bed online feedback) are on Modbus, but KS-201 doesn't read them. The model's valves always follow HS-201, so feedback = command. Willem: leave as is.

**Workflow per step:**
1. Run `docker compose run --rm tests` before every commit.
2. An interface change means a regenerated inventory in the same commit, and a sentence in the PR.
3. New tags need a data block. New scenarios need a `SIGNATURES` entry.
4. Adding to `UNMAPPED_FIELD_POINTS` / `DRAWING_ONLY` / `KNOWN_OUT_OF_RANGE` is a design decision: give the reason.
5. After retuning, try another seed.

## Conventions carried over from SHT

- **Packaging:** one `docker compose up -d --build`. Python 3.12-slim, `USER nobody`, pinned `requirements.txt` (`dcs/` only; `plant/` is stdlib-only).
- **Compose:** the build context is the repo root, so both images can copy `common/`.
- **Settings:** env vars with code defaults, documented in the wiki's Configuration page.
- **Networking:** services reach each other by compose service name (`plant`), never localhost.
- **Web:** self-contained `web/*.html`, vanilla JS, no build, no CDN.
  - **Hub:** `/` is navigation; persona pages link back with `⌂ All systems`. External tools (Node-RED) get a card with `target=_blank`, and their URL comes from `/api/config` links (`PUBLIC_NODERED_URL`).
  - **Reference data:** pages fetch it from the API (`/api/config`) rather than hardcoding it.
  - **Mimic config:** layout lives in JS consts (`PLACEMENTS`, `VALVES`, `LEVELS`, `MINIS`).
- **Styling:**
  - **Operator console:** ISA-101 high-performance HMI. Background, equipment and text are grey. Saturated colour is reserved for alarms (P1 red, P2 amber, P3 violet, bad quality magenta). HCW blue is brand only.
  - **Process media** get muted colours for line and liquid identification only (CSS vars `--air`, `--o2`, `--n2`, `--rich`, `--util` and the fills `--lox`, `--lin`, `--richliq`; classes `.pipe.o2|n2|rich|util`, `.liquid.lox|lin|rich`). O₂ blue follows DIN 2403; N₂ is muted green because DIN's yellow would clash with alarm amber. The legend sits at the right of the display bar. Every new line needs its media class.
  - **Analog bars** in every data block (except KPI-701): the tag's full range as a 4 px grey track. Zones beyond L/H are medium grey, beyond LL/HH dark grey (from `alarms` in `config.py`). There is a PV pointer and, for controllers, a hollow SP triangle. Built by `barHtml()`, positioned each poll by `barX()`. Data blocks keep their 96 × 34 / 48 size; the alarm badge sits top-right.
  - **Flow arrows** are generated, not drawn: `addFlowArrows()` puts one small arrowhead per `path.pipe`, half-way along its longest straight run and moved off valves, in the media colour. So **draw every pipe in flow direction**. Shafts get `.noarrow`; `.thin` signal lines and runs < 50 units get none. `pathSegments()` understands M/H/V/L and the `hop()` arc only.
  - **Liquids** are clipped to their vessel's inner shape (`clipPath`; `clip()` helper on detail displays). Level spans (`LEVELS`, `levels`) are the *inner* height, so 0 % is the inside bottom.
  - **Overview line hops:** the static SVG writes hops literally as `H{x-6}a6 6 0 0 1 12 0` (the same arc the detail displays' `hop(x)` makes). The horizontal line hops; a vertical line never does.
  - **Instructor station:** deliberately dark and violet ("simulator side of the glass"), so nobody mistakes it for the DCS.
- **Never rebuild an open form from under the user on a poll.**
  - **Faceplate:** built once per selected tag; polls only patch text, and tuning inputs are skipped while focused or dirty.
  - **No browser dialogs:** ESD uses an inline arm-then-confirm button instead of `confirm()`.

## Not built yet / open

Ideas for these are tracked in `ROADMAP.md`.

- **Not built:** OPC UA writes and an APC/MPC layer; OPC UA Alarms & Conditions (alarms are only exposed as `AlarmState` strings and `Diagnostics` counts); the argon column; tank truck loadouts (deliberately not planned; the tanks only fill from the column and drain through the backups); vaporizer outlet temperatures (E-507/E-508 are assumed big enough); business systems (LIMS purity cross-checks, SAP-PM, ERP volumes — research §6).
- **Only the PPU is compressible:** the instructor slider speeds up the mole-sieve cycle. Everything else runs in real time. A load change at the default ramp takes ~40 min, and purity recovery after a restart ~1 h. That's intentional (research §4.4) but slow for live demos.

## Roadmap and parallel work

Plans, ideas and progress live in **`ROADMAP.md`**: what is in progress (owner, branch, reserved tag names and Modbus addresses), what is planned, ideas under discussion, what was parked or dropped and why, and what is done. Several people and agents work in parallel, so before starting anything:
- work in **your own git worktree** on your own branch, never in a checkout another agent is using: no `git checkout`/`switch`, `stash`, `reset` or `add -A` there (details in `ROADMAP.md`, "Several agents on one machine");
- check `ROADMAP.md` on `main` and on the other branches (the command is in its rules) so you don't take claimed work or reserved addresses;
- claim the item in your branch's first commit;
- never move an idea to Planned, or take someone else's item, without Willem or David.

Lasting knowledge from a finished item (how it works, why, gotchas) belongs in this file; `ROADMAP.md` keeps only a summary.
