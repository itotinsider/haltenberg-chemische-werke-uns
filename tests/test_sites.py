"""The global demo: three sites, one engine (common/sites.py), and the standard model (dataops/uns_model.py).

    contracts    every site's DCS configuration is consistent, its plant writes exactly its field points
    presentation each site's OPC UA address space is what dcs/native.py's table says it is (committed
    inventory), every published value has a place in the standard model, and the mapping's
    conversions really turn native values into standard ones
    behaviour    every site runs steady without serious alarms; Port Arden's standby LOX pump takes over;
                 Vadhana (no GAN product) restarts after an ASU trip

Haltenberg, the reference site, is covered by the other test files; here it only takes part in the
mapping checks.
"""

import contextlib
import csv
import importlib
import io
import json
import os
import re

import pytest

import config as C
import iomap
import sites
from conftest import read
from control import DCS
from model import Plant, scenarios_for
from offline_sim import Sim, Window, use_site

SEED, T0 = int(os.getenv("HCW_TEST_SEED", "7")), 1.7e9
NEW_SITES = [k for k in sites.SITES if k != sites.DEFAULT]


@contextlib.contextmanager
def on_site(key):
    """Rebuild the DCS config (and native.py, which derives from it) for `key`; back afterwards."""
    import native
    import opcua_server
    old = use_site(key)
    importlib.reload(opcua_server)
    importlib.reload(native)
    try:
        yield native
    finally:
        use_site(old)
        importlib.reload(opcua_server)
        importlib.reload(native)


def committed(rel):
    return read(f"inventory/{rel}")


MAPPING = list(csv.DictReader(io.StringIO(committed("uns/mapping.csv"))))


# ---------------------------------------------------------------- per-site contracts
@pytest.mark.parametrize("key", list(sites.SITES))
def test_site_contracts(key):
    with on_site(key):
        site = sites.get(key)
        absent = sites.absent_points(site)
        plant = Plant(0.0, site=site)
        ai = [n for n, _, _ in iomap.ANALOG_INPUTS]
        di = [n for n, _, _ in iomap.DISCRETE_INPUTS]
        assert list(plant.measurements()) == [n for n in ai if n not in absent]
        assert set(plant.discretes()) == set(di) - absent
        # Every field point the site uses has a consumer, and nothing uses a point the site doesn't have.
        used = ({t["io"] for t in C.TAGS if t.get("io")} | {t["out"] for t in C.TAGS if t.get("out")}
                | {e["cmd"] for e in C.EQUIPMENT.values()} | {e["fb"] for e in C.EQUIPMENT.values()})
        assert used & absent == set()
        loops = {t["tag"]: t for t in C.TAGS if t["kind"] == "PID"}
        for t in loops.values():
            for link in t["cascade"]:
                assert link["slave"] in loops and "CAS" in loops[link["slave"]]["modes"], (key, t["tag"])
            assert t["mode"] in t["modes"], (key, t["tag"])
            if t.get("suppress"):
                assert t["suppress"] in C.EQUIPMENT, (key, t["tag"])
        for k, il in C.INTERLOCKS.items():
            for tag, limit, armed in il["causes"]:
                assert limit in C.TAG_INDEX[tag]["alarms"], (key, k, tag)
                assert armed == "always" or armed in C.EQUIPMENT, (key, k)
            assert set(il["forces"]) <= set(loops) and set(il["stops"]) <= set(C.EQUIPMENT), (key, k)
        for tag, mode in C.LINEUP.items():
            assert mode in loops[tag]["modes"], (key, tag)
        assert set(C.CALC_INPUTS) == {t["tag"] for t in C.TAGS if t["kind"] == "CALC" or t.get("pv")}
        dcs = DCS(ppu_cycle_s=site["ppu_cycle_s"])
        dcs.scan(plant.measurements(), plant.discretes(), 1.0)
        assert [t["tag"] for t in C.TAGS if dcs.values.get(t["tag"]) is None] == []
        assert set(scenarios_for(site)) == set(read_json(f"sites/{key}/plant.json" if key != sites.DEFAULT
                                                         else "plant.json")["scenarios"])


def read_json(rel):
    return json.loads(committed(rel))


def test_sites_differ_where_intended():
    """Scale, equipment and presentation are what common/sites.py promises."""
    overview = read_json("uns/sites.json")["sites"]
    assert list(overview) == list(sites.SITES)
    assert {k: s["scale"] for k, s in overview.items()} == {"haltenberg": 1.0, "port-arden": 1.5, "vadhana": 0.6}
    assert "LOXPB" in overview["port-arden"]["dcs"]["equipment"]
    assert "GANC" not in overview["vadhana"]["dcs"]["equipment"] and "I-503" not in overview["vadhana"]["dcs"]["interlocks"]
    assert len({s["opcua"]["namespace"] for s in overview.values()}) == 3
    with on_site("port-arden"):
        assert C.TAG_INDEX["FIC-101"]["sp"] == 217.5 and C.TAG_INDEX["FIC-101"]["unit"] == "kNm³/h"


