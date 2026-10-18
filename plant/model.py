"""Demo-grade dynamic model of HCW ASU1, a Linde-type air separation unit (~1,000 t/d GOX).

Six blocks, following the research note ("linde air seperation research.md" §2-§4):
    1 Main air compressor (MAC) + DCAC      fast: first-order lags, tau ~ 10 s
    2 Front-end purification (PPU, TSA)     periodic: bed loading sawtooth, breakthrough
    3 Main heat exchanger + turbo-expander  refrigeration balance, very slow cold-box drift
    4 Linde double column                   pressures (minutes), levels (integrators),
                                            purities (static map -> dead time -> slow lag)
    5 Product delivery (GOX / GAN headers)  internal compression (P-501), GAN compressor K-503,
                                            integrating headers vs. consumer demand
    6 Backup & storage (LOX / LIN tanks)    integrators, backup pumps P-505 / P-506 + vaporizers

No rigorous thermodynamics: a handful of "handles" (air flow, LP pressure, reflux, expander
flow, product draws) drive purities, levels and power as visible consequences. All flows are in
kNm3/h, pressures in bar, temperatures in degC.

The model knows nothing about controllers. It reads final-element positions (0-100 %) and
run/select commands exactly as the DCS writes them to the Modbus I/O rack, and produces raw
transmitter readings. Scenarios (instructor-station faults) live here and are never exposed on
the field I/O: the DCS, OPC UA and any agent behind it must infer them from process values.
"""

import math
import random
from collections import deque

# ---- Nominal design point (research §1/§3) ---------------------------------------------------
PPU_DESIGN_CYCLE = 14400.0   # s, realistic mole-sieve bed online time (instructor can compress it)
AIR_DESIGN = 145.0          # kNm3/h air
O2_FRACTION = 0.2095
O2_RECOVERY = 0.981         # GOX + LOX that can be drawn at spec, as a fraction of O2 in air
P_ATM = 1.013
P_HP_DESIGN = 5.30
P_LP_DESIGN = 1.35
MAC_POWER_DESIGN = 10.5     # MW at design flow / pressure
EXPANDER_DESIGN = 18.0      # kNm3/h
EXPANDER_SPEED_DESIGN = 25000.0
HEAT_LEAK = 0.8             # kNm3/h liquid-equivalent lost to cold-box heat in-leak
EXPANDER_REFRIG = (1.4 + HEAT_LEAK) / EXPANDER_DESIGN   # net liquid make 1.4 at design
LOX_TANK_CAPACITY = 1050.0  # kNm3 gas-equivalent (~1,500 t)
# Internal compression (research §3 block 5): P-501 pumps LOX from the LP sump to ~33 bar(g); it
# boils in E-301 against booster air (K-102, ~60 bar(a)) that condenses on the other side.
BOOSTER_DESIGN = 44.0       # kNm3/h, ~30 % of the air
BOOSTER_P_DESIGN = 60.25    # bar(a)
BOOSTER_POWER_DESIGN = 4.2  # MW (same specific work as the MAC per unit of ln(pressure ratio))
BOOSTER_RATIO = 1.52        # kNm3 booster air needed per kNm3 pumped O2 to vaporize it in E-301
# LP sump / reboiler hold-up: % level per (kNm3/h x s) of imbalance. Sized so a refrigeration deficit
# drains it over hours, and a stopped LOX pump floods it in ~10 min (research §4.4).
LP_SUMP_GAIN = 0.00185
LIN_TANK_CAPACITY = 700.0
# GAN compressor K-503: HP-column GAN (~5.05 bar(a) after E-301) to the 8 bar(g) header. Power from the
# MAC's specific work per unit of ln(pressure ratio): 0.64 MW at 25 kNm3/h.
GAN_DESIGN = 25.0
GAN_POWER_DESIGN = 0.64
GAN_RATIO_DESIGN = 9.41 / 5.05
# Site N2 grid hold-up: bar/s per kNm3/h of imbalance (~70 m3 of pipe). It was 0.02 (~14 m3), which
# emptied the header from 8 to 3 bar(g) within a minute of losing the GAN supply.
GAN_HEADER_GAIN = 0.004
LIN_VAP_CAPACITY = 40.0     # kNm3/h, E-508: covers peak N2 demand (purge jobs, a purge campaign)


def lag(x, target, tau, dt):
    """First-order lag step."""
    if tau <= 0:
        return target
    return x + (target - x) * (1.0 - math.exp(-dt / tau))


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def t_sat(p_bar, t_boil_k, dh_vap):
    """Saturation temperature (degC) by Clausius-Clapeyron around the normal boiling point."""
    p = max(p_bar, 0.05)
    inv = 1.0 / t_boil_k - 8.314 / dh_vap * math.log(p / P_ATM)
    return 1.0 / inv - 273.15


def t_sat_o2(p):
    return t_sat(p, 90.19, 6820.0)


def t_sat_n2(p):
    return t_sat(p, 77.36, 5570.0)


class DeadTime:
    """Fixed transport delay, as a ring buffer of samples."""

    def __init__(self, delay_s, dt, initial):
        self.buf = deque([initial] * max(1, round(delay_s / dt)), maxlen=max(1, round(delay_s / dt)))

    def step(self, value):
        out = self.buf[0]
        self.buf.append(value)
        return out


class Walk:
    """Mean-reverting random walk (Ornstein-Uhlenbeck), for ambient and demand wander."""

    def __init__(self, sigma, tau):
        self.x = 0.0
        self.sigma = sigma
        self.tau = tau

    def step(self, dt):
        self.x += -self.x * dt / self.tau + self.sigma * math.sqrt(2 * dt / self.tau) * random.gauss(0, 1)
        return self.x


