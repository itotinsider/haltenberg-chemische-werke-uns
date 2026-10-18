# Roadmap and progress

This file is where plans, ideas and progress for HCW are tracked. People and AI agents use it
to propose work, discuss it, claim it and report on it. Several of them may work in parallel.

`CLAUDE.md` describes how the system *works*. This file lists what we are going to change, who
is on it, and what has been done.

## How to use this file

### Lifecycle

| Status | Meaning | Who moves it here |
|---|---|---|
| 💡 Idea | Anyone's proposal, open for discussion. Rough is fine. | anyone |
| 📋 Planned | Agreed scope and "done when". Ready to pick up. | Willem or David |
| 🚧 In progress | Claimed: it has an owner and a branch. | the owner |
| ✅ Done | Merged. One-line result plus a PR link; the item moves to [Done](#done). | the owner, in the PR |
| ⏸ Parked / ❌ Dropped | Not now, or not at all. Always give the reason, so it isn't proposed again. | Willem or David |

AI agents may add ideas, but they never move an item to Planned on their own, and they never
take an item that has a different owner.

### Item template

Copy it to the end of the right section. IDs are short slugs (not numbers), so two people
adding items at the same time don't collide.

```markdown
### `slug` — Title
- **Status:** 💡 Idea · **Owner:** — · **Branch:** — · **PR:** —
- **Goal:** what changes for a user of the demo, in one or two sentences.
- **Touches:** the source files and interfaces it changes (iomap areas, config blocks, displays, API).
- **Reserves:** new tag names and Modbus addresses it takes (only when In progress).
- **Depends on:** other slugs, if any.
- **Done when:** checkable acceptance criteria.
- **Notes:** discussion and decisions, each dated and named: `2026-10-18 Willem: …`.
```

### Rules for parallel work

1. **Check before you start.** Look at this file on `main` *and* on every other branch, because a
   claim is made on a branch:
   ```bash
   git fetch
   git grep -n -F "In progress ·" $(git branch -a --format='%(refname:short)') -- ROADMAP.md
   ```
2. **Claim with your first commit.** On your new branch, set Status, Owner and Branch, and fill in
   **Touches** and **Reserves**. Push the branch early (after an OK) so remote collaborators see it.
3. **Reserve shared names and addresses.** Tag numbers and Modbus addresses are where parallel work
   collides. `common/iomap.py` gives each process area its own address range, and a new point takes
   the next free address in its area. If two items need the same area, the second one starts
   after the first one's reservation.
4. **Watch the shared sources of truth.** `common/iomap.py`, `dcs/config.py`, `plant/model.py`,
   `dcs/web/operator.html` and `inventory/` change in almost every step. Two in-progress items that
   touch the same block or display should be sequenced, or agreed in their Notes.
5. **Edit only your own item.** Append new items at the end of their section. If this file
   conflicts in a merge, keep both sides.
6. **Decisions go into the item while it runs.** When it is merged, lasting knowledge (how
   something works, why it was built this way, gotchas) moves to `CLAUDE.md`. This file keeps only
   a short summary.
7. **Every item ends green.** Before the PR: `docker compose run --rm tests`, plus a regenerated
   `inventory/` if an interface changed. For new behaviour or graphics, also check the offline
   sim, the API, an OPC UA client and a headless screenshot. One branch and one PR per item.

Tag names in Ideas and Planned may not exist yet. That is why this file is not part of the
"docs only name existing instruments" test, unlike README and CLAUDE.md.

## In progress

### `mhe-expander-tags` — Step 3: E-301 and expander tags
- **Status:** 🚧 In progress · **Owner:** Willem (with Claude) · **Branch:** not committed yet (uncommitted work in the main checkout) · **PR:** —
- **Goal:** every stream through E-301 gets in/out temperatures, so the E-301 detail display gets
  data blocks. X-303 gets inlet/outlet temperature and pressure (≈ −110 °C at 5.5 bar(a) in,
  ≈ −178 °C at 1.3 bar(a) out). The plant closes an enthalpy balance over the exchanger.
- **Touches:** `common/iomap.py` (area 3), `dcs/config.py` (ColdBoxHeatExchange block),
  `plant/model.py`, the E-301 detail display, `inventory/`.
- **Reserves:** field TT-307 … TT-315, PT-316, TT-317, PT-318 at IR 312–335; DCS tags
  TI-307 … TI-315, PI-316, TI-317, PI-318.
- **Done when:** the E-301 display shows every stream at both ends; the values agree with the flows
  (balance) and stay in range in every scenario; tests green, inventory regenerated.

## Planned

### `training-links` — Step 4: training
- **Status:** 📋 Planned · **Owner:** — · **Branch:** — · **PR:** —
- **Goal:** link the `TRAIN` steps to their detail displays. The column step gets a boiling-point
  table: N₂ −196 / O₂ −183 °C at 1 atm; HP-top N₂ ≈ −177 °C against LP-sump O₂ ≈ −180 °C. It also
  shows the LP feed temperatures before and after their valves: rich liquid −174 → ≈ −189 °C,
  LIN −177 → ≈ −193 °C.
- **Touches:** `dcs/web/operator.html` (`TRAIN`, detail displays); possibly a calculated tag.
- **Depends on:** `mhe-expander-tags`, because the E-301 step needs its tags.
- **Notes:** the rich-liquid flash needs a composition estimate; TY-413 already gives the LIN value.

### `docs-refresh` — Step 5: README, CLAUDE.md and screenshots
- **Status:** 📋 Planned · **Owner:** — · **Branch:** — · **PR:** —
- **Goal:** README, CLAUDE.md and `docs/screenshots/` match the system after steps 3 and 4.
- **Depends on:** `mhe-expander-tags`, `training-links`.

## Ideas

### `agentic-pdm` — Agentic predictive maintenance, as add-on repos
- **Status:** 💡 Idea · **Owner:** Willem · **Branch:** — · **PR:** —
- **Goal:** a demo in which agents watch ASU1 proactively. They flag anomalies and predict how
  they develop, then start sub-agents for temporal analysis, root cause analysis and FMEA, and
  prepare an evidence-linked report for a reliability engineer.
- **Shape:** separate repos that work together, not a monolith. This repo stays the core and keeps
  running on its own.
  - `hcw-contracts`: asset registry and schemas.
  - `hcw-cmms`: work orders, through REST and MCP.
  - `hcw-docs`: manuals, SOPs, FMEA worksheets, RCA reports, shift logs, with search.
  - `hcw-analytics`: deterministic anomaly detection and RUL.
  - `hcw-agents`: the agents, plus a reliability engineer page.
  - `hcw-eval`: scores the agents against ground truth.
  - `hcw-stack`: compose wiring and demo storylines.
- **What it needs from this repo:**
  - slow degradation modes with precursors (bearing wear, exchanger fouling, expander drift,
    mole-sieve ageing);
  - condition-monitoring tags (vibration, bearing temperatures, motor current);
  - a long historian backfill driven by a "campaign" timeline;
  - a ground-truth ledger for the seeders and the scorer, which agents never see (the SHT rule);
  - a historian MCP server.
- **Notes:** a fuller plan exists as a draft (`agentic-pdm-plan.md`, not in this repo yet). It
  overlaps with `business-systems`.

### `opcua-writes-apc` — Writable setpoints for an external APC agent
- **Status:** 💡 Idea · **Owner:** — · **Branch:** — · **PR:** —
- **Goal:** writable SPs over OPC UA, with an "APC ON/OFF" handshake (research §5.3), so an
  external advanced-control or agent layer can act on the plant.

### `opcua-alarms-conditions` — OPC UA Alarms & Conditions
- **Status:** 💡 Idea · **Owner:** — · **Branch:** — · **PR:** —
- **Goal:** expose alarms as A&C events instead of only `AlarmState` strings and `Diagnostics` counts.

### `business-systems` — LIMS, SAP-PM and ERP
- **Status:** 💡 Idea · **Owner:** — · **Branch:** — · **PR:** —
- **Goal:** simulated business systems (research §6): LIMS purity cross-checks, SAP-PM maintenance
  and ERP volumes, as the next layer of the IT/OT story. Probably best as an add-on repo; see
  `agentic-pdm`.

### `demo-time-compression` — Faster than real time beyond the PPU
- **Status:** 💡 Idea · **Owner:** — · **Branch:** — · **PR:** —
- **Goal:** a load change takes ~40 min and purity recovery after a restart ~1 h. That is
  realistic (research §4.4) but slow for live demos. Today only the PPU cycle can be compressed.

### `argon-column` — Argon column
- **Status:** 💡 Idea · **Owner:** — · **Branch:** — · **PR:** —

## Parked and dropped

- ⏸ **Route GAN and waste N₂ through E-301 on the overview.** Deliberately not done in step 2c: it
  would mean moving ~8 data blocks in the cold-box area, and the E-301 detail display already shows it.
- ❌ **Tank truck loadouts.** Not planned: the tanks only fill from the column and drain through the backups.
- ❌ **Vaporizer outlet temperatures.** E-507 and E-508 are assumed big enough.
- ❌ **KS-201 reading ZS-201A/B.** Accepted simplification: the valves always follow HS-201 (Willem).
- ❌ **CI for the tests.** Willem chose local runs only (one Docker command).
- ❌ **Extra protective logic and rare-case physics** (e.g. tank overfill protection, an expander
  "liquid make" override). HCW is for training on data, not a full operator trainer (Willem, 2026-10-18).

## Done

- ✅ **Step 1: L3 navigation framework and C-401 column display.** Calculated tags TY-411/412/413. PR #1.
- ✅ **Step 1b: graphics-only detail displays** for every unit, the display bar and linked off-page
  connectors. No new tags. PR #1.
- ✅ **Step 2a: internal compression for oxygen.** PR #2.
  - **Equipment:** K-102 booster, P-501 LOX pump.
  - **Control:** the cross-limited ratio FIC-111/FIC-501, FFI-111, TI-512, PI-511, the LIC-415 override.
  - **Interlocks:** I-110 and I-500, with generic interlock parents and per-equipment bypass.
  - **Also:** booster and cavitation scenarios, KPI-701 including the booster, the new K-102 display.
  - **Willem's step-2 choices:** internal compression for O₂, a K-503 GAN compressor for N₂, a
    single LOX pump (no standby), a separate motor-driven booster, backup pumps at both tanks.
- ✅ **Step 2b: the nitrogen product and backups.** PR #3.
  - **K-503:** the GAN compressor (FIC-503, PI-513, JI-514, VI-515; I-503 is a child of I-100).
  - **Backups:** P-505 and P-506, kept cold on recirculation (I-505 and I-506 have no parents),
    plus E-508 and FIC-508 with the PIC-504 split range.
  - **Control:** trip handover and handback.
  - **Also:** the GAN header hold-up ×5, the GOX load limit FY-501 (a step 2a gap), and two
    scenarios.
  - **Known, not fixed:** after a restart at low air, GOX purity sits at ~99.2 % until the air is raised.
- ✅ **Step 2c: overview drawing fixes** ("light" scope). LOX from the LP sump, LIN from E-404, line
  hops at every crossing, junction dots on the backup send-outs. PR #3.
- ✅ **Step 2.5: tests baseline.** PR #4.
  - **Tests:** the generated interface inventory and a pytest suite (inventory, contracts,
    behaviour, end-to-end).
  - **What the tests led to:** Modbus addresses fixed per process area; KPI-701 and FFI-111 not
    applicable without GOX production; FY-501 clamped; TDI-410 range −15…10.
