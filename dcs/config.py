"""DCS configuration for HCW ASU1: blocks, tags, controllers, alarm limits, interlocks.

This is the DCS "engineering database". Everything the operator console, the historian and the
OPC UA server show is generated from it — add a tag here, not in the HMI or the OPC UA code.

Tag kinds:
    AI    analog indicator, PV read from a field point (`io`)
    PID   controller: PV from `io` (or a calculated tag via `pv`), output either to a final
          element (`out`) or to cascade slaves (`cascade`), or as an override limit (`limits`)
    CALC  value calculated from other tags (`fn` is evaluated in DCS.calc())
Equipment / interlocks / sequences are configured further down.

`suppress` names the equipment whose run state gates this tag's alarms (state-based alarming,
ISA-18.2): a stopped MAC must not flood the alarm list with low-flow alarms — the same rule
SHT applied with its "unit not running => don't evaluate limits".
"""

BLOCKS = [
    ("MainAirCompressor", "1 · Air intake, main air & booster compressors"),
    ("FrontEndPurification", "2 · Front-end purification (mole sieve)"),
    ("ColdBoxHeatExchange", "3 · Main heat exchanger & expander"),
    ("DoubleColumn", "4 · Linde double column"),
    ("ProductDelivery", "5 · Product delivery"),
    ("BackupStorage", "6 · Backup & storage"),
    ("Energy", "7 · Energy"),
]

ENTERPRISE = "HCW"
SITE = "Haltenberg"
UNIT = "ASU1"
UNIT_LABEL = "Air Separation Unit 1"


def ai(tag, desc, unit, lo, hi, io, block, dec=2, alarms=None, suppress=None):
    return dict(tag=tag, kind="AI", desc=desc, unit=unit, lo=lo, hi=hi, io=io, block=block,
                dec=dec, alarms=alarms or {}, suppress=suppress)


def pid(tag, desc, unit, lo, hi, block, io=None, pv=None, out=None, cascade=None, limits=None,
        action="reverse", kc=1.0, ti=60.0, sp=0.0, op=0.0, mode="AUTO", sp_lo=None, sp_hi=None,
        op_lo=0.0, op_hi=100.0, sp_rate=None, dec=2, alarms=None, suppress=None, modes=("MAN", "AUTO"),
        ppu_time=False, ratio_of=None, ratio=None, ratio_lo=None, ratio_hi=None, xlimit=None, sp_cap=None):
    # Cross-limiting ratio control (as in burner air/fuel control):
    #   ratio_of: in CAS this loop's setpoint is ratio x max(PV, demand) of that tag -> it leads on
    #             increase and follows on decrease. The operator sets the ratio (ratio_lo..ratio_hi).
    #   xlimit:   this loop's setpoint is capped at PV(xlimit) / its ratio -> it can never ask for more
    #             than the measured ratio flow can handle.
    # sp_cap: this loop's setpoint is capped at the PV of that (calculated) tag, e.g. a load limit.
    return dict(sp_cap=sp_cap, ppu_time=ppu_time, ratio_of=ratio_of, ratio=ratio, ratio_lo=ratio_lo, ratio_hi=ratio_hi,
                xlimit=xlimit, tag=tag, kind="PID", desc=desc, unit=unit, lo=lo, hi=hi, io=io, pv=pv, out=out,
                cascade=cascade or [], limits=limits, action=action, kc=kc, ti=ti, sp=sp, op=op,
                mode=mode, sp_lo=lo if sp_lo is None else sp_lo, sp_hi=hi if sp_hi is None else sp_hi,
                op_lo=op_lo, op_hi=op_hi, sp_rate=sp_rate, block=block, dec=dec,
                alarms=alarms or {}, suppress=suppress, modes=list(modes))


def calc(tag, desc, unit, lo, hi, block, dec=3, alarms=None, suppress=None):
    return dict(tag=tag, kind="CALC", desc=desc, unit=unit, lo=lo, hi=hi, block=block, dec=dec,
                alarms=alarms or {}, suppress=suppress)


CAS = ("MAN", "AUTO", "CAS")

