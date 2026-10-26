"""Run the plant model and the DCS control layer together, faster than real time, no I/O.

For tuning controllers and checking scenarios before building the containers:

    docker run --rm -v "$PWD":/w -w /w python:3.12-slim python tools/offline_sim.py \
        --hours 3 --scenario eo_demand_step@1800 --every 300

`--scenario key@t[:t_stop]` starts a plant scenario at t seconds (and stops it at t_stop);
`--cmd action@t` issues a DCS operator command (lineup, ack, reset:I-100, start:MAC,
start:EXP, switch, sp:FIC-101=120, mode:FIC-111=MAN, op:FIC-111=40, ratio:FIC-111=1.6).
`--seed n` makes a run repeatable (the model's noise and demand wander are random).
`--site key` runs another site of the global demo (common/sites.py; default: HCW_SITE or haltenberg).

The `Sim` class is also the harness of the behaviour tests (tests/test_behaviour.py).
"""

import argparse
import importlib
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "plant"), os.path.join(ROOT, "dcs"), os.path.join(ROOT, "common")]

from model import Plant  # noqa: E402
from control import DCS  # noqa: E402
import config as C  # noqa: E402
import sites  # noqa: E402


def use_site(key):
    """Switch this process to another site: the DCS config is rebuilt for it, and new Plant objects
    follow HCW_SITE too. Returns the previous site key (tests switch back when done)."""
    old = sites.current_key()
    os.environ["HCW_SITE"] = key
    importlib.reload(C)
    return old

COLUMNS = ["FIC-101", "JI-104", "UIC-103", "AI-202", "PIC-402", "LIC-403", "LIC-404", "FIC-405",
           "AIC-602", "AI-601", "FIC-501", "FIC-111", "TI-512", "PIC-502", "FIC-503", "PIC-504", "FIC-507", "FIC-508",
           "KPI-701"]


class Window:
    """What happened during one Sim.run(): PV min/max per tag, alarms that were active, events."""

    def __init__(self):
        self.lo, self.hi = {}, {}
        self.alarms = set()      # alarm keys active at any scan, e.g. "PIC-502.L", "I-100.TRIP"
        self.events = []

    def min(self, tag):
        return self.lo.get(tag)

    def max(self, tag):
        return self.hi.get(tag)


class Sim:
    """The plant model and the DCS coupled directly: one 1 s DCS scan, two 0.5 s plant steps."""

    def __init__(self, seed=None, cycle=None, t0=None):
        if seed is not None:
            random.seed(seed)
        self.t0 = time.time() if t0 is None else t0
        self.t = 0.0
        cycle = C.SITE_CFG["ppu_cycle_s"] if cycle is None else cycle
        self.plant = Plant(self.t0, ppu_cycle_s=cycle, site=C.SITE_CFG)
        self.dcs = DCS(ppu_cycle_s=cycle)
        self.events = []         # every DCS event so far (ts relative to the start, s)

    def step(self, win=None):
        ao, do = self.dcs.scan(self.plant.measurements(), self.plant.discretes(), self.t0 + self.t)
        self.plant.ao.update(ao)
        self.plant.do.update(do)
        self.plant.step(0.5)
        self.plant.step(0.5)
        for e in self.dcs.events:
            e = dict(e, ts=e["ts"] - self.t0)
            self.events.append(e)
            if win:
                win.events.append(e)
        self.dcs.events.clear()
        if win:
            for tag, v in self.dcs.values.items():
                if v is not None and tag in C.TAG_INDEX:
                    if v < win.lo.get(tag, v + 1):
                        win.lo[tag] = v
                    if v > win.hi.get(tag, v - 1):
                        win.hi[tag] = v
            win.alarms.update(k for k, a in self.dcs.alarms.items() if a.active)
        self.t += 1.0

    def run(self, seconds, until=None, win=None):
        """Advance `seconds` (or until `until(sim)` is true) and return a Window over that span
        (or keep filling `win`, so `until` can look at what happened so far)."""
        win = win or Window()
        end = self.t + seconds
        while self.t < end:
            self.step(win)
            if until and until(self):
                break
        return win

    # ---- convenience ----
    def pv(self, tag):
        return self.dcs.values.get(tag)

    def loop(self, tag):
        return self.dcs.loops[tag]

    def tripped(self):
        return {k for k, il in self.dcs.interlocks.items() if il.latched}

    def state(self, eq):
        e = self.dcs.equipment[eq]
        return e.state(self.dcs.interlocks[e.cfg["trip"]].latched)

    def scenario(self, key, start=True):
        (self.plant.start_scenario if start else self.plant.stop_scenario)(key)

    def cmd(self, what):
        """An operator command in the --cmd syntax; returns the DCS's (ok, message)."""
        res = run_cmd(self.dcs, what)
        if what.startswith("cycle:"):
            self.plant.ppu_cycle = self.dcs.ppu.cycle
        return res


