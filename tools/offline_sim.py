"""Run the plant model and the DCS control layer together, faster than real time, no I/O.

For tuning controllers and checking scenarios before building the containers:

    docker run --rm -v "$PWD":/w -w /w python:3.12-slim python tools/offline_sim.py \
        --hours 3 --scenario eo_demand_step@1800 --every 300

`--scenario key@t[:t_stop]` starts a plant scenario at t seconds (and stops it at t_stop);
`--cmd action@t` issues a DCS operator command (lineup, ack, reset:I-100, start:MAC,
start:EXP, switch, sp:FIC-101=120, mode:FIC-111=MAN, op:FIC-111=40, ratio:FIC-111=1.6).
"""

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "plant"), os.path.join(ROOT, "dcs"), os.path.join(ROOT, "common")]

from model import Plant  # noqa: E402
from control import DCS  # noqa: E402

COLUMNS = ["FIC-101", "JI-104", "UIC-103", "AI-202", "PIC-402", "LIC-403", "LIC-404", "FIC-405",
           "AIC-602", "AI-601", "FIC-501", "FIC-111", "TI-512", "PIC-502", "FIC-503", "PIC-504", "FIC-507",
           "KPI-701"]


def main():
    if hasattr(sys.stdout, "reconfigure"):   # Windows consoles can't print "→" / "°C" in cp1252
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--every", type=float, default=600.0, help="print interval, s")
    ap.add_argument("--cycle", type=float, default=14400.0)
    ap.add_argument("--scenario", action="append", default=[])
    ap.add_argument("--cmd", action="append", default=[])
    ap.add_argument("--cols", default=",".join(COLUMNS))
    args = ap.parse_args()
    cols = args.cols.split(",")

    t0 = time.time()
    plant = Plant(t0, ppu_cycle_s=args.cycle)
    dcs = DCS(ppu_cycle_s=args.cycle)
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
    t = 0.0
    next_print = 0.0
    while t <= args.hours * 3600:
        while sched and sched[0][0] <= t:
            _, kind, what = sched.pop(0)
            if kind == "start":
                plant.start_scenario(what)
            elif kind == "stop":
                plant.stop_scenario(what)
            else:
                run_cmd(dcs, what)
                if what.startswith("cycle:"):
                    plant.ppu_cycle = dcs.ppu.cycle
            print(f"   --- t={t/60:.1f} min: {kind} {what}")
        ao, do = dcs.scan(plant.measurements(), plant.discretes(), t0 + t)
        plant.ao.update(ao)
        plant.do.update(do)
        plant.step(0.5)
        plant.step(0.5)
        for e in dcs.events:
            if e["kind"] in ("trip", "sequence") or e["priority"] == 1:
                print(f"   [{(e['ts'] - t0) / 60:7.1f}] {e['kind']:9s} {e['source']:10s} {e['message']}")
        dcs.events.clear()
        if t >= next_print:
            snap = dcs.snapshot()
            row = []
            for c in cols:
                v = snap["tags"][c]["pv"]
                row.append(f"{v:8.3f}" if v is not None else "     ---")
            trips = [k for k, il in snap["interlocks"].items() if il["latched"]]
            print(f"{t/60:6.1f} " + " ".join(row) + f"  {snap['sequences']['KS-201']['display']} {trips or ''}")
            next_print += args.every
        t += 1.0


def run_cmd(dcs, what):
    if what == "lineup":
        print("     ", dcs.lineup())
    elif what == "ack":
        dcs.ack()
    elif what == "switch":
        print("     ", dcs.switch_beds())
    elif what.startswith("reset:"):
        print("     ", dcs.reset_interlock(what[6:]))
    elif what.startswith("start:"):
        print("     ", dcs.start_equipment(what[6:]))
    elif what.startswith("stop:"):
        print("     ", dcs.stop_equipment(what[5:]))
    elif what.startswith("sp:"):
        tag, val = what[3:].split("=")
        print("     ", dcs.set_loop(tag, sp=float(val)))
    elif what.startswith("cycle:"):
        print("     ", dcs.set_ppu_cycle(what[6:]))
    elif what.startswith("mode:"):
        tag, val = what[5:].split("=")
        print("     ", dcs.set_loop(tag, mode=val))
    elif what.startswith("op:"):
        tag, val = what[3:].split("=")
        print("     ", dcs.set_loop(tag, op=float(val)))
    elif what.startswith("ratio:"):
        tag, val = what[6:].split("=")
        print("     ", dcs.set_loop(tag, ratio=float(val)))


if __name__ == "__main__":
    main()
