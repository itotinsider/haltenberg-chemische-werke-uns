"""Behaviour: the plant model and the DCS together, faster than real time (tools/offline_sim.Sim).

These pin down *how the system behaves*, with bands rather than exact values, so retuning stays
possible but a change in cause and effect fails a test:

- steady state stays in spec without a single P1/P2 alarm,
- every instructor scenario produces its signature (first-out trip, alarm or trend),
- the documented restart procedures (README "Restart after a trip") bring the plant back,
- permissives, interlock resets, I/O loss, checkpoint, ESD and PPU time compression.

A fixed seed makes every run repeatable (~1 plant-hour per 2 s). The bands are meant to hold for
any seed; check that after retuning with e.g.  docker compose run --rm -e HCW_TEST_SEED=3 tests
"""

import json
import os
import re

import pytest

import config as C
from model import SCENARIOS
from offline_sim import Sim, Window

SEED, T0 = int(os.getenv("HCW_TEST_SEED", "7")), 1.7e9
RANGE_TOLERANCE = 0.02     # of span: a transmitter reads a little beyond its range before it saturates

# Every tag stays inside its configured range (= EURange on OPC UA), in every scenario. A value that
# can't stay in range must either be a real measurement (widen the range) or a calculation that
# doesn't apply (None -> BadOutOfService). There is deliberately no exception list.


# On a trip the backups must keep both headers above LL. Brief L alarms are accepted: GOX while the
# vaporizer ramps up; GAN when a site purge job happens to run at the moment of the trip (~7.27 bar(g)
# for under a minute). Without the trip handover the GAN header fell to 3–5 bar(g).
GAN_LL = C.TAG_INDEX["PIC-504"]["alarms"]["LL"]
GOX_LL = C.TAG_INDEX["PIC-502"]["alarms"]["LL"]


def new_sim(warmup=300, **kw):
    sim = Sim(seed=SEED, t0=T0, **kw)
    sim.run(warmup)
    return sim


def assert_in_range(win):
    for t in C.TAGS:
        lo, hi = win.min(t["tag"]), win.max(t["tag"])
        if lo is None:
            continue
        tol = RANGE_TOLERANCE * (t["hi"] - t["lo"])
        assert t["lo"] - tol <= lo and hi <= t["hi"] + tol, f"{t['tag']} [{lo:g}, {hi:g}] outside its range [{t['lo']}, {t['hi']}]"


def assert_trip_tree_consistent(sim):
    """A child interlock is latched whenever one of its parents is."""
    for key, il in C.INTERLOCKS.items():
        for parent in il.get("parents", []):
            if sim.dcs.interlocks[parent].latched:
                assert sim.dcs.interlocks[key].latched, f"{parent} latched but its child {key} isn't"


def first_out(sim, key):
    return sim.dcs.interlocks[key].first_out or ""


def active(sim):
    return {k for k, a in sim.dcs.alarms.items() if a.active}


def serious(alarm_keys):
    """The P1/P2 alarms among these keys (P3 are informational, e.g. a backup vaporizer cutting in)."""
    return sorted(k for k in alarm_keys if _PRIO[k] <= 2)


_PRIO = {k: a.priority for k, a in Sim(seed=0, t0=0).dcs.alarms.items()}


# ---------------------------------------------------------------- steady state
@pytest.fixture(scope="module")
def steady():
    sim = new_sim()
    return sim, sim.run(2 * 3600)


def test_steady_state_has_no_serious_alarms_or_trips(steady):
    sim, win = steady
    assert serious(win.alarms) == []
    assert sim.tripped() == set()
    assert {k: sim.state(k) for k in C.EQUIPMENT} == {k: "RUNNING" for k in C.EQUIPMENT}


def test_steady_state_in_spec(steady):
    """Normal operating windows (the GAN header only within its alarm limits: site N2 purge jobs move it)."""
    _, win = steady
    gan = C.TAG_INDEX["PIC-504"]["alarms"]
    assert win.min("AI-601") >= 99.5, "GOX purity"
    assert 29.0 <= win.min("PIC-502") and win.max("PIC-502") <= 31.0, "GOX header"
    assert gan["L"] < win.min("PIC-504") and win.max("PIC-504") < gan["H"], "GAN header"
    assert 0.50 <= win.min("KPI-701") and win.max("KPI-701") <= 0.60, "specific power"
    assert win.max("AI-202") < 1.0, "CO2 after the PPU"


def test_steady_state_every_tag_live_and_in_range(steady):
    sim, win = steady
    assert [t["tag"] for t in C.TAGS if sim.pv(t["tag"]) is None] == []
    assert_in_range(win)


