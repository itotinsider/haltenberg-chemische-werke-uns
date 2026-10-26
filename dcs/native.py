"""Site-native OPC UA presentation: how each site's DCS shows its data to the IT side.

All three sites run the same DCS engine, with the same engine tags (FIC-101, ...), on their operator
consoles and in their historians. What a client sees over OPC UA differs per site, the way it does
between real plants built by different contractors with different DCS vendors and standards:

    hcw  Haltenberg  ISA-5.1 tags, structured by process block, AnalogItems with units and ranges,
                     string NodeIds HCW.ASU1.<tag>.<attr>, kNm3/h / bar / degC / MW, modes and alarm
                     states as strings. The reference (opcua_server.OpcUaServer, unchanged).
    us   Port Arden  A flat "point database": one object per point (71FC1001), its parameters as
                     plain variables (PV, SP, OP, MODE, ALMSTAT, EUDESC, PVEULO/PVEUHI). US customary
                     units (MSCFH, PSIG, DEGF, HP, GAL), modes and alarm states as integers, terse
                     upper-case descriptions.
    in   Vadhana     Asset codes in the style of KKS (02MAA10CF001): system folders, a measuring point
                     and its controller are separate objects, numeric NodeIds (browse by name!), SI with
                     local habits (Nm3/h, kg/cm2, kelvin in the cold box, ppb, tonnes in the tanks,
                     kWh/t O2), and source timestamps in local time (IST) although OPC UA wants UTC.

`table()` describes the address space of the current site as data: the folders and objects, and every
variable with the engine value it carries (`src`, as in opcua_server.engine_values) and how that value is
converted (`conv` = native = engine x factor + offset, `enc` = enum mapping). NativeOpcUaServer builds the
us / in address spaces from it; for hcw it describes what OpcUaServer builds. tools/inventory.py joins
these tables with the standard model (dataops/uns_model.py) into the mapping in inventory/uns/.
"""

import logging
from datetime import datetime, timedelta, timezone

from asyncua import ua
from asyncua.common.instantiate_util import instantiate

import config as C
import opcua_server as O

log = logging.getLogger("dcs.opcua")

SITE = C.SITE_CFG
PROFILE = SITE["profile"]

PROFILES = {
    "hcw": dict(namespace=O.NAMESPACE, path="/hcw/asu1/", server_name="HCW ASU1 DCS",
                app_uri="urn:haltenberg:hcw:asu1:dcs:server", ts_offset_s=0, nodeids="string"),
    "us": dict(namespace="urn:hcw-americas:port-arden:asu7100:dcs", path="/PortArden/ASU7100",
               server_name="Port Arden ASU 7100 DCS", app_uri="urn:hcw-americas:port-arden:asu7100:dcs:server",
               ts_offset_s=0, nodeids="string"),
    # Source timestamps in IST (+05:30) labelled as UTC: a real and common gateway misconfiguration.
    "in": dict(namespace="urn:hcw-india:vadhana:asu2", path="/vadhana/asu2", server_name="VDH ASU-2 OPC Server",
               app_uri="urn:hcw-india:vadhana:asu2:opcserver", ts_offset_s=19800, nodeids="numeric"),
}
META = PROFILES[PROFILE]

# Storage tanks, engine design (plant/model.py LOX_TANK_CAPACITY / LIN_TANK_CAPACITY, kNm3 gas), scaled
# per site. The US site reports tank contents in gallons of liquid, Vadhana in tonnes: converting
# back to a level needs this asset master data.
TANK_KNM3 = {"LI-505": ("O2", 1050.0), "LI-506": ("N2", 700.0)}
LIQUID_L_PER_NM3 = {"O2": 1.2524, "N2": 1.5497}       # litres of liquid per Nm3 of gas
KG_PER_NM3 = {"O2": 1.429, "N2": 1.2506}
GAL_L = 3.78541
SCF_PER_NM3 = 37.326          # 1 Nm3 (0 degC, 1.01325 bar) = 37.326 scf (60 degF, 14.696 psia)


def tank_capacity(tag, unit):
    gas, knm3 = TANK_KNM3[tag]
    nm3 = knm3 * 1000.0 * C.SCALE
    return nm3 * LIQUID_L_PER_NM3[gas] / GAL_L if unit == "GAL" else nm3 * KG_PER_NM3[gas] / 1000.0


