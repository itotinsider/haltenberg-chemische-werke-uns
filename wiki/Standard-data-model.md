# Standard data model

The [three sites](Global-sites) publish the same kind of plant data in three shapes. The standard
data model is what they **should** map onto: one ISA-95 **Unified Namespace (UNS)** for every air
separation unit of HCW. It is the target of the global DataOps demo.

- **The model:** [`dataops/uns_model.py`](https://github.com/itotinsider/haltenberg-chemische-werke-uns/blob/main/dataops/uns_model.py).
- **The mapping (the answer key):**
  [`inventory/uns/mapping.csv`](https://github.com/itotinsider/haltenberg-chemische-werke-uns/blob/main/inventory/uns/mapping.csv).
  Every native OPC UA node of every site, with its standard point and the conversion to it. It is
  generated from the model and the sites' own address spaces, and tested against what the live
  servers publish.

The mapping is **open reference material**, not an exercise: use it to build the DataOps layer.
Browse it with live values on any hub's **Site mapping explorer**
(http://localhost:8110/mapping), or download it there.

![Site mapping explorer](images/mapping-explorer.png)

## Paths

```
hcw/<site>/air-separation/<unit>/<equipment>/<point>[/<attribute>]

hcw/port-arden/air-separation/asu7100/main-air-compressor/air-flow
hcw/port-arden/air-separation/asu7100/main-air-compressor/air-flow/setpoint
hcw/vadhana/air-separation/asu2/lox-pump/pump-a/running
```

| ISA-95 level | Standard | Values |
|---|---|---|
| Enterprise | `hcw` | |
| Site | site key | `haltenberg`, `port-arden`, `vadhana` |
| Area | `air-separation` | |
| Unit (work unit) | the site's unit, lower case | `asu1`, `asu7100`, `asu2` |
| Equipment | function, kebab-case | `main-air-compressor`, `pre-purification`, `double-column`, `gox-product`, `lox-pump/pump-a`, `protection/asu-shutdown`, `control-system`, … |
| Point | measurement, kebab-case | `air-flow`, `discharge-pressure`, `purity`, `running`, `tripped`, … |
| Attribute | controllers only | `setpoint`, `output`, `mode`, `alarm-state` (the value is the point itself) |

Equipment can have a sub-level for instances of one function: `lox-pump/pump-a` everywhere,
`lox-pump/pump-b` only at Port Arden. 21 equipment groups in all.

## Values

Each point carries one value object:

```json
{"value": 217500.0, "unit": "Nm3/h", "quality": "GOOD", "ts": "2026-10-26T14:05:31.000Z"}
```

| | Standard |
|---|---|
| Flow | Nm³/h (0 °C, 1.01325 bar), not kNm³/h |
| Pressure | bar, with its reference kept: `bar(a)` or `bar(g)`. Converting between them needs the ambient pressure, so the model doesn't |
| Temperature | °C (`degC`); a temperature **difference** in K |
| Power | kW |
| Level, valve output | % (a tank's contents too: % of its capacity) |
| Composition | purity %mol; impurities ppm(mol) |
| Vibration | mm/s (velocity), µm (displacement) |
| Specific power | kWh/Nm³ of O₂ |
| Mode | `MANUAL`, `AUTO`, `CASCADE` |
| Alarm state | `NORMAL`, `LOW`, `HIGH`, `LOW-LOW`, `HIGH-HIGH` |
| Equipment state | `RUNNING`, `STARTING`, `STOPPING`, `STOPPED`, `TRIPPED` |
| Booleans | `true` / `false` |
| Time | UTC, ISO 8601 |
| Quality | `GOOD`, `BAD` (no communication), `UNCERTAIN`; a calculation that doesn't apply now is `BAD` with reason `not-applicable` (OPC UA BadOutOfService) |

## Reading mapping.csv

One row per standard point and attribute (219 rows), one column group per site:

| Column | Meaning |
|---|---|
| `uns_point`, `attribute` | the path below the unit, and value / setpoint / output / mode / alarm-state |
| `type`, `unit`, `description` | the standard's type and unit |
| `<site>.node` | the browse path on that site's OPC UA server, from `Objects` |
| `<site>.nodeid` | the NodeId (`s=` string, `i=` numeric), in the site's namespace |
| `<site>.native_unit` | the unit the site publishes |
| `<site>.to_uns` | the conversion: `x * a + b` (x = native value), or `map native→standard, …` |
| `engine_key` | the simulator's internal key (an implementation detail: the join key of the generator) |
| `notes` | `not at vadhana`, `only at port-arden`: a gap, never a silent one |

Examples:

| Standard point | Haltenberg | Port Arden | Vadhana |
|---|---|---|---|
| `main-air-compressor/air-flow` (Nm³/h) | `FIC-101/PV` kNm³/h: `x * 1000` | `71FC1001/PV` MSCFH: `x * 26.7909768` | `02MAA10CF001/XQ01` Nm³/h: `x` |
| `main-air-compressor/air-flow/mode` | `"AUTO"` → AUTO | `1` → AUTO | `"AUTO"` → AUTO, `"CASC"` → CASCADE |
| `main-heat-exchanger/cold-end-air-temperature` (°C) | `TI-301` °C: `x` | DEGF: `x * 0.5555555556 - 17.77777778` | **K**: `x - 273.15` |
| `main-heat-exchanger/warm-end-delta-t` (K) | `TDI-302` K: `x` | DEGF: `x * 0.5555555556` (no offset: a difference) | K: `x` |
| `gox-product/header-pressure` (bar(g)) | bar(g): `x` | PSIG: `x * 0.06894744825` | kg/cm² g: `x * 0.9806652048` |
| `lox-storage/level` (%) | %: `x` | **GAL**: `x * 0.0001919063335` (÷ 521,087 gal capacity) | **t**: `x * 0.1110777878` (÷ 900.3 t) |
| `double-column/lox-acetylene` (ppm) | ppm: `x` | PPM: `x` | **ppb**: `x * 0.001` |
| `performance/specific-power` (kWh/Nm³) | kWh/Nm³: `x` | KWH/MSCF: `x * 0.037326` | kWh/**t** O₂: `x * 0.001429` |
| `lox-pump/pump-b/running` | – | `71P5001B/PV` `0`/`1` | – |

**Not in the CSV, per site** (in `inventory/uns/sites.json`): Vadhana's timestamps are local time
labelled as UTC. Subtract 5 h 30 min from every Vadhana timestamp, live and from history.

## How it is checked

`tests/test_sites.py`, on every test run:

- every native variable that carries plant data is in the mapping exactly once;
- every row that misses a site says so in `notes`;
- each conversion, applied to a value as the site publishes it, gives the same standard value as the
  engine value converted directly (ranges, enums, booleans);
- each site's address space is the one the mapping was generated from.

`tests/test_e2e.py` reads every setpoint from the live Port Arden and Vadhana servers, converts it
with the CSV, and compares it with the DCS's own value; it also checks Vadhana's +5:30 timestamps.

## Changing the model

The standard is the DataOps side's own source of truth. To rename a point or change a unit, edit
`dataops/uns_model.py`, regenerate the inventory
(`docker compose run --rm tests python tools/inventory.py`) and commit the new `mapping.csv` with
it. A new tag in the DCS fails the generator until it has a standard point.
