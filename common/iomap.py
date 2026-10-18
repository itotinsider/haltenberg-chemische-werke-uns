"""Field I/O map between the ASU plant (Modbus TCP server) and the DCS (Modbus TCP client).

This is the single source of truth for the register layout — both images copy this file, so
the plant and the DCS can never disagree about which register holds which value.

The field link is deliberately "dumb": instrument tag names (FT-101, PT-102, ...) and raw
engineering values in IEEE-754 float32 registers, no units, no hierarchy, no quality. The DCS
adds all of that (controller tags like FIC-101, units, ranges, alarms, history) before anything
is exposed over OPC UA — the same raw-vs-modelled contrast SHT showed with its _RAW topic tree.

Layout (all addresses 0-based, float32 = 2 registers, big-endian word order):
    Input registers   (FC4, plant -> DCS)  measurements,   2 registers per point
    Holding registers (FC3/16, DCS -> plant) final elements (valve/heater outputs, %)
    Discrete inputs   (FC2, plant -> DCS)  equipment status
    Coils             (FC1/5/15, DCS -> plant) equipment commands
"""

import struct

# (point, description) — measurements from field transmitters, in engineering units.
ANALOG_INPUTS = [
    ("FT-101", "MAC forward air flow, kNm3/h"),
    ("PT-102", "MAC discharge pressure, bar(a)"),
    ("FT-103", "MAC suction (compressor) flow, kNm3/h"),
    ("JT-104", "MAC motor power, MW"),
    ("VT-105", "MAC bearing vibration DE, mm/s"),
    ("TT-106", "MAC bearing temperature, degC"),
    ("PT-107", "MAC lube-oil pressure, bar(g)"),
    ("TT-108", "Air temperature after DCAC, degC"),
    ("TT-109", "Ambient air temperature, degC"),
    ("FT-111", "Booster air flow (K-102), kNm3/h"),
    ("PT-112", "Booster discharge pressure, bar(a)"),
    ("JT-113", "Booster motor power, MW"),
    ("VT-114", "Booster bearing vibration, mm/s"),
    ("AT-202", "CO2 at PPU outlet, ppm"),
    ("PDT-203", "PPU bed differential pressure, mbar"),
    ("TT-204", "Regeneration gas temperature, degC"),
    ("ZT-205", "Switching valve stroke time (last), s"),
    ("TT-301", "MHE cold-end air temperature, degC"),
    ("TDT-302", "MHE warm-end delta T, K"),
    ("FT-303", "Expander flow, kNm3/h"),
    ("ST-304", "Expander speed, rpm"),
    ("VT-305", "Expander vibration, um"),
    ("TT-306", "Expander bearing temperature, degC"),
    ("PT-401", "HP column top pressure, bar(a)"),
    ("PT-402", "LP column top pressure, bar(a)"),
    ("LT-403", "HP column sump level, %"),
    ("LT-404", "Reboiler / LP sump level, %"),
    ("FT-405", "LIN reflux to LP column, kNm3/h"),
    ("TT-406", "LP sump (LOX) temperature, degC"),
    ("TT-407", "HP column top (N2) temperature, degC"),
    ("FT-408", "LOX draw to storage, kNm3/h"),
    ("FT-409", "LIN draw to storage, kNm3/h"),
    ("FT-501", "GOX product flow, kNm3/h"),
    ("PT-502", "GOX header pressure, bar(g)"),
    ("FT-503", "GAN product flow, kNm3/h"),
    ("PT-504", "GAN header pressure, bar(g)"),
    ("LT-505", "LOX tank level, %"),
    ("LT-506", "LIN tank level, %"),
    ("FT-507", "LOX backup vaporizer E-507 send-out, kNm3/h"),
    ("FT-508", "LIN backup vaporizer E-508 send-out, kNm3/h"),
    ("PT-511", "LOX pump P-501 discharge pressure, bar(g)"),
    ("TT-512", "GOX temperature leaving E-301 (warm end), degC"),
    ("PT-513", "GAN compressor K-503 discharge pressure, bar(g)"),
    ("JT-514", "GAN compressor K-503 motor power, MW"),
    ("VT-515", "GAN compressor K-503 vibration, mm/s"),
    ("PT-516", "LOX backup pump P-505 discharge pressure, bar(g)"),
    ("PT-517", "LIN backup pump P-506 discharge pressure, bar(g)"),
    ("AT-601", "GOX purity, % O2"),
    ("AT-602", "O2 in GAN, ppm"),
    ("AT-603", "Total hydrocarbons in LOX, ppm CH4-eq"),
    ("AT-604", "Acetylene in LOX, ppm"),
    ("HB-900", "Plant I/O heartbeat counter"),
]

