"""Cross-layer contracts: every name a layer uses must exist in the layer it comes from.

Fast (no physics beyond one scan). Layers and owners: see inventory/README.md.

    field I/O (common/iomap.py) <- plant model (plant/model.py) and DCS config (dcs/config.py)
    DCS config -> OPC UA, historian, HTTP API (generated) -> operator console, Node-RED
    plant model -> instructor station (ground truth; never visible to the DCS side)
"""

import collections
import pathlib
import re

import pytest

import config as C
import iomap
from control import DCS
from model import SCENARIOS, Plant
from conftest import read
from inventory import load_module

AI = [n for n, _, _ in iomap.ANALOG_INPUTS]
AO = [n for n, _, _ in iomap.ANALOG_OUTPUTS]
DI = [n for n, _, _ in iomap.DISCRETE_INPUTS]
DO = [n for n, _, _ in iomap.COILS]
LOOPS = {t["tag"]: t for t in C.TAGS if t["kind"] == "PID"}

# Field points that no DCS tag maps, with the reason. Adding to this list is a design decision.
UNMAPPED_FIELD_POINTS = {
    "HB-900": "plant heartbeat, read by dcs/main.py itself (frozen-rack detection)",
    "WD-900": "DCS watchdog, written by dcs/main.py itself",
    "HS-201": "PPU bed select, written by the KS-201 sequence",
    "ZS-201A": "PPU bed A online feedback: on the Modbus map, but KS-201 doesn't check it (open)",
    "ZS-201B": "PPU bed B online feedback: on the Modbus map, but KS-201 doesn't check it (open)",
}
# Drawn on the console but deliberately without I/O.
DRAWING_ONLY = {"HV-116": "JT valve booster air -> HP column, fixed open"}


def one_scan():
    plant, dcs = Plant(0.0), DCS()
    ao, do = dcs.scan(plant.measurements(), plant.discretes(), 1.0)
    return plant, dcs, ao, do


# ---------------------------------------------------------------- field I/O <-> plant <-> DCS
def test_field_point_names_are_unique():
    names = AI + AO + DI + DO
    assert [n for n, k in collections.Counter(names).items() if k > 1] == []


@pytest.mark.parametrize("points, width, area_size, max_request", [
    (iomap.ANALOG_INPUTS, 2, iomap.REGISTER_AREA, 125), (iomap.ANALOG_OUTPUTS, 2, iomap.REGISTER_AREA, 123),
    (iomap.DISCRETE_INPUTS, 1, iomap.BIT_AREA, 2000), (iomap.COILS, 1, iomap.BIT_AREA, 1968)])
def test_modbus_layout(points, width, area_size, max_request):
    """Fixed addresses per process area (first digit of the tag number), no overlaps, whole float pairs."""
    used = {}
    for name, addr, _ in points:
        lo = iomap.area(name) * area_size
        assert lo <= addr and addr + width <= lo + area_size, f"{name} at {addr}: outside area {lo}..{lo + area_size - 1}"
        assert addr % width == 0, f"{name} at {addr}: a float32 starts on an even register"
        for a in range(addr, addr + width):
            assert a not in used, f"{name} at {addr} overlaps {used[a]}"
            used[a] = name
    if width == 2:
        blocks = iomap.blocks(points)
        assert all(count <= max_request for _, count in blocks), "one area per request must fit the Modbus limit"
        assert all(any(s <= a < s + c for s, c in blocks) for a in used), "every point is covered by a request"


def test_plant_writes_exactly_the_field_inputs():
    plant = Plant(0.0)
    assert list(plant.measurements()) == AI
    assert set(plant.discretes()) == set(DI)


def test_plant_reads_exactly_the_field_outputs():
    plant = Plant(0.0)
    assert set(plant.ao) == set(AO)
    assert set(plant.do) == set(DO)


def test_dcs_writes_only_field_outputs():
    _, _, ao, do = one_scan()
    assert set(ao) <= set(AO)
    assert set(do) == set(DO)


