# Trainers

ASU1 has three trainers, one per audience. They all explain the same running plant, with live
values, at a different depth. Pick one on the hub (http://localhost:8110, "Learn · ASU1 trainers"),
or switch between them at any time with the switch in the trainer's header.

| Trainer | For | What it does | Where |
|---|---|---|---|
| 🎓 **Process** | everyone, no background needed | walks through the whole plant in plain language, step by step | the operator console's **🎓 Training** button, `/operator?training=1` |
| ⚙️ **Engineering** | people who want to know how a unit works | guided walks through the live detail displays, plus deep dives into the physics | `/trainer#eng` |
| 🔌 **IT/OT** | IT/OT specialists | follows one value from the sensor to OPC UA, and explains what dead time, exception reporting and quality mean for anyone who analyses plant data | `/trainer#ot` |

**Switching keeps your place.** All three trainers are organised by the same units. On the column
step of the Process trainer, "Go deeper" opens the column topic of the Engineering trainer; in the
Engineering trainer, "🎓 Process" goes back to the matching overview step. You choose the depth
once, instead of following links inside links.

## The trainer page

The Engineering and IT/OT trainers share one page, `/trainer`. The **live operator console** sits
on the left, exactly as an operator sees it, and the **lesson** on the right. Each step does one of
two things:

- **Steers the console.** It opens the right display, outlines the part the step is about (in teal,
  the training colour) and opens a faceplate. You can still click around the console freely; "↺
  Back to this step's display" brings you back.
- **Shows a deep dive.** A figure with live values replaces the console, with sliders for "what
  if". "◀ Live console" switches back.

Every step has a "Right now in ASU1" box built from live DCS data, and buttons for the tags worth
watching (a click opens the faceplate). Like the console, the trainers only use DCS data. They
never reveal the instructor's hidden scenarios.

![Engineering trainer: the main condenser on the live C-401 display](images/trainer-engineering.png)

## ⚙️ Engineering trainer

**Linde double column** (6 steps, on the C-401 display):
1. Two columns on top of each other: how distillation separates air.
2. The main condenser: one exchanger that condenses nitrogen and boils oxygen, with the live
   boiling-point ladder.
3. *Deep dive:* **boiling point vs pressure**. The nitrogen and oxygen curves, drawn with the DCS's
   own formula (the one behind TY-411 … TY-413), with ASU1's operating points on them. A slider
   runs the HP column at another pressure and shows when the condenser stops working.
4. *Deep dive:* **liquids that cool themselves**. The boiling-point table, and the two liquids that
   flash across their valves into the LP column: LIN from TY-411 to TY-413, and the rich liquid (an
   ideal-mixture estimate at 35 % O₂).
5. Reflux: the AIC-602 → FIC-405 cascade.
6. The sump: level, the LOX pump, the LIC-415 override and hydrocarbons in the boiling oxygen.

**Main heat exchanger & expander** (4 steps, on the E-301 display):
1. Counter-current flow, the warm end and the cold end, the warm-end ΔT TDI-302.
2. *Deep dive:* **the temperature profile**. Every stream through E-301 with its live end
   temperatures, and the boiling pumped LOX.
3. Internal compression: why the booster air follows the oxygen in a ratio (FFI-111, TI-512).
4. The expander: how X-303 makes the plant's cold (TI-315 → TI-317).

![Deep dive: boiling point vs pressure](images/trainer-deep-dive.png)

## 🔌 IT/OT trainer

**From sensor to OPC UA** (5 steps). The lesson shows one tag at every layer of the stack, at the
same moment, with the field at the bottom and IT at the top:

| Layer | What you see, live |
|---|---|
| L0 instrument | the field point, e.g. FT-101, or "calculated from …" for a calculated tag |
| L1 field I/O | the Modbus input registers, the two 16-bit words on the wire, the float32 they make; the output register the DCS writes back |
| L2 DCS | the tag with unit, range, alarm limits, SP / OP / mode and quality |
| L2 historian | the deadband and heartbeat, the last stored sample, how many of the last hour's 3,600 scans were stored, and the stored samples of the last 15 minutes |
| L3 OPC UA | NodeId, browse path, type, StatusCode, endpoint, and a Python snippet that reads the node |
| L4 IT | what every client sees: the top of the stack only |

**Click any value on the console** to follow that tag instead, or pick one from the list. The
steps go through the layers one by one.

![IT/OT trainer: FIC-101 at the field I/O layer](images/trainer-itot.png)

**Time and quality in plant data** (3 steps):
1. *Deep dive:* **cause and effect are minutes apart**. A dead-time-and-lag step response with
   sliders, the same pair of tags (FIC-501, AI-601) from the historian, and what that means for
   correlations and machine-learning features.
2. *Deep dive:* **not every scan is stored**. The historian's own storage rule on a ten-minute
   signal: change the deadband and see the stored samples and the error. Why averages must be
   time-weighted.
3. **Bad is not zero**: Good, Bad (no I/O) and not applicable, on the console, in the historian
   and over OPC UA.

## Deep links

| Link | Opens |
|---|---|
| `/trainer#eng`, `/trainer#ot` | a trainer's topic list |
| `/trainer#eng/C-401/2` | a topic at a step (steps count from 0) |
| `/trainer?tag=PIC-502#ot/datapath/1` | the IT/OT data path for a tag |
| `/operator?training=1&step=4` | the Process trainer at a step |

The data path comes from `GET /api/datapath?tag=<TAG>` (see [Data access](Data-access)).

## Planned

More topics are planned: the mole sieve, compressor surge, control strategies (cascade, split
range, override, ratio), interlocks and trips. See
[ROADMAP.md](https://github.com/itotinsider/haltenberg-chemische-werke-uns/blob/main/ROADMAP.md).
