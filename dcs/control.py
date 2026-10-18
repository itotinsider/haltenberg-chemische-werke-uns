"""Control layer of the HCW ASU1 DCS: controllers, alarms, interlocks, equipment, sequences.

Pure logic — no I/O, no clocks, no threads. `DCS.scan(ai, di, now, io_ok)` takes one snapshot
of the field inputs and returns the field outputs; everything else (Modbus, historian, OPC UA,
HTTP) lives in main.py. That keeps the control layer runnable offline against the plant model
(tools/offline_sim.py) for tuning, much faster than real time.

Callers must hold `DCS.lock` around scan() and every command — the HTTP server threads issue
commands while the scan loop runs.
"""

import math
import threading

import config as C

MODE_CODE = {"MAN": 0, "AUTO": 1, "CAS": 2}


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def t_sat(p_bar, t_boil_k, dh_vap):
    """Saturation temperature (degC) at p_bar(a): Clausius-Clapeyron around the normal boiling point.

    The DCS's own copy of the engineering formula (same constants as the plant model), so that the
    calculated boiling points are a fair reference for the measured column temperatures."""
    if p_bar is None or p_bar <= 0.05:
        return None
    inv = 1.0 / t_boil_k - 8.314 / dh_vap * math.log(p_bar / 1.013)
    return round(1.0 / inv - 273.15, 4)


T_SAT_N2 = (77.36, 5570.0)   # normal boiling point K, heat of vaporisation J/mol
T_SAT_O2 = (90.19, 6820.0)


