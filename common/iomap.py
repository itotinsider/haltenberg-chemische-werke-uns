"""Field I/O map between the ASU plant (Modbus TCP server) and the DCS (Modbus TCP client).

This is the single source of truth for the register layout — both images copy this file, so
the plant and the DCS can never disagree about which register holds which value.

The field link is deliberately "dumb": instrument tag names (FT-101, PT-102, ...) and raw
engineering values in IEEE-754 float32 registers, no units, no hierarchy, no quality. The DCS
adds all of that (controller tags like FIC-101, units, ranges, alarms, history) before anything
is exposed over OPC UA — the same raw-vs-modelled contrast SHT showed with its _RAW topic tree.

Layout (all addresses 0-based, float32 = 2 registers, big-endian word order):
    Input registers   (FC4, plant -> DCS)  measurements
    Holding registers (FC3/16, DCS -> plant) final elements (valve/heater outputs, %)
    Discrete inputs   (FC2, plant -> DCS)  equipment status
    Coils             (FC1/5/15, DCS -> plant) equipment commands

Addresses are FIXED and grouped by process area = the first digit of the tag number:
    registers  area n -> n*100 ... n*100+99   (FT-101 at 100, FT-501 at 500, HB-900 at 900)
    bits       area n -> n*10  ... n*10+9     (YS-101 at 10, YS-501 at 50)
A new point goes at the END of its area with the next free address; an existing address never
changes, gaps are fine (a removed point leaves its address unused). The DCS reads and writes one
area per request, which also keeps every request within the Modbus limits (125 registers per
read, 123 per write). tests/test_contracts.py enforces all of this.
"""

import struct

REGISTER_AREA = 100     # registers per process area (50 float32 points)
BIT_AREA = 10           # discrete inputs / coils per process area

# (point, address, description) — measurements from field transmitters, in engineering units.
ANALOG_INPUTS = [
    ("FT-101", 100, "MAC forward air flow, kNm3/h"),
    ("PT-102", 102, "MAC discharge pressure, bar(a)"),
    ("FT-103", 104, "MAC suction (compressor) flow, kNm3/h"),
    ("JT-104", 106, "MAC motor power, MW"),
    ("VT-105", 108, "MAC bearing vibration DE, mm/s"),
    ("TT-106", 110, "MAC bearing temperature, degC"),
    ("PT-107", 112, "MAC lube-oil pressure, bar(g)"),
    ("TT-108", 114, "Air temperature after DCAC, degC"),
    ("TT-109", 116, "Ambient air temperature, degC"),
    ("FT-111", 118, "Booster air flow (K-102), kNm3/h"),
    ("PT-112", 120, "Booster discharge pressure, bar(a)"),
    ("JT-113", 122, "Booster motor power, MW"),
    ("VT-114", 124, "Booster bearing vibration, mm/s"),
    ("AT-202", 200, "CO2 at PPU outlet, ppm"),
    ("PDT-203", 202, "PPU bed differential pressure, mbar"),
    ("TT-204", 204, "Regeneration gas temperature, degC"),
    ("ZT-205", 206, "Switching valve stroke time (last), s"),
    ("TT-301", 300, "MHE cold-end air temperature, degC"),
    ("TDT-302", 302, "MHE warm-end delta T, K"),
    ("FT-303", 304, "Expander flow, kNm3/h"),
    ("ST-304", 306, "Expander speed, rpm"),
    ("VT-305", 308, "Expander vibration, um"),
    ("TT-306", 310, "Expander bearing temperature, degC"),
    ("TT-307", 312, "Main air into E-301 (warm end), degC"),
    ("TT-308", 314, "Booster air into E-301 (warm end), degC"),
    ("TT-309", 316, "GAN leaving E-301 (warm end), degC"),
    ("TT-310", 318, "Waste N2 leaving E-301 (warm end), degC"),
    ("TT-311", 320, "Booster air leaving E-301 (cold end, liquid), degC"),
    ("TT-312", 322, "Pumped LOX into E-301 (cold end), degC"),
    ("TT-313", 324, "GAN into E-301 (cold end), degC"),
    ("TT-314", 326, "Waste N2 into E-301 (cold end), degC"),
    ("TT-315", 328, "Expander inlet temperature, degC"),
    ("PT-316", 330, "Expander inlet pressure, bar(a)"),
    ("TT-317", 332, "Expander outlet temperature, degC"),
    ("PT-318", 334, "Expander outlet pressure, bar(a)"),
    ("PT-401", 400, "HP column top pressure, bar(a)"),
    ("PT-402", 402, "LP column top pressure, bar(a)"),
    ("LT-403", 404, "HP column sump level, %"),
    ("LT-404", 406, "Reboiler / LP sump level, %"),
    ("FT-405", 408, "LIN reflux to LP column, kNm3/h"),
    ("TT-406", 410, "LP sump (LOX) temperature, degC"),
    ("TT-407", 412, "HP column top (N2) temperature, degC"),
    ("FT-408", 414, "LOX draw to storage, kNm3/h"),
    ("FT-409", 416, "LIN draw to storage, kNm3/h"),
    ("FT-501", 500, "GOX product flow, kNm3/h"),
    ("PT-502", 502, "GOX header pressure, bar(g)"),
    ("FT-503", 504, "GAN product flow, kNm3/h"),
    ("PT-504", 506, "GAN header pressure, bar(g)"),
    ("LT-505", 508, "LOX tank level, %"),
    ("LT-506", 510, "LIN tank level, %"),
    ("FT-507", 512, "LOX backup vaporizer E-507 send-out, kNm3/h"),
    ("FT-508", 514, "LIN backup vaporizer E-508 send-out, kNm3/h"),
    ("PT-511", 516, "LOX pump P-501 discharge pressure, bar(g)"),
    ("TT-512", 518, "GOX temperature leaving E-301 (warm end), degC"),
    ("PT-513", 520, "GAN compressor K-503 discharge pressure, bar(g)"),
    ("JT-514", 522, "GAN compressor K-503 motor power, MW"),
    ("VT-515", 524, "GAN compressor K-503 vibration, mm/s"),
    ("PT-516", 526, "LOX backup pump P-505 discharge pressure, bar(g)"),
    ("PT-517", 528, "LIN backup pump P-506 discharge pressure, bar(g)"),
    ("AT-601", 600, "GOX purity, % O2"),
    ("AT-602", 602, "O2 in GAN, ppm"),
    ("AT-603", 604, "Total hydrocarbons in LOX, ppm CH4-eq"),
    ("AT-604", 606, "Acetylene in LOX, ppm"),
    ("HB-900", 900, "Plant I/O heartbeat counter"),
]