TAGS = [
    # ---- 1 · MAC ----
    pid("FIC-101", "MAC air flow (load)", "kNm³/h", 0, 180, "MainAirCompressor", io="FT-101", out="FV-101",
        kc=0.4, ti=12, sp=145, op=76.3, op_lo=0, sp_lo=80, sp_hi=175, sp_rate=1.45, dec=1,
        alarms={"L": 120, "H": 165}, suppress="MAC"),
    pid("PIC-102", "MAC discharge pressure (override)", "bar(a)", 0, 8, "MainAirCompressor", io="PT-102",
        limits="FIC-101", kc=2.0, ti=20, sp=6.5, op=100, dec=3, alarms={"H": 6.4, "HH": 6.8}, suppress="MAC"),
    ai("FI-103", "MAC compressor (suction) flow", "kNm³/h", 0, 200, "FT-103", "MainAirCompressor", dec=1, suppress="MAC"),
    pid("UIC-103", "MAC antisurge (surge margin)", "%", -20, 80, "MainAirCompressor", pv="surge_margin",
        out="UV-103", kc=1.5, ti=6, sp=10, op=0, dec=1, alarms={"L": 5}, suppress="MAC"),
    ai("JI-104", "MAC motor power", "MW", 0, 15, "JT-104", "MainAirCompressor", dec=2, alarms={"H": 12.5}),
    ai("VI-105", "MAC bearing vibration (DE)", "mm/s", 0, 25, "VT-105", "MainAirCompressor", dec=2,
       alarms={"H": 7.1, "HH": 11.0}, suppress="MAC"),
    ai("TI-106", "MAC bearing temperature", "°C", 0, 120, "TT-106", "MainAirCompressor", dec=1,
       alarms={"H": 95, "HH": 105}),
    ai("PI-107", "MAC lube-oil pressure", "bar(g)", 0, 4, "PT-107", "MainAirCompressor", dec=2,
       alarms={"L": 1.5, "LL": 1.0}),
    ai("TI-108", "Air temperature after DCAC", "°C", 0, 40, "TT-108", "MainAirCompressor", dec=1, alarms={"H": 20}),
    ai("TI-109", "Ambient air temperature", "°C", -20, 45, "TT-109", "MainAirCompressor", dec=1, alarms={"H": 32}),
    # Booster air compressor K-102 (internal compression): ~30 % of the clean air to ~60 bar(a). It
    # condenses in E-301 against the pumped LOX, so its flow follows FIC-501 in ratio (CAS).
    pid("FIC-111", "Booster air flow (ratio to pumped LOX)", "kNm³/h", 0, 60, "MainAirCompressor", io="FT-111",
        out="FV-111", kc=0.4, ti=8, sp=44, op=73.3, mode="CAS", sp_lo=15, sp_hi=55, dec=1,
        alarms={"L": 20}, suppress="BAC", modes=CAS, ratio_of="FIC-501", ratio=1.52, ratio_lo=1.35, ratio_hi=2.0),
    calc("FFI-111", "Booster air / pumped LOX ratio (FIC-111 ÷ FIC-501)", "–", 0, 3, "MainAirCompressor",
         dec=3, alarms={"L": 1.40}, suppress="LOXP"),
    ai("PI-112", "Booster discharge pressure", "bar(a)", 0, 80, "PT-112", "MainAirCompressor", dec=1,
       alarms={"H": 66, "HH": 70}, suppress="BAC"),
    ai("JI-113", "Booster motor power", "MW", 0, 6, "JT-113", "MainAirCompressor", dec=2, alarms={"H": 5.0}),
    ai("VI-114", "Booster bearing vibration", "mm/s", 0, 25, "VT-114", "MainAirCompressor", dec=2,
       alarms={"H": 7.1, "HH": 11.0}, suppress="BAC"),

    # ---- 2 · PPU ----
    ai("AI-202", "CO2 at PPU outlet", "ppm", 0, 20, "AT-202", "FrontEndPurification", dec=3,
       alarms={"H": 1.0, "HH": 10.0}, suppress="MAC"),
    ai("PDI-203", "PPU bed differential pressure", "mbar", 0, 200, "PDT-203", "FrontEndPurification", dec=1,
       alarms={"H": 100}, suppress="MAC"),
    pid("TIC-204", "Regeneration gas temperature", "°C", 0, 250, "FrontEndPurification", io="TT-204", out="TV-204",
        kc=1.2, ti=300, sp=180, op=0, mode="MAN", dec=1, ppu_time=True),
    ai("ZI-205", "Switching valve stroke time (last)", "s", 0, 60, "ZT-205", "FrontEndPurification", dec=1,
       alarms={"H": 20}),

    # ---- 3 · MHE + expander ----
    ai("TI-301", "MHE cold-end air temperature", "°C", -200, 40, "TT-301", "ColdBoxHeatExchange", dec=2,
       alarms={"H": -170}, suppress="MAC"),
    ai("TDI-302", "MHE warm-end ΔT", "K", 0, 15, "TDT-302", "ColdBoxHeatExchange", dec=2,
       alarms={"H": 5.0, "HH": 7.0}, suppress="MAC"),
    pid("FIC-303", "Expander flow (refrigeration)", "kNm³/h", 0, 30, "ColdBoxHeatExchange", io="FT-303", out="FV-303",
        kc=0.5, ti=8, sp=18, op=60, sp_lo=8, sp_hi=26, dec=2, alarms={"L": 12}, suppress="EXP"),
    ai("SI-304", "Expander speed", "rpm", 0, 40000, "ST-304", "ColdBoxHeatExchange", dec=0,
       alarms={"H": 28000, "HH": 30000}),
    ai("VI-305", "Expander vibration", "µm", 0, 100, "VT-305", "ColdBoxHeatExchange", dec=1,
       alarms={"H": 40, "HH": 60}),
    ai("TI-306", "Expander bearing temperature", "°C", 0, 120, "TT-306", "ColdBoxHeatExchange", dec=1,
       alarms={"H": 85, "HH": 95}),
    # Every stream through E-301 at both ends (step 3). The plant closes an enthalpy balance over the
    # exchanger, so these agree with each other and with the flows; TDI-302 = TI-307 − TI-310.
    ai("TI-307", "Main air into E-301 (warm end, after the PPU)", "°C", -20, 50, "TT-307", "ColdBoxHeatExchange",
       dec=1, suppress="MAC"),
    ai("TI-308", "Booster air into E-301 (warm end, after E-102)", "°C", -20, 60, "TT-308", "ColdBoxHeatExchange",
       dec=1, suppress="BAC"),
    ai("TI-309", "GAN leaving E-301 (warm end, to K-503)", "°C", -50, 50, "TT-309", "ColdBoxHeatExchange",
       dec=1, suppress="GANC"),
    ai("TI-310", "Waste N2 leaving E-301 (warm end, to PPU regeneration)", "°C", -50, 50, "TT-310",
       "ColdBoxHeatExchange", dec=1, suppress="MAC"),
    ai("TI-311", "Booster air leaving E-301 (cold end, liquefied, to HV-116)", "°C", -200, -100, "TT-311",
       "ColdBoxHeatExchange", dec=1, suppress="BAC"),
    ai("TI-312", "Pumped LOX into E-301 (cold end, from P-501)", "°C", -200, -100, "TT-312", "ColdBoxHeatExchange",
       dec=1, suppress="LOXP"),
    ai("TI-313", "GAN into E-301 (cold end, from the HP column)", "°C", -200, -100, "TT-313", "ColdBoxHeatExchange",
       dec=1, suppress="MAC"),
    ai("TI-314", "Waste N2 into E-301 (cold end, from the LP column)", "°C", -200, -100, "TT-314",
       "ColdBoxHeatExchange", dec=1, suppress="MAC"),
    # Expander X-303: a too-warm inlet means a warm cold box or an unbalanced E-301; an outlet close to the
    # dew point (~ −188 °C at 1.4 bar(a)) means liquid in the expander; a warm outlet, too little work extracted.
    ai("TI-315", "Expander inlet temperature (E-301 side draw)", "°C", -160, -40, "TT-315", "ColdBoxHeatExchange",
       dec=1, alarms={"H": -95}, suppress="EXP"),
    ai("PI-316", "Expander inlet pressure", "bar(a)", 0, 8, "PT-316", "ColdBoxHeatExchange", dec=3, suppress="EXP"),
    ai("TI-317", "Expander outlet temperature (to the LP column)", "°C", -200, -100, "TT-317", "ColdBoxHeatExchange",
       dec=1, alarms={"L": -186, "H": -165}, suppress="EXP"),
    ai("PI-318", "Expander outlet pressure", "bar(a)", 0, 3, "PT-318", "ColdBoxHeatExchange", dec=3, suppress="EXP"),

    # ---- 4 · double column ----
    ai("PI-401", "HP column top pressure", "bar(a)", 0, 8, "PT-401", "DoubleColumn", dec=3,
       alarms={"L": 4.8, "H": 5.8}, suppress="MAC"),
    pid("PIC-402", "LP column top pressure", "bar(a)", 0, 3, "DoubleColumn", io="PT-402", out="PV-402",
        action="direct", kc=1.5, ti=60, sp=1.35, op=50, sp_lo=1.15, sp_hi=1.6, dec=3,
        alarms={"L": 1.25, "H": 1.45}, suppress="MAC"),
    pid("LIC-403", "HP column sump level", "%", 0, 100, "DoubleColumn", io="LT-403", out="LV-403",
        action="direct", kc=2.0, ti=300, sp=50, op=53, dec=1, alarms={"L": 30, "H": 70, "HH": 85}, suppress="MAC"),
    # Kc 12: the sump hold-up tripled with internal compression (model LP_SUMP_GAIN), so the gain
    # tripled too, keeping the loop as fast as before without the integral overshooting.
    pid("LIC-404", "Reboiler / LP sump level", "%", 0, 100, "DoubleColumn", io="LT-404", out="LV-404",
        action="direct", kc=12.0, ti=1200, sp=70, op=26.7, dec=1, alarms={"L": 55, "LL": 35, "H": 85, "HH": 95},
        suppress="MAC"),
    pid("FIC-405", "LIN reflux to LP column", "kNm³/h", 0, 60, "DoubleColumn", io="FT-405", out="FV-405",
        kc=0.5, ti=8, sp=40, op=57.1, mode="CAS", sp_lo=20, sp_hi=55, dec=2, suppress="MAC", modes=CAS),
    ai("TI-406", "LP sump (LOX) temperature", "°C", -200, -150, "TT-406", "DoubleColumn", dec=2, suppress="MAC"),
    ai("TI-407", "HP column top (N2) temperature", "°C", -200, -150, "TT-407", "DoubleColumn", dec=2, suppress="MAC"),
    # Range below 0: after a shutdown the HP column depressurises to ~1 bar(a); its N2 then saturates
    # at −196 °C under the −183 °C LOX in the reboiler sump, a real (negative) ΔT of about −13 K.
    calc("TDI-410", "Main condenser ΔT (TI-407 − TI-406)", "K", -15, 10, "DoubleColumn", dec=2,
         alarms={"L": 1.0}, suppress="MAC"),
    # Boiling points from the column pressures (ISA-5.1: Y = compute). No alarms: they are references
    # for the column detail display, compared there with the measured TI-407 / TI-406.
    calc("TY-411", "N2 condensing temperature at HP pressure (PI-401)", "°C", -200, -150, "DoubleColumn",
         dec=2, suppress="MAC"),
    calc("TY-412", "O2 boiling temperature at LP pressure (PIC-402)", "°C", -200, -150, "DoubleColumn",
         dec=2, suppress="MAC"),
    calc("TY-413", "N2 boiling temperature at LP pressure (LIN after FV-405)", "°C", -200, -150, "DoubleColumn",
         dec=2, suppress="MAC"),
    ai("FI-408", "LOX draw to storage", "kNm³/h", 0, 3, "FT-408", "DoubleColumn", dec=3),
    pid("FIC-409", "LIN draw to storage", "kNm³/h", 0, 2, "DoubleColumn", io="FT-409", out="FV-409",
        kc=0.5, ti=8, sp=0.6, op=30, dec=3),
    ai("AI-603", "Total hydrocarbons in LOX", "ppm CH4", 0, 600, "AT-603", "DoubleColumn", dec=1,
       alarms={"H": 250, "HH": 450}),
    ai("AI-604", "Acetylene in LOX", "ppm", 0, 2, "AT-604", "DoubleColumn", dec=3, alarms={"H": 0.3, "HH": 1.0}),

    # ---- 5 · products ----
    pid("FIC-501", "GOX product flow (pumped LOX, P-501)", "kNm³/h", 0, 35, "ProductDelivery", io="FT-501",
        out="FV-501", kc=0.5, ti=8, sp=29, op=72.5, mode="CAS", sp_lo=0, sp_hi=31.5, dec=2, suppress="LOXP",
        modes=CAS, xlimit="FIC-111", sp_cap="FY-501"),
    # Load limit: the pumped LOX can't be more than the oxygen the air brings in. Above it the pump
    # would empty the sump and pull N2/Ar down the LP column (GOX purity). FIC-501's SP is capped here;
    # the header demand above it goes to the backup vaporizer through PIC-502's split range.
    calc("FY-501", "GOX load limit (0.208 × FIC-101 − FI-408 + sump surplus allowance)", "kNm³/h", 0, 35, "ProductDelivery",
         dec=2, suppress="MAC"),
    # Override: with pumped LOX the product is drawn as liquid, so a header-pressure demand the column
    # can't make (e.g. at low air after a restart) would empty the sump. Below its SP this controller
    # takes over FIC-501's output (low select), and the backup vaporizer covers the header instead.
    pid("LIC-415", "Reboiler low-level override (limits P-501)", "%", 0, 100, "ProductDelivery", io="LT-404",
        limits="FIC-501", action="direct", kc=3.0, ti=120, sp=45, op=77.5, sp_lo=30, sp_hi=60, dec=1,
        suppress="LOXP"),
    ai("PI-511", "LOX pump P-501 discharge pressure", "bar(g)", 0, 50, "PT-511", "ProductDelivery", dec=1,
       alarms={"L": 25, "LL": 20}, suppress="LOXP"),
    ai("TI-512", "GOX temperature leaving E-301 (to carbon-steel header)", "°C", -200, 50, "TT-512",
       "ProductDelivery", dec=1, alarms={"L": -5, "LL": -25}, suppress="LOXP"),
    pid("PIC-502", "GOX header pressure (split range GOX / vaporizer)", "bar(g)", 0, 40, "ProductDelivery",
        io="PT-502", cascade=[{"slave": "FIC-501", "op": [0, 80], "sp": [0, "sp_hi"]},
                              {"slave": "FIC-507", "op": [80, 100], "sp": [0, 30]}],
        kc=3.0, ti=240, sp=30, op=73.7, sp_lo=25, sp_hi=34, dec=2, alarms={"LL": 27, "L": 28.5, "H": 33}),
    # GAN from the HP column top (~5.3 bar(a)) is warmed in E-301 and compressed by K-503 to the
    # 8 bar(g) site header. FIC-503 drives the compressor inlet guide vanes; SP low limit 10 = minimum
    # flow (it surges below ~8 kNm³/h, VI-515).
    pid("FIC-503", "GAN product flow (K-503 compressor)", "kNm³/h", 0, 60, "ProductDelivery", io="FT-503",
        out="FV-503", kc=0.5, ti=8, sp=25, op=41.7, mode="CAS", sp_lo=10, sp_hi=45, dec=2, suppress="GANC",
        modes=CAS),
    # Split range like PIC-502: GAN compressor (OP 0-80 %), LIN backup vaporizer E-508 (OP 80-100 %).
    pid("PIC-504", "GAN header pressure (split range GAN / LIN vaporizer)", "bar(g)", 0, 12, "ProductDelivery",
        io="PT-504", cascade=[{"slave": "FIC-503", "op": [0, 80], "sp": [0, "sp_hi"]},
                              {"slave": "FIC-508", "op": [80, 100], "sp": [0, 40]}],
        kc=4.0, ti=200, sp=8, op=44.4, sp_lo=6, sp_hi=10, dec=3, alarms={"LL": 6.5, "L": 7.3, "H": 9.0}),
    ai("PI-513", "GAN compressor K-503 discharge pressure", "bar(g)", 0, 15, "PT-513", "ProductDelivery", dec=2,
       alarms={"H": 10.0, "HH": 11.0}, suppress="GANC"),
    ai("JI-514", "GAN compressor K-503 motor power", "MW", 0, 2, "JT-514", "ProductDelivery", dec=3,
       alarms={"H": 1.3}),
    ai("VI-515", "GAN compressor K-503 vibration", "mm/s", 0, 25, "VT-515", "ProductDelivery", dec=2,
       alarms={"H": 7.1, "HH": 11.0}, suppress="GANC"),
    ai("AI-601", "GOX purity", "% O2", 95, 100, "AT-601", "ProductDelivery", dec=3,
       alarms={"L": 99.5, "LL": 99.0}, suppress="MAC"),
    pid("AIC-602", "O2 in GAN (purity → reflux)", "ppm", 0, 100, "ProductDelivery", io="AT-602",
        cascade=[{"slave": "FIC-405", "op": [0, 100], "sp": [20, 55]}],
        action="direct", kc=1.0, ti=900, sp=3.0, op=57.1, sp_lo=1, sp_hi=10, dec=2,
        alarms={"H": 10, "HH": 50}, suppress="MAC"),

    # ---- 6 · storage & backup ----
    ai("LI-505", "LOX tank level", "%", 0, 100, "LT-505", "BackupStorage", dec=2, alarms={"LL": 10, "L": 20, "H": 90}),
    ai("LI-506", "LIN tank level", "%", 0, 100, "LT-506", "BackupStorage", dec=2, alarms={"LL": 10, "L": 20, "H": 90}),
    # Backup pumps P-505 / P-506 are kept cold: they run all the time on minimum-flow recirculation to
    # their tank, so the vaporizers can take over at once through the split ranges. Flow loops Kc 1.2 /
    # Ti 20 (vaporizer lag ~20 s): with the trip handover the headers stay above LL when their supply trips.
    pid("FIC-507", "LOX backup vaporizer E-507 send-out (P-505)", "kNm³/h", 0, 30, "BackupStorage", io="FT-507",
        out="FV-507", kc=1.2, ti=20, sp=0, op=0, mode="CAS", dec=2, alarms={"H": 0.5}, suppress="LOXB", modes=CAS),
    ai("PI-516", "LOX backup pump P-505 discharge pressure", "bar(g)", 0, 50, "PT-516", "BackupStorage", dec=1,
       alarms={"L": 25}, suppress="LOXB"),
    pid("FIC-508", "LIN backup vaporizer E-508 send-out (P-506)", "kNm³/h", 0, 45, "BackupStorage", io="FT-508",
        out="FV-508", kc=1.2, ti=20, sp=0, op=0, mode="CAS", dec=2, alarms={"H": 0.5}, suppress="LINB", modes=CAS),
    ai("PI-517", "LIN backup pump P-506 discharge pressure", "bar(g)", 0, 20, "PT-517", "BackupStorage", dec=2,
       alarms={"L": 7.0}, suppress="LINB"),

    # ---- 7 · energy ----
    calc("KPI-701", "Specific power ((JI-104 + JI-113 + JI-514) ÷ FIC-501)", "kWh/Nm³", 0, 1, "Energy", dec=4,
         alarms={"H": 0.60}, suppress="MAC"),
]

