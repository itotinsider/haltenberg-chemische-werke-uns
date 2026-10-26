# Interface reference

Every name one part of HCW exposes to another is listed in the **interface inventory**, the
[`inventory/`](https://github.com/itotinsider/haltenberg-chemische-werke-uns/tree/main/inventory)
folder in the repository. It is generated from the code and checked by the tests, so you can trust
it.

| File | Contains |
|---|---|
| [`tags.csv`](https://github.com/itotinsider/haltenberg-chemische-werke-uns/blob/main/inventory/tags.csv) | **Start here.** One row per tag, across all layers: description, unit, range, Modbus address, OPC UA node, historian points |
| [`field.json`](https://github.com/itotinsider/haltenberg-chemische-werke-uns/blob/main/inventory/field.json) | The Modbus register map |
| [`dcs.json`](https://github.com/itotinsider/haltenberg-chemische-werke-uns/blob/main/inventory/dcs.json) | DCS tags, alarm limits, interlocks, equipment |
| [`opcua.json`](https://github.com/itotinsider/haltenberg-chemische-werke-uns/blob/main/inventory/opcua.json) | The OPC UA address space: every node, its NodeId, type and unit |
| [`api.json`](https://github.com/itotinsider/haltenberg-chemische-werke-uns/blob/main/inventory/api.json) | HTTP API routes and response shapes, historian points |
| [`plant.json`](https://github.com/itotinsider/haltenberg-chemische-werke-uns/blob/main/inventory/plant.json) | Instructor scenarios and ground truth |

The top-level files describe Haltenberg, the reference site. The other sites have their own
`dcs.json`, `opcua.json` and `plant.json` under
[`inventory/sites/`](https://github.com/itotinsider/haltenberg-chemische-werke-uns/tree/main/inventory/sites),
and [`inventory/uns/`](https://github.com/itotinsider/haltenberg-chemische-werke-uns/tree/main/inventory/uns)
joins all three with the [Standard data model](Standard-data-model): `sites.json` (what each site
is, has and publishes) and `mapping.csv` (every native node mapped onto the standard).

Who owns which name, and how a change is made, is in
[`inventory/README.md`](https://github.com/itotinsider/haltenberg-chemische-werke-uns/blob/main/inventory/README.md).

**Stability:** a rename or removal breaks external clients, so it is always mentioned in the pull
request that makes it.
