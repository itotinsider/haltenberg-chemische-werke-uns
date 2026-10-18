"""HCW ASU1 plant simulator: process model + Modbus TCP field I/O + instructor station.

    process model (model.py)  <->  register bank  <->  Modbus TCP :5020  <->  DCS
                                                   +-> instructor station HTTP :8111

The plant runs in real time on its own clock, like a real process: the DCS samples it
asynchronously. Instructor scenarios are only reachable over HTTP here — nothing about them is
written to the field I/O, so the control system and everything behind it (historian, OPC UA,
an external agent) has to diagnose them from process values alone.
"""

import json
import logging
import os
import signal
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "common"))

import iomap  # noqa: E402
from model import SCENARIOS, Plant  # noqa: E402
from modbus_server import RegisterBank, serve  # noqa: E402

MODBUS_PORT = int(os.getenv("MODBUS_PORT", "5020"))
WEB_PORT = int(os.getenv("WEB_PORT", "8111"))
STEP_SECONDS = float(os.getenv("PLANT_STEP_SECONDS", "0.5"))
PPU_CYCLE_SECONDS = float(os.getenv("PPU_CYCLE_SECONDS", "14400"))
PPU_CYCLE_MIN, PPU_CYCLE_MAX = 300.0, 14400.0   # must match dcs/config.py
DCS_URL = os.getenv("DCS_URL", "http://dcs:8110")
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("plant")


class Runtime:
    """The plant plus its I/O rack, stepped by the main thread."""

    def __init__(self):
        self.lock = threading.Lock()   # guards the model against instructor HTTP threads
        self.plant = Plant(time.time(), ppu_cycle_s=PPU_CYCLE_SECONDS)
        self.bank = RegisterBank(iomap.DO_COUNT, iomap.DI_COUNT, iomap.AO_REGISTERS, iomap.AI_REGISTERS)
        # PPU cycle time (instructor time compression). The DCS's KS-201 sequence owns the
        # persisted value; the plant follows it, except while an instructor change is still
        # waiting to be pushed to a DCS that isn't reachable.
        self.cycle_pending = False
        self.dcs_cycle = None
        self.last_watchdog = None
        self.watchdog_changed = 0.0
        # Seed the holding registers / coils with the plant's design line-up, so the plant
        # holds steady state until a DCS connects and starts writing.
        with self.bank.lock:
            self.bank.holding[:] = iomap.pack_floats(iomap.ANALOG_OUTPUTS, self.plant.ao, iomap.AO_REGISTERS)
            for name, addr in iomap.DO_ADDR.items():
                self.bank.coils[addr] = bool(self.plant.do[name])

    def exchange(self):
        """Copy DCS outputs out of the register bank, publish measurements into it."""
        with self.bank.lock:
            ao = iomap.unpack_floats(iomap.ANALOG_OUTPUTS, self.bank.holding)
            do = {name: self.bank.coils[addr] for name, addr in iomap.DO_ADDR.items()}
        if ao["WD-900"] != self.last_watchdog:
            self.last_watchdog = ao["WD-900"]
            self.watchdog_changed = time.time()
        self.plant.ao.update(ao)
        self.plant.do.update(do)
        self.plant.heartbeat = (self.plant.heartbeat + 1) % 65536
        regs = iomap.pack_floats(iomap.ANALOG_INPUTS, self.plant.measurements(), iomap.AI_REGISTERS)
        di = self.plant.discretes()
        with self.bank.lock:
            self.bank.input[:] = regs
            for name, addr in iomap.DI_ADDR.items():
                self.bank.discrete[addr] = bool(di[name])

    def set_cycle(self, seconds):
        seconds = max(PPU_CYCLE_MIN, min(PPU_CYCLE_MAX, float(seconds)))
        with self.lock:
            self.plant.ppu_cycle = seconds
            self.cycle_pending = True
        self.sync_cycle()
        return seconds

    def sync_cycle(self):
        """Push a pending instructor change to the DCS, otherwise adopt the DCS's value."""
        try:
            if self.cycle_pending:
                body = json.dumps({"seconds": self.plant.ppu_cycle, "source": "instructor"}).encode()
                req = urllib.request.Request(DCS_URL + "/api/sequences/KS-201/cycle", data=body,
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=3) as r:
                    json.load(r)
                self.cycle_pending = False
                self.dcs_cycle = self.plant.ppu_cycle
                log.info("Instructor: PPU cycle %.0f s, pushed to DCS", self.plant.ppu_cycle)
            else:
                with urllib.request.urlopen(DCS_URL + "/api/config", timeout=3) as r:
                    self.dcs_cycle = float(json.load(r)["ppu_cycle_s"])
                with self.lock:
                    if not self.cycle_pending and abs(self.dcs_cycle - self.plant.ppu_cycle) >= 0.5:
                        log.info("PPU cycle %.0f s -> %.0f s (following DCS)", self.plant.ppu_cycle, self.dcs_cycle)
                        self.plant.ppu_cycle = self.dcs_cycle
        except Exception as e:  # DCS not up (yet): keep the local value, retry next round
            self.dcs_cycle = None
            log.debug("PPU cycle sync: %s", e)

    def sync_forever(self):
        while True:
            self.sync_cycle()
            time.sleep(5)

    def snapshot(self):
        with self.lock:
            snap = self.plant.snapshot()
            snap["measurements"] = {k: round(v, 4) for k, v in self.plant.measurements().items()}
            snap["discretes"] = self.plant.discretes()
        with self.bank.lock:
            snap["modbus"] = {"requests": self.bank.requests, "port": MODBUS_PORT}
        age = time.time() - self.watchdog_changed if self.last_watchdog is not None else None
        snap["ppu_cycle"] = dict(seconds=self.plant.ppu_cycle, dcs=self.dcs_cycle, pending=self.cycle_pending,
                                 min=PPU_CYCLE_MIN, max=PPU_CYCLE_MAX, design=PPU_CYCLE_MAX)
        snap["dcs_watchdog_age_s"] = None if age is None else round(age, 1)
        snap["io_map"] = {
            "input_registers": [dict(point=n, desc=d, address=a) for n, a, d in iomap.ANALOG_INPUTS],
            "holding_registers": [dict(point=n, desc=d, address=a) for n, a, d in iomap.ANALOG_OUTPUTS],
            "discrete_inputs": [dict(point=n, desc=d, address=a) for n, a, d in iomap.DISCRETE_INPUTS],
            "coils": [dict(point=n, desc=d, address=a) for n, a, d in iomap.COILS],
        }
        return snap