TAG_INDEX = {t["tag"]: t for t in TAGS}

# The tags each calculated value is computed from in DCS.calc(), for the IT/OT trainer's data path:
# a calculated tag has no field point, its inputs are other tags. UIC-103 is a controller whose PV
# is calculated (the surge margin).
CALC_INPUTS = {
    "UIC-103": ["FI-103", "PIC-102"],
    "FFI-111": ["FIC-111", "FIC-501"],
    "TDI-410": ["TI-407", "TI-406"],
    "TY-411": ["PI-401"],
    "TY-412": ["PIC-402"],
    "TY-413": ["PIC-402"],
    "FY-501": ["FIC-101", "FI-408", "LIC-404"],
    "KPI-701": ["JI-104", "JI-113", "JI-514", "FIC-501"],
}

# Severity per limit type (ISA-18.2-ish: 1 = urgent ... 3 = low)
ALARM_PRIORITY = {"HH": 1, "LL": 1, "H": 2, "L": 2}
# A few alarms are informational rather than abnormal-condition alarms.
PRIORITY_OVERRIDE = {("FIC-507", "H"): 3, ("FIC-508", "H"): 3, ("KPI-701", "H"): 3, ("TI-109", "H"): 3, ("TI-108", "H"): 3}

# ---- Equipment (start/stop from the console) ----
# bypass_s: start-up bypass. Interlock causes armed with this equipment's key only count once it has
# run that long (a stopped machine's low pressures / levels must not block the reset).
EQUIPMENT = {
    "MAC": dict(label="Main air compressor", cmd="HS-101", fb="YS-101", block="MainAirCompressor",
                trip="I-100", bypass_s=600),
    "EXP": dict(label="Turbo-expander", cmd="HS-301", fb="YS-301", block="ColdBoxHeatExchange",
                trip="I-300", bypass_s=0),
    "BAC": dict(label="Booster air compressor K-102", cmd="HS-102", fb="YS-102", block="MainAirCompressor",
                trip="I-110", bypass_s=120),
    "LOXP": dict(label="LOX pump P-501", cmd="HS-501", fb="YS-501", block="ProductDelivery",
                 trip="I-500", bypass_s=60),
    "GANC": dict(label="GAN compressor K-503", cmd="HS-503", fb="YS-503", block="ProductDelivery",
                 trip="I-503", bypass_s=120),
    "LOXB": dict(label="LOX backup pump P-505", cmd="HS-505", fb="YS-505", block="BackupStorage",
                 trip="I-505", bypass_s=30),
    "LINB": dict(label="LIN backup pump P-506", cmd="HS-506", fb="YS-506", block="BackupStorage",
                 trip="I-506", bypass_s=30),
}

