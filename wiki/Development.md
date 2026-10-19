# Development

Developer knowledge (how each part works, why, and the gotchas) lives in
[CLAUDE.md](https://github.com/itotinsider/haltenberg-chemische-werke-uns/blob/main/CLAUDE.md).
Plans and work in progress are in
[ROADMAP.md](https://github.com/itotinsider/haltenberg-chemische-werke-uns/blob/main/ROADMAP.md):
propose an idea there, or claim an item before you start, so people and agents working in parallel
don't collide.

## Repository layout

| Path | What |
|---|---|
| `common/iomap.py` | Modbus register map, shared by plant and DCS |
| `plant/` | Process model, Modbus server, instructor station |
| `dcs/` | DCS configuration, control logic, historian, OPC UA server, operator console |
| `nodered/`, `nodered_data/flows.json` | Node-RED image and flow |
| `tools/` | Offline simulator, inventory generator, wiki publisher |
| `inventory/` | Generated interface inventory |
| `tests/` | The test suite |
| `wiki/` | The source of this wiki |

After any code or web change: `docker compose up -d --build` (files are copied into the images).

## Offline simulation

`tools/offline_sim.py` couples the plant model and the DCS control layer directly, without
containers or I/O, and runs hours of plant time in seconds:

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim python tools/offline_sim.py \
  --hours 1.5 --every 300 --scenario eo_demand_step@600 --cmd sp:FIC-101=160@1800
```

`--seed 7` makes a run repeatable.

## Tests

The model will keep growing, and the systems around it must keep agreeing. The tests guard
consistent behaviour and stable interfaces: no tag, register, OPC UA node or API field goes
missing or changes without anyone noticing. They are not there to prove the physics is perfect.

```bash
docker compose run --rm tests                       # everything, ~1 min (builds the test image once)
docker compose run --rm tests python -m pytest -m "not e2e"     # without the end-to-end part
docker compose run --rm tests python tools/inventory.py         # after an intended interface change
```

The test container mounts the working tree, so there is nothing to rebuild after a code change.
It does not touch the running stack. The tests come in four parts:

| File | What it checks | Time |
|---|---|---|
| `tests/test_inventory.py` | The committed [interface inventory](Interface-reference) still matches the code | 5 s |
| `tests/test_contracts.py` | Every name a layer uses exists in the layer it comes from: Modbus map ↔ plant model ↔ DCS config ↔ OPC UA ↔ operator console, instructor station, Node-RED flow and docs (including this wiki). Hidden faults never reach the DCS side | 8 s |
| `tests/test_behaviour.py` | Plant model + DCS, faster than real time: steady state in spec with no P1/P2 alarm; a signature for every instructor scenario; the [restart procedures](Process-behaviour#restart-after-a-trip); permissives, I/O loss, checkpoint, ESD, PPU time compression | 30 s |
| `tests/test_e2e.py` | The real plant and DCS programs over real Modbus: HTTP API, OPC UA browse / values / HistoryRead and the raw registers against the inventory; Bad quality when the plant goes away | 20 s |

**When a test fails:**

- **The inventory test:** an interface changed. If you intended the change, regenerate the
  inventory and commit it with the change. The PR then shows exactly which tags, nodes or
  registers changed.
- **A behaviour test:** cause and effect changed. Either fix the regression, or, if the new
  behaviour is right, update the test and say why in the PR.
- **New instructor scenario:** add its expected effect to `SIGNATURES` in
  `tests/test_behaviour.py`. A test fails until you do.

The behaviour tests use a fixed seed. Their limits are meant to hold for any seed (seeds 1–44 pass
except 37, see CLAUDE.md), so check another one after retuning:

```bash
docker compose run --rm -e HCW_TEST_SEED=3 tests python -m pytest tests/test_behaviour.py
```

## Editing this wiki

The wiki's source is the [`wiki/`](https://github.com/itotinsider/haltenberg-chemische-werke-uns/tree/main/wiki)
folder in the repository, reviewed in pull requests like code. Don't edit pages on GitHub: the next
publish overwrites them. Screenshots live in `wiki/images/`, so image links work in the repo and in the wiki alike.

Publishing is automatic: when a change to `wiki/` is merged into `main`, a
GitHub Action (`.github/workflows/wiki.yml`) runs `tools/publish_wiki.py`. By hand, from an
up-to-date `main`:

```bash
python tools/publish_wiki.py --dry-run    # show what would change
python tools/publish_wiki.py              # copy wiki/ (pages and images) to the wiki repo, commit, push
```

Pages link to each other by page name (`[Architecture](Architecture)`), and to repository files by
full GitHub URL.