# ---------------------------------------------------------------- units: engine unit -> (native unit, factor, offset)
US_UNITS = {
    "kNm³/h": ("MSCFH", SCF_PER_NM3, 0.0), "bar(a)": ("PSIA", 14.5038, 0.0), "bar(g)": ("PSIG", 14.5038, 0.0),
    "mbar": ("INH2O", 0.40146, 0.0), "°C": ("DEGF", 1.8, 32.0), "K": ("DEGF", 1.8, 0.0), "MW": ("HP", 1341.02, 0.0),
    "mm/s": ("IN/S", 1 / 25.4, 0.0), "µm": ("MILS", 1 / 25.4, 0.0), "rpm": ("RPM", 1.0, 0.0), "%": ("%", 1.0, 0.0),
    "% O2": ("%", 1.0, 0.0), "ppm": ("PPM", 1.0, 0.0), "ppm CH4": ("PPM", 1.0, 0.0), "s": ("SEC", 1.0, 0.0),
    "kWh/Nm³": ("KWH/MSCF", 1000.0 / SCF_PER_NM3, 0.0), "–": ("", 1.0, 0.0),
}
IN_UNITS = {
    "kNm³/h": ("Nm3/h", 1000.0, 0.0), "bar(a)": ("kg/cm2 a", 1.019716, 0.0), "bar(g)": ("kg/cm2 g", 1.019716, 0.0),
    "mbar": ("mmWC", 10.19716, 0.0), "°C": ("°C", 1.0, 0.0), "K": ("K", 1.0, 0.0), "MW": ("kW", 1000.0, 0.0),
    "mm/s": ("mm/s", 1.0, 0.0), "µm": ("µm", 1.0, 0.0), "rpm": ("rpm", 1.0, 0.0), "%": ("%", 1.0, 0.0),
    "% O2": ("%", 1.0, 0.0), "ppm": ("ppm", 1.0, 0.0), "ppm CH4": ("ppm", 1.0, 0.0), "s": ("s", 1.0, 0.0),
    "kWh/Nm³": ("kWh/t", 1.0 / (KG_PER_NM3["O2"] / 1000.0), 0.0), "–": ("", 1.0, 0.0),
}


def pv_unit(t):
    """(native unit, factor, offset) of a tag's PV and SP at this site."""
    u = t["unit"]
    if PROFILE == "hcw":
        return u, 1.0, 0.0
    if t["tag"] in TANK_KNM3:
        unit = "GAL" if PROFILE == "us" else "t"
        return unit, tank_capacity(t["tag"], unit) / 100.0, 0.0
    if PROFILE == "us":
        return US_UNITS[u]
    if u == "°C" and t["lo"] <= -150 and t["block"] in ("ColdBoxHeatExchange", "DoubleColumn"):
        return "K", 1.0, 273.15                      # Vadhana's cold-box engineers think in kelvin
    if t["tag"] == "AI-604":
        return "ppb", 1000.0, 0.0                    # acetylene in ppb
    return IN_UNITS[u]


MODE_ENC = {"us": {"MAN": 0, "AUTO": 1, "CAS": 2}, "in": {"MAN": "MAN", "AUTO": "AUTO", "CAS": "CASC"}}
ALARM_ENC = {"us": {"NORMAL": 0, "L": 1, "H": 2, "LL": 3, "HH": 4},
             "in": {"NORMAL": "OK", "L": "LO", "H": "HI", "LL": "LOLO", "HH": "HIHI"}}


# ---------------------------------------------------------------- Port Arden: point names
US_EQUIPMENT = {"MAC": "71K1001", "BAC": "71K1002", "EXP": "71X3003", "LOXP": "71P5001A", "LOXPB": "71P5001B",
                "GANC": "71K5003", "LOXB": "71P5005", "LINB": "71P5006"}
US_EXCEPTIONS = {"TI-109": "WX_AMBTEMP", "KPI-701": "71EN_SPCPWR", "AI-601": "71AT6001"}
US_ABBREV = [("main air compressor", "MAC"), ("air separation", "ASU"), ("temperature", "TEMP"), ("pressure", "PRESS"),
             ("discharge", "DISCH"), ("compressor", "COMP"), ("vibration", "VIB"), ("bearing", "BRG"),
             ("booster", "BSTR"), ("expander", "EXP"), ("product", "PROD"), ("header", "HDR"), ("column", "COL"),
             ("differential", "DIFF"), ("regeneration", "REGEN"), ("ambient", "AMB"), ("motor", "MTR"),
             ("power", "PWR"), ("level", "LVL"), ("reboiler", "RBLR"), ("vaporizer", "VAP"), ("backup", "BKUP"),
             ("nitrogen", "N2"), ("oxygen", "O2"), ("specific", "SPEC"), ("hydrocarbons", "HC"), ("acetylene", "C2H2"),
             ("leaving", "OUT"), ("into", "IN"), ("liquefied", "LIQ"), ("condensing", "COND"), ("boiling", "BOIL"),
             ("switching", "SW"), ("purification", "PURIF"), ("stroke", "STRK"), ("override", "OVRD"), ("limit", "LIM"),
             ("draw to storage", "TO STOR"), ("storage", "STOR"), ("waste", "WST"), ("purity", "PUR"), ("speed", "SPD"),
             ("inlet", "INL"), ("outlet", "OUTL"), ("flow", "FLOW"), ("ratio", "RATIO"), ("total", "TOT"),
             ("main heat exchanger", "MHE"), (" the ", " "), (" and ", " & "), ("guide vanes", "IGV"), ("surge margin", "SURGE MGN")]


