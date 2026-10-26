"""The HCW standard data model: one ISA-95 Unified Namespace (UNS) for every air separation unit.

This is the "should" side of the global DataOps demo. Three sites deliver the same kind of plant
data in three different shapes (dcs/native.py); a DataOps layer standardises them onto this model.
The answer key, which native node maps onto which standard point and how its value converts, is
generated from this file and the sites' own address spaces into inventory/uns/ (tools/inventory.py),
and tested against what the servers really publish.

Path:   hcw/<site>/air-separation/<unit>/<equipment>/<point>[/<attribute>]
        e.g. hcw/port-arden/air-separation/asu7100/main-air-compressor/air-flow
             hcw/port-arden/air-separation/asu7100/main-air-compressor/air-flow/setpoint
Levels: enterprise / site / area / unit (ISA-95 work unit) / equipment / point. Equipment can have a
        sub-level for instances of the same function (lox-pump/pump-a, lox-pump/pump-b).
Value:  {"value": ..., "unit": ..., "quality": "GOOD" | "BAD" | "UNCERTAIN", "reason": ..., "ts": UTC ISO-8601}

Standard units: SI as process engineers use it. Flow Nm3/h (0 degC, 1.01325 bar), pressure bar with
its reference kept (bar(a) / bar(g): converting needs the ambient pressure, so the model doesn't),
temperature degC, temperature difference K, power kW, level and valve output %, purity %mol,
impurities ppm(mol), vibration mm/s (velocity) or um (displacement), specific power kWh/Nm3.
Tank contents are a level in % of the tank's capacity (gallons and tonnes convert with asset data).

Encodings: mode MANUAL | AUTO | CASCADE; alarm state NORMAL | LOW | HIGH | LOW-LOW | HIGH-HIGH;
equipment state RUNNING | STARTING | STOPPING | STOPPED | TRIPPED; booleans true / false.

The model is keyed by the simulator engine's tag (FIC-101, ...): every site runs the same engine, so
that is the join key the generator uses. It is an implementation detail of the simulator; a DataOps
team only sees the native names of each site.
"""

ENTERPRISE = "hcw"
AREA = "air-separation"

# Engine unit -> (standard unit, factor, offset): standard = engine x factor + offset.
UNITS = {
    "kNm³/h": ("Nm3/h", 1000.0, 0.0), "MW": ("kW", 1000.0, 0.0), "bar(a)": ("bar(a)", 1.0, 0.0),
    "bar(g)": ("bar(g)", 1.0, 0.0), "mbar": ("mbar", 1.0, 0.0), "°C": ("degC", 1.0, 0.0), "K": ("K", 1.0, 0.0),
    "%": ("%", 1.0, 0.0), "% O2": ("%mol", 1.0, 0.0), "ppm": ("ppm(mol)", 1.0, 0.0),
    "ppm CH4": ("ppm(mol) CH4-eq", 1.0, 0.0), "mm/s": ("mm/s", 1.0, 0.0), "µm": ("um", 1.0, 0.0),
    "rpm": ("rpm", 1.0, 0.0), "s": ("s", 1.0, 0.0), "kWh/Nm³": ("kWh/Nm3", 1.0, 0.0), "–": ("1", 1.0, 0.0),
}

