"""HCW ASU1 DCS runtime: field I/O scan, control, historian, OPC UA server, operator console.

    plant (Modbus TCP :5020) --scan 1 s--> control.DCS --> historian (SQLite, data/)
                                                     +--> OPC UA server :4840 (+ HistoryRead)
                                                     +--> HTTP :8110 (hub, operator console, API)

One asyncio loop owns the scan and the OPC UA server; the HTTP server runs in its own threads.
Operator commands from HTTP take `dcs.lock`; GET /api/state serves the snapshot taken at the end
of the last scan, so polling consoles never contend with the scan.
"""

import asyncio
import json
import logging
import os
import re
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))

from pymodbus.client import AsyncModbusTcpClient  # noqa: E402

import config as C  # noqa: E402
import iomap  # noqa: E402
import sites  # noqa: E402
from control import DCS, T_SAT_N2, T_SAT_O2  # noqa: E402
from historian import NA, Historian  # noqa: E402
import native  # noqa: E402
from opcua_server import engine_values  # noqa: E402

PLANT_HOST = os.getenv("PLANT_HOST", "plant")
PLANT_PORT = int(os.getenv("PLANT_PORT", "5020"))
SCAN_SECONDS = float(os.getenv("SCAN_SECONDS", "1"))
WEB_PORT = int(os.getenv("WEB_PORT", "8110"))
OPCUA_PORT = int(os.getenv("OPCUA_PORT", "4840"))
PPU_CYCLE_SECONDS = float(os.getenv("PPU_CYCLE_SECONDS") or C.SITE_CFG["ppu_cycle_s"])
DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
HISTORIAN_PATH = os.path.join(DATA_DIR, "historian.db")
STATE_PATH = os.path.join(DATA_DIR, "dcs_state.json")
HISTORIAN_DEADBAND_PCT = float(os.getenv("HISTORIAN_DEADBAND_PCT", "0.25"))
HISTORIAN_MAX_INTERVAL = float(os.getenv("HISTORIAN_MAX_INTERVAL_SECONDS", "60"))
HISTORIAN_RETENTION_DAYS = float(os.getenv("HISTORIAN_RETENTION_DAYS", "14"))
HEARTBEAT_TIMEOUT = 5.0
# Shown on the hub page: how a browser / client on the host reaches the other endpoints.
PUBLIC_LINKS = {
    "instructor": os.getenv("PUBLIC_INSTRUCTOR_URL", "http://localhost:8111"),
    "opcua": os.getenv("PUBLIC_OPCUA_ENDPOINT", f"opc.tcp://localhost:{OPCUA_PORT}{native.META['path']}"),
    "modbus": os.getenv("PUBLIC_MODBUS_ENDPOINT", "localhost:5020"),
    "nodered": os.getenv("PUBLIC_NODERED_URL", "http://localhost:1880"),
}
# The hubs of all sites of the global demo, as a browser on the host reaches them (hub page cards).
SITE_LINKS = [dict(key=k, name=v["site"], company=v["company"], country=v["country_name"], unit=v["unit"],
                   url=os.getenv(f"PUBLIC_HUB_{k.upper().replace('-', '_')}", ""))
              for k, v in sites.SITES.items()]
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
PAGES = {"/": "index.html", "/operator": "operator.html", "/mapping": "mapping.html"}
# The standard model's mapping of all three sites (generated into inventory/uns/, copied into the image).
UNS_DIR = next(d for d in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "uns"),
                           os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "inventory", "uns"))
               if os.path.isdir(d))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("dcs")
logging.getLogger("pymodbus").setLevel(logging.ERROR)


def historian_points():
    pts = {}
    for t in C.TAGS:
        span = t["hi"] - t["lo"]
        pts[f"{t['tag']}.PV"] = (t["unit"], t["desc"], span)
        if t["kind"] == "PID":
            pts[f"{t['tag']}.SP"] = (t["unit"], f"{t['desc']} — setpoint", span)
            pts[f"{t['tag']}.OP"] = ("%", f"{t['desc']} — output", 100.0)
    return pts


def historian_values(snap):
    out = {}
    for t in C.TAGS:
        row = snap["tags"][t["tag"]]
        # No value while the I/O is fine = a calculation that doesn't apply now (not "no data").
        out[f"{t['tag']}.PV"] = row["pv"] if row["pv"] is not None or not snap["io_ok"] else NA
        if t["kind"] == "PID":
            ok = snap["io_ok"]
            out[f"{t['tag']}.SP"] = row["sp"] if ok else None
            out[f"{t['tag']}.OP"] = row["op"] if ok else None
    return out