def us_point(tag):
    if tag in US_EXCEPTIONS:
        return US_EXCEPTIONS[tag]
    letters, num = tag.split("-")
    if C.TAG_INDEX[tag]["kind"] == "PID" and letters.endswith("IC"):
        letters = letters[:-2] + "C"                 # FIC -> FC: US practice, no "indicating" letter
    return f"71{letters}{num[0]}0{num[1:]}"


def us_desc(text):
    import re
    d = re.sub(r"\(.*?\)", "", text).replace("—", " ").replace("–", " ").lower()
    for long, short in US_ABBREV:
        d = d.replace(long, short.lower())
    d = re.sub(r"[^a-z0-9/&+.\- ]", "", d)
    return re.sub(r"\s+", " ", d).strip().upper()[:32]


# ---------------------------------------------------------------- Vadhana: asset codes (KKS style)
IN_SYSTEMS = [   # (system code, designation, engine tags in it)
    ("MAA00", "Air intake", ["TI-109"]),
    ("MAA10", "Main air compressor K-101", ["FIC-101", "PIC-102", "FI-103", "UIC-103", "JI-104", "VI-105", "TI-106", "PI-107"]),
    ("MAA20", "Direct-contact aftercooler", ["TI-108"]),
    ("MAB10", "Booster air compressor K-102", ["FIC-111", "FFI-111", "PI-112", "JI-113", "VI-114"]),
    ("MBA10", "Pre-purification unit R-201", ["AI-202", "PDI-203", "TIC-204", "ZI-205"]),
    ("MCA10", "Main heat exchanger E-301", ["TI-301", "TDI-302", "TI-307", "TI-308", "TI-309", "TI-310", "TI-311",
                                            "TI-312", "TI-313", "TI-314"]),
    ("MCB10", "Expander X-303", ["FIC-303", "SI-304", "VI-305", "TI-306", "TI-315", "PI-316", "TI-317", "PI-318"]),
    ("MDA10", "Double column C-401", ["PI-401", "PIC-402", "LIC-403", "LIC-404", "FIC-405", "TI-406", "TI-407",
                                      "TDI-410", "TY-411", "TY-412", "TY-413", "FI-408", "FIC-409", "AI-603", "AI-604"]),
    ("MEA10", "Oxygen product", ["FIC-501", "FY-501", "TI-512", "PIC-502", "AI-601"]),
    ("MEA20", "LOX pump P-501", ["LIC-415", "PI-511"]),
    ("MEB10", "Nitrogen product", ["FIC-503", "PIC-504", "AIC-602"]),
    ("MEB20", "GAN compressor K-503", ["PI-513", "JI-514", "VI-515"]),
    ("MFA10", "LOX storage and backup", ["LI-505", "FIC-507", "PI-516"]),
    ("MFB10", "LIN storage and backup", ["LI-506", "FIC-508", "PI-517"]),
    ("MGA10", "Energy", ["KPI-701"]),
]
IN_EQUIPMENT = {"MAC": ("MAA10", "AN001"), "BAC": ("MAB10", "AN001"), "EXP": ("MCB10", "AN001"),
                "LOXP": ("MEA20", "AP001"), "LOXPB": ("MEA20", "AP002"), "GANC": ("MEB20", "AN001"),
                "LOXB": ("MFA10", "AP001"), "LINB": ("MFB10", "AP001")}
IN_TYPE = {"F": "CF", "P": "CP", "T": "CT", "L": "CL", "A": "CQ", "V": "CY", "J": "CE", "S": "CS", "Z": "CG",
           "U": "CU", "K": "CU"}
UNIT_NO = "02"


