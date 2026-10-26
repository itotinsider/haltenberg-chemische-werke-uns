"""End to end: the real plant/main.py and dcs/main.py as processes, talking real Modbus TCP.

Checks what crosses each system boundary against the committed inventory:
raw Modbus registers -> DCS tags -> HTTP API, OPC UA address space, values, HistoryRead,
operator commands, and bad quality when the plant goes away. ~30 s. Skip with  -m "not e2e".
"""

import asyncio
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

import config as C
import iomap
from conftest import ROOT
from inventory import walk

pytestmark = pytest.mark.e2e

NAMESPACE = "urn:haltenberg:hcw:asu1:dcs"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.load(r)


def post(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body or {}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def wait_for(cond, timeout=30.0, step=0.5):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if cond():
                return True
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(step)
    return False


def launch(tmp, site="haltenberg", plant_site=None, path="/hcw/asu1/", wait_io=True):
    """Start plant/main.py and dcs/main.py for `site` (the plant may be another site's: rack check)."""
    p = dict(modbus=free_port(), plant=free_port(), dcs=free_port(), opcua=free_port())
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "common"), HCW_SITE=site)
    if site == "haltenberg":
        env["PPU_CYCLE_SECONDS"] = "14400"
    plant_env = dict(env, HCW_SITE=plant_site or site, MODBUS_PORT=str(p["modbus"]), WEB_PORT=str(p["plant"]),
                     DCS_URL=f"http://127.0.0.1:{p['dcs']}")
    dcs_env = dict(env, PLANT_HOST="127.0.0.1", PLANT_PORT=str(p["modbus"]), WEB_PORT=str(p["dcs"]),
                   OPCUA_PORT=str(p["opcua"]), DATA_DIR=str(tmp / "data"), SCAN_SECONDS="1")
    logs = {k: open(tmp / f"{k}.log", "w") for k in ("plant", "dcs")}
    procs = {
        "plant": subprocess.Popen([sys.executable, "plant/main.py"], cwd=ROOT, env=plant_env,
                                  stdout=logs["plant"], stderr=subprocess.STDOUT),
        "dcs": subprocess.Popen([sys.executable, "dcs/main.py"], cwd=ROOT, env=dcs_env,
                                stdout=logs["dcs"], stderr=subprocess.STDOUT),
    }
    s = dict(ports=p, procs=procs, logs=logs, tmp=tmp, dcs=f"http://127.0.0.1:{p['dcs']}",
             plant=f"http://127.0.0.1:{p['plant']}", opcua=f"opc.tcp://127.0.0.1:{p['opcua']}{path}")
    ok = wait_for(lambda: get(s["dcs"] + "/api/state").get("io_ok") if wait_io else get(s["dcs"] + "/api/state"),
                  timeout=40)
    if not ok:
        stop(s)
        pytest.fail("stack did not come up:\n" + "\n".join(
            f"--- {k}.log\n" + open(tmp / f"{k}.log").read()[-3000:] for k in logs))
    time.sleep(3)          # a few scans: historian samples, OPC UA values
    return s


def stop(s):
    for proc in s["procs"].values():
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)
    for f in s["logs"].values():
        f.close()


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    s = launch(tmp_path_factory.mktemp("e2e"))
    yield s
    stop(s)


def opcua(fn):
    """Run `fn(client, ns)` against the DCS's OPC UA server."""
    from asyncua import Client

    async def go(url):
        async with Client(url, timeout=10) as client:
            ns = await client.get_namespace_index(NAMESPACE)
            return await fn(client, ns)
    return go