# ---------------------------------------------------------------- presentation (OPC UA) and the standard
@pytest.mark.parametrize("key", list(sites.SITES))
def test_native_table_is_the_address_space(key):
    """dcs/native.py's table (which the mapping is generated from) == the committed, walked address space."""
    nodes = read_json("opcua.json" if key == sites.DEFAULT else f"sites/{key}/opcua.json")["nodes"]
    with on_site(key) as native:
        tb = native.table()
        want = {}
        for o in tb.objects:
            want["/".join(o["path"])] = dict(id=o["id"], cls="Object")
        for v in tb.variables:
            want["/".join(v["path"])] = dict(id=v["id"], cls="Variable", type=v["type"], unit=v["unit"] if v["analog"] else None,
                                             range=[float(x) for x in v["range"]] if v["analog"] else None,
                                             history=v["history"])
    got = {p: dict(id=n.get("id"), cls=n["class"], **({} if n["class"] == "Object" else dict(
        type=n["type"], unit=n.get("unit"), range=n.get("range"), history=n.get("history", False))))
        for p, n in nodes.items()}
    assert got == want


def test_every_published_value_has_a_standard_point():
    """The mapping covers every native variable of every site that carries plant data, exactly once."""
    for key in sites.SITES:
        with on_site(key) as native:
            published = {v["id"] for v in native.table().variables if v["src"]}
        ident = {r[f"{key}.nodeid"] for r in MAPPING if r[f"{key}.nodeid"]}
        as_ids = {int(i[2:]) if i.startswith("i=") else i[2:] for i in ident}
        assert as_ids == published, key
        assert len(ident) == len([r for r in MAPPING if r[f"{key}.nodeid"]]), f"{key}: a node mapped twice"


def to_standard(formula, x):
    """Apply a mapping.csv conversion ("x * a + b", "x", or "map n→s, ...") to a native value."""
    if formula.startswith("map "):
        table = dict(pair.split("→") for pair in formula[4:].split(", "))
        return table[str(int(x)) if isinstance(x, bool) and "0" in table else str(x)]
    assert re.fullmatch(r"x( \* [-\d.e]+)?( [+-] [\d.e]+)?", formula), formula
    return eval(formula, {"x": x})


@pytest.mark.parametrize("key", list(sites.SITES))
def test_mapping_converts_native_values_to_the_standard(key):
    """Native value (as the site's server publishes it) -> mapping.csv -> standard value equals the engine value
    converted straight to the standard (dataops/uns_model.py). This is what a DataOps pipeline will rely on."""
    import uns_model as U
    with on_site(key) as native:
        by_id = {v["id"]: v for v in native.table().variables if v["src"]}
        for r in MAPPING:
            nid = r[f"{key}.nodeid"]
            if not nid:
                continue
            v = by_id[int(nid[2:]) if nid.startswith("i=") else nid[2:]]
            tag = v["src"].rsplit(".", 1)[0]
            std = U.standard_point(v["src"], C.TAG_INDEX.get(tag))
            if r["type"] == "Double" and std["factor"] is not None:
                t = C.TAG_INDEX.get(tag)
                for engine in ([t["lo"], (t["lo"] + t["hi"]) / 2, t["hi"]] if t and v["src"].endswith((".PV", ".SP")) else [0.0, 37.5]):
                    expect = engine * std["factor"] + std["offset"]
                    got = to_standard(r[f"{key}.to_uns"], native.encode(v, engine))
                    assert got == pytest.approx(expect, rel=1e-8, abs=1e-6), (key, r["uns_point"], engine)
            elif std["enum"] is not None:
                for engine, standard in std["enum"].items():
                    assert to_standard(r[f"{key}.to_uns"], native.encode(v, engine)) == standard, (key, r["uns_point"])
            elif v["type"] in ("Boolean", "Int32") and v["src"].endswith(("Running", "Tripped", "FieldIOConnected", "RegenIncomplete")):
                for engine in (False, True):
                    out = to_standard(r[f"{key}.to_uns"], native.encode(v, engine))
                    assert str(out).lower() == str(engine).lower(), (key, r["uns_point"])


def test_mapping_rows_name_their_gaps():
    """A standard point a site doesn't have says so in the notes (no silent holes in the answer key)."""
    for r in MAPPING:
        missing = [k for k in sites.SITES if not r[f"{k}.nodeid"]]
        assert bool(missing) == bool(r["notes"]), r["uns_point"]