def in_codes():
    """{engine tag: (system, measuring point code, controller code or None)}"""
    out, seq = {}, {}
    for system, _, tags in IN_SYSTEMS:
        for tag in tags:
            if tag not in C.TAG_INDEX:
                continue
            typ = IN_TYPE[tag[0]]
            seq[system, typ] = seq.get((system, typ), 0) + 1
            meas = f"{UNIT_NO}{system}{typ}{seq[system, typ]:03d}"
            ctl = None
            if C.TAG_INDEX[tag]["kind"] == "PID":
                seq[system, "DU"] = seq.get((system, "DU"), 0) + 1
                ctl = f"{UNIT_NO}{system}DU{seq[system, 'DU']:03d}"
            out[tag] = (system, meas, ctl)
    return out


# ---------------------------------------------------------------- the table
class Table:
    def __init__(self):
        self.objects, self.variables = [], []
        self._next = 1000

    def nid(self, sid):
        if META["nodeids"] == "string":
            return sid
        self._next += 1
        return self._next

    def obj(self, path, sid, desc, kind="object", props=None):
        node = dict(path=tuple(path), id=self.nid(sid), kind=kind, desc=desc, props=[])
        self.objects.append(node)
        for name, value in (props or {}).items():
            self.var(path + [name], f"{sid}.{name}", None, "String", value=value, prop=True)
        return node

    def var(self, path, sid, src, vtype, unit=None, rng=None, conv=None, enc=None, history=False,
            analog=False, value=None, prop=False):
        node = dict(path=tuple(path), id=self.nid(sid), src=src, type=vtype, unit=unit, range=rng, conv=conv,
                    enc=enc, history=history, analog=analog, value=value, prop=prop)
        self.variables.append(node)
        return node


def _rng(t, f, o):
    return [round(t["lo"] * f + o, 6), round(t["hi"] * f + o, 6)]


def _hcw():
    tb = Table()
    E, S, U = C.ENTERPRISE, C.SITE, C.UNIT
    pre = f"{E}.{U}"
    root = [E, S, U]
    tb.obj([E], E, "Haltenberg Chemische Werke", "folder")
    tb.obj([E, S], f"{E}.{S}", "", "folder")
    tb.obj(root, pre, C.UNIT_LABEL)
    for key, label in C.BLOCKS:
        tb.obj(root + [key], f"{pre}.{key}", label, "folder")
    for t in C.TAGS:
        tag, p = t["tag"], root + [t["block"], t["tag"]]
        tb.obj(p, f"{pre}.{tag}", t["desc"], props={"Description": t["desc"]})
        tb.var(p + ["PV"], f"{pre}.{tag}.PV", f"{tag}.PV", "Double", t["unit"], [t["lo"], t["hi"]], (1.0, 0.0),
               history=True, analog=True)
        if t["kind"] == "PID":
            tb.var(p + ["SP"], f"{pre}.{tag}.SP", f"{tag}.SP", "Double", t["unit"], [t["lo"], t["hi"]], (1.0, 0.0),
                   history=True, analog=True)
            tb.var(p + ["OP"], f"{pre}.{tag}.OP", f"{tag}.OP", "Double", "%", [0, 100], (1.0, 0.0), history=True, analog=True)
            tb.var(p + ["Mode"], f"{pre}.{tag}.Mode", f"{tag}.Mode", "String")
        if t["alarms"]:
            tb.var(p + ["AlarmState"], f"{pre}.{tag}.AlarmState", f"{tag}.AlarmState", "String")
    ks = root + ["FrontEndPurification", "KS-201"]
    tb.obj(ks, f"{pre}.KS-201", "PPU bed switching sequence", props={"Description": "PPU bed switching sequence"})
    for name, vt in [("State", "String"), ("ActiveBed", "String"), ("Step", "String"), ("StepTime", "Double"),
                     ("OnlineTime", "Double"), ("CycleTime", "Double"), ("RegenIncomplete", "Boolean")]:
        tb.var(ks + [name], f"{pre}.KS-201.{name}", f"KS-201.{name}", vt)
    tb.obj(root + ["Equipment"], f"{pre}.Equipment", "Rotating equipment run states", "folder")
    for key, cfg in C.EQUIPMENT.items():
        p = root + ["Equipment", key]
        tb.obj(p, f"{pre}.Equipment.{key}", cfg["label"], props={"Description": cfg["label"]})
        tb.var(p + ["State"], f"{pre}.Equipment.{key}.State", f"Equipment.{key}.State", "String")
        tb.var(p + ["Running"], f"{pre}.Equipment.{key}.Running", f"Equipment.{key}.Running", "Boolean")
    tb.obj(root + ["Interlocks"], f"{pre}.Interlocks", "Safety interlocks / trips", "folder")
    for key, cfg in C.INTERLOCKS.items():
        p = root + ["Interlocks", key]
        tb.obj(p, f"{pre}.Interlocks.{key}", cfg["label"], props={"Description": cfg["label"]})
        tb.var(p + ["Tripped"], f"{pre}.Interlocks.{key}.Tripped", f"Interlocks.{key}.Tripped", "Boolean")
        tb.var(p + ["FirstOut"], f"{pre}.Interlocks.{key}.FirstOut", f"Interlocks.{key}.FirstOut", "String")
    tb.obj(root + ["Diagnostics"], f"{pre}.Diagnostics", "DCS diagnostics", "folder")
    for name, vt in [("FieldIOConnected", "Boolean"), ("ScanCount", "UInt32"), ("ActiveAlarms", "Int32"),
                     ("UnackedAlarms", "Int32"), ("HistorianSamples", "UInt64")]:
        tb.var(root + ["Diagnostics", name], f"{pre}.Diagnostics.{name}", f"Diagnostics.{name}", vt)
    return tb


