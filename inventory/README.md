# Interface inventory

Everything one HCW subsystem exposes to another, in one place: Modbus registers, DCS tags, OPC UA
nodes, historian points, HTTP API fields and instructor scenarios.

**Generated, never edited by hand.** Each layer keeps its own source of truth (table below).
`tools/inventory.py` reads them all and writes these files. The tests fail as soon as the code and
this folder disagree, so every interface change shows up as a diff in the PR:

```bash
docker compose run --rm tests python tools/inventory.py           # regenerate after a change
docker compose run --rm tests python tools/inventory.py --check   # only compare
```

## Who owns what

| Layer | Source of truth (the only place to change it) | Written by → read by | File |
|---|---|---|---|
| Field I/O (Modbus TCP) | `common/iomap.py` | plant → DCS (inputs), DCS → plant (outputs) | `field.json` |
| DCS tags, alarms, interlocks, equipment | `dcs/config.py` | DCS → everything above it | `dcs.json` |
| OPC UA address space | generated from `dcs/config.py` by `dcs/opcua_server.py` | DCS → OPC UA clients, Node-RED | `opcua.json` |
| Historian points, HTTP API | `dcs/main.py` | DCS → operator console, hub, scripts | `api.json` (`dcs`) |
| Instructor scenarios, ground truth | `plant/model.py` | plant → instructor station only | `plant.json`, `api.json` (`plant`) |
| One tag across all layers | (joined from the above) | for people looking up a tag | `tags.csv` |
| The other sites | `common/sites.py` + the sources above, `dcs/native.py` (OPC UA presentation) | per site | `sites/<site>/dcs.json`, `opcua.json`, `plant.json` |
| Standard model and mapping | `dataops/uns_model.py` + every site's `dcs/native.py` table | the DataOps side | `uns/sites.json`, `uns/mapping.csv` |

The top-level files describe Haltenberg, the reference site. Port Arden and Vadhana run the same engine
with their own configuration (`HCW_SITE`); `tests/test_sites.py` checks their files and the mapping.

A layer only uses names that exist in the layer it reads from, for example:

- a DCS tag's `io=` must be an input register;
- an interlock cause must be one of the tag's alarm limits;
- every tag must have a data block on a display;
- every node Node-RED subscribes to must exist;
- the instructor page may only read ground truth the model provides.

`tests/test_contracts.py` checks all of these.

**Hidden faults stay hidden:** scenario names and ground truth may never appear on the DCS side (Modbus, DCS, OPC UA, DCS API).

## Changing an interface

1. Change the source of truth, plus everything that uses the name: displays, training, README.
2. Regenerate the inventory, run the tests, and commit both together.
3. In the PR, say what was added, renamed or removed. A rename or removal breaks whatever reads the old name: OPC UA clients, Node-RED, historian queries, saved trends.
4. **Modbus addresses are fixed per process area** (the first digit of the tag number).
   - **Registers:** area *n* = *n*×100 … *n*×100+99.
   - **Bits:** area *n* = *n*×10 ….
   - **Adding a point:** it goes at the end of its area with the next free address in `common/iomap.py`. Never renumber an existing point; a removed point leaves a gap.
   - **Checks:** `test_modbus_layout` checks the areas, overlaps and request sizes. Any changed address is visible in the `field.json` diff, and it breaks external Modbus clients.

## Files

- `field.json`: the four Modbus tables. Point, address, description, and who writes and reads each table.
- `dcs.json`: every tag with:
  - kind, block, unit, range and modes;
  - field I/O;
  - alarm limits with priorities;
  - the equipment it suppresses on;
  - control wiring (cascade, override, ratio, cross-limit, load limit).

  Also equipment, interlocks (causes, forces, parents, handover), discrete alarms, the line-up and KS-201.
- `opcua.json`: every node by browse path, with:
  - its string NodeId;
  - the data type;
  - historizing, the EURange and the engineering unit.
- `api.json`: the HTTP routes and response shapes (field names, not values) of the DCS and plant APIs, and the historian points.
- `plant.json`: instructor scenarios and the ground-truth fields.
- `sites/<site>/`: `dcs.json`, `opcua.json` (every native node, with numeric NodeIds at Vadhana) and
  `plant.json` of Port Arden and Vadhana.
- `uns/sites.json`: per site its identity, size, equipment, DCS tag / equipment / interlock counts and
  OPC UA presentation (namespace, endpoint path, NodeId style, timestamp habit).
- `uns/mapping.csv`: one row per standard point (and controller attribute); per site the native browse
  path, NodeId, unit and the conversion to the standard (`x * a + b` or `map n→s, ...`); a note where a
  site doesn't have the point.
- `tags.csv`: one row per DCS tag:
  - description, unit and range;
  - field point and Modbus address (IR = input register, HR = holding register);
  - OPC UA variables, historian points and alarm limits.