# ---- Instructor-station scenarios ------------------------------------------------------------
# kind: "latched" stays on until stopped; "timed" ends by itself after `duration` seconds;
# "oneshot" fires once (start == trigger, nothing to stop).
SCENARIOS = {
    "eo_demand_step": dict(
        label="EO plant demand step (+20 %)", kind="latched", group="Consumers",
        description="The ethylene-oxide plant raises its oxygen intake by 20 %. GOX header "
                    "pressure drops first; the ASU load must follow slowly."),
    "acetylene_burner_trip": dict(
        label="Acetylene burner trip", kind="timed", duration=900, group="Consumers",
        description="One of three partial-oxidation burners trips: a sharp ~4 kNm3/h drop in "
                    "O2 demand, restored after 15 minutes."),
    "n2_purge_campaign": dict(
        label="N2 purge campaign (turnaround)", kind="latched", group="Consumers",
        description="A turnaround elsewhere on site draws an extra ~10 kNm3/h of GAN for purging."),
    "mac_motor_trip": dict(
        label="MAC motor trip", kind="oneshot", group="Main air compressor",
        description="Motor protection trips the MAC. It stays down until the DCS drops and "
                    "re-issues its run command."),
    "mac_bearing_wear": dict(
        label="MAC bearing degradation", kind="latched", group="Main air compressor",
        description="DE bearing wear develops over ~40 min: vibration and bearing temperature "
                    "creep up towards the trip limits."),
    "mac_efficiency_drift": dict(
        label="MAC impeller fouling (efficiency drift)", kind="latched", group="Main air compressor",
        description="Polytropic efficiency drops ~3 %/h (accelerated). Same air, more power: the "
                    "specific-power KPI degrades. Stopping the scenario = an online water wash."),
    "ppu_water_carryover": dict(
        label="DCAC water carry-over into PPU", kind="latched", group="Front-end purification",
        description="Free water reaches the online adsorber and spends it within the hour: CO2 "
                    "rises 30-40 min in, trips the ASU at 10 ppm unless the beds are switched."),
    "regen_heater_fault": dict(
        label="Regeneration heater fault", kind="latched", group="Front-end purification",
        description="Heater output is capped at 40 %: regeneration gas never gets hot enough, so "
                    "the offline bed comes back on line still loaded."),
    "booster_motor_trip": dict(
        label="Booster motor trip", kind="oneshot", group="Internal compression",
        description="Motor protection trips the booster air compressor K-102. Without booster air the "
                    "pumped LOX can't vaporize in E-301: the LOX pump must trip with it."),
    "lox_pump_cavitation": dict(
        label="LOX pump cavitation (NPSH loss)", kind="latched", group="Internal compression",
        description="Suction conditions of P-501 degrade over ~10 min (e.g. a warm suction line): "
                    "discharge pressure becomes unsteady and falls, flow drops, until the pump trips. A "
                    "stopped pump re-cools in ~5 min; restarted, it degrades again until the fault is cleared."),
    "gan_compressor_trip": dict(
        label="GAN compressor K-503 motor trip", kind="oneshot", group="Nitrogen & backup",
        description="Motor protection trips the GAN compressor. The GAN header pressure falls until the "
                    "split range brings in the LIN backup (P-506 / E-508); the column has to vent the GAN."),
    "lox_backup_pump_trip": dict(
        label="LOX backup pump P-505 motor trip", kind="oneshot", group="Nitrogen & backup",
        description="The LOX backup pump trips. Nothing changes in the GOX header yet, but until it is "
                    "restarted there is no backup if P-501 or the ASU trips."),
    "expander_loss_of_load": dict(
        label="Expander loss of loading (overspeed)", kind="oneshot", group="Cold box",
        description="The booster brake loses load and the expander accelerates towards overspeed."),
    "mhe_fouling": dict(
        label="MHE fouling / icing", kind="latched", group="Cold box",
        description="Warm-end delta T creeps up ~2 K/h (accelerated). Stopping it = a derime."),
    "hydrocarbon_event": dict(
        label="Hydrocarbon event (flare upwind)", kind="latched", group="Environment",
        description="Hydrocarbons in the intake air rise six-fold; they concentrate in the "
                    "reboiler LOX unless the purge is maximised."),
    "heatwave": dict(
        label="Heatwave (+12 degC)", kind="latched", group="Environment",
        description="Ambient rises 12 K over ~30 min: less MAC capacity, more power, more water "
                    "load on the PPU."),
}


class ScenarioState:
    def __init__(self):
        self.active = {k: False for k in SCENARIOS}
        self.started = {k: None for k in SCENARIOS}

    def start(self, key, t):
        self.active[key] = True
        self.started[key] = t

    def stop(self, key):
        self.active[key] = False
        self.started[key] = None

    def on(self, key):
        return self.active[key]

    def elapsed(self, key, t):
        return 0.0 if self.started[key] is None else t - self.started[key]