def _us():
    tb = Table()
    root = ["PTA_ASU7100"]
    tb.obj(root, "PTA_ASU7100", "HCW Americas - Port Arden - ASU 7100 DCS", "folder")
    tb.obj(root + ["Points"], "Points", "Point database", "folder")
    mode, alm = MODE_ENC["us"], ALARM_ENC["us"]

    def point(name, desc):
        p = root + ["Points", name]
        tb.obj(p, name, desc)
        tb.var(p + ["DESC"], f"{name}.DESC", None, "String", value=desc)
        return p

    for t in C.TAGS:
        tag = t["tag"]
        name = us_point(tag)
        p = point(name, us_desc(t["desc"]))
        unit, f, o = pv_unit(t)
        tb.var(p + ["EUDESC"], f"{name}.EUDESC", None, "String", value=unit)
        tb.var(p + ["PV"], f"{name}.PV", f"{tag}.PV", "Double", unit, _rng(t, f, o), (f, o), history=True)
        tb.var(p + ["PVEULO"], f"{name}.PVEULO", None, "Double", value=_rng(t, f, o)[0])
        tb.var(p + ["PVEUHI"], f"{name}.PVEUHI", None, "Double", value=_rng(t, f, o)[1])
        if t["kind"] == "PID":
            tb.var(p + ["SP"], f"{name}.SP", f"{tag}.SP", "Double", unit, _rng(t, f, o), (f, o), history=True)
            tb.var(p + ["OP"], f"{name}.OP", f"{tag}.OP", "Double", "%", [0, 100], (1.0, 0.0), history=True)
            tb.var(p + ["MODE"], f"{name}.MODE", f"{tag}.Mode", "Int32", enc=mode)
        if t["alarms"]:
            tb.var(p + ["ALMSTAT"], f"{name}.ALMSTAT", f"{tag}.AlarmState", "Int32", enc=alm)
    p = point("71KS2001", "PPU BED SWITCHING SEQ")
    for name, src, vt in [("STATE", "State", "String"), ("ACTBED", "ActiveBed", "String"), ("STEP", "Step", "String"),
                          ("STEPTIME", "StepTime", "Double"), ("ONLINETIME", "OnlineTime", "Double"),
                          ("CYCLETIME", "CycleTime", "Double"), ("REGENINC", "RegenIncomplete", "Int32")]:
        tb.var(p + [name], f"71KS2001.{name}", f"KS-201.{src}", vt, enc={False: 0, True: 1} if vt == "Int32" else None)
    for key, cfg in C.EQUIPMENT.items():
        name = US_EQUIPMENT[key]
        p = point(name, us_desc(cfg["label"]))
        tb.var(p + ["PV"], f"{name}.PV", f"Equipment.{key}.Running", "Int32", enc={False: 0, True: 1})
        tb.var(p + ["STATE"], f"{name}.STATE", f"Equipment.{key}.State", "String")
    for key, cfg in C.INTERLOCKS.items():
        name = f"71UZ{key[2]}0{key[3:]}"             # I-500 -> 71UZ5000
        p = point(name, us_desc(cfg["label"]))
        tb.var(p + ["PV"], f"{name}.PV", f"Interlocks.{key}.Tripped", "Int32", enc={False: 0, True: 1})
        tb.var(p + ["FIRSTOUT"], f"{name}.FIRSTOUT", f"Interlocks.{key}.FirstOut", "String")
    tb.obj(root + ["System"], "System", "DCS system status", "folder")
    for name, src, vt, enc in [("IOSTATUS", "FieldIOConnected", "Int32", {False: 0, True: 1}),
                               ("SCANCNT", "ScanCount", "UInt32", None), ("ALMCNT", "ActiveAlarms", "Int32", None),
                               ("UNACKCNT", "UnackedAlarms", "Int32", None), ("HISTCNT", "HistorianSamples", "UInt64", None)]:
        tb.var(root + ["System", name], f"System.{name}", f"Diagnostics.{src}", vt, enc=enc)
    return tb