# ---------------------------------------------------------------- HTTP
def test_dcs_state_matches_inventory(stack, inv):
    st = get(stack["dcs"] + "/api/state")
    shape = inv["api"]["dcs"]["state"]
    assert list(st) == shape["keys"]
    assert list(st["tags"]) == list(inv["dcs"]["tags"])
    for tag, row in st["tags"].items():
        keys = shape[f"tags.<{inv['dcs']['tags'][tag]['kind']}>"]
        required = {k for k in keys if not k.endswith("?")}
        assert required <= set(row) <= required | {k[:-1] for k in keys}, tag
        assert row["pv"] is not None, f"{tag} has no value"
    assert list(st["equipment"]) == list(inv["dcs"]["equipment"])
    assert list(st["interlocks"]) == list(inv["dcs"]["interlocks"])
    for k in ("scan", "io", "historian"):
        assert list(st[k]) == shape[k], k
    assert all(e["state"] == "RUNNING" for e in st["equipment"].values())
    assert not any(il["latched"] for il in st["interlocks"].values())


def test_dcs_config_and_historian_match_inventory(stack, inv):
    cfg = get(stack["dcs"] + "/api/config")
    assert list(cfg) == inv["api"]["dcs"]["config"]["keys"]
    assert [t["tag"] for t in cfg["tags"]] == list(inv["dcs"]["tags"])
    hist = get(stack["dcs"] + "/api/historian")
    assert set(hist["points"]) == set(inv["api"]["dcs"]["historian_points"])


def test_plant_api_matches_inventory(stack, inv):
    pl = get(stack["plant"] + "/api/plant")
    shape = inv["api"]["plant"]["plant"]
    assert list(pl) == shape["keys"] and list(pl["hidden"]) == shape["hidden"]
    assert list(pl["scenarios"]) == list(inv["plant"]["scenarios"])
    assert list(pl["measurements"]) == list(inv["field"]["input_registers"]["points"])
    assert pl["io_map"]["input_registers"][0] == dict(point="FT-101", desc=iomap.ANALOG_INPUTS[0][2], address=100)
    assert post(stack["plant"] + "/api/scenarios/no_such_fault/start")[0] == 404


def test_datapath_api(stack, inv):
    # The IT/OT trainer's diagram: the same value at every layer.
    shape = inv["api"]["dcs"]["datapath"]
    p = get(stack["dcs"] + "/api/datapath?tag=FIC-101")
    assert list(p) == shape["keys"] and list(p["field_in"]) == shape["field_in"] and list(p["dcs"]) == shape["dcs"]
    assert list(p["historian"]) == shape["historian"] and list(p["opcua"]) == shape["opcua"]
    assert [list(h) for h in p["historian"]["points"]] == [shape["historian.points[]"]] * 3
    assert [list(n) for n in p["opcua"]["nodes"]] == [shape["opcua.nodes[]"]] * 3
    fi = p["field_in"]
    assert (fi["point"], fi["address"]) == ("FT-101", iomap.AI_ADDR["FT-101"])
    assert abs(iomap.regs_to_float(*fi["words"]) - fi["value"]) < 1e-6
    assert abs(fi["value"] - p["dcs"]["pv"]) < 0.05 * 180
    assert p["field_out"]["point"] == "FV-101" and p["dcs"]["quality"] == "GOOD"
    assert p["opcua"]["nodes"][0]["nodeid"].endswith(";s=HCW.ASU1.FIC-101.PV")
    assert p["opcua"]["nodes"][0]["status"] == "Good"
    assert p["historian"]["points"][0]["stored_last_hour"] >= 1
    c = get(stack["dcs"] + "/api/datapath?tag=TY-411")
    assert c["field_in"] is None and c["calc_from"] == ["PI-401"]
    with pytest.raises(urllib.error.HTTPError):
        get(stack["dcs"] + "/api/datapath?tag=NO-SUCH")


def test_history_api(stack):
    h = get(stack["dcs"] + "/api/history?tags=FIC-101.PV,AI-601.PV&minutes=5&points=50")
    assert h["series"]["FIC-101.PV"] and h["series"]["AI-601.PV"]


