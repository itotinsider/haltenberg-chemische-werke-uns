# Architecture

```
plant container                         dcs container
┌──────────────────────────┐  Modbus   ┌────────────────────────────────────┐   OPC UA
│ ASU process model        │   TCP     │ 1 s scan                           │  :4840
│ (6 blocks, 0.5 s step)   │◄─────────►│ 19 PI controllers (cascade, ratio, ├──────────► agents,
│ instructor station :8111 │  :5020    │   split range, override)           │            UaExpert,
│ fault scenarios (hidden) │           │ 7 interlocks (I-100 … I-506)       │            IT systems
└──────────────────────────┘           │ PPU sequence KS-201                │
                                       │ alarms (state-based)               │
                                       │ historian (SQLite) ── data/        │
                                       │ operator console :8110             │
                                       └────────────────────────────────────┘
                                                                                     │ OPC UA
                                                                            nodered container
                                                                            subscribe → 15-min
                                                                            TWA → exports/*.csv
```

HCW runs this pair three times, once per site (Haltenberg, Port Arden, Vadhana): the same two
images, with `HCW_SITE` selecting the site. Each pair has its own network names (`plant`/`dcs`,
`plant-us`/`dcs-us`, `plant-in`/`dcs-in`), ports and data folder. See [Global sites](Global-sites).

## The containers

- The **plant** knows nothing about control. It reads valve positions and run commands from its
  Modbus registers and writes raw transmitter values back, exactly like a remote I/O rack. It also
  serves the instructor station.
- The **DCS** turns those raw registers into tags with units, ranges, modes, alarms and history.
  That modelled view is the only one exposed over OPC UA. It also serves the hub and the operator
  console.
- **Node-RED** is an ordinary OPC UA client, like any IT system would be.

## Two clocks, like a real plant

- The plant integrates in real time at a 0.5 s step.
- The DCS samples it every second over Modbus. Nothing is lock-stepped.
- If the DCS goes away, the plant holds the last outputs, the way an I/O rack does. It starts at
  the design steady state, before any DCS connects.
- A plant heartbeat (HB-900) lets the DCS detect a frozen rack: every value then turns Bad quality.

## Modbus: the raw layer

- **Fixed addresses per process area:** the area is the first digit of the tag number.
  - **Registers:** area *n* uses *n*×100 … *n*×100+99, so FT-101 is at input register 100, FT-501 at
    500 and the heartbeat HB-900 at 900. Values are float32 register pairs.
  - **Discrete inputs and coils:** area *n* uses *n*×10 …, so YS-101 is at 10.
  - **Adding a point:** it goes at the end of its area. An address never changes, and gaps are normal.
  - **Reading:** the DCS reads one area per request. The plant refuses a read of more than 125
    registers, like a real device.
- **Where to look:** the instructor station and the [Interface reference](Interface-reference)
  list the full map.

## Hidden faults

Fault scenarios are started on the [instructor station](Instructor-station) and never published.
The DCS, the historian and any OPC UA client only see the process respond.

## Compared with Sweet Harmony Treats

HCW is the sibling of the Sweet Harmony Treats (SHT) waffle-line simulator. Same build philosophy,
one deliberate difference:

| | SHT | HCW |
|---|---|---|
| Field data | one simulator publishes to an MQTT broker | the plant is a Modbus TCP I/O rack |
| Control | — | a simulated DCS with control loops, alarms and interlocks |
| Data for IT | MQTT topics, HTTP API | OPC UA with history, from the DCS's built-in historian |

## Built with

Python 3.12 (the plant uses the standard library only; the DCS uses pymodbus and asyncua), SQLite,
vanilla HTML/JS without a build step, the official Node-RED image, Docker Compose.