def test_runs_are_repeatable():
    def run(seed):
        sim = Sim(seed=seed, t0=T0)
        sim.run(900)
        return sim.dcs.snapshot()["tags"]
    assert run(SEED) == run(SEED)
    assert run(SEED) != run(SEED + 1)


# ---------------------------------------------------------------- scenario signatures
def _delta(tag, d):
    return lambda sim, win, base: (win.max(tag) - base[tag] >= d) if d > 0 else (win.min(tag) - base[tag] <= d)


# key: (max seconds, {interlock: first-out regex}, alarms that must occur, extra check(sim, win, base) or None)
# A scenario ends as soon as all of it is seen. Interlocks not listed must stay reset (children of
# a listed interlock are implied).
SIGNATURES = {
    "eo_demand_step": (1800, {}, {"FIC-507.H"},
                       lambda s, w, b: s.loop("FIC-501").lim_by == "FY-501" and w.min("PIC-502") >= 28.5),
    "acetylene_burner_trip": (900, {}, set(), _delta("FIC-501", -3.0)),
    "n2_purge_campaign": (1800, {}, set(), lambda s, w, b: _delta("FIC-503", 8.0)(s, w, b) and w.min("PIC-504") >= 7.2),
    "mac_motor_trip": (60, {"I-100": r"^YS-101 "}, {"I-100.TRIP"}, None),
    "mac_bearing_wear": (3600, {"I-100": r"^VI-105 HH"}, {"VI-105.H", "TI-106.H"}, None),
    "mac_efficiency_drift": (3600, {}, set(), lambda s, w, b: s.pv("JI-104") - b["JI-104"] >= 0.15),
    "ppu_water_carryover": (3600, {"I-100": r"^AI-202 HH"}, {"AI-202.H"}, None),
    "regen_heater_fault": (2 * 3600, {}, {"KS-201.REGEN_INCOMPLETE"}, None),
    "booster_motor_trip": (120, {"I-110": r"^YS-102 "}, {"I-110.TRIP", "I-500.TRIP"},
                           lambda s, w, b: s.pv("FIC-507") > 15),
    "lox_pump_cavitation": (900, {"I-500": r"^PI-511 LL"}, {"PI-511.L"}, None),
    "gan_compressor_trip": (120, {"I-503": r"^YS-503 "}, {"I-503.TRIP"}, lambda s, w, b: s.pv("FIC-508") > 15),
    "lox_backup_pump_trip": (60, {"I-505": r"^YS-505 "}, {"I-505.TRIP"}, None),
    "expander_loss_of_load": (300, {"I-300": r"^SI-304 HH"}, {"SI-304.HH"}, None),
    "mhe_fouling": (2 * 3600, {}, {"TDI-302.H"}, None),
    "hydrocarbon_event": (1800, {}, set(), lambda s, w, b: w.max("AI-603") >= 2 * b["AI-603"]),
    "heatwave": (1800, {}, set(), lambda s, w, b: _delta("TI-109", 8.0)(s, w, b) and s.pv("JI-104") > b["JI-104"]),
}


def test_every_scenario_has_a_signature():
    assert set(SIGNATURES) == set(SCENARIOS), "add the new scenario's expected effect to SIGNATURES"


def _children(keys):
    out, grew = set(keys), True
    while grew:
        grew = False
        for k, il in C.INTERLOCKS.items():
            if k not in out and out & set(il.get("parents", [])):
                out.add(k)
                grew = True
    return out


@pytest.mark.parametrize("key", list(SIGNATURES))
def test_scenario_signature(key):
    limit, trips, alarms, check = SIGNATURES[key]
    sim = new_sim()
    base = {t["tag"]: sim.pv(t["tag"]) for t in C.TAGS}
    sim.scenario(key)
    win = Window()

    def done(s):
        return set(trips) <= s.tripped() and alarms <= win.alarms and (check is None or check(s, win, base))

    sim.run(limit, until=done, win=win)
    missing = {"trips": sorted(set(trips) - sim.tripped()), "alarms": sorted(alarms - win.alarms)}
    assert missing == {"trips": [], "alarms": []}, f"{key} after {sim.t - 300:.0f} s"
    if check:
        assert check(sim, win, base), f"{key}: expected effect not seen within {limit} s"
    for il, pattern in trips.items():
        assert re.search(pattern, first_out(sim, il)), f"{il} first out: {first_out(sim, il)!r}"
    assert sim.tripped() <= _children(trips), f"unexpected trips {sorted(sim.tripped() - _children(trips))}"
    assert_trip_tree_consistent(sim)
    assert_in_range(win)


