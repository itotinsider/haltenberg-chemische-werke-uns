# Plant automation in brief

This page is for people who have never set foot on a production site. It explains just enough to
find your way around the rest of this wiki and the screens.

## What ASU1 does

Air is 78 % nitrogen and 21 % oxygen. ASU1 compresses air, cleans out water and CO₂, and cools it
to about −175 °C until part of it turns liquid. A distillation column then separates it: oxygen
boils at −183 °C and nitrogen at −196 °C, so nitrogen rises to the top and liquid oxygen collects
at the bottom. The products go by pipeline ("header") to other plants on the site: oxygen to an
ethylene-oxide plant and an acetylene plant, nitrogen to the whole site for purging and blanketing
tanks. Tanks of liquid oxygen and nitrogen are the backup: if ASU1 stops, pumps and vaporizers turn
tank liquid back into gas, so the customers don't notice.

The process is a means to an end here. ASU1 exists to produce realistic plant data, and to show
how that data travels from a sensor to an IT system. More in [The process](The-process).

## The layers, from sensor to IT

```
 plant (simulated)       sensors measure, valves and motors act
     │ Modbus TCP        raw numbers in numbered registers: no names, no units
 DCS                     reads every second, controls, alarms, protects
     ├─ historian        stores every value over time
     ├─ operator console the screens the operator works with
     └─ OPC UA           the standard door for IT systems: names, units, quality, history
           │
      Node-RED           an IT-side client: 15-minute averages → CSV
```

In a real plant each layer is a separate box, often from a different vendor and owned by a
different team. Here each is a container. The direction matters: the operator controls the plant
through the DCS, and IT systems only *read* from the DCS. Nothing from the IT side writes back.

## Words you will meet

| Term | What it means | Where to see it here |
|---|---|---|
| **Field device** | Out in the plant: a *transmitter* (sensor) measures a flow, pressure, temperature…; an *actuator* (control valve, motor) acts | The plant container plays all of them |
| **Modbus TCP** | An old, simple and very common protocol between devices. A device is a table of numbered registers, and a client asks "give me registers 100 to 131". There are no names, units or validity flags: you need a separate document, the *register map*, to know that register 100 is the air flow | Instructor station, [Interface reference](Interface-reference) |
| **DCS** | *Distributed Control System*: the plant's control computer. It reads every measurement each second, runs the control loops, raises alarms, enforces interlocks, records history and gives the operators their screens | `dcs` container, operator console |
| **Tag** | The DCS name for one measurement or control loop, e.g. `FIC-101`. See *Reading a tag name* below | Tag list tab |
| **PV / SP / OP** | *Process value* (what is measured), *setpoint* (what we want), *output* (how far the controller opens its valve, in %) | Every data block and faceplate |
| **Control loop (PID)** | A controller compares PV with SP and moves its OP until they match, like cruise control in a car. ASU1 has 19 | Faceplate (click a value) |
| **Mode** | **MAN**: the operator sets the OP by hand. **AUTO**: the controller follows the operator's SP. **CAS** (cascade): another controller sets the SP | Faceplate |
| **Cascade, split range** | One controller drives others. PIC-502 holds the oxygen header pressure: it tells FIC-501 how much oxygen to send, and if that isn't enough, the backup vaporizer FIC-507 takes over the top of its output range | `products` detail display |
| **Alarm** | A value past a limit: **H / L** (high / low, a warning) or **HH / LL** (high-high / low-low, serious). Priority 1 (red, act now) to 3 (violet). The operator *acknowledges* it ("seen"); it clears only when the value is back | Alarms tab, red/amber badges |
| **Interlock** | Automatic protection: "if this goes wrong, stop that machine". It acts on its own and the operator can't override it. ASU1 has seven, I-100 to I-506 | Interlocks tab |
| **Trip** | What an interlock does when it fires: stop the machine at once and put its valves in a safe position. A trip *latches*: it stays tripped until someone resets it | Interlocks tab, event journal |
| **First-out** | The cause that tripped first. When ten alarms arrive at once, this is the one that tells you why | Interlocks tab |
| **Reset** | Clears a trip, and is only allowed once its cause is gone. It doesn't restart anything: that is a separate, deliberate start | Interlocks tab |
| **Parent interlock** | A trip that takes others with it. When the main air compressor trips (I-100), the expander, booster, LOX pump and nitrogen compressor trip too. Children are reset after their parent | Interlocks tab |
| **Permissive** | A condition that must be true before a machine may start, e.g. the LOX pump needs the booster running and enough liquid in the column sump | Start buttons, Interlocks tab |
| **Line-up** | Putting every loop back in its normal mode after a start (**Normal line-up** button) | Interlocks tab |
| **ESD** | Emergency shutdown: one button that trips the whole unit | Interlocks tab |
| **Sequence** | A step program. KS-201 switches the two mole-sieve beds every 4 hours | R-201 detail display |
| **Historian** | A database of values over time. It only stores a value when it has changed noticeably (*exception reporting*), which saves a lot of space | Trends tab, `data/historian.db` |
| **Quality** | Every value carries a status: *Good*, *Bad* (e.g. the link to the plant is down) or *not applicable*. Check it before you use the number | Magenta values, OPC UA status codes |
| **OPC UA** | The modern standard for reading plant data. Unlike Modbus it has a browsable tree with names, units, ranges, timestamps, quality and history | `opc.tcp://localhost:4840/hcw/asu1/`, Node-RED |
| **HMI, faceplate** | *Human-machine interface*: the operator's screens. A faceplate is the small window for one tag, opened by clicking its value | Operator console |
| **Instructor station** | The simulator's control panel, where a trainer starts faults. The DCS can't see the faults, only their effects, just like in a real plant | http://localhost:8111 |

## Reading a tag name

`FIC-101` is a **F**low **I**ndicating **C**ontroller in area **1**, loop **01**.

- **First letter, what is measured:** F flow, P pressure, T temperature, L level, A analysis
  (purity), S speed, V vibration, J power, Z position.
- **Next letters, what it does:** I indicator, C controller, D difference, Y calculated. `FFI` is a
  flow ratio, `KPI-701` a performance figure.
- **The number:** the first digit is the process area, from 1 (air compressors) to 7 (energy). The
  Modbus addresses follow the same areas.
- **Equipment** has its own letters: K compressor, P pump, E heat exchanger or vaporizer,
  C column, R adsorber vessel, T tank, X expander. Interlocks are `I-…`.

So `TI-512` is a temperature indicator in area 5 (products), and `LIC-404` a level controller
in area 4 (the columns).

## What a trip looks like

Take the instructor scenario **MAC bearing degradation**:

1. A bearing in the main air compressor starts to wear. Its vibration, VI-105, creeps up.
2. At 7.1 mm/s it crosses its **H** limit: an amber priority-2 alarm. The operator acknowledges it
   and should call maintenance.
3. At 11 mm/s it crosses **HH**: interlock **I-100** trips. The compressor stops, and its child
   interlocks stop the expander, booster, LOX pump and nitrogen compressor.
4. The oxygen and nitrogen headers start to lose pressure, but their controllers open the backup
   vaporizers. The customers keep their gas, now from the tanks.
5. The Interlocks tab shows I-100 with first-out `VI-105 HH`. Once the compressor has stopped the
   cause is gone, so I-100 can be reset. In real life you would now repair the bearing; here the
   instructor stops the scenario. Then the unit is restarted step by step
   ([Restart after a trip](Process-behaviour#restart-after-a-trip)).

Every step of this lands in the historian and on OPC UA: alarms, the trip, the falling flows, the
backups taking over. That is the data you will work with.