class Plant:
    def __init__(self, t0, ppu_cycle_s=PPU_DESIGN_CYCLE):
        self.t = t0
        self.ppu_cycle = ppu_cycle_s
        self.sc = ScenarioState()
        self.pending_oneshots = set()

        # Commands / final elements, initialised to the design line-up so the plant sits at a
        # steady state even before the DCS connects (an I/O rack holds its last outputs).
        self.ao = {
            "FV-101": 76.3, "UV-103": 0.0, "TV-204": 0.0, "FV-303": 60.0, "PV-402": 50.0,
            "LV-403": 53.0, "LV-404": 26.7, "FV-405": 57.1, "FV-409": 30.0,
            "FV-501": 72.5, "FV-503": 41.7, "FV-507": 0.0, "FV-508": 0.0, "WD-900": 0.0, "FV-111": 73.3,
        }
        self.do = {"HS-101": True, "HS-301": True, "HS-201": False, "HS-102": True, "HS-501": True,
                   "HS-503": True, "HS-505": True, "HS-506": True}

        # Disturbance generators
        self.w_amb = Walk(1.2, 3 * 3600)
        self.w_eo = Walk(0.025, 1800)
        self.w_misc = Walk(0.06, 900)
        self.w_n2 = Walk(0.04, 1200)
        self.purges = []           # active N2 purge jobs: [end_t, kNm3/h]
        self.heatwave = 0.0

        # Block 1 — MAC
        self.mac_run = 1.0          # 0..1 motor/rotor state (eased spin-up/coast-down)
        self.mac_trip_latched = False
        self.mac_eff = 1.0
        self.bearing = 0.0          # 0..1 degradation
        self.f_comp = AIR_DESIGN
        self.f_air = AIR_DESIGN
        self.p_dis = 5.75
        self.t108 = 12.0
        self.t106 = 75.0
        self.power = MAC_POWER_DESIGN
        self.surge_kick = 0.0

        # Block 1b — booster air compressor K-102 (internal compression)
        self.bac_run = 1.0
        self.bac_trip_latched = False
        self.f_bst = BOOSTER_DESIGN
        self.p_bst = BOOSTER_P_DESIGN
        self.bst_power = BOOSTER_POWER_DESIGN
        self.bst_vib = 2.1

        # Block 2 — PPU: loading of each bed as a fraction of design capacity
        self.bed_b_online = False
        self.loading = {"A": 0.0, "B": 0.0}
        self.t204 = 15.0
        self.stroke_time = 12.0
        self.switch_transient = 0.0
        self.co2 = 0.03

        # Block 3 — MHE + expander
        self.exp_run = 1.0
        self.exp_flow = EXPANDER_DESIGN
        self.exp_overspeed = 0.0
        self.exp_speed = EXPANDER_SPEED_DESIGN
        self.t306 = 60.0
        self.dt_coldbox = 0.0       # K, lumped cold-box temperature deviation (hours/days)
        self.fouling = 0.0          # K added to warm-end dT

        # Block 4 — double column
        self.p_hp = P_HP_DESIGN
        self.p_lp = P_LP_DESIGN
        self.l403 = 50.0
        self.l404 = 70.0
        self.reflux = 40.0
        self.gox = 29.0
        self.gan = 25.0
        self.lox = 0.8
        self.lin = 0.6
        self.imp_gox = 0.35         # % non-O2 in GOX
        self.ppm_gan = 3.0
        self.dead_gox = DeadTime(240, 0.5, 0.35)
        self.dead_gan = DeadTime(120, 0.5, 3.0)
        self.thc = 7.6              # ppm: low, the pumped LOX flushes the sump continuously
        self.c2h2 = 0.02

        # Block 5 — LOX pump P-501 and the pumped LOX boiling in E-301
        self.loxp_run = 1.0
        self.cav_scn = 0.0          # 0..1 suction degradation (instructor scenario)
        self.cav = 0.0              # 0..1 cavitation severity
        self.p511 = 33.3
        self.t_gox = 9.5            # degC, GOX leaving the E-301 warm end

        # Block 5 — GAN compressor K-503
        self.gnc_run = 1.0
        self.gnc_trip_latched = False
        self.gan_power = GAN_POWER_DESIGN
        self.gan_vib = 1.8
        self.p513 = 8.4

        # Blocks 5/6 — headers, tanks, backup pumps (kept cold on recirculation) and vaporizers
        self.p_gox = 30.0
        self.p_gan = 8.0
        self.p505_run = 1.0
        self.p505_trip_latched = False
        self.p506_run = 1.0
        self.vap = 0.0
        self.vap_n = 0.0
        self.p516 = 35.5
        self.p517 = 12.0
        self.l505 = 65.0
        self.l506 = 60.0
        self.o2_demand = 29.0
        self.n2_demand = 25.0

        self.heartbeat = 0
        self.step(0.5)  # populate the derived values (vibration, ambient, consumers, ...)

    # ---- scenario control (instructor station) ----
    def start_scenario(self, key):
        spec = SCENARIOS[key]
        if spec["kind"] == "oneshot":
            self.pending_oneshots.add(key)
            self.sc.started[key] = self.t
        else:
            self.sc.start(key, self.t)

    def stop_scenario(self, key):
        self.sc.stop(key)
        if key == "mac_efficiency_drift":
            self.mac_eff = 1.0
        elif key == "mhe_fouling":
            self.fouling = 0.0
        elif key == "mac_bearing_wear":
            self.bearing = 0.0
        elif key == "lox_pump_cavitation":
            self.cav_scn = 0.0

    # ---- one integration step ----
    def step(self, dt):
        self.t += dt
        t = self.t
        ao, do, sc = self.ao, self.do, self.sc

        for key, spec in SCENARIOS.items():
            if spec["kind"] == "timed" and sc.on(key) and sc.elapsed(key, t) > spec["duration"]:
                sc.stop(key)
        if "mac_motor_trip" in self.pending_oneshots:
            self.mac_trip_latched = True
        if "booster_motor_trip" in self.pending_oneshots:
            self.bac_trip_latched = True
        if "gan_compressor_trip" in self.pending_oneshots:
            self.gnc_trip_latched = True
        if "lox_backup_pump_trip" in self.pending_oneshots:
            self.p505_trip_latched = True
        if "expander_loss_of_load" in self.pending_oneshots:
            self.exp_overspeed = 1.0
        self.pending_oneshots.clear()

        # ---- environment ----
        hour = (t % 86400) / 3600.0
        self.heatwave = lag(self.heatwave, 12.0 if sc.on("heatwave") else 0.0, 600, dt)
        t_amb = 15.0 + 5.0 * math.sin(2 * math.pi * (hour - 9.0) / 24.0) + self.w_amb.step(dt) + self.heatwave
        self.t_amb = t_amb

        # ---- block 1: MAC ----
        # The motor trip is latched in the motor protection relay until the run command drops.
        if not do["HS-101"]:
            self.mac_trip_latched = False
        mac_cmd = do["HS-101"] and not self.mac_trip_latched
        self.mac_run = lag(self.mac_run, 1.0 if mac_cmd else 0.0, 20.0 if mac_cmd else 8.0, dt)
        r = self.mac_run if self.mac_run > 0.02 else 0.0
        cap = 1.0 - 0.0035 * (t_amb - 15.0)
        igv = clamp(ao["FV-101"], 0, 100)
        f_comp_ss = r * cap * 190.0 * igv / 100.0 * math.sqrt(5.75 / max(self.p_dis, 1.0))
        self.f_comp = lag(self.f_comp, f_comp_ss, 10.0, dt)
        recycle = min(clamp(ao["UV-103"], 0, 100) / 100.0 * 70.0 * r, 0.6 * self.f_comp)
        # Bed switch-over: repressurising the fresh bed briefly steals forward flow.
        self.switch_transient = lag(self.switch_transient, 0.0, 25.0, dt)
        f_fwd_ss = max(0.0, self.f_comp - recycle) * (1.0 - 0.04 * self.switch_transient)
        self.f_air = lag(self.f_air, f_fwd_ss, 3.0, dt)
        load = self.f_air / AIR_DESIGN

        p_dis_ss = r * (self.p_hp + 0.45 * load ** 2) + (1.0 - r) * P_ATM
        self.p_dis = lag(self.p_dis, p_dis_ss, 5.0, dt)
        f_surge = 105.0 * self.p_dis / 5.75
        margin = (self.f_comp / f_surge - 1.0) * 100.0 if r > 0.5 else 50.0
        self.surge_margin = margin
        # In surge the machine shakes: flow reversals show up as vibration spikes.
        self.surge_kick = lag(self.surge_kick, 1.0 if margin < 0 else 0.0, 4.0, dt)

        p_in = 0.98
        t_in_k = t_amb + 273.15
        ratio = max(self.p_dis / p_in, 1.0)
        power_ss = (MAC_POWER_DESIGN * (self.f_comp / AIR_DESIGN) * (math.log(ratio) / math.log(5.75 / 0.98))
                    * (t_in_k / 288.15) / self.mac_eff)
        self.power = lag(self.power, power_ss + 0.25 * r, 3.0, dt)   # +0.25 MW no-load losses

        if sc.on("mac_efficiency_drift"):
            self.mac_eff = max(0.80, self.mac_eff - 0.03 / 3600.0 * dt)
        if sc.on("mac_bearing_wear"):
            self.bearing = min(1.0, self.bearing + dt / 2400.0)
        self.vib = r * (2.4 + 9.5 * self.bearing ** 2 + 6.0 * self.surge_kick) + (1 - r) * 0.3
        self.t106 = lag(self.t106, r * (75.0 + 0.25 * (t_amb - 15.0) + 36.0 * self.bearing) + (1 - r) * 45.0, 300.0, dt)
        self.t108 = lag(self.t108, 6.0 + 0.4 * t_amb, 300.0, dt)

        # ---- block 2: PPU ----
        if do["HS-201"] != self.bed_b_online:
            self.bed_b_online = do["HS-201"]
            self.switch_transient = 1.0
            self.stroke_time = 12.0 + random.gauss(0, 0.4)
        online = "B" if self.bed_b_online else "A"
        offline = "A" if self.bed_b_online else "B"
        # Saturated air out of the DCAC: water load roughly doubles per +10 K.
        water = 2.0 ** ((self.t108 - 12.0) / 10.0)
        rate = 0.65 / self.ppu_cycle * load * water   # design: 65 % loaded at end of cycle
        if sc.on("ppu_water_carryover"):
            rate += load / 3000.0   # free water: a fresh bed is spent in ~50 min, whatever the cycle
        self.loading[online] += rate * dt
        heater = clamp(ao["TV-204"], 0, 100)
        if sc.on("regen_heater_fault"):
            heater = min(heater, 40.0)
        waste_frac = clamp(load, 0.0, 1.0)  # regen gas is the cold box's waste N2
        # The regeneration circuit is time-compressed together with the cycle (instructor
        # slider), so a 10-minute demo cycle still heats and cools the bed in its HEAT/COOL steps.
        tau_regen = clamp(600.0 * self.ppu_cycle / PPU_DESIGN_CYCLE, 15.0, 600.0)
        self.t204 = lag(self.t204, self.t108 + 3.0 + heater / 100.0 * 230.0 * waste_frac ** 0.3, tau_regen, dt)
        if self.t204 > 120.0:
            k = 1.3 / (60.0 * 0.40 * self.ppu_cycle)
            self.loading[offline] = max(0.0, self.loading[offline] - k * (self.t204 - 120.0) * waste_frac * dt)
        x = self.loading[online]
        # Breakthrough front: ~0.15 ppm at the end of a design cycle (x = 0.75), 1 ppm at
        # x = 0.86, 10 ppm at x = 1.0 — the CO2 sawtooth the research note asks for.
        co2_ss = 0.03 + min(20.0, 10.0 * math.exp((x - 1.0) / 0.06)) + 0.4 * self.switch_transient
        self.co2 = lag(self.co2, co2_ss if r > 0.1 else self.co2, 30.0, dt)

        # ---- block 1b: booster air compressor K-102 ----
        # Takes clean air downstream of the PPU; no air without the MAC. Latched motor trip like the MAC.
        if not do["HS-102"]:
            self.bac_trip_latched = False
        bac_cmd = do["HS-102"] and not self.bac_trip_latched and r > 0.5
        self.bac_run = lag(self.bac_run, 1.0 if bac_cmd else 0.0, 15.0 if bac_cmd else 6.0, dt)
        rb = self.bac_run if self.bac_run > 0.02 else 0.0
        f_bst_ss = min(rb * 60.0 * clamp(ao["FV-111"], 0, 100) / 100.0, 0.45 * self.f_air)
        self.f_bst = lag(self.f_bst, f_bst_ss, 5.0, dt)
        # Discharge pressure is set by what the condensing side (E-301 + JT valve) accepts.
        p_bst_ss = self.p_dis + rb * (BOOSTER_P_DESIGN - 5.75) * (self.f_bst / BOOSTER_DESIGN) ** 0.6
        self.p_bst = lag(self.p_bst, p_bst_ss, 8.0, dt)
        ratio_b = max(self.p_bst / max(self.p_dis, 1.0), 1.0)
        bst_ss = (BOOSTER_POWER_DESIGN * (self.f_bst / BOOSTER_DESIGN)
                  * math.log(ratio_b) / math.log(BOOSTER_P_DESIGN / 5.75))
        self.bst_power = lag(self.bst_power, bst_ss + 0.12 * rb, 3.0, dt)
        # Below ~14 kNm3/h the booster approaches surge: vibration rises sharply.
        self.bst_vib = rb * (2.1 + 10.0 * max(0.0, (14.0 - self.f_bst) / 14.0)) + (1 - rb) * 0.3

        # ---- block 3: MHE + expander ----
        if not do["HS-301"]:
            self.exp_overspeed = 0.0
        exp_cmd = do["HS-301"] and self.p_hp > 2.5
        self.exp_run = lag(self.exp_run, 1.0 if exp_cmd else 0.0, 15.0 if exp_cmd else 3.0, dt)
        exp_flow_ss = self.exp_run * 0.30 * clamp(ao["FV-303"], 0, 100) * (self.p_hp / P_HP_DESIGN)
        self.exp_flow = lag(self.exp_flow, exp_flow_ss, 5.0, dt)
        if self.exp_overspeed > 0:
            self.exp_overspeed = min(1.6, self.exp_overspeed + dt / 40.0)
        # Unloaded, the rotor runs away: +35 % at once, rising further until it is tripped.
        speed_ss = EXPANDER_SPEED_DESIGN * (self.exp_flow / EXPANDER_DESIGN) \
            * (1.0 + 0.35 * min(self.exp_overspeed, 1.0) + 0.25 * max(0.0, self.exp_overspeed - 1.0))
        self.exp_speed = lag(self.exp_speed, speed_ss, 5.0, dt)
        self.t306 = lag(self.t306, 35.0 + 25.0 * (self.exp_speed / EXPANDER_SPEED_DESIGN) ** 2, 400.0, dt)
        # Net liquid-make capacity: what the expander refrigeration buys beyond heat in-leak.
        p_liq = EXPANDER_REFRIG * self.exp_flow - HEAT_LEAK
        # Booster air with no pumped LOX to condense against goes through the JT valve as warm,
        # dense fluid: it costs refrigeration (and shows up as a warm-end imbalance).
        self.bst_excess = max(0.0, self.f_bst - BOOSTER_RATIO * self.gox)
        p_liq -= 0.04 * self.bst_excess
        refrig_balance = p_liq - self.lox - self.lin
        # Lumped cold-box mass: hours to days. Surplus refrigeration cools it, deficit warms it.
        self.dt_coldbox = clamp(self.dt_coldbox - refrig_balance * 0.6 / 3600.0 * dt, -5.0, 60.0)
        if sc.on("mhe_fouling"):
            self.fouling += 2.0 / 3600.0 * dt

        # ---- block 4: double column ----
        air_eff = max(self.f_air, 5.0)
        lp_ratio = (self.p_lp / P_LP_DESIGN) ** 0.8
        hp_target = self.p_lp + (P_HP_DESIGN * lp_ratio - self.p_lp) * (0.75 + 0.25 * load) * min(1.0, load / 0.3)
        self.p_hp = lag(self.p_hp, hp_target, 120.0 if load > 0.3 else 300.0, dt)
        dp_hl = max(0.0, self.p_hp - self.p_lp)

        rich_in = 0.585 * self.f_air
        rich_out = 1.6 * clamp(ao["LV-403"], 0, 100) * math.sqrt(dp_hl / 3.95)
        self.l403 = clamp(self.l403 + 0.0167 * (rich_in - rich_out) * dt, 0.0, 100.0)
        self.rich_out = rich_out

        reflux_ss = min(70.0 * clamp(ao["FV-405"], 0, 100) / 100.0 * math.sqrt(dp_hl / 3.95), 0.42 * self.f_air)
        self.reflux = lag(self.reflux, reflux_ss, 8.0, dt)
        # P-501 pumps LOX out of the LP sump (internal compression). Low sump level or the
        # cavitation scenario starve the suction: flow and discharge pressure fall.
        # Cavitation scenario: the suction degrades while the pump runs; once stopped, the suction
        # line re-cools over ~5 min and a restart works again (until the fault builds up again).
        if self.loxp_run < 0.5:
            self.cav_scn = max(0.0, self.cav_scn - dt / 300.0)
        elif sc.on("lox_pump_cavitation"):
            self.cav_scn = min(1.0, self.cav_scn + dt / 600.0)
        self.cav = clamp(max((25.0 - self.l404) / 15.0, self.cav_scn), 0.0, 1.0)
        loxp_cmd = do["HS-501"]
        self.loxp_run = lag(self.loxp_run, 1.0 if loxp_cmd else 0.0, 5.0 if loxp_cmd else 3.0, dt)
        rp = self.loxp_run if self.loxp_run > 0.02 else 0.0
        gox_ss = rp * clamp(ao["FV-501"], 0, 100) / 100.0 * 40.0 * (1.0 - 0.8 * self.cav) if self.l404 > 2 else 0.0
        self.gox = lag(self.gox, gox_ss, 10.0 if gox_ss > self.gox else 3.0, dt)   # a stopped pump stops fast
        # GAN compressor K-503 draws the HP-column GAN (warmed in E-301). No column pressure, no suction:
        # it can't run without the air. Latched motor trip like the MAC. A stopped compressor stops the
        # draw; the column then vents that nitrogen with the waste N2 (PIC-402 opens).
        if not do["HS-503"]:
            self.gnc_trip_latched = False
        gnc_cmd = do["HS-503"] and not self.gnc_trip_latched and self.p_hp > 2.5
        self.gnc_run = lag(self.gnc_run, 1.0 if gnc_cmd else 0.0, 10.0 if gnc_cmd else 4.0, dt)
        rg = self.gnc_run if self.gnc_run > 0.02 else 0.0
        gan_ss = rg * min(clamp(ao["FV-503"], 0, 100) / 100.0 * 60.0, 0.30 * self.f_air)
        self.gan = lag(self.gan, gan_ss, 10.0 if gan_ss > self.gan else 4.0, dt)
        lox_ss = 3.0 * clamp(ao["LV-404"], 0, 100) / 100.0 if self.l404 > 2 else 0.0
        self.lox = lag(self.lox, lox_ss, 10.0, dt)
        self.lin = lag(self.lin, 2.0 * clamp(ao["FV-409"], 0, 100) / 100.0 if self.l404 > 2 else 0.0, 10.0, dt)

        o2_avail = O2_RECOVERY * O2_FRACTION * self.f_air
        self.l404 = clamp(self.l404 + LP_SUMP_GAIN * ((o2_avail - self.gox - self.lox) + refrig_balance) * dt, 0.0, 100.0)

        waste = max(0.0, self.f_air - self.gox - self.gan - self.lox - self.lin)
        self.waste = waste
        vent = max(clamp(ao["PV-402"], 0, 100), 1.0)
        p_lp_ss = P_ATM + (waste / (3.09 * vent)) ** 2
        self.p_lp = lag(self.p_lp, p_lp_ss, 60.0, dt)

        # Purity: a static map of (O2 draw ratio, reflux ratio, GAN draw, sump level) passed
        # through dead time and a slow lag — research §4.5.
        # Liquid drawn from a sump that is fuller than normal is surplus inventory (e.g. what piled up
        # while P-501 was stopped), not production: it doesn't pull N2/Ar down the column. Only a
        # sustained over-draw does. Credit the drawdown fully above 75 %, not at all below 65 %.
        overdraw = max(0.0, self.gox + self.lox - o2_avail)
        surplus = clamp((self.l404 - 65.0) / 10.0, 0.0, 1.0)
        draw = (self.gox + self.lox - surplus * overdraw) / (O2_FRACTION * air_eff)
        rr = self.reflux / air_eff
        gr = self.gan / air_eff
        level_pen = 1.0 + max(0.0, 45.0 - self.l404) / 10.0
        dT_cond = t_sat_n2(self.p_hp) - t_sat_o2(self.p_lp)
        cond_pen = 1.0 + max(0.0, 1.2 - dT_cond) * 2.0
        imp_ss = clamp(0.35 * math.exp(clamp((draw - O2_RECOVERY) / 0.020, -3, 5))
                       * math.exp(clamp((rr - 0.276) / 0.05, -3, 3)) * level_pen * cond_pen, 0.15, 6.0)
        ppm_ss = clamp(3.0 * math.exp(clamp(-(rr - 0.276) / 0.012, -3, 5)) * math.exp(clamp((gr - 0.172) / 0.06, -3, 3)), 0.3, 250.0)
        if self.f_air < 0.25 * AIR_DESIGN:
            imp_ss, ppm_ss = self.imp_gox, self.ppm_gan   # column not separating: hold, drift
        self.imp_gox = lag(self.imp_gox, self.dead_gox.step(imp_ss), 600.0, dt)
        self.ppm_gan = lag(self.ppm_gan, self.dead_gan.step(ppm_ss), 300.0, dt)
        self.dT_cond = dT_cond

        hc_amb = 6.0 if sc.on("hydrocarbon_event") else 1.0
        # Every liquid leaving the sump flushes it: with internal compression that is the whole
        # pumped-LOX product, so hydrocarbons only concentrate when P-501 is down.
        flush = max(self.lox + self.gox + 0.05, 0.1)
        thc_ss = 45.0 * hc_amb * math.sqrt(0.85 / flush) * min(1.0, load + 0.05)
        self.thc = lag(self.thc, thc_ss, 1800.0, dt)
        c2h2_ss = (0.02 * hc_amb + 0.04 * max(0.0, self.co2 - 0.5)) * math.sqrt(0.85 / flush)
        if load > 0.1:      # no air, no new acetylene (and no flush): the sump just sits
            self.c2h2 = lag(self.c2h2, c2h2_ss, 1200.0, dt)

        # ---- blocks 5/6: headers, consumers, tanks, vaporizer ----
        eo = 14.6 * (1.0 + self.w_eo.step(dt)) * (1.2 if sc.on("eo_demand_step") else 1.0)
        ace = 11.7 * (2.0 / 3.0 if sc.on("acetylene_burner_trip") else 1.0)
        misc = max(0.0, 2.7 * (1.0 + self.w_misc.step(dt)))
        self.o2_demand = (eo + ace + misc) * math.sqrt(max(self.p_gox, 0.0) / 30.0)
        self.consumers = {"EO": eo, "Acetylene": ace, "Other": misc}

        if random.random() < dt / 2400.0:        # a purge job every ~40 min on average
            self.purges.append([t + random.uniform(300, 1200), random.uniform(2.0, 6.0)])
        self.purges = [p for p in self.purges if p[0] > t]
        n2 = 24.0 * (1.0 + self.w_n2.step(dt)) + sum(p[1] for p in self.purges) + (10.0 if sc.on("n2_purge_campaign") else 0.0)
        self.n2_demand = n2 * math.sqrt(max(self.p_gan, 0.0) / 8.0)
        self.consumers["N2 grid"] = n2

        # The pumped LOX boils in E-301 only if enough booster air condenses against it. Short of
        # ~95 % of the design ratio the exchanger pinches and cold (eventually liquid) oxygen leaves
        # the warm end towards the carbon-steel header.
        if self.gox > 1.0:
            ratio = self.f_bst / (BOOSTER_RATIO * self.gox)
            deficit = clamp((0.95 - ratio) / 0.35, 0.0, 1.0)
            # The outlet cools as fast as cold oxygen arrives: the time constant scales with flow.
            self.t_gox = lag(self.t_gox, self.t108 - 2.5 - 200.0 * deficit ** 1.5, 45.0 * 29.0 / max(self.gox, 3.0), dt)
        else:
            self.t_gox = lag(self.t_gox, t_amb, 300.0, dt)     # no flow: the line warms up
        self.p511 = rp * (self.p_gox + 2.5 + 0.8 * self.gox / 29.0) * (1.0 - 0.6 * self.cav) \
            + (1 - rp) * max(0.0, self.p_lp - P_ATM + 0.3)

        # Backup pumps P-505 (LOX) / P-506 (LIN) lift tank liquid (~0.5 bar(g)) to header pressure; the
        # vaporizers E-507 / E-508 boil it. Kept cold on minimum-flow recirculation, so they deliver as
        # soon as their flow valve opens. No pump, no send-out.
        if not do["HS-505"]:
            self.p505_trip_latched = False
        self.p505_run = lag(self.p505_run, 1.0 if do["HS-505"] and not self.p505_trip_latched else 0.0, 3.0, dt)
        self.p506_run = lag(self.p506_run, 1.0 if do["HS-506"] else 0.0, 3.0, dt)
        r5 = self.p505_run if self.p505_run > 0.02 else 0.0
        r6 = self.p506_run if self.p506_run > 0.02 else 0.0
        vap_ss = r5 * 30.0 * clamp(ao["FV-507"], 0, 100) / 100.0 if self.l505 > 2.0 else 0.0
        self.vap = lag(self.vap, vap_ss, 20.0, dt)
        vapn_ss = r6 * LIN_VAP_CAPACITY * clamp(ao["FV-508"], 0, 100) / 100.0 if self.l506 > 2.0 else 0.0
        self.vap_n = lag(self.vap_n, vapn_ss, 20.0, dt)
        self.p_gox = clamp(self.p_gox + 0.00556 * (self.gox + self.vap - self.o2_demand) * dt, 0.0, 40.0)
        self.p_gan = clamp(self.p_gan + GAN_HEADER_GAIN * (self.gan + self.vap_n - self.n2_demand) * dt, 0.0, 12.0)
        self.l505 = clamp(self.l505 + (self.lox - self.vap) / LOX_TANK_CAPACITY / 36.0 * dt, 0.0, 100.0)
        self.l506 = clamp(self.l506 + (self.lin - self.vap_n) / LIN_TANK_CAPACITY / 36.0 * dt, 0.0, 100.0)
        # Discharge: shut-off head on recirculation, falling with send-out; a nearly empty tank starves
        # the suction. Stopped: tank pressure.
        starve5 = 1.0 - 0.6 * clamp((5.0 - self.l505) / 5.0, 0.0, 1.0)
        starve6 = 1.0 - 0.6 * clamp((5.0 - self.l506) / 5.0, 0.0, 1.0)
        self.p516 = r5 * (self.p_gox + 1.5 + 4.0 * (1.0 - self.vap / 30.0)) * starve5 + (1 - r5) * 0.5
        self.p517 = r6 * (self.p_gan + 1.0 + 3.0 * (1.0 - self.vap_n / LIN_VAP_CAPACITY)) * starve6 + (1 - r6) * 0.5

        # K-503 discharge, power and vibration (the header sets the discharge pressure).
        p_suc = max(self.p_hp - 0.25, 1.0)
        p_d = self.p_gan + P_ATM + 0.4 * (self.gan / GAN_DESIGN) ** 2
        self.p513 = rg * (p_d - P_ATM) + (1 - rg) * max(0.0, p_suc - P_ATM)
        gan_pw = (GAN_POWER_DESIGN * (self.gan / GAN_DESIGN)
                  * math.log(max(p_d / p_suc, 1.0)) / math.log(GAN_RATIO_DESIGN))
        self.gan_power = lag(self.gan_power, gan_pw + 0.05 * rg, 3.0, dt)
        # Below ~8 kNm3/h the compressor approaches surge: vibration rises sharply.
        self.gan_vib = rg * (1.8 + 10.0 * max(0.0, (8.0 - self.gan) / 8.0)) + (1 - rg) * 0.3

    # ---- outputs ----
    def measurements(self):
        g = random.gauss
        running = self.mac_run > 0.5
        return {
            "FT-101": max(0.0, self.f_air + g(0, 0.25)),
            "PT-102": self.p_dis + g(0, 0.004),
            "FT-103": max(0.0, self.f_comp + g(0, 0.3)),
            "JT-104": max(0.0, self.power + g(0, 0.015)),
            "VT-105": max(0.0, self.vib + g(0, 0.08)),
            "TT-106": self.t106 + g(0, 0.08),
            "PT-107": 2.0 + g(0, 0.01),
            "TT-108": self.t108 + g(0, 0.05),
            "TT-109": self.t_amb + g(0, 0.05),
            "FT-111": max(0.0, self.f_bst + g(0, 0.12)),
            "PT-112": self.p_bst + g(0, 0.05),
            "JT-113": max(0.0, self.bst_power + g(0, 0.01)),
            "VT-114": max(0.0, self.bst_vib + g(0, 0.07)),
            "AT-202": max(0.0, self.co2 + g(0, 0.004)),
            "PDT-203": max(0.0, 60.0 * (self.f_air / AIR_DESIGN) ** 2 + 40.0 * self.switch_transient + g(0, 0.4)),
            "TT-204": self.t204 + g(0, 0.3),
            "ZT-205": self.stroke_time,
            "TT-301": -174.0 + 0.5 * self.dt_coldbox + 0.6 * (1.0 - min(1.0, self.f_air / AIR_DESIGN)) + g(0, 0.03),
            "TDT-302": (3.0 + self.fouling + 0.6 * (self.f_air / AIR_DESIGN - 1.0)
                        + 3.0 * min(1.0, abs(self.f_bst - BOOSTER_RATIO * self.gox) / BOOSTER_DESIGN) + g(0, 0.03))
                       if running else 0.5 + self.fouling,
            "FT-303": max(0.0, self.exp_flow + g(0, 0.06)),
            "ST-304": max(0.0, self.exp_speed + g(0, 25)) if self.exp_speed > 200 else 0.0,
            "VT-305": max(0.0, 15.0 * (self.exp_speed / EXPANDER_SPEED_DESIGN) ** 2 * (1 + 0.6 * max(0, self.exp_overspeed - 0.5)) + g(0, 0.4)),
            "TT-306": self.t306 + g(0, 0.1),
            "PT-401": self.p_hp + g(0, 0.003),
            "PT-402": self.p_lp + g(0, 0.002),
            "LT-403": clamp(self.l403 + g(0, 0.3), 0, 100),
            "LT-404": clamp(self.l404 + g(0, 0.15), 0, 100),
            "FT-405": max(0.0, self.reflux + g(0, 0.12)),
            "TT-406": t_sat_o2(self.p_lp) - 0.3 * self.imp_gox + g(0, 0.02),
            "TT-407": t_sat_n2(self.p_hp) + g(0, 0.02),
            "FT-408": max(0.0, self.lox + g(0, 0.01)),
            "FT-409": max(0.0, self.lin + g(0, 0.01)),
            "FT-501": max(0.0, self.gox + g(0, 0.06)),
            "PT-502": max(0.0, self.p_gox + g(0, 0.02)),
            "FT-503": max(0.0, self.gan + g(0, 0.08)),
            "PT-504": max(0.0, self.p_gan + g(0, 0.01)),
            "LT-505": clamp(self.l505 + g(0, 0.02), 0, 100),
            "LT-506": clamp(self.l506 + g(0, 0.02), 0, 100),
            "FT-507": max(0.0, self.vap + g(0, 0.03) if self.vap > 0.05 else 0.0),
            "FT-508": max(0.0, self.vap_n + g(0, 0.03) if self.vap_n > 0.05 else 0.0),
            "PT-511": max(0.0, self.p511 + g(0, 0.05 + 1.5 * self.cav)),
            "TT-512": self.t_gox + g(0, 0.05),
            "PT-513": max(0.0, self.p513 + g(0, 0.01)),
            "JT-514": max(0.0, self.gan_power + g(0, 0.003)),
            "VT-515": max(0.0, self.gan_vib + g(0, 0.06)),
            "PT-516": max(0.0, self.p516 + g(0, 0.05)),
            "PT-517": max(0.0, self.p517 + g(0, 0.02)),
            "AT-601": clamp(100.0 - self.imp_gox + g(0, 0.005), 90.0, 100.0),
            "AT-602": max(0.0, self.ppm_gan + g(0, 0.05)),
            "AT-603": max(0.0, self.thc + g(0, 0.5)),
            "AT-604": max(0.0, self.c2h2 + g(0, 0.002)),
            "HB-900": float(self.heartbeat),
        }

    def discretes(self):
        return {
            "YS-101": self.mac_run > 0.9,
            "YS-301": self.exp_run > 0.9,
            "YS-102": self.bac_run > 0.9,
            "YS-501": self.loxp_run > 0.9,
            "YS-503": self.gnc_run > 0.9,
            "YS-505": self.p505_run > 0.9,
            "YS-506": self.p506_run > 0.9,
            "ZS-201A": not self.bed_b_online,
            "ZS-201B": self.bed_b_online,
        }

    def snapshot(self):
        """Ground truth for the instructor station — includes hidden states the DCS cannot see."""
        r1 = lambda v, n=2: round(v, n)
        return {
            "sim_time": self.t,
            "environment": {"ambient_C": r1(self.t_amb), "heatwave_offset_K": r1(self.heatwave)},
            "consumers": {k: r1(v) for k, v in self.consumers.items()},
            "o2_demand": r1(self.o2_demand), "n2_demand": r1(self.n2_demand),
            "purge_jobs": len(self.purges),
            "hidden": {
                "mac_efficiency": r1(self.mac_eff, 4),
                "mac_bearing_degradation": r1(self.bearing, 3),
                "mac_trip_latched": self.mac_trip_latched,
                "surge_margin_pct": r1(self.surge_margin, 1),
                "ppu_loading": {k: r1(v, 3) for k, v in self.loading.items()},
                "coldbox_dT_K": r1(self.dt_coldbox, 3),
                "mhe_fouling_K": r1(self.fouling, 2),
                "expander_overspeed": r1(self.exp_overspeed, 2),
                "condenser_dT_K": r1(self.dT_cond, 2),
                "waste_n2": r1(self.waste, 1),
                "booster_trip_latched": self.bac_trip_latched,
                "lox_pump_cavitation": r1(self.cav, 2),
                "booster_excess_air": r1(self.bst_excess, 1),
                "gan_compressor_trip_latched": self.gnc_trip_latched,
                "lox_backup_pump_trip_latched": self.p505_trip_latched,
            },
            "commands": {"ao": {k: r1(v, 1) for k, v in self.ao.items()}, "do": dict(self.do)},
            "scenarios": {
                k: {**{f: v for f, v in spec.items()}, "active": self.sc.active[k],
                    "started": self.sc.started[k]}
                for k, spec in SCENARIOS.items()
            },
        }