# ---------------------------------------------------------------- restart procedures (README)
def start(sim, eq, lineup=True, wait=1800):
    """Start equipment once its permissive allows, wait until RUNNING, then press Normal line-up."""
    t0 = sim.t
    while True:
        ok, msg = sim.cmd(f"start:{eq}")
        if ok or sim.t - t0 > wait:
            break
        sim.run(10)
    assert ok, f"start {eq}: {msg}"
    sim.run(300, until=lambda s: s.state(eq) == "RUNNING")
    assert sim.state(eq) == "RUNNING"
    if lineup:
        assert sim.cmd("lineup")[0]


def reset(sim, *keys):
    for k in keys:
        ok, msg = sim.cmd(f"reset:{k}")
        assert ok, f"reset {k}: {msg}"


def test_mac_trip_backups_hold_and_full_restart_recovers():
    sim = new_sim()
    sim.scenario("mac_motor_trip")
    win = sim.run(900)
    assert sim.tripped() == {"I-100", "I-300", "I-110", "I-500", "I-503"}, "I-505/I-506 must survive an ASU trip"
    assert win.min("PIC-502") > GOX_LL and win.min("PIC-504") > GAN_LL, "backup vaporizers hold both headers"
    assert sim.pv("FIC-507") > 20 and sim.pv("FIC-508") > 15
    with pytest.raises(AssertionError):
        reset(sim, "I-110")                       # a child can't be reset before its parent
    reset(sim, "I-100", "I-300", "I-110", "I-503", "I-500")
    start(sim, "MAC")
    assert sim.cmd("sp:FIC-101=145")[0]
    start(sim, "EXP")
    assert sim.cmd("sp:FIC-303=18")[0]
    start(sim, "BAC")
    start(sim, "LOXP")
    start(sim, "GANC")
    win = sim.run(3600)
    assert win.max("PIC-504") <= 8.8, "handback: no GAN header bump at line-up"
    assert win.min("PIC-502") >= 28.0
    win = sim.run(3600)
    assert serious(win.alarms) == [] and sim.tripped() == set()
    assert win.min("AI-601") >= 99.5
    assert sim.loop("FIC-501").mode == "CAS" and sim.loop("FIC-503").mode == "CAS"


def test_booster_trip_quick_restart_avoids_asu_trip():
    sim = new_sim()
    sim.scenario("booster_motor_trip")
    win = sim.run(180)
    assert win.min("PIC-502") > GOX_LL, "trip handover: the vaporizer takes the GOX header at once"
    reset(sim, "I-110", "I-500")
    start(sim, "BAC")
    start(sim, "LOXP")
    win = sim.run(1800)
    assert "I-100" not in sim.tripped() and win.max("LIC-404") < 95
    assert sim.loop("FIC-501").mode == "CAS" and sim.pv("FIC-507") < 1.0, "P-501 has the header back"


def test_gan_compressor_trip_and_restart():
    sim = new_sim()
    sim.scenario("gan_compressor_trip")
    win = sim.run(600)
    assert win.min("PIC-504") > GAN_LL, "trip handover: the LIN backup takes the GAN grid at once"
    reset(sim, "I-503")
    start(sim, "GANC")
    win = sim.run(1800)
    assert GAN_LL < win.min("PIC-504") and win.max("PIC-504") <= 8.8, "handback without a bump"
    assert sim.tripped() == set() and sim.pv("FIC-508") < 1.0, "K-503 has the GAN grid back"
    assert win.min("AI-601") > 99.3, "the GAN draw returning shifts the column only slightly"


def test_lox_pump_cavitation_restart_after_recooling():
    sim = new_sim()
    sim.scenario("lox_pump_cavitation")
    sim.run(900, until=lambda s: "I-500" in s.tripped())
    sim.scenario("lox_pump_cavitation", start=False)       # fault cleared (suction line re-cooled)
    sim.run(300)
    reset(sim, "I-500")
    start(sim, "LOXP")
    win = sim.run(1200)
    assert sim.tripped() == set() and win.min("PI-511") > 20


def test_backup_pump_trip_and_restart():
    sim = new_sim()
    sim.scenario("lox_backup_pump_trip")
    win = sim.run(120)
    assert sim.tripped() == {"I-505"} and win.min("PIC-502") >= 29.0
    reset(sim, "I-505")
    start(sim, "LOXB")
    assert sim.loop("FIC-507").mode == "CAS"
    sim.run(120)
    assert sim.tripped() == set()