def run_cmd(dcs, what):
    if what == "lineup":
        return dcs.lineup()
    if what == "ack":
        return True, f"{dcs.ack()} acknowledged"
    if what == "switch":
        return dcs.switch_beds()
    if what == "esd":
        return dcs.esd()
    if what.startswith("reset:"):
        return dcs.reset_interlock(what[6:])
    if what.startswith("start:"):
        return dcs.start_equipment(what[6:])
    if what.startswith("stop:"):
        return dcs.stop_equipment(what[5:])
    if what.startswith("cycle:"):
        return dcs.set_ppu_cycle(what[6:])
    for prefix, field, conv in (("sp:", "sp", float), ("mode:", "mode", str), ("op:", "op", float),
                                ("ratio:", "ratio", float)):
        if what.startswith(prefix):
            tag, val = what[len(prefix):].split("=")
            return dcs.set_loop(tag, **{field: conv(val)})
    raise ValueError(f"unknown command {what!r}")


def main():
    if hasattr(sys.stdout, "reconfigure"):   # Windows consoles can't print "→" / "°C" in cp1252
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--every", type=float, default=600.0, help="print interval, s")
    ap.add_argument("--cycle", type=float, default=None, help="PPU bed cycle, s (default: the site's)")
    ap.add_argument("--site", default=None, choices=list(sites.SITES))
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--scenario", action="append", default=[])
    ap.add_argument("--cmd", action="append", default=[])
    ap.add_argument("--cols", default=",".join(COLUMNS))
    args = ap.parse_args()
    if args.site:
        use_site(args.site)
    cols = [c for c in args.cols.split(",") if c in C.TAG_INDEX]

    sim = Sim(seed=args.seed, cycle=args.cycle)
    sched = []
    for s in args.scenario:
        key, when = s.split("@")
        start, _, stop = when.partition(":")
        sched.append((float(start), "start", key))
        if stop:
            sched.append((float(stop), "stop", key))
    for c in args.cmd:
        action, when = c.rsplit("@", 1)
        sched.append((float(when), "cmd", action))
    sched.sort()

    print("t[min] " + " ".join(f"{c:>8s}" for c in cols) + "  KS-201 / trips")
    next_print = 0.0
    while sim.t <= args.hours * 3600:
        while sched and sched[0][0] <= sim.t:
            _, kind, what = sched.pop(0)
            print(f"   --- t={sim.t/60:.1f} min: {kind} {what}")
            if kind == "cmd":
                print("     ", sim.cmd(what))
            else:
                sim.scenario(what, kind == "start")
        n = len(sim.events)
        sim.step()
        for e in sim.events[n:]:
            if e["kind"] in ("trip", "sequence") or e["priority"] == 1:
                print(f"   [{e['ts'] / 60:7.1f}] {e['kind']:9s} {e['source']:10s} {e['message']}")
        if sim.t - 1.0 >= next_print:
            snap = sim.dcs.snapshot()
            row = []
            for c in cols:
                v = snap["tags"][c]["pv"]
                row.append(f"{v:8.3f}" if v is not None else "     ---")
            trips = sorted(sim.tripped())
            print(f"{(sim.t - 1.0)/60:6.1f} " + " ".join(row) + f"  {snap['sequences']['KS-201']['display']} {trips or ''}")
            next_print += args.every


if __name__ == "__main__":
    main()