class Runtime:
    def __init__(self):
        self.dcs = DCS(ppu_cycle_s=PPU_CYCLE_SECONDS, scan_s=SCAN_SECONDS)
        self.historian = Historian(HISTORIAN_PATH, historian_points(), HISTORIAN_DEADBAND_PCT,
                                   HISTORIAN_MAX_INTERVAL, HISTORIAN_RETENTION_DAYS)
        self.opcua = native.make_server(self.historian, port=OPCUA_PORT)
        self.snapshot = {}
        self.snapshot_json = b"{}"
        self.scan_count = 0
        self.scan_ms = 0.0
        self.io = {"connected": False, "last_heartbeat": None, "heartbeat_changed": 0.0, "errors": 0}
        self.watchdog = 0
        # The raw field I/O of the last scan, as it was on the wire (for /api/datapath).
        self.wire = {"ir": None, "read_at": None, "hr": None, "written_at": None}
        if os.path.exists(STATE_PATH):
            try:
                with open(STATE_PATH) as f:
                    self.dcs.restore(json.load(f))
                log.info("Restored controller modes/setpoints and sequence state from %s", STATE_PATH)
            except (OSError, ValueError, KeyError, TypeError) as e:
                log.warning("Ignoring unreadable state file %s: %s", STATE_PATH, e)

    async def read_io(self, client):
        if not client.connected:
            await client.connect()
            if not client.connected:
                return None, None
            log.info("Connected to plant I/O at %s:%d", PLANT_HOST, PLANT_PORT)
        # One request per process area (iomap.AI_BLOCKS): the map has gaps between the areas,
        # and a single Modbus read may not exceed 125 registers.
        regs = [0] * iomap.AI_REGISTERS
        for start, count in iomap.AI_BLOCKS:
            rr = await client.read_input_registers(start, count=count)
            if rr.isError():
                raise IOError(f"Modbus read error at input register {start}: {rr}")
            regs[start:start + count] = rr.registers[:count]
        dr = await client.read_discrete_inputs(0, count=iomap.DI_COUNT)
        if dr.isError():
            raise IOError(f"Modbus read error: {dr}")
        ai = iomap.unpack_floats(iomap.ANALOG_INPUTS, regs)
        # Never control another site's plant: with three plants on one network, a reconnect to a recycled
        # container address once cross-wired two DCSs to one plant. The rack tells which site it is.
        if round(ai["RID-901"]) != C.SITE_CFG["rack_id"]:
            raise IOError(f"wrong I/O rack at {PLANT_HOST}:{PLANT_PORT}: rack {ai['RID-901']:.0f}, "
                          f"this DCS is site {C.SITE_CFG['key']} (rack {C.SITE_CFG['rack_id']})")
        di = {name: bool(dr.bits[addr]) for name, addr in iomap.DI_ADDR.items()}
        self.wire.update(ir=regs, read_at=time.time())
        return ai, di

    async def write_io(self, client, ao, do):
        self.watchdog = (self.watchdog + 1) % 65536
        ao = dict(ao, **{"WD-900": float(self.watchdog)})
        regs = iomap.pack_floats(iomap.ANALOG_OUTPUTS, ao, iomap.AO_REGISTERS)
        for start, count in iomap.AO_BLOCKS:
            await client.write_registers(start, regs[start:start + count])
        self.wire.update(hr=regs, written_at=time.time())
        bits = [False] * iomap.DO_COUNT
        for name, addr in iomap.DO_ADDR.items():
            bits[addr] = bool(do.get(name, False))
        await client.write_coils(0, bits)

    async def scan_loop(self, stop):
        # A fresh client after every failure: it looks the plant's host name up again (a recreated
        # container gets a new address, and the old one may already belong to another plant).
        def new_client():
            return AsyncModbusTcpClient(PLANT_HOST, port=PLANT_PORT, timeout=2, retries=0, reconnect_delay=0)
        client = new_client()
        last_error = None
        next_t = time.monotonic()
        last_checkpoint = 0.0
        while not stop.is_set():
            t_start = time.monotonic()
            now = time.time()
            ai = di = None
            try:
                ai, di = await self.read_io(client)
            except Exception as e:   # link failures must never stop the DCS
                self.io["errors"] += 1
                if self.io["connected"] or ("wrong I/O rack" in str(e) and str(e) != last_error):
                    log.warning("Field I/O read failed: %s", e)
                last_error = str(e)
                client.close()
                client = new_client()
            io_ok = ai is not None
            if io_ok:
                hb = ai.get("HB-900")
                if hb != self.io["last_heartbeat"]:
                    self.io["last_heartbeat"] = hb
                    self.io["heartbeat_changed"] = now
                elif now - self.io["heartbeat_changed"] > HEARTBEAT_TIMEOUT:
                    io_ok = False   # link up but the rack has frozen: treat as bad
            if io_ok != self.io["connected"]:
                log.info("Field I/O %s", "OK" if io_ok else "LOST")
            self.io["connected"] = io_ok

            with self.dcs.lock:
                ao, do = self.dcs.scan(ai or {}, di or {}, now, io_ok)
                snap = self.dcs.snapshot()
                events = list(self.dcs.events)
                self.dcs.events.clear()
            if io_ok:
                try:
                    await self.write_io(client, ao, do)
                except Exception as e:
                    self.io["errors"] += 1
                    log.warning("Field I/O write failed: %s", e)
                    client.close()
                    client = new_client()

            self.historian.store(now, historian_values(snap), events)
            self.scan_count += 1
            await self.opcua.update(snap, now, self.scan_count, self.historian.stored)

            self.scan_ms = (time.monotonic() - t_start) * 1000.0
            snap.update(runtime_status(self))
            self.snapshot = snap
            self.snapshot_json = json.dumps(snap).encode("utf-8")

            if now - last_checkpoint > 10:
                last_checkpoint = now
                with self.dcs.lock:
                    state = self.dcs.checkpoint()
                tmp = STATE_PATH + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(state, f)
                os.replace(tmp, STATE_PATH)

            next_t += SCAN_SECONDS
            delay = next_t - time.monotonic()
            if delay < 0:
                next_t = time.monotonic()
                delay = 0
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
        client.close()


