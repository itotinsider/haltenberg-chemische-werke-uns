# Configuration

Settings are environment variables in `docker-compose.yml`. Each has a default in the code.

**Which site** a plant or DCS container is, is one variable: `HCW_SITE` (`haltenberg` by default,
`port-arden`, `vadhana`). Everything that differs per site (size, climate, equipment, customers,
OPC UA presentation, PPU cycle) is in `common/sites.py`, not in the environment. See
[Global sites](Global-sites).

| Variable | Default | Purpose |
|---|---|---|
| `HCW_SITE` | `haltenberg` | The site this container is (`common/sites.py`): same images for all three sites |
| `PPU_CYCLE_SECONDS` | the site's (Haltenberg `14400`) | Initial mole-sieve bed online time, shared by plant and DCS (YAML anchor, Haltenberg only; the other sites take theirs from `common/sites.py`). Changed at runtime with the instructor's time-compression slider (300–14400 s); the DCS checkpoint then wins |
| `DCS_URL` | `http://dcs:8110` | Plant → DCS link for the cycle-time slider |
| `TWA_WINDOW_MINUTES` | `15` | Node-RED averaging window; override from the shell for a quick demo |
| `TWA_CSV_FILE` | `/data/exports/asu1_twa_15min.csv` | Where Node-RED appends rows (`exports/` on the host) |
| `SCAN_SECONDS` | `1` | DCS scan period |
| `PLANT_HOST` / `PLANT_PORT` | `plant` / `5020` | Where the DCS finds the I/O rack (compose service name, not localhost) |
| `HISTORIAN_DEADBAND_PCT` | `0.25` | Exception deadband, % of tag span |
| `HISTORIAN_MAX_INTERVAL_SECONDS` | `60` | Heartbeat: store at least this often |
| `HISTORIAN_RETENTION_DAYS` | `14` | Purge older samples/events |
| `PUBLIC_OPCUA_ENDPOINT`, `PUBLIC_INSTRUCTOR_URL`, `PUBLIC_MODBUS_ENDPOINT`, `PUBLIC_NODERED_URL` | localhost URLs | Links shown on the hub page |
| `PUBLIC_HUB_HALTENBERG`, `PUBLIC_HUB_PORT_ARDEN`, `PUBLIC_HUB_VADHANA` | localhost URLs | The site cards on every hub |

Host ports (compose, from a git-ignored `.env` for a second stack): `HCW_DCS_PORT` 8110,
`HCW_INSTRUCTOR_PORT` 8111, `HCW_OPCUA_PORT` 4840, `HCW_MODBUS_PORT` 5020, `HCW_NODERED_PORT` 1880;
Port Arden `HCW_US_DCS_PORT` 8120, `HCW_US_INSTRUCTOR_PORT` 8121, `HCW_US_OPCUA_PORT` 4841,
`HCW_US_MODBUS_PORT` 5021; Vadhana `HCW_IN_DCS_PORT` 8130, `HCW_IN_INSTRUCTOR_PORT` 8131,
`HCW_IN_OPCUA_PORT` 4842, `HCW_IN_MODBUS_PORT` 5022.