# ---------------------------------------------------------------- permissives and interlock logic
def test_start_permissives():
    sim = new_sim()
    sim.cmd("esd")
    sim.run(120)
    ok, msg = sim.cmd("start:MAC")
    assert not ok and "reset first" in msg
    reset(sim, "I-100", "I-300", "I-110", "I-503", "I-500")
    for eq in ("EXP", "BAC", "GANC"):            # all need the MAC running
        ok, msg = sim.cmd(f"start:{eq}")
        assert not ok and msg.startswith("permissive: MAC not running"), (eq, msg)
    ok, msg = sim.cmd("start:LOXP")
    assert not ok and "booster" in msg, msg
    sim.plant.l505 = 5.0                          # LOX tank below LL: I-505 trips the backup pump
    sim.run(60)
    assert "I-505" in sim.tripped()
    reset(sim, "I-505")
    ok, msg = sim.cmd("start:LOXB")
    assert not ok and "tank level" in msg, msg


def test_interlock_reset_refused_while_cause_active():
    sim = new_sim()
    ai = sim.plant.measurements()
    ai["TT-106"] = 110.0                          # MAC bearing temperature above HH (armed "always")
    sim.dcs.scan(ai, sim.plant.discretes(), T0 + sim.t)
    assert "I-100" in sim.tripped() and first_out(sim, "I-100").startswith("TI-106 HH")
    sim.dcs.scan(ai, sim.plant.discretes(), T0 + sim.t + 1)
    assert not sim.dcs.reset_interlock("I-100")[0]
    sim.run(30)                                    # the real (cool) bearing temperature again
    assert sim.dcs.reset_interlock("I-100")[0]


def test_per_gox_figures_not_applicable_without_production():
    """KPI-701 / FFI-111 divide by the GOX flow: only while P-501 delivers, else None (not 5 kWh/Nm³)."""
    sim = new_sim()
    assert sim.pv("KPI-701") is not None and sim.pv("FFI-111") is not None
    sim.scenario("booster_motor_trip")
    win = sim.run(120)
    assert sim.pv("KPI-701") is None and sim.pv("FFI-111") is None
    assert win.max("KPI-701") <= C.TAG_INDEX["KPI-701"]["hi"] and win.max("FFI-111") <= C.TAG_INDEX["FFI-111"]["hi"]
    assert sim.pv("FY-501") <= C.TAG_INDEX["FY-501"]["hi"]


def test_esd_trips_the_whole_tree_except_the_backups():
    sim = new_sim()
    assert sim.cmd("esd")[0]
    sim.run(10)
    assert sim.tripped() == {"I-100", "I-300", "I-110", "I-500", "I-503"}
    assert first_out(sim, "I-100").startswith("Manual ESD")
    assert sim.state("LOXB") == "RUNNING" and sim.state("LINB") == "RUNNING"


def test_io_loss_goes_bad_quality_without_tripping():
    sim = new_sim()
    ao_before = dict(sim.dcs.ao)
    for i in range(30):
        ao, do = sim.dcs.scan({}, {}, T0 + sim.t + i, io_ok=False)
    assert [t for t in C.TAG_INDEX if sim.pv(t) is not None] == [], "every PV bad quality"
    assert active(sim) == {"IO.COMM"}
    assert sim.tripped() == set() and ao == ao_before, "outputs held, nothing trips on bad quality"
    sim.t += 30
    sim.run(60)
    assert active(sim) == set() and sim.pv("FIC-101") is not None


def test_checkpoint_restores_loops_and_sequence():
    sim = new_sim()
    sim.cmd("sp:FIC-101=150")
    sim.cmd("mode:FIC-303=MAN")
    sim.run(60)
    state = json.loads(json.dumps(sim.dcs.checkpoint()))
    other = Sim(seed=SEED + 1, t0=T0)
    other.dcs.restore(state)
    assert other.loop("FIC-101").sp == sim.loop("FIC-101").sp and other.loop("FIC-303").mode == "MAN"
    assert other.dcs.ppu.online == sim.dcs.ppu.online and other.dcs.ppu.step == sim.dcs.ppu.step


@pytest.mark.parametrize("cycle", [900, 300])
def test_ppu_time_compression(cycle):
    sim = new_sim(cycle=cycle)
    win = sim.run(3 * cycle)
    assert sim.dcs.ppu.switches >= 2
    assert "KS-201.REGEN_INCOMPLETE" not in win.alarms and win.max("AI-202") < 1.0
    assert win.max("TIC-204") >= C.REGEN_PEAK_MIN
