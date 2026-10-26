# HCW Haltenberg — ASU1 simulator & DCS

> **Disclaimer.** This code is part of the [IT/OT Academy](https://itot.academy), offered by
> [The IT/OT Insider](https://itotinsider.com). It is a generated, vibe-coded system made **only for
> demo purposes**. It may not be used for anything other than demonstration or training: not in
> production, not connected to real equipment, and not as the basis for any real control, quality
> or maintenance decision.

A demo plant for **Haltenberg Chemische Werke (HCW)**: a Linde-type **Air Separation Unit**
(~1,000 t/d O₂, ~145,000 Nm³/h air) simulated in real time, controlled by a small but complete
**DCS** with a **built-in historian**, which exposes its data over **OPC UA**.

It explains the **classic process-industry stack**, and produces realistic plant data to work with:
field I/O over raw Modbus TCP, a DCS that turns it into modelled controller tags, a historian, and
OPC UA as the interface IT-side clients (here Node-RED) consume.

**Three sites, one standard.** HCW also runs an ASU at Port Arden (Texas) and at Vadhana (Gujarat):
the same simulator engine at another size, with other equipment and customers, and a DCS that presents
its data over OPC UA in its own way (names, units, structure, timestamps). That is the starting point
of a global DataOps demo: map all three onto one ISA-95 standard model
([Global sites](https://github.com/itotinsider/haltenberg-chemische-werke-uns/wiki/Global-sites),
[Standard data model](https://github.com/itotinsider/haltenberg-chemische-werke-uns/wiki/Standard-data-model)).

![Operator console overview](wiki/images/operator-overview.png)

**📖 Documentation: [the wiki](https://github.com/itotinsider/haltenberg-chemische-werke-uns/wiki).**
Never been on a production site? Start with
[Plant automation in brief](https://github.com/itotinsider/haltenberg-chemische-werke-uns/wiki/Plant-automation-in-brief).

## Quick start

Needs Docker Desktop (or Docker Engine + Compose v2) and free ports 8110, 8111, 4840, 5020, 1880
(Haltenberg and Node-RED), 8120, 8121, 4841, 5021 (Port Arden), 8130, 8131, 4842, 5022 (Vadhana).

```bash
docker compose up -d --build
```

| What | Where |
|---|---|
| Hub (all systems) | http://localhost:8110 |
| DCS operator console | http://localhost:8110/operator |
| Instructor station (fault scenarios) | http://localhost:8111 |
| OPC UA endpoint | `opc.tcp://localhost:4840/hcw/asu1/` (anonymous, no security) |
| Modbus TCP field I/O (raw) | `localhost:5020`, unit 1 |
| Node-RED (OPC UA client flows) | http://localhost:1880 |
| Port Arden, US (hub · OPC UA) | http://localhost:8120 · `opc.tcp://localhost:4841/PortArden/ASU7100` |
| Vadhana, IN (hub · OPC UA) | http://localhost:8130 · `opc.tcp://localhost:4842/vadhana/asu2` |
| Site mapping explorer (all three sites → the standard, live) | http://localhost:8110/mapping |
| The standard model and the mapping of all three | [`dataops/uns_model.py`](dataops/uns_model.py), [`inventory/uns/mapping.csv`](inventory/uns/mapping.csv) |

Or open Claude Code in this folder and ask: `read the CLAUDE.md file and README.md file and set up the environment`.

Then take the [15-minute first tour](https://github.com/itotinsider/haltenberg-chemische-werke-uns/wiki/Getting-started#a-first-tour-15-minutes).

## Architecture

```
plant container                         dcs container
┌──────────────────────────┐  Modbus   ┌────────────────────────────────────┐   OPC UA
│ ASU process model        │   TCP     │ 1 s scan                           │  :4840
│ (6 blocks, 0.5 s step)   │◄─────────►│ 19 PI controllers (cascade, ratio, ├──────────► Node-RED,
│ instructor station :8111 │  :5020    │   split range, override)           │            UaExpert,
│ fault scenarios (hidden) │           │ 7 interlocks (I-100 … I-506)       │            IT systems
└──────────────────────────┘           │ historian, operator console :8110  │
                                       └────────────────────────────────────┘
```

More: [Architecture](https://github.com/itotinsider/haltenberg-chemische-werke-uns/wiki/Architecture),
[Data access](https://github.com/itotinsider/haltenberg-chemische-werke-uns/wiki/Data-access).

## For developers

```bash
docker compose run --rm tests                                   # all tests, ~1 min
docker compose run --rm tests python tools/inventory.py         # after an intended interface change
```

- [CLAUDE.md](CLAUDE.md): how everything works, and the gotchas.
- [inventory/](inventory/README.md): every tag, register, OPC UA node and API field, tested against the code.
- [ROADMAP.md](ROADMAP.md): plans and work in progress. Claim an item before you start.
- [wiki/](wiki/): the source of the wiki. Edit pages there, then `python tools/publish_wiki.py`.
  See [Development](https://github.com/itotinsider/haltenberg-chemische-werke-uns/wiki/Development).