def test_dcs_tags_point_at_existing_field_points():
    for t in C.TAGS:
        if t.get("io"):
            assert t["io"] in AI, f"{t['tag']} io={t['io']} is not an input register"
        if t.get("out"):
            assert t["out"] in AO, f"{t['tag']} out={t['out']} is not a holding register"
    for key, e in C.EQUIPMENT.items():
        assert e["cmd"] in DO, f"{key} cmd={e['cmd']} is not a coil"
        assert e["fb"] in DI, f"{key} fb={e['fb']} is not a discrete input"


def test_every_field_point_is_used_or_explained():
    used = ({t["io"] for t in C.TAGS if t.get("io")} | {t["out"] for t in C.TAGS if t.get("out")}
            | {e["cmd"] for e in C.EQUIPMENT.values()} | {e["fb"] for e in C.EQUIPMENT.values()})
    unused = set(AI + AO + DI + DO) - used
    assert unused == set(UNMAPPED_FIELD_POINTS), "map the point to a DCS tag, or explain it in UNMAPPED_FIELD_POINTS"


def test_each_output_has_one_driver():
    outs = collections.Counter(t["out"] for t in C.TAGS if t.get("out"))
    assert [o for o, n in outs.items() if n > 1] == []


# ---------------------------------------------------------------- DCS configuration
def test_tag_names_unique_and_blocks_known():
    assert len(C.TAG_INDEX) == len(C.TAGS)
    blocks = dict(C.BLOCKS)
    for t in C.TAGS:
        assert t["block"] in blocks, t["tag"]
        assert t["lo"] < t["hi"], t["tag"]


def test_control_references_exist():
    for tag, t in LOOPS.items():
        for link in t["cascade"]:
            slave = LOOPS.get(link["slave"])
            assert slave, f"{tag} cascades to unknown loop {link['slave']}"
            assert "CAS" in slave["modes"], f"{link['slave']} can't take a cascade setpoint (no CAS mode)"
        for key in ("limits", "ratio_of", "xlimit"):
            if t.get(key):
                assert t[key] in LOOPS, f"{tag} {key}={t[key]} is not a loop"
        if t.get("sp_cap"):
            assert t["sp_cap"] in C.TAG_INDEX, f"{tag} sp_cap={t['sp_cap']} is not a tag"
        if t.get("suppress"):
            assert t["suppress"] in C.EQUIPMENT, f"{tag} suppress={t['suppress']}"
        assert t["mode"] in t["modes"], f"{tag} starts in {t['mode']}, not one of its modes"
    for t in C.TAGS:
        if t.get("suppress"):
            assert t["suppress"] in C.EQUIPMENT, f"{t['tag']} suppress={t['suppress']}"


def test_calc_inputs_cover_every_calculated_value():
    # The IT/OT trainer's data path shows a calculated tag's inputs instead of a field point.
    calculated = {t["tag"] for t in C.TAGS if t["kind"] == "CALC" or (t["kind"] == "PID" and t.get("pv"))}
    assert set(C.CALC_INPUTS) == calculated
    for tag, inputs in C.CALC_INPUTS.items():
        assert inputs and set(inputs) <= set(C.TAG_INDEX), tag


def test_interlock_references_exist():
    for key, il in C.INTERLOCKS.items():
        for tag, limit, armed in il["causes"]:
            assert tag in C.TAG_INDEX, f"{key}: cause tag {tag}"
            # control.py evaluates a cause through the tag's alarm limit; a missing limit never trips.
            assert limit in C.TAG_INDEX[tag]["alarms"], f"{key}: {tag} has no {limit} alarm limit, the cause could never trip"
            assert armed == "always" or armed in C.EQUIPMENT, f"{key}: armed={armed}"
        for tag in il["forces"]:
            assert tag in LOOPS, f"{key} forces unknown loop {tag}"
        for eq in il.get("stops", []):
            assert eq in C.EQUIPMENT, f"{key} stops unknown equipment {eq}"
        for parent in il.get("parents", []):
            assert parent in C.INTERLOCKS, f"{key} parent {parent}"
        for master, slave in il.get("handover", {}).items():
            assert slave in [link["slave"] for link in LOOPS[master]["cascade"]], f"{key} handover {master}->{slave}"
    for key, e in C.EQUIPMENT.items():
        assert e["trip"] in C.INTERLOCKS, f"{key} trip={e['trip']}"


