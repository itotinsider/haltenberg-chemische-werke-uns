# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A demo plant for a fictional chemical site, **Haltenberg Chemische Werke (HCW)**. The first unit is **ASU1**, a Linde-type Air Separation Unit (~1,000 t/d GOX, 145 kNm³/h air). The process model, tag list and control strategy follow `linde air seperation research.md`; section references (§) below point into it.

It is the sibling of the Sweet Harmony Treats waffle-line simulator (`../sht-scada/sweet-harmony-treats-main`, "SHT"). The build philosophy is the same, with one deliberate architectural difference:

- **SHT**: one simulator publishes to an MQTT broker; HMIs poll its HTTP API.
- **HCW**: a simulated **DCS** with a **built-in historian** exposes its data over **OPC UA** (not MQTT). There is no broker. The plant and the DCS are separate containers joined by a raw **Modbus TCP** field link.

Keep this file current. It is the main knowledge carrier between sessions.

## Layout

```
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
dcs/web/operator.html    operator console (overview mimic + L3 detail displays, tag list, interlocks, alarms/events, trends)
tools/offline_sim.py     plant model + control layer coupled directly, faster than real time
nodered/Dockerfile       official Node-RED image + node-red-contrib-opcua (baked in, /data is a bind mount)
nodered_data/flows.json  the only tracked file in nodered_data/ (runtime state is git-ignored)
data/                    bind mount: historian.db, dcs_state.json (git-ignored)
exports/                 bind mount: CSVs Node-RED writes (git-ignored, .gitkeep tracked)
```

## Commands

```bash
docker compose up -d --build          # REQUIRED after any code/web change (files are copied into images)
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
```

**OPC UA client checks:** the DCS image has asyncua installed, so a throwaway client container on the compose network works:
```bash
docker run --rm --network <repo-folder>_default -v /path/to/script:/s --entrypoint python hcw/asu1-dcs:latest /s/client.py
# network = compose project (folder) name + _default, e.g. haltenberg-chemische-werke-uns_default
# connect to opc.tcp://dcs:4840/hcw/asu1/, namespace "urn:haltenberg:hcw:asu1:dcs"
```

**Screenshots** (headless Chrome; Playwright is not installed). `?tag=<TAG>` opens a faceplate, `?trend=A.PV,B.PV&range=<min>` preselects trend points and window (a RANGES value), `#overview|tags|ilk|alarms|trends` selects a tab and `#detail/<id>` (e.g. `#detail/C-401`) a detail display, so every view can be captured without clicking. README screenshots live in `docs/screenshots/`; for a lively trend, compress the PPU cycle to 300 s for ~15 min first, then restore 14400:
```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars \
  --window-size=1600,1000 --virtual-time-budget=6000 --screenshot=out.png "http://localhost:8110/operator?tag=PIC-502#tags"
```

There is no test suite. Verification is the offline sim, the API, an OPC UA client and screenshots. **Shell gotcha:** in zsh, `echo =====` fails ("= not found"), and `"${2:-{}}"` mangles JSON bodies. Pass JSON literally.

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
  - Booster air with no LOX to condense against (`bst_excess`) costs 0.04 refrigeration per kNm³/h and raises the warm-end ΔT.
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
  - `ratio_of` (FIC-111): in CAS, `rsp = ratio × max(partner PV, partner demand)`, so it leads on increase. The ratio is operator-settable within `ratio_lo..ratio_hi` (API field `ratio`, faceplate "Ratio").
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
- **Bad quality** is written once as a NULL row, so trends show gaps.
- **Events table:** the operator console's event journal.
- **Concurrency:** WAL mode, one connection per thread; the scan loop is the only writer.
- **Trend API:** `trend()` returns per-bucket min/max plus the last value before the window.

**OPC UA (`dcs/opcua_server.py`, asyncua 2.1).**
- **Address space:** generated from config: `Objects/HCW/Haltenberg/ASU1/<Block>/<Tag>/{PV,SP,OP,Mode,AlarmState}`.
- **NodeIds:** strings, `HCW.ASU1.<tag>.<attr>`, in namespace `urn:haltenberg:hcw:asu1:dcs`.
- **AnalogItems:** PV/SP/OP are instantiated from `AnalogItemType`, with EURange and EngineeringUnits. AccessLevel = CurrentRead|HistoryRead, Historizing = true.
- **HistoryRead** goes through `HistorianStorage`, a custom `HistoryStorageInterface` that reads the SQLite historian. There is no second copy of the history.
- **Quality:** `BadNoCommunication` while the I/O link is down.
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

**Training overlay (`operator.html`, 🎓 Training).** For students.
- **Data:** the `TRAIN` array holds, per step: title, plain-language `text`, the mimic `regions` it highlights (viewBox rects), `marker` position, `watch` tags and a `think` line.
- **Live text:** `trainLive(i)` builds the "Right now in ASU1" bullets from `ST`/`TAG` only, i.e. DCS data. It must never reference instructor scenarios or plant ground truth.
- **Rendering:**
  - Highlighting is an SVG mask in `#train-layer`.
  - The card is absolutely positioned next to the region inside `#mimic-wrap`; below 900 px wide it is stacked under the mimic instead.
  - The card is built once per step; polls only patch the `[data-live]` list.
  - Teal (`--train`) is the training colour only.
- **Deep link:** `?training=1&step=n`. On/off and the current step are kept in localStorage.
- **Maintenance:** when you move mimic elements, check the `regions`/`marker` coordinates.