def _in():
    tb = Table()
    root = ["VDH_ASU2"]
    tb.obj(root, "VDH_ASU2", "HCW India, Vadhana works - ASU-2", "folder")
    mode, alm = MODE_ENC["in"], ALARM_ENC["in"]
    codes = in_codes()
    for system, designation, tags in IN_SYSTEMS:
        here = [t for t in tags if t in C.TAG_INDEX]
        eq = [k for k, (s, _) in IN_EQUIPMENT.items() if s == system and k in C.EQUIPMENT]
        if not here and not eq:
            continue
        sp = root + [UNIT_NO + system]
        tb.obj(sp, UNIT_NO + system, designation, "folder")
        for tag in here:
            t = C.TAG_INDEX[tag]
            _, meas, ctl = codes[tag]
            unit, f, o = pv_unit(t)
            p = sp + [meas]
            tb.obj(p, meas, t["desc"], props={"Designation": t["desc"]})
            tb.var(p + ["XQ01"], f"{meas}.XQ01", f"{tag}.PV", "Double", unit, _rng(t, f, o), (f, o),
                   history=True, analog=True)
            if t["alarms"]:
                tb.var(p + ["XA01"], f"{meas}.XA01", f"{tag}.AlarmState", "String", enc=alm)
            if ctl:
                p = sp + [ctl]
                tb.obj(p, ctl, f"Controller for {meas}", props={"Designation": f"{t['desc']} controller",
                                                                "MeasuringPoint": meas})
                tb.var(p + ["XW01"], f"{ctl}.XW01", f"{tag}.SP", "Double", unit, _rng(t, f, o), (f, o),
                       history=True, analog=True)
                tb.var(p + ["XY01"], f"{ctl}.XY01", f"{tag}.OP", "Double", "%", [0, 100], (1.0, 0.0),
                       history=True, analog=True)
                tb.var(p + ["XM01"], f"{ctl}.XM01", f"{tag}.Mode", "String", enc=mode)
        for key in eq:
            code = UNIT_NO + IN_EQUIPMENT[key][0] + IN_EQUIPMENT[key][1]
            p = sp + [code]
            tb.obj(p, code, C.EQUIPMENT[key]["label"], props={"Designation": C.EQUIPMENT[key]["label"]})
            tb.var(p + ["XB01"], f"{code}.XB01", f"Equipment.{key}.Running", "Boolean")
            tb.var(p + ["XS01"], f"{code}.XS01", f"Equipment.{key}.State", "String")
        if system == "MBA10":
            code = f"{UNIT_NO}MBA10EU001"
            p = sp + [code]
            tb.obj(p, code, "PPU bed switching sequence", props={"Designation": "PPU bed switching sequence"})
            for name, src, vt in [("XS01", "State", "String"), ("XS02", "ActiveBed", "String"), ("XS03", "Step", "String"),
                                  ("XQ01", "StepTime", "Double"), ("XQ02", "OnlineTime", "Double"),
                                  ("XQ03", "CycleTime", "Double"), ("XB01", "RegenIncomplete", "Boolean")]:
                tb.var(p + [name], f"{code}.{name}", f"KS-201.{src}", vt)
    sp = root + [f"{UNIT_NO}CJA00"]
    tb.obj(sp, f"{UNIT_NO}CJA00", "Protection system", "folder")
    for i, (key, cfg) in enumerate(C.INTERLOCKS.items(), 1):
        code = f"{UNIT_NO}CJA00EZ{i:03d}"
        p = sp + [code]
        tb.obj(p, code, cfg["label"], props={"Designation": cfg["label"]})
        tb.var(p + ["XB01"], f"{code}.XB01", f"Interlocks.{key}.Tripped", "Boolean")
        tb.var(p + ["XF01"], f"{code}.XF01", f"Interlocks.{key}.FirstOut", "String")
    sp = root + [f"{UNIT_NO}CYA00"]
    code = f"{UNIT_NO}CYA00GH001"
    tb.obj(sp, f"{UNIT_NO}CYA00", "Control system diagnostics", "folder")
    tb.obj(sp + [code], code, "DCS gateway", props={"Designation": "DCS gateway"})
    for name, src, vt in [("XB01", "FieldIOConnected", "Boolean"), ("XQ01", "ActiveAlarms", "Int32"),
                          ("XQ02", "UnackedAlarms", "Int32"), ("XQ03", "ScanCount", "UInt32"),
                          ("XQ04", "HistorianSamples", "UInt64")]:
        tb.var(sp + [code, name], f"{code}.{name}", f"Diagnostics.{src}", vt)
    return tb


