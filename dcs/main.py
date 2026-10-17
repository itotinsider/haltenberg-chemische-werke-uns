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
from control import DCS  # noqa: E402
from historian import Historian  # noqa: E402
from opcua_server import OpcUaServer  # noqa: E402

PLANT_HOST = os.getenv("PLANT_HOST", "plant")
PLANT_PORT = int(os.getenv("PLANT_PORT", "5020"))
SCAN_SECONDS = float(os.getenv("SCAN_SECONDS", "1"))
WEB_PORT = int(os.getenv("WEB_PORT", "8110"))
OPCUA_PORT = int(os.getenv("OPCUA_PORT", "4840"))
PPU_CYCLE_SECONDS = float(os.getenv("PPU_CYCLE_SECONDS", "14400"))
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
    "opcua": os.getenv("PUBLIC_OPCUA_ENDPOINT", f"opc.tcp://localhost:{OPCUA_PORT}/hcw/asu1/"),
    "modbus": os.getenv("PUBLIC_MODBUS_ENDPOINT", "localhost:5020"),
    "nodered": os.getenv("PUBLIC_NODERED_URL", "http://localhost:1880"),
}
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
PAGES = {"/": "index.html", "/operator": "operator.html"}

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
        out[f"{t['tag']}.PV"] = row["pv"]
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
        self.opcua = OpcUaServer(self.historian, port=OPCUA_PORT)
        self.snapshot = {}
        self.snapshot_json = b"{}"
        self.scan_count = 0
        self.scan_ms = 0.0
        self.io = {"connected": False, "last_heartbeat": None, "heartbeat_changed": 0.0, "errors": 0}
        self.watchdog = 0
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
        rr = await client.read_input_registers(0, count=iomap.AI_REGISTERS)
        dr = await client.read_discrete_inputs(0, count=len(iomap.DISCRETE_INPUTS))
        if rr.isError() or dr.isError():
            raise IOError(f"Modbus read error: {rr} / {dr}")
        ai = iomap.unpack_floats(iomap.ANALOG_INPUTS, rr.registers)
        di = {name: bool(dr.bits[addr]) for name, addr in iomap.DI_ADDR.items()}
        return ai, di

    async def write_io(self, client, ao, do):
        self.watchdog = (self.watchdog + 1) % 65536
        ao = dict(ao, **{"WD-900": float(self.watchdog)})
        await client.write_registers(0, iomap.pack_floats(iomap.ANALOG_OUTPUTS, ao))
        await client.write_coils(0, [bool(do.get(name, False)) for name, _ in iomap.COILS])

    async def scan_loop(self, stop):
        client = AsyncModbusTcpClient(PLANT_HOST, port=PLANT_PORT, timeout=2, retries=0)
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
                if self.io["connected"]:
                    log.warning("Field I/O read failed: %s", e)
                client.close()
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

            self.historian.store(now, historian_values(snap), events)
            self.scan_count += 1
            await self.opcua.update(snap, now, self.scan_count, self.historian.stored)

            self.scan_ms = (time.monotonic() - t_start) * 1000.0
            snap["scan"] = dict(count=self.scan_count, ms=round(self.scan_ms, 1), period_s=SCAN_SECONDS)
            snap["io"] = dict(self.io, plant=f"{PLANT_HOST}:{PLANT_PORT}")
            snap["historian"] = dict(samples_written=self.historian.stored)
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


def config_payload(rt):
    return dict(
        enterprise=C.ENTERPRISE, site=C.SITE, unit=C.UNIT, unit_label=C.UNIT_LABEL,
        blocks=[dict(id=k, label=v) for k, v in C.BLOCKS],
        tags=[{k: v for k, v in t.items()} for t in C.TAGS],
        equipment={k: dict(label=v["label"], block=v["block"], trip=v["trip"]) for k, v in C.EQUIPMENT.items()},
        interlocks={k: dict(label=v["label"], block=v["block"]) for k, v in C.INTERLOCKS.items()},
        lineup=C.LINEUP, links=PUBLIC_LINKS, scan_s=SCAN_SECONDS, ppu_cycle_s=rt.dcs.ppu.cycle,
        ppu_cycle_range=[C.PPU_CYCLE_MIN, C.PPU_CYCLE_MAX], opcua_namespace="urn:haltenberg:hcw:asu1:dcs", opcua_prefix=f"{C.ENTERPRISE}.{C.UNIT}",
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

        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
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