def test_lineup_and_priority_overrides_are_valid():
    for tag, mode in C.LINEUP.items():
        assert tag in LOOPS and mode in LOOPS[tag]["modes"], f"LINEUP {tag}={mode}"
    for (tag, limit), prio in C.PRIORITY_OVERRIDE.items():
        assert limit in C.TAG_INDEX[tag]["alarms"], f"PRIORITY_OVERRIDE {tag}.{limit}"
        assert prio in (1, 2, 3)


def test_every_tag_has_a_value_after_one_scan():
    _, dcs, _, _ = one_scan()
    snap = dcs.snapshot()
    assert list(snap["tags"]) == [t["tag"] for t in C.TAGS]
    dead = [tag for tag, row in snap["tags"].items() if row["pv"] is None]
    assert dead == [], "tags without a value (missing io=, or no calc in control.py)"
    for t in LOOPS.values():
        if t.get("pv"):
            assert dcs.values.get(t["pv"]) is not None, f"{t['tag']} pv={t['pv']} is never computed"


def test_historian_points_follow_the_tags():
    dmain = load_module("dcs_main", "dcs/main.py")
    expected = {f"{t['tag']}.PV" for t in C.TAGS} | {f"{tag}.{a}" for tag in LOOPS for a in ("SP", "OP")}
    assert set(dmain.historian_points()) == expected


# ---------------------------------------------------------------- DCS -> OPC UA (via the inventory)
def test_opcua_matches_dcs_tags(code):
    nodes = {n["id"]: n for n in code["opcua"]["nodes"].values() if "id" in n}
    for t in C.TAGS:
        tag = t["tag"]
        pv = nodes.get(f"HCW.ASU1.{tag}.PV")
        assert pv, f"{tag} has no OPC UA PV node"
        assert pv["unit"] == t["unit"] and pv["range"] == [t["lo"], t["hi"]] and pv.get("history"), tag
        for attr in ("SP", "OP", "Mode"):
            assert (f"HCW.ASU1.{tag}.{attr}" in nodes) == (t["kind"] == "PID"), f"{tag}.{attr}"
        assert (f"HCW.ASU1.{tag}.AlarmState" in nodes) == bool(t["alarms"]), f"{tag}.AlarmState"
    for key in C.EQUIPMENT:
        assert f"HCW.ASU1.Equipment.{key}.State" in nodes
    for key in C.INTERLOCKS:
        assert f"HCW.ASU1.Interlocks.{key}.Tripped" in nodes


def test_hidden_faults_stay_hidden(code):
    """SHT rule: scenarios and ground truth live only in the plant, never on the DCS side."""
    dcs_side = " ".join(str(code[k]) for k in ("field", "dcs", "opcua")) + str(code["api"]["dcs"])
    secrets = set(SCENARIOS) | set(code["plant"]["ground_truth"]["hidden"])
    assert [s for s in secrets if s in dcs_side] == []


def test_ppu_cycle_limits_agree():
    pmain = load_module("plant_main", "plant/main.py")
    assert (pmain.PPU_CYCLE_MIN, pmain.PPU_CYCLE_MAX) == (C.PPU_CYCLE_MIN, C.PPU_CYCLE_MAX)


# ---------------------------------------------------------------- consumers: HMIs, Node-RED, docs
INSTRUMENT = re.compile(r"\b[A-Z]{2,4}-\d{3}[A-Z]?\b")