**Detail displays (ISA-101 L3, `operator.html`).**
- **Displays:** K-101 (MAC + DCAC), R-201 (mole sieve + KS-201 panel), K-102 (booster + ratio panel), E-301 (MHE + X-303), C-401 (columns + E-404 + P-501), `products` (GOX/GAN headers, K-503, PIC-502 and PIC-504 split-range bars), T-505 (tanks, P-505/E-507, P-506/E-508).
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

## Conventions carried over from SHT

- **Packaging:** one `docker compose up -d --build`. Python 3.12-slim, `USER nobody`, pinned `requirements.txt` (`dcs/` only; `plant/` is stdlib-only).
- **Compose:** the build context is the repo root, so both images can copy `common/`.
- **Settings:** env vars with code defaults, documented in the README table.
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
  - **Instructor station:** deliberately dark and violet ("simulator side of the glass"), so nobody mistakes it for the DCS.
- **Never rebuild an open form from under the user on a poll.**
  - **Faceplate:** built once per selected tag; polls only patch text, and tuning inputs are skipped while focused or dirty.
  - **No browser dialogs:** ESD uses an inline arm-then-confirm button instead of `confirm()`.

## Not built yet / open

- **Not built:** OPC UA writes and an APC/MPC layer; OPC UA Alarms & Conditions (alarms are only exposed as `AlarmState` strings and `Diagnostics` counts); the argon column; tank truck loadouts (deliberately not planned; the tanks only fill from the column and drain through the backups); vaporizer outlet temperatures (E-507/E-508 are assumed big enough); business systems (LIMS purity cross-checks, SAP-PM, ERP volumes — research §6).
- **Only the PPU is compressible:** the instructor slider speeds up the mole-sieve cycle. Everything else runs in real time. A load change at the default ramp takes ~40 min, and purity recovery after a restart ~1 h. That's intentional (research §4.4) but slow for live demos.

## Roadmap: physically complete displays (started Oct 2026)

Working agreement: one branch and one PR per step. Verify each step with the offline sim, the API, an OPC UA client and a headless screenshot.

- **Done, step 1:** L3 navigation framework, C-401 column display, `TY-411/412/413` calc tags.
- **Done, step 1b:** graphics-only detail displays for every unit (no new tags or model data), display bar, linked off-page connectors.
- **Step 2: physically correct products.** Willem chose **internal compression** for oxygen, a **K-503 GAN compressor** for nitrogen, a **single** LOX pump (no standby), a separate motor-driven booster, and backup pumps at both tanks.
  - **Done, step 2a (oxygen internal compression):** K-102 booster, P-501 LOX pump, cross-limited ratio FIC-111/FIC-501, FFI-111, TI-512, PI-511, LIC-415 override, I-110/I-500 with generic interlock parents and per-equipment bypass, booster/cavitation scenarios, KPI-701 incl. booster.
    - **Displays done here:** a new K-102 display; E-301, C-401 and products updated; booster and pump symbols on the overview. The rest of the L2 redraw is still open.
  - **Done, step 2b (nitrogen + backups):** K-503 GAN compressor (FIC-503 on its IGV, PI-513, JI-514, VI-515, I-503 child of I-100); P-505 / P-506 backup pumps kept cold on recirculation (PI-516, PI-517, I-505 / I-506 on tank LL, no parents); E-508 + FIC-508 with PIC-504 split range; trip handover / handback; GAN header hold-up ×5; KPI-701 incl. JI-514 (H 0.60); scenarios `gan_compressor_trip`, `lox_backup_pump_trip`.
    - **Also fixed here (2a gap):** the GOX load limit FY-501 (`sp_cap`), see the DCS section.
    - **Displays done here:** products (K-503, second split bar) and T-505 (pumps, E-508); on the overview K-503, P-505 and the P-506 / E-508 riser. The rest of the L2 redraw is 2c.
    - **Known, not fixed:** after a restart at low air (FIC-101 not yet raised) the site still takes full GAN, AIC-602 raises the reflux and GOX purity sits at ~99.2 % (L alarm) until the air is raised — the documented procedure raises it.
  - **Step 2c:** the overview (L2) redraw below. The nitrogen-side and storage L3 displays were already updated in 2b. Also on L2: the GAN line crosses the reflux / LIN-to-storage (x 872) and rich-liquid (x 902) risers, and the FV-507 riser (x 1330) crosses the GAN header, all without line hops.
  - **Also in step 2: overview (L2) drawing fixes.** The L3 displays already draw these correctly:
    - LOX is drawn leaving at the condenser's height instead of from the LP sump.
    - LIN is not drawn as coming from E-404.
    - GOX, GAN and waste N₂ bypass E-301 on the overview.
- **Step 3: MHE + expander tags.**
  - Every stream through E-301 gets in/out temperatures, so the E-301 display gets data blocks.
  - X-303 gets inlet/outlet temperature and pressure tags (≈ −110 °C at 5.5 bar(a) in, ≈ −178 °C at 1.3 bar(a) out).
- **Step 4: training.**
  - Link the `TRAIN` steps to their detail displays.
  - The column step gets a boiling-point table: N₂ −196 / O₂ −183 °C at 1 atm; HP-top N₂ ≈ −177 °C vs LP-sump O₂ ≈ −180 °C.
  - It also gets the LP feed temperatures before/after their valves: rich liquid −174 → ≈ −189 °C, LIN −177 → ≈ −193 °C. The rich-liquid flash needs a composition estimate; TY-413 already gives the LIN value.
- **Step 5:** README, CLAUDE.md, screenshots.