# ---- Interlocks ----
# A cause is (tag, limit, armed): armed "always", or an equipment key (e.g. "MAC") = only while that
# equipment has been running for longer than its start-up bypass (otherwise a low level / no oil
# pressure that is perfectly normal for a stopped machine would make the trip impossible to reset).
# parents: interlocks whose trip trips this one too (and that must be reset first).
# handover: {split-range master: tripped slave} — trip feed-forward: the master's OP jumps to where its
#           backup slave (the vaporizer) takes over the lost flow at once.
STARTUP_BYPASS_S = EQUIPMENT["MAC"]["bypass_s"]
INTERLOCKS = {
    "I-100": dict(
        label="ASU shutdown (MAC trip)", block="MainAirCompressor",
        causes=[
            ("VI-105", "HH", "MAC"), ("TI-106", "HH", "always"), ("PI-107", "LL", "MAC"),
            ("AI-202", "HH", "MAC"), ("AI-603", "HH", "always"), ("AI-604", "HH", "always"),
            ("LIC-404", "LL", "MAC"), ("LIC-404", "HH", "MAC"),
        ],
        # Evaluated in control.py rather than as a tag limit:
        extra_causes=["YS-101 MAC stopped while commanded (motor protection)",
                      "AI-202 > 1 ppm for 30 min (beds not switched)", "Manual ESD / operator stop"],
        forces={"FIC-101": ("MAN", 0.0),
                "LIC-404": ("MAN", 0.0), "FIC-409": ("MAN", 0.0), "FIC-405": ("MAN", 0.0)},
        stops=["MAC"], trip_message="ASU shutdown — MAC tripped",
    ),
    "I-300": dict(
        label="Expander trip", block="ColdBoxHeatExchange",
        causes=[("SI-304", "HH", "always"), ("VI-305", "HH", "always"), ("TI-306", "HH", "always")],
        extra_causes=["I-100 ASU shutdown", "Operator stop"],
        forces={"FIC-303": ("MAN", 0.0)},
        stops=["EXP"], parents=["I-100"], trip_message="Expander tripped",
    ),
    "I-110": dict(
        label="Booster air compressor trip", block="MainAirCompressor",
        causes=[("VI-114", "HH", "BAC"), ("PI-112", "HH", "always")],
        extra_causes=["I-100 ASU shutdown", "YS-102 booster stopped while commanded (motor protection)",
                      "Operator stop"],
        forces={"FIC-111": ("MAN", 0.0)},
        stops=["BAC"], parents=["I-100"], trip_message="Booster air compressor tripped",
    ),
    "I-500": dict(
        # Without booster air the pumped LOX can't boil in E-301: liquid oxygen would reach the
        # carbon-steel GOX header. So the pump trips with the booster, and on cold GOX itself.
        label="LOX pump trip (internal compression)", block="ProductDelivery",
        causes=[("TI-512", "LL", "LOXP"), ("PI-511", "LL", "LOXP")],
        extra_causes=["I-100 ASU shutdown", "I-110 booster trip (no booster air to vaporize LOX)",
                      "YS-501 pump stopped while commanded", "Operator stop"],
        forces={"FIC-501": ("MAN", 0.0)}, handover={"PIC-502": "FIC-501"},
        stops=["LOXP"], parents=["I-100", "I-110"], trip_message="LOX pump P-501 tripped",
    ),
    "I-503": dict(
        # No air, no HP-column GAN to compress: the compressor goes down with the ASU, and the LIN
        # backup (P-506 / E-508) holds the GAN header through PIC-504's split range.
        label="GAN compressor trip", block="ProductDelivery",
        causes=[("VI-515", "HH", "GANC"), ("PI-513", "HH", "always")],
        extra_causes=["I-100 ASU shutdown", "YS-503 compressor stopped while commanded (motor protection)",
                      "Operator stop"],
        forces={"FIC-503": ("MAN", 0.0)}, handover={"PIC-504": "FIC-503"},
        stops=["GANC"], parents=["I-100"], trip_message="GAN compressor K-503 tripped",
    ),
    # The backup pumps deliberately have no parent: they must keep running when the ASU trips.
    "I-505": dict(
        label="LOX backup pump trip", block="BackupStorage",
        causes=[("LI-505", "LL", "LOXB")],
        extra_causes=["YS-505 pump stopped while commanded (motor protection)", "Operator stop"],
        forces={"FIC-507": ("MAN", 0.0)},
        stops=["LOXB"], trip_message="LOX backup pump P-505 tripped — no GOX backup",
    ),
    "I-506": dict(
        label="LIN backup pump trip", block="BackupStorage",
        causes=[("LI-506", "LL", "LINB")],
        extra_causes=["YS-506 pump stopped while commanded (motor protection)", "Operator stop"],
        forces={"FIC-508": ("MAN", 0.0)},
        stops=["LINB"], trip_message="LIN backup pump P-506 tripped — no GAN backup",
    ),
}
CO2_BREAKTHROUGH_TRIP_S = 1800
# FY-501 GOX load limit = GOX_AIR_LIMIT x air - LOX to storage. 0.208 kNm3 O2 per kNm3 air is just above the
# normal GOX + LOX peaks at design (purity ~99.4 % if held there): a sustained over-demand shows as a
# purity L alarm and backup-vaporizer send-out, not as a purity crash.
GOX_AIR_LIMIT = 0.208
PER_GOX_MIN_FLOW = 10.0     # kNm3/h: KPI-701 / FFI-111 only while P-501 delivers at least this (else not applicable)
GOX_SUMP_ALLOWANCE = 0.5    # kNm3/h per % that LIC-404 stands above its SP (surplus liquid, already separated)

# Design line-up: the modes the "Normal line-up" button restores (after a restart).
LINEUP = {
    "FIC-101": "AUTO", "PIC-102": "AUTO", "UIC-103": "AUTO", "FIC-303": "AUTO", "PIC-402": "AUTO",
    "LIC-403": "AUTO", "LIC-404": "AUTO", "AIC-602": "AUTO", "FIC-405": "CAS", "FIC-409": "AUTO",
    "PIC-502": "AUTO", "FIC-501": "CAS", "PIC-504": "AUTO", "FIC-503": "CAS", "FIC-507": "CAS",
    "FIC-111": "CAS", "LIC-415": "AUTO", "FIC-508": "CAS",
}

# ---- PPU sequence KS-201: step durations as fractions of the bed cycle ----
PPU_STEPS = [("DEPRESS", 0.02), ("HEAT", 0.40), ("COOL", 0.40), ("REPRESS", 0.03), ("STANDBY", None)]
# Allowed bed cycle range: the instructor's time-compression slider (plant/web/instructor.html).
PPU_DESIGN_CYCLE = 14400.0
PPU_CYCLE_MIN, PPU_CYCLE_MAX = 300.0, 14400.0
REGEN_SP = 180.0
REGEN_PEAK_MIN = 160.0