# ---------------------------------------------------------------- raw Modbus = what the DCS reads
def test_raw_modbus_registers(stack, inv):
    from pymodbus.client import ModbusTcpClient
    client = ModbusTcpClient("127.0.0.1", port=stack["ports"]["modbus"])
    assert client.connect()
    try:
        ir = [0] * iomap.AI_REGISTERS
        for start, count in iomap.AI_BLOCKS:                  # one request per process area
            ir[start:start + count] = client.read_input_registers(start, count=count).registers
        di = client.read_discrete_inputs(0, count=iomap.DI_COUNT).bits
        coils = client.read_coils(0, count=iomap.DO_COUNT).bits
    finally:
        client.close()
    # Like a real device, the plant refuses a read of more than 125 registers (exception 3).
    # pymodbus won't even send one, so this is a raw Modbus TCP frame: MBAP header + FC4 0..126.
    with socket.create_connection(("127.0.0.1", stack["ports"]["modbus"]), timeout=5) as sock:
        sock.sendall(struct.pack(">HHHBBHH", 1, 0, 6, 1, 4, 0, 126))
        reply = sock.recv(64)
    assert reply[7:9] == bytes([0x84, 3])
    raw = iomap.unpack_floats(iomap.ANALOG_INPUTS, ir)
    st = get(stack["dcs"] + "/api/state")
    for tag, t in inv["dcs"]["tags"].items():       # the DCS tag reads the register the inventory says
        if "io" in t:
            span = t["range"][1] - t["range"][0]
            assert abs(raw[t["io"]] - st["tags"][tag]["pv"]) < 0.05 * span, f"{tag} vs {t['io']}"
    for key, e in C.EQUIPMENT.items():
        assert di[iomap.DI_ADDR[e["fb"]]] is True, f"{e['fb']} (run feedback) at DI {iomap.DI_ADDR[e['fb']]}"
    for key, e in C.EQUIPMENT.items():
        assert coils[iomap.DO_ADDR[e["cmd"]]] is True, f"{e['cmd']} run command at coil {iomap.DO_ADDR[e['cmd']]}"


# ---------------------------------------------------------------- OPC UA
def test_opcua_address_space_matches_inventory(stack, inv):
    from asyncua import ua

    async def browse(client, ns):
        return await walk(client.get_node(ua.NodeId(C.ENTERPRISE, ns)))
    nodes = asyncio.run(opcua(browse)(stack["opcua"]))
    expected = inv["opcua"]["nodes"]
    assert sorted(set(expected) - set(nodes)) == [], "nodes missing on the live server"
    assert sorted(set(nodes) - set(expected)) == [], "nodes on the live server that the inventory doesn't know"
    assert {p: n for p, n in nodes.items() if n != expected[p]} == {}


def test_opcua_values_are_good_and_in_range(stack, inv):
    from asyncua import ua

    async def read(client, ns):
        ids = [f"HCW.ASU1.{t}.PV" for t in inv["dcs"]["tags"]] + ["HCW.ASU1.Diagnostics.FieldIOConnected"]
        ids += [f"HCW.ASU1.Equipment.{k}.State" for k in inv["dcs"]["equipment"]]
        nodes = [client.get_node(ua.NodeId(i, ns)) for i in ids]
        return dict(zip(ids, await client.read_attributes(nodes, ua.AttributeIds.Value)))
    dvs = asyncio.run(opcua(read)(stack["opcua"]))
    for nid, dv in dvs.items():
        assert dv.StatusCode.is_good(), f"{nid}: {dv.StatusCode}"
    for tag, t in inv["dcs"]["tags"].items():
        v, (lo, hi) = dvs[f"HCW.ASU1.{tag}.PV"].Value.Value, t["range"]
        assert lo - 0.02 * (hi - lo) <= v <= hi + 0.02 * (hi - lo), f"{tag} = {v} outside [{lo}, {hi}]"
    assert dvs["HCW.ASU1.Diagnostics.FieldIOConnected"].Value.Value is True
    assert {dvs[f"HCW.ASU1.Equipment.{k}.State"].Value.Value for k in inv["dcs"]["equipment"]} == {"RUNNING"}


