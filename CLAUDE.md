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
  - **Split range:** PIC-502 GOX header → FIC-501 GOX (OP 0–80 %) and FIC-507 vaporizer (OP 80–100 %). That is how the backup vaporizer cuts in.
  - **Bumpless return:** a master whose slaves are all out of CAS back-calculates its OP from the first slave's SP.
- **Override:** PIC-102 has `limits: "FIC-101"`. Its OP becomes FIC-101's output high limit (low select). External reset keeps PIC-102 at FIC-101.OP + 5 so it can take over without unwinding from 100 %.
- **Scan order:** cascade masters, then overrides, then the rest.
- **Alarms (state-based, ISA-18.2-ish):**
  - H/HH/L/LL with a 1 % of span deadband, priorities 1–3.
  - Tags with `suppress: "MAC"|"EXP"` are not evaluated while that equipment isn't running. They clear silently.
  - Discrete alarms: `I-100.TRIP`, `I-300.TRIP`, `KS-201.*`, `IO.COMM`.
- **Interlocks:**
  - They latch and record a first-out. Reset is only allowed when no armed cause is active.
  - `"mac"`-armed causes, such as LIC-404 LL or PI-107 LL, only count while the MAC has run longer than `STARTUP_BYPASS_S`; otherwise a stopped plant could never be reset.
  - While latched, `forces` hold loops in MAN at a fixed OP. An operator *stop* deliberately goes through the trip path, so it has to be reset like a trip.
- **Normal line-up** restores `LINEUP` modes, but skips loops on stopped equipment (in AUTO they would just wind up; this once made the expander overspeed-trip on restart). Equipment start sets a fixed start OP: FIC-101 40 %, FIC-303 30 %.
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
- **Displays:** K-101 (MAC + DCAC), R-201 (mole sieve + KS-201 panel), E-301 (MHE + X-303), C-401 (columns + E-404), `products` (GOX/GAN headers + PIC-502 split-range bar), T-505 (tanks + E-507). Graphics only: they use existing tags and DCS state (equipment, interlocks, KS-201), no new model data.
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
  - **Operator console:** ISA-101, a grey world with colour only for alarms (P1 red, P2 amber, P3 violet, bad quality magenta). HCW blue is brand only.
  - **Instructor station:** deliberately dark and violet ("simulator side of the glass"), so nobody mistakes it for the DCS.
- **Never rebuild an open form from under the user on a poll.**
  - **Faceplate:** built once per selected tag; polls only patch text, and tuning inputs are skipped while focused or dirty.
  - **No browser dialogs:** ESD uses an inline arm-then-confirm button instead of `confirm()`.

## Not built yet / open

- **Not built:** OPC UA writes and an APC/MPC layer; OPC UA Alarms & Conditions (alarms are only exposed as `AlarmState` strings and `Diagnostics` counts); the argon column; LIN backup for the GAN header (it collapses on a MAC trip; planned in roadmap step 2); tank truck loadouts (deliberately not planned) (tank levels only drift); business systems (LIMS purity cross-checks, SAP-PM, ERP volumes — research §6).
- **Only the PPU is compressible:** the instructor slider speeds up the mole-sieve cycle. Everything else runs in real time. A load change at the default ramp takes ~40 min, and purity recovery after a restart ~1 h. That's intentional (research §4.4) but slow for live demos.

## Roadmap: physically complete displays (started Oct 2026)

Working agreement: one branch and one PR per step. Verify each step with the offline sim, the API, an OPC UA client and a headless screenshot.

- **Done, step 1:** L3 navigation framework, C-401 column display, `TY-411/412/413` calc tags.
- **Done, step 1b:** graphics-only detail displays for every unit (no new tags or model data), display bar, linked off-page connectors.
- **Step 2: physically correct products.** It changes `model.py`, `iomap.py`, `config.py`, the overview and the products/storage displays.
  - **Open decision (ask Willem first):** product compressors or internal compression (LOX pump + vaporizing in E-301).
  - **Why:** today GOX goes from the LP column (~1.3 bar(a)) through only FV-501 into a 30 bar(g) header, and GAN from the HP column (~5.3 bar(a)) into an 8 bar(g) header. A valve can't raise pressure; the model hides this because product flow = valve % × constant.
  - **Also in step 2: LIN backup vaporizer.** Add E-508 / `FIC-508` from T-506 into the GAN header. PIC-504 becomes split range like PIC-502: FIC-503 at OP 0–80 %, FIC-508 at OP 80–100 %. The storage display then gets T-506's outlet (today the tank only fills, about +0.09 %/h), and the products display gets a second split-range bar. No truck loadouts.
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