ROOT = pathlib.Path(__file__).resolve().parent.parent
WIKI_PAGES = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "wiki").glob("*.md"))


@pytest.mark.parametrize("page", ["dcs/web/operator.html", "dcs/web/trainer.html", "dcs/web/index.html", "plant/web/instructor.html",
                                  "README.md", "CLAUDE.md", "linde air seperation research.md"] + WIKI_PAGES)
def test_pages_only_name_existing_instruments(page):
    known = set(C.TAG_INDEX) | set(AI + AO + DI + DO) | {"KS-201", "ISA-101"} | set(DRAWING_ONLY)
    assert sorted(set(INSTRUMENT.findall(read(page))) - known) == []


def test_every_tag_is_placed_on_a_display():
    html = read("dcs/web/operator.html")
    placed = set(re.findall(r'\["([A-Z]{2,4}-\d{3}[A-Z]?)",\s*-?\d', html))
    assert sorted(set(C.TAG_INDEX) - placed) == [], "every DCS tag needs a data block on the overview or a detail display"


def test_console_equipment_and_valves_exist():
    html = read("dcs/web/operator.html")
    eq = set(re.findall(r'id="[ed]q-([A-Z]+)', html)) | set(re.findall(r"ST\.equipment\.([A-Z]+)", html))
    eq |= set(re.findall(r'"dq-[\w-]+":\s*"([A-Z]+)"', html))
    assert eq and eq <= set(C.EQUIPMENT), sorted(eq - set(C.EQUIPMENT))
    for valve, loop in re.findall(r'"([A-Z]{2}-\d{3})":\s*"([A-Z]{3,4}-\d{3})"', html):
        assert valve in AO and LOOPS.get(loop, {}).get("out") == valve, f"valve {valve} -> {loop}"


def test_instructor_reads_existing_ground_truth(code):
    html = read("plant/web/instructor.html")
    gt = code["plant"]["ground_truth"]
    assert set(re.findall(r"\bh\.(\w+)", html)) <= set(gt["hidden"])
    assert set(re.findall(r'\bc\["(\w+)"\]', html)) <= set(gt["consumers"])
    assert set(re.findall(r"S\.environment\.(\w+)", html)) <= set(gt["environment"])


def test_nodered_subscribes_to_existing_nodes(code):
    ids = {n.get("id") for n in code["opcua"]["nodes"].values()}
    subscribed = re.findall(r'ns=2;s=([\w.-]+)', read("nodered_data/flows.json"))
    assert subscribed and sorted(set(subscribed) - ids) == []


@pytest.mark.parametrize("page", ["dcs/web/index.html", "README.md", "wiki/Architecture.md"])
def test_documented_counts(page):
    text = read(page)
    assert re.search(rf"\b{len(LOOPS)} (PI )?controllers", text), "controller count"
    assert re.search(rf"\b{len(C.INTERLOCKS)} interlocks \({min(C.INTERLOCKS)} … {max(C.INTERLOCKS)}\)", text), \
        "interlock count / range"


def test_wiki_links_resolve():
    """Wiki pages link to each other by page name and to images/ (published from docs/screenshots/)."""
    assert "wiki/Home.md" in WIKI_PAGES and "wiki/_Sidebar.md" in WIKI_PAGES
    pages = {pathlib.Path(p).stem for p in WIKI_PAGES}
    shots = {p.name for p in (ROOT / "docs" / "screenshots").glob("*.png")}
    broken = []
    for page in WIKI_PAGES:
        for target in re.findall(r"\]\(([^)\s]+)\)", read(page)):
            if target.startswith(("http://", "https://", "#")):
                continue
            if target.startswith("images/"):
                ok = target.removeprefix("images/") in shots
            else:
                ok = target.split("#")[0] in pages
            if not ok:
                broken.append(f"{page}: {target}")
    assert broken == []