class PID:
    """Velocity-form PI controller with MAN/AUTO/CAS, SP ramp, SP tracking and output limits.

    Velocity form gives bumpless mode changes and anti-windup for free: the output is the
    integrated sum of increments, clamped every scan, so it can't wind up beyond its limits.
    Error is normalised to % of PV span, so Kc is dimensionless (% output per % PV).
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.tag = cfg["tag"]
        self.mode = cfg["mode"]
        self.sp = cfg["sp"]
        self.target = cfg["sp"]
        self.op = cfg["op"]
        self.kc = cfg["kc"]
        self.ti = cfg["ti"]
        self.sp_lo = cfg["sp_lo"]
        self.sp_hi = cfg["sp_hi"]
        self.sp_rate = cfg["sp_rate"]     # engineering units per minute, None = step
        self.ratio = cfg.get("ratio")     # ratio station: CAS setpoint = ratio x PV(ratio_of)
        self.pv = None
        self.e_prev = None
        self.rsp = None                   # remote SP written by a cascade master
        self.op_hi_eff = cfg["op_hi"]     # after override limiting
        self.limited = False
        self.sp_lim = None                # SP cap: cross-limit partner or sp_cap tag (None = none)
        self.lim_by = None                # tag that sets sp_lim
        self.xlimited = False
        self.forced = False               # held by an interlock

    def error(self):
        span = self.cfg["hi"] - self.cfg["lo"]
        e = (self.sp - self.pv) / span * 100.0
        return -e if self.cfg["action"] == "direct" else e

    def track_pv(self):
        if self.pv is not None:
            self.sp = self.target = clamp(self.pv, self.sp_lo, self.sp_hi)

    def update(self, dt):
        if self.pv is None:
            self.e_prev = None
            return
        if self.mode == "CAS" and self.rsp is not None:
            self.target = self.sp = clamp(self.rsp, self.sp_lo, self.sp_hi)
        elif self.mode == "MAN":
            self.track_pv()
        if self.mode != "MAN" and self.sp != self.target:
            if self.sp_rate:
                step = self.sp_rate / 60.0 * dt
                self.sp += clamp(self.target - self.sp, -step, step)
            else:
                self.sp = self.target
        self.xlimited = self.mode != "MAN" and self.sp_lim is not None and self.sp > self.sp_lim
        if self.xlimited:
            self.sp = self.sp_lim
        e = self.error()
        if self.mode == "MAN" or self.e_prev is None:
            self.e_prev = e
            return
        dop = self.kc * ((e - self.e_prev) + e * dt / max(self.ti, 0.1))
        self.e_prev = e
        self.op = clamp(self.op + dop, self.cfg["op_lo"], self.op_hi_eff)

    def initialise(self, op):
        """Back-calculated output while a cascade slave is not accepting our setpoint."""
        self.op = clamp(op, self.cfg["op_lo"], self.cfg["op_hi"])
        self.e_prev = None


class Alarm:
    def __init__(self, key, tag, limit, setpoint, priority, message):
        self.key = key
        self.tag = tag
        self.limit = limit
        self.setpoint = setpoint
        self.priority = priority
        self.message = message
        self.active = False
        self.acked = True
        self.since = None
        self.value = None

    def as_dict(self):
        return dict(id=self.key, tag=self.tag, limit=self.limit, setpoint=self.setpoint,
                    priority=self.priority, message=self.message, active=self.active,
                    acked=self.acked, since=self.since, value=self.value)


class Equipment:
    def __init__(self, key, cfg):
        self.key = key
        self.cfg = cfg
        self.cmd = True
        self.fb = True
        self.running_since = 0.0
        self.cmd_since = 0.0

    def state(self, trip_latched):
        if self.cmd and self.fb:
            return "RUNNING"
        if self.cmd:
            return "STARTING"
        if self.fb:
            return "STOPPING"
        return "TRIPPED" if trip_latched else "STOPPED"


class Interlock:
    def __init__(self, key, cfg):
        self.key = key
        self.cfg = cfg
        self.latched = False
        self.first_out = None
        self.tripped_at = None
        self.active_causes = []


class PPUSequence:
    """KS-201: two-bed TSA switching. One bed online, the other through
    DEPRESS -> HEAT -> COOL -> REPRESS -> STANDBY; switch-over when the online time is up and the
    offline bed is in STANDBY (permissive), otherwise hold and alarm."""

    def __init__(self, cycle_s):
        self.cycle = cycle_s
        self.online = "A"
        self.online_time = 0.0
        self.step = 0
        self.step_time = 0.0
        self.heat_peak = 0.0
        self.regen_incomplete = False
        self.switches = 0

    @property
    def offline(self):
        return "B" if self.online == "A" else "A"

    @property
    def step_name(self):
        return C.PPU_STEPS[self.step][0]

    def step_duration(self):
        frac = C.PPU_STEPS[self.step][1]
        return None if frac is None else frac * self.cycle

    def display(self):
        return f"{self.online}-ONLINE / {self.offline}-{self.step_name}"


class DCS:
    def __init__(self, ppu_cycle_s=14400.0, scan_s=1.0):
        self.lock = threading.RLock()
        self.scan_s = scan_s
        self.loops = {t["tag"]: PID(t) for t in C.TAGS if t["kind"] == "PID"}
        self.values = {}            # tag -> PV (float) or None (bad quality)
        self.io_ok = False
        self.io_bad_since = None
        self.now = 0.0
        self.events = []            # drained by the historian (event journal)
        self.equipment = {k: Equipment(k, cfg) for k, cfg in C.EQUIPMENT.items()}
        self.interlocks = {k: Interlock(k, cfg) for k, cfg in C.INTERLOCKS.items()}
        self.ppu = PPUSequence(ppu_cycle_s)
        self.co2_high_since = None
        self.ao = {}
        self.alarms = {}
        for t in C.TAGS:
            for limit, setpoint in t["alarms"].items():
                key = f"{t['tag']}.{limit}"
                prio = C.PRIORITY_OVERRIDE.get((t["tag"], limit), C.ALARM_PRIORITY[limit])
                self.alarms[key] = Alarm(key, t["tag"], limit, setpoint, prio, f"{t['desc']} {limit}")
        for key, msg, prio in [
            *[(f"{k}.TRIP", cfg.get("trip_message", cfg["label"]), 1) for k, cfg in C.INTERLOCKS.items()],
            ("KS-201.SWITCH_DELAYED", "PPU switch-over delayed — offline bed not ready", 2),
            ("KS-201.REGEN_INCOMPLETE", "PPU regeneration incomplete — heat peak not reached", 2),
            ("IO.COMM", "Field I/O communication failure", 1),
        ]:
            self.alarms[key] = Alarm(key, key.split(".")[0], "DISC", None, prio, msg)
        # Scan order: cascade masters, then override controllers, then everything else.
        loops = list(self.loops.values())
        self.order = ([p for p in loops if p.cfg["cascade"]] + [p for p in loops if p.cfg["limits"]]
                      + [p for p in loops if not p.cfg["cascade"] and not p.cfg["limits"]])
        self.limiters = {p.cfg["limits"]: p for p in loops if p.cfg["limits"]}
        # Trip handover pairs, reversed: slave -> split-range master (see _handback).
        self.handback = {s: m for il in C.INTERLOCKS.values() for m, s in il.get("handover", {}).items()}
        self._seq_entered = None

    # ---------------------------------------------------------------- helpers
    def event(self, kind, source, message, priority=0):
        self.events.append(dict(ts=self.now, kind=kind, source=source, message=message, priority=priority))

    def running(self, eq):
        return self.equipment[eq].fb and self.equipment[eq].cmd

    def armed(self, eq):
        """True once `eq` has run longer than its start-up bypass (interlock causes armed on it)."""
        e = self.equipment[eq]
        return self.running(eq) and self.now - e.running_since > e.cfg.get("bypass_s", 0)

    def mac_armed(self):
        return self.armed("MAC")

    def pv(self, tag):
        return self.values.get(tag)

    # ---------------------------------------------------------------- scan
    def scan(self, ai, di, now, io_ok=True):
        dt = self.scan_s if not self.now else clamp(now - self.now, 0.05, 5.0)
        self.now = now
        self.io_ok = io_ok
        if io_ok:
            self.io_bad_since = None
        elif self.io_bad_since is None:
            self.io_bad_since = now

        # 1. equipment feedback
        for eq in self.equipment.values():
            fb = bool(di.get(eq.cfg["fb"], False)) if io_ok else eq.fb
            if fb and not eq.fb:
                eq.running_since = now
                self.event("equipment", eq.key, f"{eq.cfg['label']} running")
            elif eq.fb and not fb:
                self.event("equipment", eq.key, f"{eq.cfg['label']} stopped", 2 if eq.cmd else 0)
                if eq.cmd:   # stopped by itself while commanded on: motor protection / trip
                    self._trip(eq.cfg["trip"], f"{eq.cfg['fb']} {eq.cfg['label']} stopped (motor protection)")
            eq.fb = fb

        # 2. PVs (bad quality = None when the I/O link is down)
        for t in C.TAGS:
            if t["kind"] == "CALC":
                continue
            if t.get("io"):
                self.values[t["tag"]] = round(ai[t["io"]], 6) if io_ok and t["io"] in ai else None
        self.calc()
        for p in self.loops.values():
            p.pv = self.values.get(p.tag)

        # 3. sequences and interlocks (before control, so forced outputs apply this scan)
        self.run_ppu(dt)
        self.run_interlocks()

        # 4. control
        if io_ok:
            for p in self.order:
                if p.cfg["limits"]:
                    target = self.loops[p.cfg["limits"]]
                    p.update(dt)
                    # External reset: an override that isn't selected stays just above the
                    # controller it limits, so it takes over without first unwinding from 100 %.
                    if p.mode != "MAN":
                        p.op = min(p.op, target.op + 5.0)
                    continue
                if p.cfg.get("ratio_of"):
                    # Lead on increase: follow the partner's demand (its SP) or its flow, whichever is higher.
                    w, partner = self.pv(p.cfg["ratio_of"]), self.loops[p.cfg["ratio_of"]]
                    demand = partner.rsp if partner.mode == "CAS" and partner.rsp is not None else partner.target
                    p.rsp = None if w is None else p.ratio * max(w, demand if partner.mode != "MAN" else w)
                if p.cfg.get("xlimit") or p.cfg.get("sp_cap"):
                    lims = []
                    if p.cfg.get("xlimit"):
                        # Follow on increase: never ask for more than the measured ratio flow can handle.
                        # 4 % margin: wider than the flow noise (no LIM flicker in steady state), narrow
                        # enough that the real ratio stays above where E-301 starts to pinch (~95 %).
                        b, partner = self.pv(p.cfg["xlimit"]), self.loops[p.cfg["xlimit"]]
                        if b is not None and partner.ratio:
                            lims.append((b / (partner.ratio * 0.96), p.cfg["xlimit"]))
                    cap = self.pv(p.cfg["sp_cap"]) if p.cfg.get("sp_cap") else None
                    if cap is not None:
                        lims.append((max(cap, 0.0), p.cfg["sp_cap"]))
                    p.sp_lim, p.lim_by = min(lims) if lims else (None, None)
                lim = self.limiters.get(p.tag)
                p.op_hi_eff = min(p.cfg["op_hi"], lim.op) if lim and lim.mode != "MAN" else p.cfg["op_hi"]
                # Loops flagged `ppu_time` (TIC-204) run on the PPU's compressed clock, matching
                # the plant's regeneration circuit when the instructor speeds the cycle up.
                p.update(dt * C.PPU_DESIGN_CYCLE / self.ppu.cycle if p.cfg.get("ppu_time") else dt)
                p.limited = (p.mode != "MAN" and p.op >= p.op_hi_eff - 0.05 and p.op_hi_eff < p.cfg["op_hi"]) or p.xlimited
                if p.cfg["cascade"]:
                    self._drive_slaves(p)
        for il in self.interlocks.values():
            if il.latched:
                for tag, (mode, op) in il.cfg["forces"].items():
                    loop = self.loops[tag]
                    loop.mode, loop.op, loop.forced = mode, op, True

        # 5. alarms
        self.run_alarms()

        # 6. outputs
        for p in self.loops.values():
            if p.cfg["out"]:
                self.ao[p.cfg["out"]] = round(p.op, 3)
        do = {eq.cfg["cmd"]: eq.cmd for eq in self.equipment.values()}
        do["HS-201"] = self.ppu.online == "B"
        return dict(self.ao), do

    def calc(self):
        v = self.values
        ft103, pt102 = v.get("FI-103"), v.get("PIC-102")
        if ft103 is None or pt102 is None:
            v["surge_margin"] = None
        elif self.running("MAC"):
            # Configured surge line of the antisurge controller: 105 kNm3/h at 5.75 bar(a).
            v["surge_margin"] = round((ft103 / (105.0 * pt102 / 5.75) - 1.0) * 100.0, 3)
        else:
            v["surge_margin"] = 50.0
        v["UIC-103"] = v["surge_margin"]
        t407, t406 = v.get("TI-407"), v.get("TI-406")
        v["TDI-410"] = None if t407 is None or t406 is None else round(t407 - t406, 4)
        p_hp, p_lp = v.get("PI-401"), v.get("PIC-402")
        v["TY-411"] = t_sat(p_hp, *T_SAT_N2)
        v["TY-412"] = t_sat(p_lp, *T_SAT_O2)
        v["TY-413"] = t_sat(p_lp, *T_SAT_N2)
        # GOX load limit: the oxygen the air brings in (0.208 ≈ 21 % O2 x ~99.3 % recovery at the
        # purity edge), less what goes to storage as LOX, plus an allowance for liquid already standing
        # in an over-full sump (e.g. after a P-501 stop): that oxygen is separated, pumping it costs no purity.
        f101, f408, l404 = v.get("FIC-101"), v.get("FI-408"), v.get("LIC-404")
        # Clamped to its range: negative with the MAC stopped, and the allowance would otherwise grow
        # past FIC-501's range when the sump floods (no effect on control: FIC-501's SP stops at 31.5).
        fy = C.TAG_INDEX["FY-501"]
        v["FY-501"] = (round(clamp(C.GOX_AIR_LIMIT * f101 - f408 + C.GOX_SUMP_ALLOWANCE * max(0.0, l404 - self.loops["LIC-404"].sp),
                                   fy["lo"], fy["hi"]), 4)
                       if None not in (f101, f408, l404) else None)
        # Per-unit-of-GOX figures only mean something while P-501 delivers real product. Outside that
        # (trip coast-down, start-up) they are "not applicable" (None -> BadOutOfService on OPC UA),
        # instead of dividing by a vanishing flow (it gave 5 kWh/Nm³ and ratios up to 12).
        f111, f501 = v.get("FIC-111"), v.get("FIC-501")
        producing = f501 is not None and f501 >= C.PER_GOX_MIN_FLOW and self.running("LOXP")
        v["FFI-111"] = round(f111 / f501, 4) if producing and f111 is not None else None
        # Specific power: the ASU's compressor power (MAC + booster + GAN compressor) per unit of GOX.
        p, pb, pg = v.get("JI-104"), v.get("JI-113"), v.get("JI-514")
        v["KPI-701"] = round((p + pb + pg) / f501, 5) if producing and None not in (p, pb, pg) else None

    def _drive_slaves(self, master):
        links = master.cfg["cascade"]
        slaves = [(self.loops[l["slave"]], l) for l in links]
        if master.mode != "MAN" and not any(s.mode == "CAS" for s, _ in slaves):
            # Nobody accepts our output: initialise from the first slave's SP (bumpless return).
            s, l = slaves[0]
            sp_lo, sp_hi = l["sp"][0], (s.sp_hi if l["sp"][1] == "sp_hi" else l["sp"][1])
            frac = (s.sp - sp_lo) / max(sp_hi - sp_lo, 1e-9)
            master.initialise(l["op"][0] + clamp(frac, 0, 1) * (l["op"][1] - l["op"][0]))
            return
        for s, l in slaves:
            op_lo, op_hi = l["op"]
            sp_lo, sp_hi = l["sp"][0], (s.sp_hi if l["sp"][1] == "sp_hi" else l["sp"][1])
            frac = clamp((master.op - op_lo) / (op_hi - op_lo), 0.0, 1.0)
            s.rsp = sp_lo + frac * (sp_hi - sp_lo)

    # ---------------------------------------------------------------- PPU sequence KS-201
    def run_ppu(self, dt):
        q = self.ppu
        if self._seq_entered is None:
            self._seq_entered = q.step
            self._enter_step()
        if not (self.io_ok and self.running("MAC")):
            return   # no air: the sequence holds
        q.online_time += dt
        q.step_time += dt
        if q.step_name == "HEAT":
            t204 = self.pv("TIC-204")
            if t204 is not None:
                q.heat_peak = max(q.heat_peak, t204)
        dur = q.step_duration()
        if dur is not None and q.step_time >= dur:
            if q.step_name == "HEAT":
                q.regen_incomplete = q.heat_peak < C.REGEN_PEAK_MIN
                self.event("sequence", "KS-201", f"Bed {q.offline} heat step done, peak {q.heat_peak:.0f} °C"
                           + (" — REGENERATION INCOMPLETE" if q.regen_incomplete else ""),
                           2 if q.regen_incomplete else 0)
            q.step += 1
            q.step_time = 0.0
            self._enter_step()
        if q.online_time >= q.cycle and q.step_name == "STANDBY":
            self.switch_beds(auto=True)

    def _enter_step(self):
        q = self.ppu
        tic = self.loops["TIC-204"]
        if q.step_name == "HEAT":
            q.heat_peak = 0.0
            tic.mode, tic.target, tic.sp = "AUTO", C.REGEN_SP, C.REGEN_SP
            tic.e_prev = None
        else:
            tic.mode, tic.op = "MAN", 0.0
        self.event("sequence", "KS-201", f"Bed {q.offline}: {q.step_name}")

    def set_ppu_cycle(self, seconds, source="engineer"):
        """Change the KS-201 bed cycle on the fly. The elapsed online/step times are rescaled so
        the sequence keeps its place (half-way through HEAT stays half-way through HEAT)."""
        q = self.ppu
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            return False, "cycle must be a number of seconds"
        if not C.PPU_CYCLE_MIN <= seconds <= C.PPU_CYCLE_MAX:
            return False, f"cycle must be {C.PPU_CYCLE_MIN:.0f}…{C.PPU_CYCLE_MAX:.0f} s"
        if abs(seconds - q.cycle) < 0.5:
            return True, "no change"
        f = seconds / q.cycle
        q.online_time *= f
        q.step_time *= f
        old, q.cycle = q.cycle, seconds
        self.event("operator", "KS-201", f"Bed cycle time {old:.0f} s → {seconds:.0f} s ({source})")
        return True, f"cycle {seconds:.0f} s"

    def switch_beds(self, auto=False):
        q = self.ppu
        if not auto and q.step_name != "STANDBY" and self.running("MAC"):
            return False, f"Bed {q.offline} is in {q.step_name}, not STANDBY"
        q.online = q.offline
        q.online_time = 0.0
        q.step = 0
        q.step_time = 0.0
        q.switches += 1
        self.event("sequence", "KS-201", f"Switch-over: bed {q.online} online" + ("" if auto else " (operator)"))
        self._enter_step()
        return True, "ok"

    # ---------------------------------------------------------------- interlocks
    def _limit_active(self, tag, limit):
        a = self.alarms.get(f"{tag}.{limit}")
        v = self.pv(tag)
        if a is None or v is None:
            return False
        return v >= a.setpoint if limit in ("H", "HH") else v <= a.setpoint

    def run_interlocks(self):
        if not self.io_ok:
            return
        co2 = self.pv("AI-202")
        if co2 is not None and co2 >= 1.0 and self.running("MAC"):
            self.co2_high_since = self.co2_high_since or self.now
        else:
            self.co2_high_since = None
        for key, il in self.interlocks.items():
            causes = []
            for tag, limit, armed in il.cfg["causes"]:
                if armed != "always" and not self.armed(armed):
                    continue
                if self._limit_active(tag, limit):
                    causes.append(f"{tag} {limit} ({self.pv(tag):.{C.TAG_INDEX[tag]['dec']}f})")
            if key == "I-100" and self.co2_high_since and self.now - self.co2_high_since > C.CO2_BREAKTHROUGH_TRIP_S:
                causes.append("AI-202 > 1 ppm for 30 min (beds not switched)")
            for parent in il.cfg.get("parents", []):
                if self.interlocks[parent].latched:
                    causes.append(self._parent_cause(parent))
            il.active_causes = causes
            if causes and not il.latched:
                self._trip(key, causes[0])

    def _trip(self, key, cause):
        il = self.interlocks[key]
        if il.latched:
            return
        il.latched = True
        il.first_out = cause
        il.tripped_at = self.now
        self.event("trip", key, f"{il.cfg['label']} — first out: {cause}", 1)
        for eq in il.cfg["stops"]:
            self.equipment[eq].cmd = False
        for master, slave in il.cfg.get("handover", {}).items():
            self._handover(master, slave)
        for tag, (mode, op) in il.cfg["forces"].items():
            loop = self.loops[tag]
            loop.mode, loop.op, loop.forced = mode, op, True
        for child, cil in self.interlocks.items():   # I-100 -> I-300 / I-110 -> I-500, ...
            if key in cil.cfg.get("parents", []):
                self._trip(child, self._parent_cause(key))

    def _handover(self, master_tag, slave_tag):
        """Trip feed-forward: the supply behind `slave_tag` just tripped. Move the split-range master's
        OP straight to where its backup slave's SP equals the lost flow, instead of waiting for the
        header pressure to fall far enough for the PI to get there. The PI trims from that point."""
        master = self.loops[master_tag]
        flow = self.pv(slave_tag)
        if master.mode == "MAN" or not flow:
            return
        for link in master.cfg["cascade"]:
            if link["slave"] == slave_tag:
                continue
            backup = self.loops[link["slave"]]
            sp_lo, sp_hi = link["sp"][0], (backup.sp_hi if link["sp"][1] == "sp_hi" else link["sp"][1])
            op_lo, op_hi = link["op"]
            op = op_lo + clamp((flow - sp_lo) / max(sp_hi - sp_lo, 1e-9), 0.0, 1.0) * (op_hi - op_lo)
            if op > master.op:
                master.op, master.e_prev = op, None
                self.event("trip", master_tag, f"Trip handover: {slave_tag} {flow:.1f} → {link['slave']} "
                           f"(OP {op:.1f} %)")
            return

    def _handback(self, slave_tag):
        """The reverse of _handover, when a restarted supply's flow loop goes back to CAS. While it was
        down (or in MAN at its start output) the master's OP drifted to wherever the backup alone, or
        backup + start flow, held the pressure: taken as is, that hands the slave an arbitrary SP
        (e.g. full range from the backup half) and bumps the header. Re-initialise the master so this
        slave's SP is the total flow all its slaves deliver now; the backup then winds down as the
        slave takes over."""
        master_tag = self.handback.get(slave_tag)
        if not master_tag:
            return
        master = self.loops[master_tag]
        link = next(l for l in master.cfg["cascade"] if l["slave"] == slave_tag)
        op_lo, op_hi = link["op"]
        if master.mode == "MAN":
            return
        total = sum(self.pv(l["slave"]) or 0.0 for l in master.cfg["cascade"])
        s = self.loops[slave_tag]
        sp_lo, sp_hi = link["sp"][0], (s.sp_hi if link["sp"][1] == "sp_hi" else link["sp"][1])
        master.op = op_lo + clamp((total - sp_lo) / max(sp_hi - sp_lo, 1e-9), 0.0, 1.0) * (op_hi - op_lo)
        master.e_prev = None
        self.event("operator", master_tag, f"Handback to {slave_tag}: {total:.1f} (OP {master.op:.1f} %)")

    def _parent_cause(self, key):
        return f"{key} {self.interlocks[key].cfg['label']}"

    def reset_interlock(self, key):
        il = self.interlocks[key]
        if not il.latched:
            return False, "not tripped"
        parents = il.cfg.get("parents", [])
        for parent in parents:
            if self.interlocks[parent].latched:
                return False, f"reset {parent} first"
        # Causes are refreshed every scan; drop a dependency on a parent reset since then, so
        # I-100 and its children can be reset back to back.
        causes = [c for c in il.active_causes if c not in [self._parent_cause(p) for p in parents]]
        if causes:
            return False, "cause still active: " + causes[0]
        il.latched = False
        il.first_out = None
        for tag in il.cfg["forces"]:
            self.loops[tag].forced = False
        self.event("operator", key, f"{il.cfg['label']} reset")
        return True, "ok"

    # ---------------------------------------------------------------- alarms
    def run_alarms(self):
        io_alarm = self.alarms["IO.COMM"]
        self._set_alarm(io_alarm, self.io_bad_since is not None and self.now - self.io_bad_since > 3, None)
        if not self.io_ok:
            return
        for a in self.alarms.values():
            if a.limit == "DISC":
                continue
            cfg = C.TAG_INDEX[a.tag]
            v = self.pv(a.tag)
            sup = cfg.get("suppress")
            if v is None or (sup and not self.running(sup)):
                if a.active:              # suppressed by state: clear silently, no RTN noise
                    a.active, a.acked = False, True
                continue
            db = 0.01 * (cfg["hi"] - cfg["lo"])
            if a.limit in ("H", "HH"):
                on = v >= a.setpoint if not a.active else v > a.setpoint - db
            else:
                on = v <= a.setpoint if not a.active else v < a.setpoint + db
            self._set_alarm(a, on, v)
        for k, il in self.interlocks.items():
            self._set_alarm(self.alarms[f"{k}.TRIP"], il.latched, None)
        q = self.ppu
        self._set_alarm(self.alarms["KS-201.SWITCH_DELAYED"],
                        self.running("MAC") and q.online_time >= q.cycle and q.step_name != "STANDBY", None)
        self._set_alarm(self.alarms["KS-201.REGEN_INCOMPLETE"], q.regen_incomplete, None)

    def _set_alarm(self, a, on, value):
        if value is not None:
            a.value = value
        if on and not a.active:
            a.active, a.acked, a.since = True, False, self.now
            self.event("alarm", a.tag, f"ALARM {a.message}" + (f" = {value:.{C.TAG_INDEX[a.tag]['dec']}f}" if value is not None and a.tag in C.TAG_INDEX else ""), a.priority)
        elif not on and a.active:
            a.active = False
            self.event("alarm", a.tag, f"RTN {a.message}", 0)

    def ack(self, key=None):
        n = 0
        for a in self.alarms.values():
            if (key is None or a.key == key) and not a.acked:
                a.acked = True
                n += 1
        if n:
            self.event("operator", key or "ALL", f"{n} alarm(s) acknowledged")
        return n

    def alarm_list(self):
        rows = [a.as_dict() for a in self.alarms.values() if a.active or not a.acked]
        return sorted(rows, key=lambda r: (r["priority"], -(r["since"] or 0)))

    # ---------------------------------------------------------------- operator commands
    def set_loop(self, tag, **kw):
        p = self.loops[tag]
        if p.forced and "mode" in kw and kw["mode"] != "MAN":
            return False, "held in MAN by interlock — reset the interlock first"
        msgs = []
        if "mode" in kw:
            mode = kw["mode"]
            if mode not in p.cfg["modes"]:
                return False, f"{mode} not allowed for {tag}"
            if mode != p.mode:
                if mode != "MAN":
                    p.e_prev = None
                msgs.append(f"mode {p.mode} → {mode}")
                p.mode = mode
                if mode == "CAS":
                    self._handback(tag)
        if "sp" in kw and kw["sp"] is not None:
            if p.mode == "CAS":
                return False, "SP is remote in CAS"
            sp = clamp(float(kw["sp"]), p.sp_lo, p.sp_hi)
            if p.mode == "MAN":
                return False, "SP tracks PV in MAN — switch to AUTO first"
            msgs.append(f"SP {p.target:.{p.cfg['dec']}f} → {sp:.{p.cfg['dec']}f}")
            p.target = sp
        if "op" in kw and kw["op"] is not None:
            if p.mode != "MAN":
                return False, "OP can only be set in MAN"
            op = clamp(float(kw["op"]), p.cfg["op_lo"], p.cfg["op_hi"])
            msgs.append(f"OP {p.op:.1f} → {op:.1f} %")
            p.op = op
        if "ratio" in kw and kw["ratio"] is not None and p.cfg.get("ratio_of"):
            r = clamp(float(kw["ratio"]), p.cfg["ratio_lo"], p.cfg["ratio_hi"])
            msgs.append(f"ratio {p.ratio:.3f} → {r:.3f}")
            p.ratio = r
        for key in ("sp_hi", "sp_lo", "kc", "ti", "sp_rate"):
            if key in kw and kw[key] is not None:
                old = getattr(p, key)
                val = float(kw[key]) if kw[key] != "" else None
                if key == "sp_rate" and val is not None and val <= 0:
                    val = None
                setattr(p, key, val)
                msgs.append(f"{key} {old} → {val}")
        if msgs:
            self.event("operator", tag, "; ".join(msgs))
        return True, "ok"

    def start_equipment(self, key):
        eq = self.equipment[key]
        il = self.interlocks[eq.cfg["trip"]]
        if il.latched:
            return False, f"{il.key} tripped — reset first"
        if eq.cmd:
            return False, "already commanded on"
        if key == "MAC":
            po = self.pv("PI-107")
            if po is None or po < 1.5:
                return False, "permissive: lube-oil pressure PI-107 < 1.5 bar(g)"
            fic = self.loops["FIC-101"]
            fic.mode, fic.op = "MAN", 40.0      # start on minimum guide vanes
            self.loops["UIC-103"].mode = "AUTO"
        if key == "EXP":
            if not self.running("MAC"):
                return False, "permissive: MAC not running"
            p401 = self.pv("PI-401")
            if p401 is None or p401 < 4.0:
                return False, "permissive: HP column pressure PI-401 < 4.0 bar(a)"
            fic = self.loops["FIC-303"]
            fic.mode, fic.op = "MAN", 30.0      # start on minimum nozzles
        if key == "BAC":
            if not self.running("MAC"):
                return False, "permissive: MAC not running (no air for the booster)"
            fic = self.loops["FIC-111"]
            fic.mode, fic.op = "MAN", 30.0      # start above minimum flow (surge)
        if key == "LOXP":
            if not self.running("BAC"):
                return False, "permissive: booster K-102 not running (pumped LOX could not vaporize)"
            lvl = self.pv("LIC-404")
            if lvl is None or lvl < 40.0:
                return False, "permissive: reboiler level LIC-404 < 40 % (pump suction / NPSH)"
            fic = self.loops["FIC-501"]
            fic.mode, fic.op = "MAN", 20.0      # start on part flow; Normal line-up returns it to CAS
        if key == "GANC":
            if not self.running("MAC"):
                return False, "permissive: MAC not running (no GAN from the HP column)"
            p401 = self.pv("PI-401")
            if p401 is None or p401 < 4.0:
                return False, "permissive: HP column pressure PI-401 < 4.0 bar(a) (compressor suction)"
            fic = self.loops["FIC-503"]
            fic.mode, fic.op = "MAN", 30.0      # start above minimum flow (surge)
        if key in ("LOXB", "LINB"):
            tank, fic = ("LI-505", "FIC-507") if key == "LOXB" else ("LI-506", "FIC-508")
            lvl, ll = self.pv(tank), C.TAG_INDEX[tank]["alarms"]["LL"]
            if lvl is None or lvl <= ll:
                return False, f"permissive: tank level {tank} ≤ {ll} % (pump suction)"
            loop = self.loops[fic]
            loop.mode, loop.op = "MAN", 0.0     # start on minimum-flow recirculation
        eq.cmd = True
        eq.cmd_since = self.now
        self.event("operator", key, f"{eq.cfg['label']} start")
        return True, "ok"

    def stop_equipment(self, key):
        eq = self.equipment[key]
        if not eq.cmd:
            return False, "already stopped"
        self.event("operator", key, f"{eq.cfg['label']} stop")
        self._trip(eq.cfg["trip"], f"Operator stop ({eq.cfg['cmd']})")
        return True, "ok"

    def esd(self):
        self.event("operator", "ESD", "Manual emergency shutdown", 1)
        self._trip("I-100", "Manual ESD (HS-100)")
        return True, "ok"

    def lineup(self):
        changed, skipped = [], []
        for tag, mode in C.LINEUP.items():
            p = self.loops[tag]
            sup = p.cfg.get("suppress")
            if p.forced or p.mode == mode:
                continue
            # Loops on stopped equipment stay in MAN: in AUTO they would just wind up.
            if sup and not self.running(sup):
                skipped.append(f"{tag} ({sup} not running)")
                continue
            p.mode = mode
            p.e_prev = None
            if mode == "CAS":
                self._handback(tag)
            changed.append(f"{tag}→{mode}")
        msg = ", ".join(changed) or "no change"
        if skipped:
            msg += "; skipped: " + ", ".join(skipped)
        self.event("operator", "LINEUP", "Normal line-up: " + msg)
        return True, msg

    # ---------------------------------------------------------------- snapshots / persistence
    def snapshot(self):
        tags = {}
        for t in C.TAGS:
            v = self.values.get(t["tag"])
            row = {"pv": v}
            if t["kind"] == "PID":
                p = self.loops[t["tag"]]
                row.update(sp=round(p.sp, 5), target=round(p.target, 5), op=round(p.op, 3),
                           mode=p.mode, forced=p.forced, limited=p.limited, xlimited=p.xlimited, lim_by=p.lim_by if p.xlimited else None, sp_hi=p.sp_hi,
                           sp_lo=p.sp_lo, kc=p.kc, ti=p.ti, sp_rate=p.sp_rate)
                if p.cfg.get("ratio_of"):
                    row["ratio"] = p.ratio
            tags[t["tag"]] = row
        q = self.ppu
        return dict(
            time=self.now, io_ok=self.io_ok, tags=tags,
            alarms=self.alarm_list(),
            equipment={k: dict(label=e.cfg["label"], cmd=e.cmd, fb=e.fb,
                               state=e.state(self.interlocks[e.cfg["trip"]].latched))
                       for k, e in self.equipment.items()},
            interlocks={k: dict(label=il.cfg["label"], latched=il.latched, first_out=il.first_out,
                                tripped_at=il.tripped_at, active_causes=il.active_causes,
                                causes=[f"{t} {l}" + ("" if a == "always" else f" (after {a} start-up bypass)")
                                        for t, l, a in il.cfg["causes"]] + il.cfg.get("extra_causes", []))
                        for k, il in self.interlocks.items()},
            sequences={"KS-201": dict(display=q.display(), online=q.online, offline=q.offline,
                                      step=q.step_name, step_time=round(q.step_time),
                                      step_duration=q.step_duration(), online_time=round(q.online_time),
                                      cycle=q.cycle, heat_peak=round(q.heat_peak, 1),
                                      regen_incomplete=q.regen_incomplete, switches=q.switches,
                                      steps=[s for s, _ in C.PPU_STEPS])},
            mac_armed=self.mac_armed(),
        )

    def checkpoint(self):
        q = self.ppu
        return dict(
            loops={t: dict(mode=p.mode, sp=p.sp, target=p.target, op=p.op, sp_hi=p.sp_hi, sp_lo=p.sp_lo,
                           kc=p.kc, ti=p.ti, sp_rate=p.sp_rate, ratio=p.ratio) for t, p in self.loops.items()},
            equipment={k: e.cmd for k, e in self.equipment.items()},
            interlocks={k: dict(latched=il.latched, first_out=il.first_out, tripped_at=il.tripped_at)
                        for k, il in self.interlocks.items()},
            ppu=dict(cycle=q.cycle, online=q.online, online_time=q.online_time, step=q.step, step_time=q.step_time,
                     heat_peak=q.heat_peak, regen_incomplete=q.regen_incomplete, switches=q.switches),
        )

    def restore(self, state):
        for tag, s in state.get("loops", {}).items():
            if tag in self.loops:
                p = self.loops[tag]
                for k, v in s.items():
                    setattr(p, k, v)
        for k, cmd in state.get("equipment", {}).items():
            if k in self.equipment:
                self.equipment[k].cmd = cmd
        for k, s in state.get("interlocks", {}).items():
            if k in self.interlocks:
                il = self.interlocks[k]
                il.latched, il.first_out, il.tripped_at = s["latched"], s["first_out"], s["tripped_at"]
                if il.latched:
                    for tag in il.cfg["forces"]:
                        self.loops[tag].forced = True
        q = self.ppu
        for k, v in state.get("ppu", {}).items():
            setattr(q, k, v)
        self._seq_entered = q.step   # don't re-enter (and re-log) the restored step

