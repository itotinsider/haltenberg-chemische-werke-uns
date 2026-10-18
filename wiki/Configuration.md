# Configuration

Settings are environment variables in `docker-compose.yml`. Each has a default in the code.

| Variable | Default | Purpose |
|---|---|---|
| `PPU_CYCLE_SECONDS` | `14400` | Initial mole-sieve bed online time, shared by plant and DCS (YAML anchor). Changed at runtime with the instructor's time-compression slider (300–14400 s); the DCS checkpoint then wins |
| `DCS_URL` | `http://dcs:8110` | Plant → DCS link for the cycle-time slider |
| `TWA_WINDOW_MINUTES` | `15` | Node-RED averaging window; override from the shell for a quick demo |
| `TWA_CSV_FILE` | `/data/exports/asu1_twa_15min.csv` | Where Node-RED appends rows (`exports/` on the host) |
| `SCAN_SECONDS` | `1` | DCS scan period |
| `PLANT_HOST` / `PLANT_PORT` | `plant` / `5020` | Where the DCS finds the I/O rack (compose service name, not localhost) |
| `HISTORIAN_DEADBAND_PCT` | `0.25` | Exception deadband, % of tag span |
| `HISTORIAN_MAX_INTERVAL_SECONDS` | `60` | Heartbeat: store at least this often |
| `HISTORIAN_RETENTION_DAYS` | `14` | Purge older samples/events |
| `PUBLIC_OPCUA_ENDPOINT`, `PUBLIC_INSTRUCTOR_URL`, `PUBLIC_MODBUS_ENDPOINT`, `PUBLIC_NODERED_URL` | localhost URLs | Links shown on the hub page |