def test_opcua_history_read(stack):
    from asyncua import ua
    from datetime import datetime, timedelta, timezone

    async def hist(client, ns):
        node = client.get_node(ua.NodeId("HCW.ASU1.FIC-101.PV", ns))
        end = datetime.now(timezone.utc)
        return await node.read_raw_history(end - timedelta(minutes=5), end)
    good = [dv for dv in asyncio.run(opcua(hist)(stack["opcua"])) if dv.StatusCode.is_good()]
    assert good and all(140 < dv.Value.Value < 150 for dv in good)


def test_operator_command_reaches_opcua(stack):
    from asyncua import ua

    async def mode(client, ns):
        return await client.get_node(ua.NodeId("HCW.ASU1.FIC-409.Mode", ns)).read_value()
    assert post(stack["dcs"] + "/api/loops/FIC-409", {"mode": "MAN"}) == (200, {"ok": True, "message": "ok"})
    assert wait_for(lambda: asyncio.run(opcua(mode)(stack["opcua"])) == "MAN", timeout=5)
    assert post(stack["dcs"] + "/api/loops/FIC-409", {"mode": "AUTO"})[0] == 200
    assert wait_for(lambda: asyncio.run(opcua(mode)(stack["opcua"])) == "AUTO", timeout=5)
    assert post(stack["dcs"] + "/api/interlocks/I-100/reset")[0] == 409, "nothing to reset"


def test_not_applicable_is_bad_out_of_service_live_and_in_history(stack):
    """After an ESD, KPI-701 (per unit of GOX) doesn't apply: BadOutOfService, not BadNoCommunication."""
    from asyncua import ua
    from datetime import datetime, timedelta, timezone
    assert post(stack["dcs"] + "/api/esd")[0] == 200
    assert wait_for(lambda: get(stack["dcs"] + "/api/state")["tags"]["KPI-701"]["pv"] is None, timeout=30)
    time.sleep(2)

    async def read(client, ns):
        kpi = client.get_node(ua.NodeId("HCW.ASU1.KPI-701.PV", ns))
        live = await kpi.read_data_value(raise_on_bad_status=False)
        air = await client.get_node(ua.NodeId("HCW.ASU1.FIC-101.PV", ns)).read_data_value()
        end = datetime.now(timezone.utc)
        hist = await kpi.read_raw_history(end - timedelta(minutes=5), end)
        return live, air, hist
    live, air, hist = asyncio.run(opcua(read)(stack["opcua"]))
    assert live.StatusCode.value == ua.StatusCodes.BadOutOfService
    assert air.StatusCode.is_good(), "measured tags stay Good"
    assert hist[-1].StatusCode.value == ua.StatusCodes.BadOutOfService, "history says the same as the live value"


# ---------------------------------------------------------------- last: take the plant away
def test_plant_loss_gives_bad_quality(stack):
    from asyncua import ua
    latched = {k for k, il in get(stack["dcs"] + "/api/state")["interlocks"].items() if il["latched"]}
    stack["procs"]["plant"].terminate()
    stack["procs"]["plant"].wait(10)
    comm = lambda st: any(a["id"] == "IO.COMM" and a["active"] for a in st["alarms"])  # noqa: E731
    assert wait_for(lambda: comm(get(stack["dcs"] + "/api/state")), timeout=15), "IO.COMM (raised after 3 s)"
    st = get(stack["dcs"] + "/api/state")
    assert st["io_ok"] is False and all(row["pv"] is None for row in st["tags"].values())
    assert {k for k, il in st["interlocks"].items() if il["latched"]} == latched, "bad quality must not trip"

    async def read(client, ns):
        node = client.get_node(ua.NodeId("HCW.ASU1.FIC-101.PV", ns))
        pv = await node.read_data_value(raise_on_bad_status=False)
        io = await client.get_node(ua.NodeId("HCW.ASU1.Diagnostics.FieldIOConnected", ns)).read_value()
        return pv, io
    pv, io = asyncio.run(opcua(read)(stack["opcua"]))
    assert pv.StatusCode.value == ua.StatusCodes.BadNoCommunication and io is False