# Engine tag -> (equipment, point, description in the standard's words)
POINTS = {
    "FIC-101": ("main-air-compressor", "air-flow", "Main air flow to the cold box (compressor load)"),
    "PIC-102": ("main-air-compressor", "discharge-pressure", "Main air compressor discharge pressure"),
    "FI-103": ("main-air-compressor", "suction-flow", "Main air compressor suction flow"),
    "UIC-103": ("main-air-compressor", "surge-margin", "Main air compressor surge margin (antisurge control)"),
    "JI-104": ("main-air-compressor", "motor-power", "Main air compressor motor power"),
    "VI-105": ("main-air-compressor", "bearing-vibration", "Main air compressor bearing vibration"),
    "TI-106": ("main-air-compressor", "bearing-temperature", "Main air compressor bearing temperature"),
    "PI-107": ("main-air-compressor", "lube-oil-pressure", "Main air compressor lube-oil pressure"),
    "TI-108": ("aftercooler", "air-outlet-temperature", "Air temperature after the direct-contact aftercooler"),
    "TI-109": ("air-intake", "ambient-temperature", "Ambient air temperature"),
    "FIC-111": ("booster-air-compressor", "air-flow", "Booster air flow"),
    "FFI-111": ("booster-air-compressor", "air-to-lox-ratio", "Booster air to pumped LOX ratio"),
    "PI-112": ("booster-air-compressor", "discharge-pressure", "Booster air compressor discharge pressure"),
    "JI-113": ("booster-air-compressor", "motor-power", "Booster air compressor motor power"),
    "VI-114": ("booster-air-compressor", "bearing-vibration", "Booster air compressor bearing vibration"),
    "AI-202": ("pre-purification", "outlet-co2", "CO2 at the pre-purification outlet"),
    "PDI-203": ("pre-purification", "bed-differential-pressure", "Adsorber bed differential pressure"),
    "TIC-204": ("pre-purification", "regeneration-gas-temperature", "Regeneration gas temperature"),
    "ZI-205": ("pre-purification", "switch-valve-stroke-time", "Switching valve stroke time (last switch)"),
    "TI-301": ("main-heat-exchanger", "cold-end-air-temperature", "Main air temperature at the cold end"),
    "TDI-302": ("main-heat-exchanger", "warm-end-delta-t", "Warm-end temperature difference"),
    "TI-307": ("main-heat-exchanger", "main-air-inlet-temperature", "Main air into the exchanger (warm end)"),
    "TI-308": ("main-heat-exchanger", "booster-air-inlet-temperature", "Booster air into the exchanger (warm end)"),
    "TI-309": ("main-heat-exchanger", "gan-outlet-temperature", "GAN leaving the exchanger (warm end)"),
    "TI-310": ("main-heat-exchanger", "waste-n2-outlet-temperature", "Waste N2 leaving the exchanger (warm end)"),
    "TI-311": ("main-heat-exchanger", "booster-air-outlet-temperature", "Liquefied booster air leaving (cold end)"),
    "TI-312": ("main-heat-exchanger", "pumped-lox-inlet-temperature", "Pumped LOX into the exchanger (cold end)"),
    "TI-313": ("main-heat-exchanger", "gan-inlet-temperature", "GAN into the exchanger (cold end)"),
    "TI-314": ("main-heat-exchanger", "waste-n2-inlet-temperature", "Waste N2 into the exchanger (cold end)"),
    "FIC-303": ("expander", "flow", "Expander flow (refrigeration)"),
    "SI-304": ("expander", "speed", "Expander speed"),
    "VI-305": ("expander", "vibration", "Expander shaft vibration (displacement)"),
    "TI-306": ("expander", "bearing-temperature", "Expander bearing temperature"),
    "TI-315": ("expander", "inlet-temperature", "Expander inlet temperature"),
    "PI-316": ("expander", "inlet-pressure", "Expander inlet pressure"),
    "TI-317": ("expander", "outlet-temperature", "Expander outlet temperature"),
    "PI-318": ("expander", "outlet-pressure", "Expander outlet pressure"),
    "PI-401": ("double-column", "hp-top-pressure", "High-pressure column top pressure"),
    "PIC-402": ("double-column", "lp-top-pressure", "Low-pressure column top pressure"),
    "LIC-403": ("double-column", "hp-sump-level", "High-pressure column sump level"),
    "LIC-404": ("double-column", "lp-sump-level", "Low-pressure column sump (reboiler) level"),
    "FIC-405": ("double-column", "reflux-flow", "Liquid nitrogen reflux to the low-pressure column"),
    "TI-406": ("double-column", "lp-sump-temperature", "Low-pressure column sump (LOX) temperature"),
    "TI-407": ("double-column", "hp-top-temperature", "High-pressure column top (N2) temperature"),
    "TDI-410": ("double-column", "main-condenser-delta-t", "Main condenser temperature difference"),
    "TY-411": ("double-column", "n2-condensing-temperature", "N2 condensing temperature at HP column pressure"),
    "TY-412": ("double-column", "o2-boiling-temperature", "O2 boiling temperature at LP column pressure"),
    "TY-413": ("double-column", "n2-boiling-temperature", "N2 boiling temperature at LP column pressure"),
    "FI-408": ("double-column", "lox-to-storage-flow", "LOX drawn to storage"),
    "FIC-409": ("double-column", "lin-to-storage-flow", "LIN drawn to storage"),
    "AI-603": ("double-column", "lox-total-hydrocarbons", "Total hydrocarbons in the reboiler LOX"),
    "AI-604": ("double-column", "lox-acetylene", "Acetylene in the reboiler LOX"),
    "FIC-501": ("gox-product", "flow", "Gaseous oxygen product flow"),
    "FY-501": ("gox-product", "load-limit", "Gaseous oxygen load limit (what the air flow supports)"),
    "LIC-415": ("lox-pump", "low-level-override", "LOX pump low sump level override"),
    "PI-511": ("lox-pump", "discharge-pressure", "LOX pump discharge pressure"),
    "TI-512": ("gox-product", "temperature", "Gaseous oxygen temperature leaving the exchanger"),
    "PIC-502": ("gox-product", "header-pressure", "Gaseous oxygen header pressure"),
    "AI-601": ("gox-product", "purity", "Gaseous oxygen purity"),
    "FIC-503": ("gan-product", "flow", "Gaseous nitrogen product flow"),
    "PIC-504": ("gan-product", "header-pressure", "Gaseous nitrogen header pressure"),
    "AIC-602": ("gan-product", "o2-content", "Oxygen content of the nitrogen product"),
    "PI-513": ("gan-compressor", "discharge-pressure", "Nitrogen compressor discharge pressure"),
    "JI-514": ("gan-compressor", "motor-power", "Nitrogen compressor motor power"),
    "VI-515": ("gan-compressor", "vibration", "Nitrogen compressor vibration"),
    "LI-505": ("lox-storage", "level", "LOX storage tank level (% of capacity)"),
    "FIC-507": ("lox-backup-vaporizer", "flow", "LOX backup vaporizer send-out"),
    "PI-516": ("lox-backup-pump", "discharge-pressure", "LOX backup pump discharge pressure"),
    "LI-506": ("lin-storage", "level", "LIN storage tank level (% of capacity)"),
    "FIC-508": ("lin-backup-vaporizer", "flow", "LIN backup vaporizer send-out"),
    "PI-517": ("lin-backup-pump", "discharge-pressure", "LIN backup pump discharge pressure"),
    "KPI-701": ("performance", "specific-power", "Specific power: compressor energy per Nm3 of oxygen"),
}

