# Data access

Four ways to get data out of HCW, from most to least "IT-like". The tag names and node ids are
listed on [Interface reference](Interface-reference).

## OPC UA

The standard door for IT systems: `opc.tcp://localhost:4840/hcw/asu1/` (anonymous, SecurityPolicy
None). Any client works, e.g. UaExpert, asyncua (Python) or Node-RED. From another container on the
compose network use `opc.tcp://dcs:4840/hcw/asu1/`.

- Namespace `urn:haltenberg:hcw:asu1:dcs`, browse `Objects / HCW / Haltenberg / ASU1`.
- One folder per process block, one object per DCS tag.
  - `PV`, `SP` and `OP` are `AnalogItemType` variables with `EngineeringUnits` and `EURange`.
  - `Mode` and `AlarmState` are strings.
- Also under `ASU1`: `Equipment`, `Interlocks`, `Diagnostics`. The `KS-201` sequence sits in
  `FrontEndPurification`.
- String NodeIds, e.g. `ns=2;s=HCW.ASU1.FIC-101.PV`.
- Values carry the DCS scan time as `SourceTimestamp`. They turn `BadNoCommunication` when the
  field link is lost.
- A calculated value that doesn't apply at the moment reads `BadOutOfService`, live and in history:
  the specific power KPI-701 and the booster ratio FFI-111 while P-501 delivers no real GOX, e.g.
  after a trip. **Filter on the status code, not on the value.**
- `PV`/`SP`/`OP` are `Historizing`. **HistoryRead (raw)** is answered from the DCS historian.
- Read-only: no OPC UA client can change the process (yet).

## Node-RED: OPC UA → 15-minute averages → CSV

The `nodered` container (http://localhost:1880) is the official Node-RED image with
`node-red-contrib-opcua` installed into it. It is an ordinary OPC UA *client* of the DCS, like any
IT system would be.

![Node-RED flow](images/nodered.png)

The pre-loaded flow **OPC UA → 15-min TWA → CSV**:

1. Subscribes to six tags in one subscription: `FIC-101`, `FIC-501`, `PIC-502`, `AI-601`,
   `AI-202` and `KPI-701` (`.PV`). The server pushes a value whenever it changes, sampled every 1 s.
2. Computes a **time-weighted average** per 15-minute window. Each value counts for as long as
   it was valid, until the next change (sample-and-hold). A plain average of the notifications
   would over-weight busy periods.
   - Bad-quality time (e.g. `BadNoCommunication`) is left out.
   - The `coverage_pct` column shows how much of the window had good data, as the lowest
     value over all the tags.
3. Appends one row per window to `exports/asu1_twa_15min.csv` (a host folder). The header is
   written when the file is new.

Notes:
- **First row:** it appears at the next quarter-hour. The first window is usually partial, so
  its coverage is below 100 %.
- **Empty KPI cell:** the specific power (`KPI-701`) has no value while no oxygen is produced,
  so its cell stays empty.
- **Adding a tag:** add a line in the **Tags to subscribe** function node.
- **Quick demo:** use 1-minute windows with `TWA_WINDOW_MINUTES=1 docker compose up -d nodered`.

## Historian

- SQLite at `data/historian.db` on the host, with exception reporting:
  - **Deadband:** store a value when it moves more than 0.25 % of span.
  - **Heartbeat:** store at least every 60 s.
  - **Retention:** 14 days.
- **Points:** `<TAG>.PV`, plus `.SP` and `.OP` for controllers.
- Bad-quality periods are stored as gaps, with their reason: no communication, or not applicable
  (see OPC UA).
- The alarm and event journal (alarms, trips, sequence steps, operator actions) lives in the
  same database.
- Controller modes, setpoints and sequence state are checkpointed to `data/dcs_state.json`, so a
  DCS restart resumes where it was.

## HTTP API (DCS, port 8110)

The operator console uses it; scripts can too. JSON throughout.

| Request | Returns / does |
|---|---|
| `GET /api/state` | every tag (PV, SP, OP, mode…), alarms, equipment, interlocks, sequences, I/O status |
| `GET /api/config` | the engineering data: tags with units, ranges, alarm limits; equipment, interlocks |
| `GET /api/history?tags=FIC-101.PV,AI-601.PV&minutes=60&points=300` | trend data (min/max per bucket) |
| `GET /api/events?limit=100` | the event journal |
| `POST /api/loops/<tag>` `{"mode": "AUTO", "sp": 145}` | change a loop (also `op`, `kc`, `ti`, `sp_rate`, `ratio`…) |
| `POST /api/equipment/<key>/start` or `/stop` | start / stop equipment |
| `POST /api/interlocks/<key>/reset`, `/api/lineup`, `/api/esd`, `/api/alarms/ack` | operator actions |

```bash
curl -s localhost:8110/api/state | python3 -m json.tool | head -50
curl -s "localhost:8110/api/history?tags=FIC-101.PV,AI-601.PV&minutes=60&points=300"
```

The instructor station has its own API on port 8111 (scenarios, PPU cycle), e.g.
`curl -X POST localhost:8111/api/scenarios/mac_motor_trip/start`.

## Modbus TCP (raw)

`localhost:5020`, unit 1: the plant's I/O rack, float32 register pairs without names or units.
Useful to see what the DCS starts from; see [Architecture](Architecture#modbus-the-raw-layer).
