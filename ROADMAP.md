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

Tag names in Ideas and Planned may not exist yet. That is why this file is not part of the
"docs only name existing instruments" test, unlike README and CLAUDE.md.

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

### Several agents on one machine

Several AI agents often work on this codebase at the same time, each for a person. Besides the
rules above:

1. **One agent, one worktree, one branch.** Never let two agents work in the same checkout. If one
   agent switches branches, the other's uncommitted files go with it. If one commits with
   `git add -A`, it takes the other's work. Start every item in a fresh worktree next to the repo:
   ```bash
   git fetch && git worktree add ../hcw-<slug> -b <branch> origin/main
   # when merged:  git worktree remove ../hcw-<slug>
   ```
   In a checkout you didn't create, never run `checkout`/`switch`, `stash`, `reset`, `clean` or
   `checkout -- .`. Stage files by name, never `git add -A` or `git add .`.
2. **Name the agent in Owner,** e.g. `Claude (session haltenberg-…-31) for Willem`, so the
   others and the person know whom to ask. If an agent stops halfway, its Notes say where it
   stopped and what comes next, so another agent can continue.
3. **Each agent runs its own live stack.** Container names, image tags and host ports come from a
   git-ignored `.env` in the worktree (see the top of `docker-compose.yml`). Without one you get the
   default stack (`hcw-…`, ports 8110, 8111, 4840, 5020, 1880), which belongs to the person.
   - **Before `docker compose up` in your worktree,** create a `.env` with your own stack name and
     ports, e.g. `HCW_STACK=hcw2`, `HCW_DCS_PORT=8210`, `HCW_INSTRUCTOR_PORT=8211`,
     `HCW_OPCUA_PORT=4940`, `HCW_MODBUS_PORT=5120`, `HCW_NODERED_PORT=1980`. Check `docker ps` for
     ports already taken. Your stack uses your worktree's own `data/` and `exports/`.
   - **When you're done,** `docker compose down` and remove your `hcw2/*` images.
   - **The isolated checks still come first.** `docker compose run --rm tests` mounts your own
     worktree, the end-to-end tests use free ports and a temporary data folder, and the offline sim
     needs no containers.
4. **Never merge `inventory/` by hand.** It is generated. After merging `main` into your branch,
   regenerate it (`python tools/inventory.py` in the tests image) and run the tests again.
5. **Merge `main` before the PR.** Rerun the tests, and check that your reservations still don't
   clash with anything merged in the meantime.
6. **Agents read the rules at session start.** A session that started before this file reached
   `main`, or that works on a branch made before it, doesn't know these rules. The person who
   runs it should point it here.

## In progress

## Planned

### `docs-refresh` — Step 5: README, CLAUDE.md and screenshots
- **Status:** 📋 Planned · **Owner:** — · **Branch:** — · **PR:** —
- **Goal:** README, CLAUDE.md and `wiki/images/` match the system after steps 3 and 4.
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


### `parallel-agents` — Better guard rails for several agents at once
- **Status:** 💡 Idea · **Owner:** — · **Branch:** — · **PR:** —
- **Goal:** fewer collisions when several agents work in parallel. Not urgent: it doesn't happen
  often, and the rules above plus separate stacks (PR "parallel-stacks") are good enough for now.
- **Ideas:**
  - **Stale-claim check:** a script (or PR-template step) that lists "In progress" items whose
    branch is merged or gone. `docs-primer` stayed "In progress" after its merge.
  - **Guard on the main checkout:** a git hook that refuses commits there on any branch but `main`.
    The first `docs-primer` agent shared the main checkout with step 3.
  - **Built-in worktrees:** start agents with Claude Code's own worktree isolation instead of
    relying on each agent to read the rules.
  - **Earlier visibility:** claims only show once committed, and to remote collaborators once
    pushed. Commit the claim first, push it early.
  - **Cleanup:** a script that removes worktrees and local branches already merged into `main`.
- **Notes:** 2026-10-18 Willem: implement only separate stacks now; keep the rest as ideas.
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
- ✅ **Step 3: E-301 and expander tags** (`mhe-expander-tags`). [PR #7](https://github.com/itotinsider/haltenberg-chemische-werke-uns/pull/7).
  - **Tags:** TI-307 … TI-315, PI-316, TI-317, PI-318 (field TT/PT at IR 312–335), all on the E-301
    detail display. Alarms only (TI-315 H, TI-317 L/H), no trips (Willem).
  - **Model:** a simple enthalpy balance over E-301 (ideal gases, constant cp, no heat leak); TT-301
    and TDT-302 now come from it. Details in `CLAUDE.md`.
  - **Found with it:** FIC-111's ratio demand is capped at the load limit FY-501.
  - **Found while verifying:** transmitter noise got its own random stream; the 2.5 seed bands were
    luck of the draw, so design O₂ demand −0.5 kNm³/h and the GAN handback band 9.1 (Willem). Seeds
    1–44 pass except 37 (documented).
  - **Not done (ideas):** an E-301 duty / balance calc tag in the DCS, an expander efficiency calc.
- ✅ **Step 4: trainers** (`training-links`). [PR #11](https://github.com/itotinsider/haltenberg-chemische-werke-uns/pull/11).
  - **Three trainers, one overlay card on the console** (Willem: one per audience; overlays, not a side page):
    🎓 Process, ⚙️ Engineering (C-401 6 steps, E-301 4 steps, deep dives with live values), 🔌 IT/OT
    (one tag from the Modbus words to the OPC UA node; dead time, exception reporting, Bad is not zero).
    The switch keeps the unit. Details in `CLAUDE.md`.
  - **Interface:** `GET /api/datapath?tag=`, `/api/config` keys `t_sat` and `calc_inputs`. No new tags or addresses.
  - **Fixed on the way:** the console reopened in a lesson by itself (on/off was remembered).
  - **Later (ideas):** topics on the mole sieve, compressor surge, control strategies, interlocks.
- ✅ **docs-primer: user documentation in the GitHub wiki.** PR #6 (primer, research doc as built), PR #8 (wiki).
  - **Wiki:** 12 pages + sidebar, source in `wiki/`, screenshots from `docs/screenshots/`. README 580 → 71 lines.
  - **Publishing:** `tools/publish_wiki.py`; since PR #9 a GitHub Action runs it on every push to
    `main` that touches `wiki/` or `docs/screenshots/`.
  - **Tests:** wiki pages only name existing instruments; wiki links and images resolve.