# Controller attributes beyond the value: engine attribute -> (standard attribute, type, unit rule)
CONTROLLER_ATTRS = {"SP": ("setpoint", "Double", "pv"), "OP": ("output", "Double", "%"),
                    "Mode": ("mode", "Enum", None), "AlarmState": ("alarm-state", "Enum", None)}
MODE = {"MAN": "MANUAL", "AUTO": "AUTO", "CAS": "CASCADE"}
ALARM = {"NORMAL": "NORMAL", "L": "LOW", "H": "HIGH", "LL": "LOW-LOW", "HH": "HIGH-HIGH"}

# Rotating equipment (engine key) -> standard equipment path; points running (Boolean) and state (Enum).
EQUIPMENT = {
    "MAC": "main-air-compressor", "BAC": "booster-air-compressor", "EXP": "expander",
    "LOXP": "lox-pump/pump-a", "LOXPB": "lox-pump/pump-b", "GANC": "gan-compressor",
    "LOXB": "lox-backup-pump", "LINB": "lin-backup-pump",
}
# Interlocks -> protection functions; points tripped (Boolean) and first-out (String).
PROTECTION = {
    "I-100": "asu-shutdown", "I-110": "booster-air-compressor-trip", "I-300": "expander-trip",
    "I-500": "lox-pump-trip", "I-503": "gan-compressor-trip", "I-505": "lox-backup-pump-trip",
    "I-506": "lin-backup-pump-trip",
}
SEQUENCE = ("pre-purification/switching-sequence", {
    "State": ("state", "String", None), "ActiveBed": ("active-bed", "String", None), "Step": ("step", "String", None),
    "StepTime": ("step-time", "Double", "s"), "OnlineTime": ("online-time", "Double", "s"),
    "CycleTime": ("cycle-time", "Double", "s"), "RegenIncomplete": ("regeneration-incomplete", "Boolean", None),
})
DIAGNOSTICS = ("control-system", {
    "FieldIOConnected": ("io-connected", "Boolean", None), "ScanCount": ("scan-count", "Integer", None),
    "ActiveAlarms": ("active-alarms", "Integer", None), "UnackedAlarms": ("unacknowledged-alarms", "Integer", None),
    "HistorianSamples": ("historian-samples", "Integer", None),
})