def make_handler(rt):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/instructor", "/instructor.html"):
                with open(os.path.join(WEB_DIR, "instructor.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif self.path == "/api/plant":
                self._json(200, rt.snapshot())
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            parts = self.path.strip("/").split("/")
            # /api/scenarios/<key>/start|stop
            if len(parts) == 4 and parts[:2] == ["api", "scenarios"] and parts[3] in ("start", "stop"):
                key, action = parts[2], parts[3]
                if key not in SCENARIOS:
                    return self._json(404, {"error": f"unknown scenario {key}"})
                with rt.lock:
                    if action == "start":
                        rt.plant.start_scenario(key)
                    else:
                        rt.plant.stop_scenario(key)
                log.info("Instructor: scenario %s %s", key, action)
                return self._json(200, {"ok": True})
            if parts == ["api", "ppu_cycle"]:
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    seconds = float(json.loads(self.rfile.read(length) or b"{}")["seconds"])
                except (ValueError, KeyError, TypeError):
                    return self._json(400, {"error": "body must be {\"seconds\": <number>}"})
                seconds = rt.set_cycle(seconds)
                return self._json(200, {"ok": True, "seconds": seconds, "pending": rt.cycle_pending})
            self._json(404, {"error": "not found"})

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


def main():
    rt = Runtime()
    serve(rt.bank, "0.0.0.0", MODBUS_PORT)
    log.info("Modbus TCP I/O rack on port %d (%d input regs, %d holding regs)", MODBUS_PORT,
             iomap.AI_REGISTERS, iomap.AO_REGISTERS)
    web = ThreadingHTTPServer(("0.0.0.0", WEB_PORT), make_handler(rt))
    threading.Thread(target=web.serve_forever, daemon=True).start()
    threading.Thread(target=rt.sync_forever, daemon=True).start()
    log.info("Instructor station on port %d", WEB_PORT)

    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    next_t = time.monotonic()
    while running:
        with rt.lock:
            rt.exchange()
            rt.plant.step(STEP_SECONDS)
        next_t += STEP_SECONDS
        delay = next_t - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            next_t = time.monotonic()   # fell behind (e.g. laptop sleep): don't try to catch up
    web.shutdown()
    log.info("Plant stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