# (point, address, description) — final control elements driven by the DCS, all 0..100 %.
ANALOG_OUTPUTS = [
    ("FV-101", 100, "MAC inlet guide vanes"),
    ("UV-103", 102, "MAC antisurge recycle valve"),
    ("FV-111", 104, "Booster inlet guide vanes"),
    ("TV-204", 200, "Regeneration heater power"),
    ("FV-303", 300, "Expander inlet nozzles"),
    ("PV-402", 400, "LP column waste N2 vent valve"),
    ("LV-403", 402, "HP sump rich-liquid valve"),
    ("LV-404", 404, "LOX draw-to-storage valve"),
    ("FV-405", 406, "LIN reflux valve"),
    ("FV-409", 408, "LIN draw-to-storage valve"),
    ("FV-501", 500, "LOX pump P-501 discharge flow valve"),
    ("FV-503", 502, "GAN compressor K-503 inlet guide vanes"),
    ("FV-507", 504, "LOX backup vaporizer E-507 flow valve (P-505)"),
    ("FV-508", 506, "LIN backup vaporizer E-508 flow valve (P-506)"),
    ("WD-900", 900, "DCS watchdog counter"),
]

# Equipment status (plant -> DCS).
DISCRETE_INPUTS = [
    ("YS-101", 10, "MAC motor running"),
    ("YS-102", 11, "Booster air compressor running"),
    ("ZS-201A", 20, "PPU bed A online"),
    ("ZS-201B", 21, "PPU bed B online"),
    ("YS-301", 30, "Expander running"),
    ("YS-501", 50, "LOX pump P-501 running"),
    ("YS-503", 51, "GAN compressor K-503 running"),
    ("YS-505", 52, "LOX backup pump P-505 running"),
    ("YS-506", 53, "LIN backup pump P-506 running"),
]

# Equipment commands (DCS -> plant). Level-type: 1 = run / select, 0 = stop / deselect.
COILS = [
    ("HS-101", 10, "MAC run command"),
    ("HS-102", 11, "Booster air compressor run command"),
    ("HS-201", 20, "PPU bed B select (0 = bed A online, 1 = bed B online)"),
    ("HS-301", 30, "Expander run command"),
    ("HS-501", 50, "LOX pump P-501 run command"),
    ("HS-503", 51, "GAN compressor K-503 run command"),
    ("HS-505", 52, "LOX backup pump P-505 run command"),
    ("HS-506", 53, "LIN backup pump P-506 run command"),
]


def area(point):
    """Process area of a point = first digit of its tag number (FT-503 -> 5)."""
    return int(point.split("-")[1][0])


def _addresses(points):
    return {name: addr for name, addr, _ in points}


AI_ADDR = _addresses(ANALOG_INPUTS)
AO_ADDR = _addresses(ANALOG_OUTPUTS)
DI_ADDR = _addresses(DISCRETE_INPUTS)
DO_ADDR = _addresses(COILS)
# Table sizes (highest address + its width); the gaps in between read as 0.
AI_REGISTERS = max(AI_ADDR.values()) + 2
AO_REGISTERS = max(AO_ADDR.values()) + 2
DI_COUNT = max(DI_ADDR.values()) + 1
DO_COUNT = max(DO_ADDR.values()) + 1


def blocks(points, width=2):
    """One (start, count) request per process area, covering that area's points."""
    out = {}
    for name, addr, _ in points:
        lo, hi = out.get(area(name), (addr, addr + width))
        out[area(name)] = (min(lo, addr), max(hi, addr + width))
    return [(lo, hi - lo) for _, (lo, hi) in sorted(out.items())]


AI_BLOCKS = blocks(ANALOG_INPUTS)
AO_BLOCKS = blocks(ANALOG_OUTPUTS)


def float_to_regs(value):
    hi, lo = struct.unpack(">HH", struct.pack(">f", float(value)))
    return [hi, lo]


def regs_to_float(hi, lo):
    return struct.unpack(">f", struct.pack(">HH", hi, lo))[0]


def pack_floats(points, values, size):
    """Dict {point: value} -> a full register table of `size` (missing points and gaps are 0)."""
    regs = [0] * size
    for name, addr, _ in points:
        regs[addr:addr + 2] = float_to_regs(values.get(name, 0.0))
    return regs


def unpack_floats(points, regs):
    """A full register table -> {point: value}."""
    return {name: regs_to_float(regs[addr], regs[addr + 1]) for name, addr, _ in points}