def table():
    return {"hcw": _hcw, "us": _us, "in": _in}[PROFILE]()


def encode(node, value):
    """Engine value -> the value this node publishes (None stays None)."""
    if value is None:
        return None
    if node["enc"] is not None:
        return node["enc"].get(value, value)
    if node["conv"] is not None and node["type"] == "Double":
        f, o = node["conv"]
        return float(value) * f + o
    return value


VT = {"Double": ua.VariantType.Double, "Int32": ua.VariantType.Int32, "String": ua.VariantType.String,
      "Boolean": ua.VariantType.Boolean, "UInt32": ua.VariantType.UInt32, "UInt64": ua.VariantType.UInt64}
DEFAULT = {"Double": 0.0, "Int32": 0, "String": "", "Boolean": False, "UInt32": 0, "UInt64": 0}


class NativeHistory(O.HistorianStorage):
    """HistoryRead from the historian, in the site's units (and with its timestamp habit)."""

    def __init__(self, historian, nodes, offset_s):
        super().__init__(historian, {nid: n["src"] for nid, n in nodes.items()})
        self.nodes, self.offset = nodes, offset_s

    async def read_node_history(self, node_id, start, end, nb_values):
        # The time window is read as real UTC; only the returned timestamps carry the site's offset
        # (Vadhana: the last hour comes back stamped 5.5 h in the future, as live values do).
        shift = timedelta(seconds=self.offset)
        dvs, cont = await super().read_node_history(node_id, start, end, nb_values)
        node = self.nodes[node_id]
        out = []
        for dv in dvs:
            v = dv.Value.Value
            ok = dv.StatusCode.is_good()
            ts = dv.SourceTimestamp + shift
            out.append(ua.DataValue(Value=ua.Variant(encode(node, v) if ok else v, ua.VariantType.Double),
                                    StatusCode=dv.StatusCode, SourceTimestamp=ts, ServerTimestamp=ts))
        return out, cont