# ---------------------------------------------------------------- the other sites (common/sites.py)
SITE_SERVERS = {"port-arden": ("/PortArden/ASU7100", "urn:hcw-americas:port-arden:asu7100:dcs", "PTA_ASU7100"),
                "vadhana": ("/vadhana/asu2", "urn:hcw-india:vadhana:asu2", "VDH_ASU2")}


@pytest.mark.parametrize("key", list(SITE_SERVERS))
def test_site_opcua_matches_inventory_and_mapping(key, tmp_path):
    """A site's live OPC UA server: the committed address space, Good values, and the mapping's conversion
    of each controller setpoint giving the DCS's own setpoint in standard units."""
    import csv
    from datetime import datetime, timezone
    from asyncua import Client, ua
    from test_sites import to_standard
    path, nsu, root = SITE_SERVERS[key]
    s = launch(tmp_path, key, path=path)
    try:
        expected = json.load(open(os.path.join(ROOT, f"inventory/sites/{key}/opcua.json")))["nodes"]
        rows = list(csv.DictReader(open(os.path.join(ROOT, "inventory/uns/mapping.csv"))))
        st = get(s["dcs"] + "/api/state")

        async def go():
            async with Client(s["opcua"], timeout=10) as client:
                ns = await client.get_namespace_index(nsu)
                root_id = expected[root].get("id", root)
                nodes = await walk(client.get_node(ua.NodeId(root_id, ns)))
                checked = 0
                for r in rows:
                    if r["attribute"] != "setpoint" or not r[f"{key}.nodeid"]:
                        continue
                    if st["tags"][r["engine_key"].split(".")[0]]["mode"] != "AUTO":
                        continue        # a cascade / ratio setpoint moves between the two reads
                    dv = await client.get_node(f"ns={ns};{r[key + '.nodeid']}").read_data_value(raise_on_bad_status=False)
                    assert dv.StatusCode.is_good(), r["uns_point"]
                    tag = r["engine_key"].split(".")[0]
                    factor = 1000.0 if r["unit"] in ("Nm3/h", "kW") else 1.0
                    assert to_standard(r[f"{key}.to_uns"], dv.Value.Value) == pytest.approx(
                        st["tags"][tag]["sp"] * factor, rel=1e-6), r["uns_point"]
                    checked += 1
                lp = next(r for r in rows if r["uns_point"] == "double-column/lp-top-pressure" and r["attribute"] == "value")
                dv = await client.get_node(f"ns={ns};{lp[key + '.nodeid']}").read_data_value()
                skew = (dv.SourceTimestamp.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).total_seconds()
                return nodes, checked, skew
        nodes, checked, skew = asyncio.run(go())
        assert nodes == expected
        assert checked >= 10
        # Vadhana's gateway stamps local time (IST) as UTC; Port Arden's is right.
        assert abs(skew - (19800 if key == "vadhana" else 0)) < 10
    finally:
        stop(s)


def test_dcs_refuses_another_sites_plant(tmp_path):
    """RID-901: a Port Arden DCS pointed at the Vadhana plant never gets good I/O and never writes to it."""
    s = launch(tmp_path, "port-arden", plant_site="vadhana", path="/PortArden/ASU7100", wait_io=False)
    try:
        time.sleep(3)
        assert get(s["dcs"] + "/api/state")["io_ok"] is False
        assert get(s["plant"] + "/api/plant")["dcs_watchdog_age_s"] > 4, "the DCS wrote to the wrong plant"
        assert "wrong I/O rack" in open(tmp_path / "dcs.log").read()
    finally:
        stop(s)