# ---------------------------------------------------------------- behaviour of the new sites
@pytest.fixture(scope="module", params=NEW_SITES)
def steady_site(request):
    with on_site(request.param):
        sim = Sim(seed=SEED, t0=T0)
        sim.run(300)
        win = sim.run(2 * 3600)
        prio = {k: a.priority for k, a in sim.dcs.alarms.items()}
        states = {k: sim.state(k) for k in C.EQUIPMENT}
        return request.param, sim, win, prio, {t["tag"]: t for t in C.TAGS}, dict(C.EQUIPMENT), states


def test_new_site_steady_state(steady_site):
    key, sim, win, prio, tags, equipment, states = steady_site
    assert sorted(a for a in win.alarms if prio[a] <= 2) == [], key
    assert sim.tripped() == set(), key
    standby = {k for k, e in equipment.items() if e.get("standby")}
    assert states == {k: "STOPPED" if k in standby else "RUNNING" for k in equipment}
    assert win.min("AI-601") >= 99.5, f"{key}: GOX purity"
    assert tags["PIC-502"]["alarms"]["L"] < win.min("PIC-502") and win.max("PIC-502") < tags["PIC-502"]["alarms"]["H"]
    assert win.max("AI-202") < 1.0, f"{key}: CO2 after the PPU"
    for tag, t in tags.items():
        lo, hi = win.min(tag), win.max(tag)
        tol = 0.02 * (t["hi"] - t["lo"])
        assert lo is not None and t["lo"] - tol <= lo and hi <= t["hi"] + tol, f"{key}: {tag} [{lo}, {hi}]"


def test_vadhana_follows_the_furnace(steady_site):
    """The steel works' heats swing the GOX send-out; the LOX backup vaporizer tops up the peaks."""
    key, sim, win, *_ = steady_site
    if key != "vadhana":
        pytest.skip("Vadhana only")
    assert win.max("FIC-501") - win.min("FIC-501") > 1.5
    assert win.max("FIC-507") > 0.2


def test_port_arden_standby_pump_takes_over():
    """The running LOX pump trips: the DCS starts the standby, no I-500, the GOX header holds."""
    with on_site("port-arden"):
        sim = Sim(seed=SEED, t0=T0)
        sim.run(600)
        sim.scenario("lox_pump_trip")
        win = sim.run(600)
        assert sim.tripped() == set()
        assert (sim.state("LOXP"), sim.state("LOXPB")) == ("STOPPED", "RUNNING")
        assert "LOXPB.AUTOSTART" in win.alarms
        assert win.min("PIC-502") > C.TAG_INDEX["PIC-502"]["alarms"]["L"]
        assert win.min("TI-512") > C.TAG_INDEX["TI-512"]["alarms"]["L"]
        # Duty swap back by the operator: start A, stop B. Nothing trips, the alarm clears.
        assert sim.cmd("start:LOXP")[0]
        sim.run(120)
        assert sim.cmd("stop:LOXPB")[0]
        sim.run(120)
        assert sim.tripped() == set() and sim.state("LOXP") == "RUNNING" and not sim.dcs.alarms["LOXPB.AUTOSTART"].active
        # The last running pump stopped by the operator: that is a trip, as with a single pump.
        assert sim.cmd("stop:LOXP")[0]
        sim.run(30)
        assert "I-500" in sim.tripped()


def test_vadhana_restart_after_asu_trip():
    """Without GAN product: MAC trip, the LOX backup holds the GOX header, the documented restart works."""
    from test_behaviour import reset, start
    with on_site("vadhana"):
        sim = Sim(seed=SEED, t0=T0)
        sim.run(300)
        sim.scenario("mac_motor_trip")
        win = sim.run(900)
        assert sim.tripped() == {"I-100", "I-300", "I-110", "I-500"}, "I-505 must survive an ASU trip"
        assert win.min("PIC-502") > C.TAG_INDEX["PIC-502"]["alarms"]["LL"]
        reset(sim, "I-100", "I-300", "I-110", "I-500")
        start(sim, "MAC")
        assert sim.cmd(f"sp:FIC-101={C.TAG_INDEX['FIC-101']['sp']}")[0]
        start(sim, "EXP")
        assert sim.cmd(f"sp:FIC-303={C.TAG_INDEX['FIC-303']['sp']}")[0]
        start(sim, "BAC")
        start(sim, "LOXP")
        sim.run(3600)
        win = sim.run(3600)
        assert sim.tripped() == set() and sim.loop("FIC-501").mode == "CAS"
        assert win.min("PIC-502") >= C.TAG_INDEX["PIC-502"]["alarms"]["L"]