def runtime_status(rt):
    """What the runtime adds to the control snapshot in /api/state."""
    return dict(scan=dict(count=rt.scan_count, ms=round(rt.scan_ms, 1), period_s=SCAN_SECONDS),
                io=dict(rt.io, plant=f"{PLANT_HOST}:{PLANT_PORT}"),
                historian=dict(samples_written=rt.historian.stored))


FIELD_DESC = {name: desc for name, _, desc in iomap.ANALOG_INPUTS + iomap.ANALOG_OUTPUTS}
OPC_STATUS = {"GOOD": "Good", "BAD": "BadNoCommunication", "NOT_APPLICABLE": "BadOutOfService"}


def _wire_point(name, addr, regs, ts):
    words = regs[addr:addr + 2] if regs else None
    return dict(point=name, address=addr, words=words, desc=FIELD_DESC.get(name, ""),
                value=iomap.regs_to_float(*words) if words else None, at=ts)


def datapath(rt, tag):
    """One tag's journey through the layers, as the IT/OT trainer draws it: the Modbus registers it
    came in on (or the tags it is calculated from), the DCS tag, its historian points and OPC UA nodes.
    Read-only, built from what the runtime already holds; it never touches the plant's ground truth."""
    t = C.TAG_INDEX.get(tag)
    if t is None:
        return None
    snap, now = rt.snapshot, time.time()
    row = snap.get("tags", {}).get(tag, {})
    io_ok = snap.get("io_ok", False)
    quality = "BAD" if not io_ok else "NOT_APPLICABLE" if row.get("pv") is None else "GOOD"
    w = rt.wire
    field_in = (_wire_point(t["io"], iomap.AI_ADDR[t["io"]], w["ir"], w["read_at"])
                if t.get("io") in iomap.AI_ADDR else None)
    field_out = (_wire_point(t["out"], iomap.AO_ADDR[t["out"]], w["hr"], w["written_at"])
                 if t.get("out") in iomap.AO_ADDR else None)
    alarm = next((a for a in snap.get("alarms", []) if a["tag"] == tag and a["active"]), None)
    attrs = ["PV", "SP", "OP"] if t["kind"] == "PID" else ["PV"]
    hist, points = [], historian_points()
    for a in attrs:
        name = f"{tag}.{a}"
        unit, _, span = points[name]
        last = rt.historian.last.get(name)
        hist.append(dict(point=name, unit=unit, deadband=span * HISTORIAN_DEADBAND_PCT / 100.0,
                         last=dict(ts=last[0], value=last[1], quality=last[2]) if last else None,
                         stored_last_hour=rt.historian.count(name, now - 3600, now)))
    block = dict(C.BLOCKS)[t["block"]]
    ns = getattr(rt.opcua, "idx", 2)
    nodes = [dict(attr=a, nodeid=f"ns={ns};{ident}", browse=browse,
                  status=OPC_STATUS[quality] if a == "PV" else "Good" if io_ok else "BadNoCommunication")
             for a, ident, browse in native.nodes_for(tag)]
    return dict(
        tag=tag, kind=t["kind"], desc=t["desc"], unit=t["unit"], lo=t["lo"], hi=t["hi"],
        block=t["block"], block_label=block, field_in=field_in, field_out=field_out,
        calc_from=C.CALC_INPUTS.get(tag),
        dcs=dict(pv=row.get("pv"), sp=row.get("sp"), op=row.get("op"), mode=row.get("mode"), quality=quality,
                 alarms=t["alarms"], alarm=alarm and dict(limit=alarm["limit"], priority=alarm["priority"]),
                 scan_s=SCAN_SECONDS, scan_count=rt.scan_count),
        historian=dict(points=hist, deadband_pct=HISTORIAN_DEADBAND_PCT, heartbeat_s=HISTORIAN_MAX_INTERVAL,
                       scans_last_hour=round(3600 / SCAN_SECONDS)),
        opcua=dict(endpoint=PUBLIC_LINKS["opcua"], namespace=native.META["namespace"], nodes=nodes),
    )