class NativeOpcUaServer(O.OpcUaServer):
    """The OPC UA server of a site with its own presentation (Port Arden, Vadhana), built from table()."""

    def __init__(self, historian, port=4840, host="0.0.0.0"):
        super().__init__(historian, port, host)
        self.endpoint = f"opc.tcp://{host}:{port}{META['path']}"
        self.table = table()
        self.nodes = {}         # ua.NodeId -> table variable
        self.by_src = {}        # engine key -> [(ua.NodeId, table variable)]

    async def start(self):
        s = self.server
        await s.init()
        s.set_endpoint(self.endpoint)
        s.set_server_name(META["server_name"])
        await s.set_application_uri(META["app_uri"])
        s.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        self.idx = await s.register_namespace(META["namespace"])
        await self._build()
        hist = {nid: n for nid, n in self.nodes.items() if n["history"] and self.historian and n["src"] in self.historian.ids}
        s.iserver.history_manager.set_storage(NativeHistory(self.historian, hist, META["ts_offset_s"]))
        await s.start()
        log.info("OPC UA server (%s profile) on %s (%d variables, %d historized)", PROFILE, self.endpoint,
                 len(self.nodes), len(hist))

    def _id(self, ident):
        return ua.NodeId(ident, self.idx)

    async def _desc(self, node, text):
        await node.write_attribute(ua.AttributeIds.Description, ua.DataValue(
            ua.Variant(ua.LocalizedText(text), ua.VariantType.LocalizedText)))

    async def _build(self):
        # Auto-generated child ids (EURange, EngineeringUnits) go far above the table's numeric ids.
        self.server.iserver.aspace._nodeid_counter[self.idx] = 900000
        parents = {(): self.server.nodes.objects}
        for o in self.table.objects:
            parent = parents[o["path"][:-1]]
            qn = ua.QualifiedName(o["path"][-1], self.idx)
            if o["kind"] == "folder":
                node = await parent.add_folder(self._id(o["id"]), qn)
            else:
                node = await parent.add_object(self._id(o["id"]), qn)
            if o["desc"]:
                await self._desc(node, o["desc"])
            parents[o["path"]] = node
        for v in self.table.variables:
            parent = parents[v["path"][:-1]]
            name = v["path"][-1]
            vt = VT[v["type"]]
            if v["prop"]:
                await parent.add_property(self._id(v["id"]), ua.QualifiedName(name, self.idx), ua.Variant(v["value"], vt))
                continue
            if v["analog"]:
                node = await self._analog_item(parent, v)
            else:
                init = v["value"] if v["value"] is not None else DEFAULT[v["type"]]
                node = await parent.add_variable(self._id(v["id"]), ua.QualifiedName(name, self.idx), ua.Variant(init, vt))
                if v["history"]:
                    await self._historizing(node)
            if v["src"]:
                self.nodes[node.nodeid] = v
                self.by_src.setdefault(v["src"], []).append((node.nodeid, v))

    async def _analog_item(self, parent, v):
        name = v["path"][-1]
        nodes = await instantiate(parent, self.server.get_node(ua.ObjectIds.AnalogItemType),
                                  nodeid=self._id(v["id"]), bname=ua.QualifiedName(name, self.idx),
                                  dname=ua.LocalizedText(name), instantiate_optional=False)
        node = nodes[0]
        await node.write_attribute(ua.AttributeIds.DataType,
                                   ua.DataValue(ua.Variant(ua.NodeId(ua.ObjectIds.Double), ua.VariantType.NodeId)))
        await node.write_attribute(ua.AttributeIds.ValueRank, ua.DataValue(ua.Variant(-1, ua.VariantType.Int32)))
        await self.server.write_attribute_value(node.nodeid, ua.DataValue(ua.Variant(0.0, ua.VariantType.Double)))
        lo, hi = v["range"]
        for child in await node.get_children():
            if (await child.read_browse_name()).Name == "EURange":
                await child.write_value(ua.Variant(ua.Range(Low=float(lo), High=float(hi)), ua.VariantType.ExtensionObject))
        await node.add_property(ua.NodeId(NamespaceIndex=self.idx), ua.QualifiedName("EngineeringUnits", 0),
                                ua.Variant(ua.EUInformation(
                                    NamespaceUri="http://www.opcfoundation.org/UA/units/un/cefact", UnitId=-1,
                                    DisplayName=ua.LocalizedText(v["unit"]), Description=ua.LocalizedText(v["unit"])),
                                    ua.VariantType.ExtensionObject),
                                datatype=ua.NodeId(ua.ObjectIds.EUInformation))
        if v["history"]:
            await self._historizing(node)
        else:
            await self._access(node, ua.AccessLevel.CurrentRead)
        return node

    async def update(self, snap, ts, scan_count, historian_samples):
        dt = O.to_dt(ts + META["ts_offset_s"])
        io_ok = snap["io_ok"]
        values = O.engine_values(snap, scan_count, historian_samples)
        proc = O.process_keys()
        for src, value in values.items():
            for nid, v in self.by_src.get(src, []):
                status = O.BAD_COMM if src in proc and not io_ok else O.BAD_OOS if value is None else O.GOOD
                bad = status != O.GOOD
                out = encode(v, value)
                if out is None:
                    out = self.last.get(nid, DEFAULT[v["type"]])
                prev = self.last.get(nid)
                if prev == out and not bad and src not in proc:
                    continue
                self.last[nid] = out
                await self.server.write_attribute_value(nid, ua.DataValue(
                    Value=ua.Variant(out, VT[v["type"]]), StatusCode=status,
                    SourceTimestamp=dt, ServerTimestamp=datetime.now(timezone.utc) + timedelta(seconds=META["ts_offset_s"])))


def make_server(historian, port=4840):
    """The site's OPC UA server: the reference server for Haltenberg, the native one elsewhere."""
    if PROFILE == "hcw":
        return O.OpcUaServer(historian, port=port)
    return NativeOpcUaServer(historian, port=port)


def endpoint_path():
    return META["path"]


def nodes_for(tag):
    """[(attr, NodeId string as a client writes it, browse path)] of one engine tag, for /api/datapath."""
    attrs = {"PV", "SP", "OP"}
    out = []
    for v in table().variables:
        if v["src"] and v["src"].split(".")[0] == tag and v["src"].split(".")[-1] in attrs:
            ident = f"s={v['id']}" if isinstance(v["id"], str) else f"i={v['id']}"
            out.append((v["src"].split(".")[-1], ident, "Objects/" + "/".join(v["path"])))
    return out