def unit_path(site_key, unit):
    return f"{ENTERPRISE}/{site_key}/{AREA}/{unit.lower()}"


def standard_point(src, tag_cfg=None):
    """Engine value key (as in dcs/opcua_server.engine_values) -> dict(path below the unit, attribute,
    type, unit, factor, offset, enum, desc), or None if the standard has no place for it."""
    head, _, attr = src.rpartition(".")
    if head.startswith("Equipment."):
        key = head.split(".", 1)[1]
        point = {"Running": ("running", "Boolean"), "State": ("state", "Enum")}[attr]
        return dict(path=f"{EQUIPMENT[key]}/{point[0]}", attribute="value", type=point[1], unit=None,
                    factor=None, offset=None, enum=None, desc=f"{EQUIPMENT[key]} {point[0]}")
    if head.startswith("Interlocks."):
        key = head.split(".", 1)[1]
        point = {"Tripped": ("tripped", "Boolean"), "FirstOut": ("first-out", "String")}[attr]
        return dict(path=f"protection/{PROTECTION[key]}/{point[0]}", attribute="value", type=point[1], unit=None,
                    factor=None, offset=None, enum=None, desc=f"Protection {PROTECTION[key]}: {point[0]}")
    if head == "KS-201":
        name, typ, unit = SEQUENCE[1][attr]
        return dict(path=f"{SEQUENCE[0]}/{name}", attribute="value", type=typ, unit=unit,
                    factor=1.0 if unit else None, offset=0.0 if unit else None, enum=None,
                    desc=f"Adsorber switching sequence: {name}")
    if head == "Diagnostics":
        name, typ, unit = DIAGNOSTICS[1][attr]
        return dict(path=f"{DIAGNOSTICS[0]}/{name}", attribute="value", type=typ, unit=unit, factor=None,
                    offset=None, enum=None, desc=f"Control system: {name}")
    if head not in POINTS:
        return None
    equipment, point, desc = POINTS[head]
    unit, f, o = UNITS[tag_cfg["unit"]]
    if head in ("LI-505", "LI-506"):
        unit, f, o = "%", 1.0, 0.0
    if attr == "PV":
        return dict(path=f"{equipment}/{point}", attribute="value", type="Double", unit=unit, factor=f, offset=o,
                    enum=None, desc=desc)
    name, typ, rule = CONTROLLER_ATTRS[attr]
    if rule == "%":
        unit, f, o = "%", 1.0, 0.0
    enum = MODE if attr == "Mode" else ALARM if attr == "AlarmState" else None
    return dict(path=f"{equipment}/{point}/{name}", attribute=name, type=typ, unit=unit if typ == "Double" else None,
                factor=f if typ == "Double" else None, offset=o if typ == "Double" else None, enum=enum,
                desc=f"{desc}: {name}")