def mapping_payload(rt):
    """inventory/uns/ as JSON for the mapping page, with this site's live values: the native value its OPC UA
    server publishes now, and that value converted to the standard by the mapping's own formula."""
    import csv
    with open(os.path.join(UNS_DIR, "mapping.csv"), encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    with open(os.path.join(UNS_DIR, "sites.json"), encoding="utf-8") as f:
        overview = json.load(f)
    key, snap = C.SITE_CFG["key"], rt.snapshot
    live = {}
    if snap.get("tags"):
        values = engine_values(snap, rt.scan_count, rt.historian.stored)
        nodes = {(f"s={v['id']}" if isinstance(v["id"], str) else f"i={v['id']}"): v for v in native.table().variables if v["src"]}
        for r in rows:
            v = nodes.get(r[f"{key}.nodeid"])
            if v is None:
                continue
            raw = values.get(v["src"])
            n = native.encode(v, raw)
            live[r[f"{key}.nodeid"]] = dict(native=n, standard=to_standard(r[f"{key}.to_uns"], n),
                                            quality="BAD" if not snap["io_ok"] else "NOT_APPLICABLE" if raw is None else "GOOD")
    return dict(site=key, sites=overview["sites"], rows=rows, live=live, time=snap.get("time"))


def to_standard(formula, x):
    """Apply a mapping.csv conversion ("x * a + b", "x" or "map n→s, ...") to a native value."""
    if x is None:
        return None
    if formula.startswith("map "):
        table = dict(pair.split("→") for pair in formula[4:].split(", "))
        k = str(int(x)) if isinstance(x, bool) and str(int(x)) in table else str(x)
        return table.get(k, "true" if x is True else "false" if x is False else x)
    a, b = 1.0, 0.0
    m = re.fullmatch(r"x(?: \* ([-\d.e]+))?(?: ([+-]) ([\d.e]+))?", formula)
    if m is None or isinstance(x, (str, bool)):
        return x
    if m.group(1):
        a = float(m.group(1))
    if m.group(2):
        b = float(m.group(3)) * (1 if m.group(2) == "+" else -1)
    return float(x) * a + b


def config_payload(rt):
    s = C.SITE_CFG
    return dict(
        enterprise=C.ENTERPRISE, site=C.SITE, unit=C.UNIT, unit_label=C.UNIT_LABEL,
        site_info=dict(key=s["key"], name=s["site"], company=s["company"], country=s["country_name"],
                       timezone=s["timezone"], capacity=s["capacity"], scale=s["scale"], customers=s["customers"],
                       consumers=s["consumers"],
                       opcua_profile=native.PROFILE),
        sites=SITE_LINKS,
        blocks=[dict(id=k, label=v) for k, v in C.BLOCKS],
        tags=[{k: v for k, v in t.items()} for t in C.TAGS],
        equipment={k: dict(label=v["label"], block=v["block"], trip=v["trip"]) for k, v in C.EQUIPMENT.items()},
        interlocks={k: dict(label=v["label"], block=v["block"]) for k, v in C.INTERLOCKS.items()},
        lineup=C.LINEUP, links=PUBLIC_LINKS, scan_s=SCAN_SECONDS, ppu_cycle_s=rt.dcs.ppu.cycle,
        ppu_cycle_range=[C.PPU_CYCLE_MIN, C.PPU_CYCLE_MAX], opcua_namespace=native.META["namespace"], opcua_prefix=f"{C.ENTERPRISE}.{C.UNIT}",
        # Clausius-Clapeyron constants of the TY-411…413 calculations (normal boiling point K, J/mol),
        # so the trainer draws its boiling-point curves with the DCS's own formula.
        t_sat=dict(N2=list(T_SAT_N2), O2=list(T_SAT_O2)), calc_inputs=C.CALC_INPUTS,
    )


def make_handler(rt):
    dcs = rt.dcs

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if url.path in PAGES or url.path.rstrip("/") in PAGES:
                page = PAGES.get(url.path) or PAGES[url.path.rstrip("/")]
                with open(os.path.join(WEB_DIR, page), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if url.path == "/api/state":
                return self._send(200, rt.snapshot_json, "application/json")
            if url.path == "/api/config":
                return self._json(200, config_payload(rt))
            if url.path == "/api/history":
                names = [n for n in q.get("tags", [""])[0].split(",") if n]
                end = float(q.get("end", [time.time()])[0])
                start = float(q.get("start", [end - 60 * float(q.get("minutes", ["60"])[0])])[0])
                points = int(q.get("points", ["600"])[0])
                return self._json(200, dict(start=start, end=end, series={
                    n: rt.historian.trend(n, start, end, points) for n in names}))
            if url.path == "/api/events":
                limit = int(q.get("limit", ["300"])[0])
                start = float(q["start"][0]) if "start" in q else None
                return self._json(200, rt.historian.events(start=start, limit=limit))
            if url.path == "/api/historian":
                return self._json(200, dict(rt.historian.stats(), points=sorted(rt.historian.ids)))
            if url.path == "/api/mapping":
                # Every hub reads the other sites' live values from here: allow cross-origin reads.
                return self._json(200, mapping_payload(rt), cors=True)
            if url.path == "/api/mapping.csv":
                with open(os.path.join(UNS_DIR, "mapping.csv"), "rb") as f:
                    return self._send(200, f.read(), "text/csv; charset=utf-8")
            if url.path == "/api/datapath":
                path = datapath(rt, q.get("tag", [""])[0])
                return self._json(200, path) if path else self._json(404, {"error": "unknown tag"})
            self._json(404, {"error": "not found"})

        def do_POST(self):
            parts = urlparse(self.path).path.strip("/").split("/")
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            except ValueError:
                return self._json(400, {"error": "invalid JSON"})
            with dcs.lock:
                result = self._command(parts, body)
            if result is None:
                return self._json(404, {"error": "not found"})
            ok, msg = result
            self._json(200 if ok else 409, {"ok": ok, "message": msg})

        def _command(self, parts, body):
            if parts[:1] != ["api"]:
                return None
            p = parts[1:]
            if len(p) == 2 and p[0] == "loops" and p[1] in dcs.loops:
                allowed = {k: body[k] for k in ("mode", "sp", "op", "sp_hi", "sp_lo", "kc", "ti", "sp_rate", "ratio") if k in body}
                return dcs.set_loop(p[1], **allowed)
            if len(p) == 3 and p[0] == "equipment" and p[1] in dcs.equipment and p[2] in ("start", "stop"):
                return dcs.start_equipment(p[1]) if p[2] == "start" else dcs.stop_equipment(p[1])
            if len(p) == 3 and p[0] == "interlocks" and p[1] in dcs.interlocks and p[2] == "reset":
                return dcs.reset_interlock(p[1])
            if p == ["esd"]:
                return dcs.esd()
            if p == ["lineup"]:
                return dcs.lineup()
            if p == ["sequences", "KS-201", "cycle"]:
                # Set by the instructor station's time-compression slider (plant container).
                return dcs.set_ppu_cycle(body.get("seconds"), body.get("source", "engineer"))
            if p == ["sequences", "KS-201", "switch"]:
                return dcs.switch_beds()
            if p == ["alarms", "ack"]:
                n = dcs.ack(body.get("id"))
                return True, f"{n} acknowledged"
            return None

        def _json(self, code, obj, cors=False):
            self._send(code, json.dumps(obj).encode("utf-8"), "application/json", cors)

        def _send(self, code, body, ctype, cors=False):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if cors:
                self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return Handler


async def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    rt = Runtime()
    await rt.opcua.start()
    web = ThreadingHTTPServer(("0.0.0.0", WEB_PORT), make_handler(rt))
    threading.Thread(target=web.serve_forever, daemon=True).start()
    log.info("Operator console on port %d; scanning plant I/O at %s:%d every %ss",
             WEB_PORT, PLANT_HOST, PLANT_PORT, SCAN_SECONDS)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await rt.scan_loop(stop)
    web.shutdown()
    await rt.opcua.stop()
    log.info("DCS stopped")


if __name__ == "__main__":
    asyncio.run(main())
