"""Generate the interface inventory (inventory/) from each layer's own source of truth.

Every layer keeps owning its part (see inventory/README.md); this tool only *reads* them and
writes one file per layer, plus tags.csv, which joins one DCS tag across all layers (field point,
Modbus address, OPC UA nodes, historian points). Nobody edits inventory/ by hand:

    docker compose run --rm tests python tools/inventory.py           # regenerate after a change
    docker compose run --rm tests python tools/inventory.py --check   # exit 1 + diff if stale

tests/test_inventory.py fails while the committed inventory differs from the code, so any added,
renamed or removed tag, register, node, scenario or API field shows up as a diff in the PR.
Needs asyncua (the OPC UA address space is built in-process, exactly as the DCS builds it).
"""

import asyncio
import csv
import difflib
import importlib.util
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("common", "dcs", "plant", "tools"):
    p = os.path.join(ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import config as C  # noqa: E402
import iomap  # noqa: E402
from control import DCS  # noqa: E402
from model import SCENARIOS  # noqa: E402

OUT_DIR = os.path.join(ROOT, "inventory")


def load_module(name, rel_path):
    """Import a file under its own name (dcs/main.py and plant/main.py are both "main")."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- JSON layout
def dumps(obj, flat_at=2, indent=0, width=118):
    """JSON with one record per line: anything `flat_at` levels deep (a tag, a node, a register)
    goes on a single line, as does anything shorter than `width`. A changed tag is a one-line diff."""
    flat = json.dumps(obj, ensure_ascii=False)
    if indent // 2 >= flat_at or len(flat) + indent <= width or not isinstance(obj, (dict, list)) or not obj:
        return flat
    pad, inner = " " * indent, " " * (indent + 2)
    if isinstance(obj, dict):
        items = [f"{inner}{json.dumps(k, ensure_ascii=False)}: {dumps(v, flat_at, indent + 2, width)}"
                 for k, v in obj.items()]
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"
    items = [inner + dumps(v, flat_at, indent + 2, width) for v in obj]
    return "[\n" + ",\n".join(items) + "\n" + pad + "]"


# ---------------------------------------------------------------- layers
def field_layer():
    def table(points, fc, direction, writer, reader, requests=None):
        t = dict(function_codes=fc, direction=direction, writer=writer, reader=reader)
        if requests:
            t["requests"] = [f"{start}+{count}" for start, count in requests]
        t["points"] = {n: dict(address=a, desc=d) for n, a, d in points}
        return t
    return {
        "_source": "common/iomap.py",
        "encoding": "float32 = 2 registers, big-endian word order; addresses 0-based, fixed per process area "
                    "(registers n*100.., bits n*10.. for tag numbers n..)",
        "input_registers": table(iomap.ANALOG_INPUTS, "FC4", "plant -> DCS", "plant/model.py measurements()",
                                 "dcs/config.py io=", iomap.AI_BLOCKS),
        "holding_registers": table(iomap.ANALOG_OUTPUTS, "FC3/FC16", "DCS -> plant", "dcs/config.py out=",
                                   "plant/model.py ao", iomap.AO_BLOCKS),
        "discrete_inputs": table(iomap.DISCRETE_INPUTS, "FC2", "plant -> DCS", "plant/model.py discretes()",
                                 "dcs/config.py EQUIPMENT fb="),
        "coils": table(iomap.COILS, "FC1/FC5/FC15", "DCS -> plant", "dcs/config.py EQUIPMENT cmd=",
                       "plant/model.py do"),
    }


def dcs_layer():
    dcs = DCS()
    tags = {}
    for t in C.TAGS:
        row = dict(kind=t["kind"], block=t["block"], desc=t["desc"], unit=t["unit"], range=[t["lo"], t["hi"]])
        for k in ("io", "out", "pv"):
            if t.get(k):
                row[k] = t[k]
        if t["kind"] == "PID":
            row["modes"] = t["modes"]
        if t["alarms"]:
            row["alarms"] = {lim: [sp, dcs.alarms[f"{t['tag']}.{lim}"].priority] for lim, sp in t["alarms"].items()}
        if t.get("suppress"):
            row["suppress"] = t["suppress"]
        ctl = {}
        if t.get("cascade"):
            ctl["cascade"] = [dict(slave=c["slave"], op=c["op"], sp=c["sp"]) for c in t["cascade"]]
        for k in ("limits", "ratio_of", "xlimit", "sp_cap"):
            if t.get(k):
                ctl[k] = t[k]
        if ctl:
            row["control"] = ctl
        tags[t["tag"]] = row
    return {
        "_source": "dcs/config.py (alarm priorities and discrete alarms as dcs/control.py builds them)",
        "unit": dict(enterprise=C.ENTERPRISE, site=C.SITE, unit=C.UNIT, label=C.UNIT_LABEL),
        "blocks": dict(C.BLOCKS),
        "tags": tags,
        "equipment": {k: dict(label=v["label"], block=v["block"], cmd=v["cmd"], fb=v["fb"], trip=v["trip"],
                              bypass_s=v["bypass_s"]) for k, v in C.EQUIPMENT.items()},
        "interlocks": {k: dict(label=v["label"], block=v["block"], causes=[list(c) for c in v["causes"]],
                               forces=sorted(v["forces"]), parents=v.get("parents", []),
                               handover=v.get("handover", {})) for k, v in C.INTERLOCKS.items()},
        "discrete_alarms": {k: [a.priority, a.message] for k, a in dcs.alarms.items() if a.limit == "DISC"},
        "lineup": C.LINEUP,
        "sequences": {"KS-201": dict(steps=[s for s, _ in C.PPU_STEPS], cycle_range_s=[C.PPU_CYCLE_MIN, C.PPU_CYCLE_MAX])},
    }


def plant_layer():
    from offline_sim import Sim
    snap = Sim(seed=0, t0=0.0).plant.snapshot()
    return {
        "_source": "plant/model.py (instructor station only: never on Modbus, in the historian or on OPC UA)",
        "scenarios": {k: dict(label=v["label"], kind=v["kind"], group=v["group"],
                              **({"duration_s": v["duration"]} if "duration" in v else {}))
                      for k, v in SCENARIOS.items()},
        "ground_truth": {"hidden": list(snap["hidden"]), "consumers": list(snap["consumers"]),
                         "environment": list(snap["environment"])},
    }


def _keys(rows):
    """Union of the keys of several dicts; keys missing from some get a trailing '?'."""
    rows = list(rows)
    seen = []
    for r in rows:
        seen += [k for k in r if k not in seen]
    return [k if all(k in r for r in rows) else k + "?" for k in seen]


def api_layer():
    from offline_sim import Sim
    dmain = load_module("dcs_main", "dcs/main.py")
    pmain = load_module("plant_main", "plant/main.py")
    sim = Sim(seed=0, t0=0.0)
    sim.step()
    snap = sim.dcs.snapshot()
    kinds = {t["tag"]: t["kind"] for t in C.TAGS}

    class RT:  # the bits of dcs/main.Runtime that config_payload(), runtime_status() and datapath() read
        dcs = sim.dcs
        scan_count, scan_ms = 1, 0.0
        io = {"connected": True, "last_heartbeat": None, "heartbeat_changed": 0.0, "errors": 0}
        snapshot = snap
        wire = {"ir": [0] * iomap.AI_REGISTERS, "read_at": 0.0, "hr": [0] * iomap.AO_REGISTERS, "written_at": 0.0}
        opcua = None

        class historian:
            stored = 0
            last = {}
            count = staticmethod(lambda name, start, end: 0)
    cfg = dmain.config_payload(RT)
    snap.update(dmain.runtime_status(RT))
    path = dmain.datapath(RT, "FIC-101")
    psnap = pmain.Runtime().snapshot()
    any_alarm = next(iter(sim.dcs.alarms.values())).as_dict()
    return {
        "_source": "dcs/main.py, plant/main.py (response shapes from a live snapshot; values are not part of it)",
        "dcs": {
            "port": 8110,
            "get": list(dmain.PAGES) + ["/api/state", "/api/config", "/api/history?tags=&minutes=&points=",
                                        "/api/events?limit=&start=", "/api/historian", "/api/datapath?tag="],
            "post": ["/api/loops/<tag> {mode, sp, op, sp_hi, sp_lo, kc, ti, sp_rate, ratio}",
                     "/api/equipment/<key>/start|stop", "/api/interlocks/<key>/reset", "/api/esd", "/api/lineup",
                     "/api/sequences/KS-201/cycle {seconds, source}", "/api/sequences/KS-201/switch",
                     "/api/alarms/ack {id?}"],
            "state": {
                "keys": list(snap),
                **{f"tags.<{k}>": _keys(snap["tags"][t] for t in snap["tags"] if kinds[t] == k) for k in ("AI", "PID", "CALC")},
                "alarms[]": list(any_alarm),
                "equipment.<key>": _keys(snap["equipment"].values()),
                "interlocks.<key>": _keys(snap["interlocks"].values()),
                "sequences.KS-201": list(snap["sequences"]["KS-201"]),
                **{k: list(snap[k]) for k in ("scan", "io", "historian")},
            },
            "config": {"keys": list(cfg), "tags[]": _keys(cfg["tags"]),
                       "equipment.<key>": _keys(cfg["equipment"].values()),
                       "interlocks.<key>": _keys(cfg["interlocks"].values())},
            "historian_points": list(dmain.historian_points()),
            "datapath": {"keys": list(path), "field_in": list(path["field_in"]), "dcs": list(path["dcs"]),
                         "historian": list(path["historian"]), "historian.points[]": list(path["historian"]["points"][0]),
                         "opcua": list(path["opcua"]), "opcua.nodes[]": list(path["opcua"]["nodes"][0])},
        },
        "plant": {
            "port": 8111,
            "get": ["/", "/api/plant"],
            "post": ["/api/scenarios/<key>/start|stop", "/api/ppu_cycle {seconds}"],
            "plant": {"keys": list(psnap), "hidden": list(psnap["hidden"]),
                      "scenarios.<key>": _keys(psnap["scenarios"].values()),
                      "ppu_cycle": list(psnap["ppu_cycle"]), "io_map": list(psnap["io_map"])},
        },
    }


# ---------------------------------------------------------------- OPC UA
async def walk(node, path=""):
    """Browse an OPC UA subtree (server-side or client-side Node) into {browse path: info}.

    Standard-namespace children of an AnalogItem (EURange, EngineeringUnits) are folded into
    their parent as range/unit, so the result depends only on what the DCS configures.
    """
    from asyncua import ua
    out = {}
    name = (await node.read_browse_name()).Name
    path = f"{path}/{name}" if path else name
    cls = await node.read_node_class()
    info = {"class": cls.name}
    nid = node.nodeid
    if nid.NodeIdType == ua.NodeIdType.String:
        info["id"] = nid.Identifier
    if cls == ua.NodeClass.Variable:
        dt = await node.read_data_type()
        info["type"] = ua.ObjectIdNames.get(dt.Identifier, str(dt.Identifier)) if dt.NamespaceIndex == 0 else str(dt)
        hist = await node.read_attribute(ua.AttributeIds.Historizing)
        if hist.Value.Value:
            info["history"] = True
    out[path] = info
    for child in await node.get_children():
        qn = await child.read_browse_name()
        if qn.NamespaceIndex == 0 and qn.Name in ("EURange", "EngineeringUnits"):
            v = await child.read_value()
            if qn.Name == "EURange":
                info["range"] = [v.Low, v.High]
            else:
                info["unit"] = v.DisplayName.Text
            continue
        out.update(await walk(child, path))
    return out


async def _opcua_nodes():
    from asyncua import ua
    osrv = load_module("opcua_server", "dcs/opcua_server.py")
    srv = osrv.OpcUaServer(historian=None)
    await srv.server.init()
    srv.idx = await srv.server.register_namespace(osrv.NAMESPACE)
    await srv._build()
    root = srv.server.get_node(ua.NodeId(C.ENTERPRISE, srv.idx))
    return osrv.NAMESPACE, await walk(root)


def opcua_layer():
    namespace, nodes = asyncio.run(_opcua_nodes())
    return {
        "_source": "dcs/opcua_server.py (address space generated from dcs/config.py)",
        "endpoint": "opc.tcp://<host>:4840/hcw/asu1/",
        "namespace": namespace,
        "nodes": nodes,
    }


# ---------------------------------------------------------------- tags.csv (one tag across all layers)
TAG_COLUMNS = ["tag", "kind", "block", "description", "unit", "lo", "hi", "modes", "field_in", "modbus_in",
               "field_out", "modbus_out", "opcua_node_prefix", "opcua_variables", "historian_points",
               "alarms", "suppress"]


def tags_csv(opcua, api):
    by_obj = {}
    for info in opcua["nodes"].values():
        nid = info.get("id", "")
        if info["class"] == "Variable" and nid.count(".") == 3:          # HCW.ASU1.<tag>.<attr>
            tag, attr = nid.split(".")[2], nid.split(".")[3]
            by_obj.setdefault(tag, []).append(attr)
    hist = {}
    for p in api["dcs"]["historian_points"]:
        tag, attr = p.rsplit(".", 1)
        hist.setdefault(tag, []).append(attr)
    dcs = DCS()
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(TAG_COLUMNS)
    for t in C.TAGS:
        tag = t["tag"]
        alarms = "; ".join(f"{lim} {sp} P{dcs.alarms[f'{tag}.{lim}'].priority}" for lim, sp in t["alarms"].items())
        w.writerow([tag, t["kind"], t["block"], t["desc"], t["unit"], t["lo"], t["hi"],
                    " ".join(t["modes"]) if t["kind"] == "PID" else "",
                    t.get("io") or "", f"IR {iomap.AI_ADDR[t['io']]}" if t.get("io") else "",
                    t.get("out") or "", f"HR {iomap.AO_ADDR[t['out']]}" if t.get("out") else "",
                    f"{C.ENTERPRISE}.{C.UNIT}.{tag}", " ".join(by_obj.get(tag, [])), " ".join(hist.get(tag, [])),
                    alarms, t.get("suppress") or ""])
    return buf.getvalue()


# ---------------------------------------------------------------- main
def build():
    """{file name: content} for everything in inventory/ except the hand-written README.md."""
    opcua, api = opcua_layer(), api_layer()
    files = {   # name: (content, depth of the one-line records)
        "field.json": (field_layer(), 3),
        "dcs.json": (dcs_layer(), 2),
        "opcua.json": (opcua, 2),
        "plant.json": (plant_layer(), 2),
        "api.json": (api, 3),
    }
    out = {name: dumps(obj, flat_at) + "\n" for name, (obj, flat_at) in files.items()}
    out["tags.csv"] = tags_csv(opcua, api)
    return out


def diff(files=None):
    """Unified diff between the committed inventory and the code (empty = up to date)."""
    files = files or build()
    lines = []
    for name, new in files.items():
        path = os.path.join(OUT_DIR, name)
        old = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
        if old != new:
            lines += difflib.unified_diff(old.splitlines(), new.splitlines(), f"inventory/{name} (committed)",
                                          f"inventory/{name} (from the code)", lineterm="", n=1)
    return lines


def main():
    files = build()
    if "--check" in sys.argv:
        d = diff(files)
        print("\n".join(d) if d else "inventory/ is up to date")
        return 1 if d else 0
    os.makedirs(OUT_DIR, exist_ok=True)
    changed = []
    for name, content in files.items():
        path = os.path.join(OUT_DIR, name)
        old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
        if old != content:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            changed.append(name)
    print("updated: " + ", ".join(changed) if changed else "inventory/ is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