# (point, description) — final control elements driven by the DCS, all 0..100 %.
ANALOG_OUTPUTS = [
    ("FV-101", "MAC inlet guide vanes"),
    ("UV-103", "MAC antisurge recycle valve"),
    ("FV-111", "Booster inlet guide vanes"),
    ("TV-204", "Regeneration heater power"),
    ("FV-303", "Expander inlet nozzles"),
    ("PV-402", "LP column waste N2 vent valve"),
    ("LV-403", "HP sump rich-liquid valve"),
    ("LV-404", "LOX draw-to-storage valve"),
    ("FV-405", "LIN reflux valve"),
    ("FV-409", "LIN draw-to-storage valve"),
    ("FV-501", "LOX pump P-501 discharge flow valve"),
    ("FV-503", "GAN compressor K-503 inlet guide vanes"),
    ("FV-507", "LOX backup vaporizer E-507 flow valve (P-505)"),
    ("FV-508", "LIN backup vaporizer E-508 flow valve (P-506)"),
    ("WD-900", "DCS watchdog counter"),
]

# Equipment status (plant -> DCS).
DISCRETE_INPUTS = [
    ("YS-101", "MAC motor running"),
    ("YS-301", "Expander running"),
    ("YS-102", "Booster air compressor running"),
    ("YS-501", "LOX pump P-501 running"),
    ("YS-503", "GAN compressor K-503 running"),
    ("YS-505", "LOX backup pump P-505 running"),
    ("YS-506", "LIN backup pump P-506 running"),
    ("ZS-201A", "PPU bed A online"),
    ("ZS-201B", "PPU bed B online"),
]

# Equipment commands (DCS -> plant). Level-type: 1 = run / select, 0 = stop / deselect.
COILS = [
    ("HS-101", "MAC run command"),
    ("HS-301", "Expander run command"),
    ("HS-201", "PPU bed B select (0 = bed A online, 1 = bed B online)"),
    ("HS-102", "Booster air compressor run command"),
    ("HS-501", "LOX pump P-501 run command"),
    ("HS-503", "GAN compressor K-503 run command"),
    ("HS-505", "LOX backup pump P-505 run command"),
    ("HS-506", "LIN backup pump P-506 run command"),
]


def _addresses(points, width):
    return {name: i * width for i, (name, _) in enumerate(points)}


AI_ADDR = _addresses(ANALOG_INPUTS, 2)
AO_ADDR = _addresses(ANALOG_OUTPUTS, 2)
DI_ADDR = _addresses(DISCRETE_INPUTS, 1)
DO_ADDR = _addresses(COILS, 1)
AI_REGISTERS = 2 * len(ANALOG_INPUTS)
AO_REGISTERS = 2 * len(ANALOG_OUTPUTS)


def float_to_regs(value):
    hi, lo = struct.unpack(">HH", struct.pack(">f", float(value)))
    return [hi, lo]


def regs_to_float(hi, lo):
    return struct.unpack(">f", struct.pack(">HH", hi, lo))[0]


def pack_floats(points, values):
    """Dict {point: value} -> flat register list in map order (missing points become 0.0)."""
    regs = []
    for name, _ in points:
        regs += float_to_regs(values.get(name, 0.0))
    return regs


def unpack_floats(points, regs):
    return {name: regs_to_float(regs[2 * i], regs[2 * i + 1]) for i, (name, _) in enumerate(points)}
