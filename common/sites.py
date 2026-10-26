"""The sites of the HCW global demo: one simulator engine, three plants.

Every site runs the same plant model (plant/model.py) and the same DCS engine (dcs/control.py). What
differs per site is set here, and read by both images (this file is copied into both, like iomap.py):

    identity      company, site, unit, country, time zone
    size          `scale`: every flow and power is scaled (geometric similarity). Pressures,
                  temperatures, levels and purities stay the same, so the dynamics are identical.
    climate       ambient mean / daily swing, and the DCAC chiller that keeps TT-108 at ~12 degC
    process       which equipment exists (`equipment`: spare LOX pump, GAN product), the customers
                  and their demand pattern, the PPU bed cycle
    presentation  `profile`: how the site's DCS presents its data over OPC UA (dcs/native.py)
    rack_id       the I/O rack's identity (RID-901): a DCS refuses a plant of another site

The site is chosen with the environment variable HCW_SITE (default "haltenberg"). Haltenberg is the
reference site: with HCW_SITE unset the system is exactly what it was before the other sites existed.

Engine units stay the same at every site (kNm3/h, bar, degC, MW): the operator console and the
historian of every site show engine tags (FIC-101, ...). Only the OPC UA server of a site speaks
that site's own language (names, units, structure). The mapping of all three onto one standard
(the ISA-95 UNS model in dataops/uns_model.py) is generated into inventory/uns/.
"""

import os

# Field points that only exist where their equipment does. A site without the equipment leaves the
# points unused (the Modbus map is the same everywhere: a spare I/O card, so to speak).
EQUIPMENT_POINTS = {
    "lox_pump_spare": {"YS-501B", "HS-501B"},
    "gan_product": {"FT-503", "PT-504", "PT-513", "JT-514", "VT-515", "FT-508", "PT-517", "TT-309", "TT-313",
                    "AT-602", "FV-503", "FV-508", "YS-503", "YS-506", "HS-503", "HS-506"},
}

SITES = {
    "haltenberg": dict(
        key="haltenberg", code="HBG", rack_id=1, company="Haltenberg Chemische Werke", enterprise="HCW",
        site="Haltenberg", country="DE", country_name="Germany", region="Rhine valley",
        timezone="Europe/Berlin", unit="ASU1", unit_label="Air Separation Unit 1",
        scale=1.0, capacity="~1,000 t/d O₂",
        ambient=(15.0, 5.0), dcac=(6.0, 0.4), ppu_cycle_s=14400.0,
        equipment={"lox_pump_spare": False, "gan_product": True},
        o2_pattern="steady",
        consumers={"o2": ["EO", "Acetylene", "Other"], "n2": "N2 grid"},
        customers="Ethylene-oxide plant, acetylene (partial oxidation), site N₂ grid",
        profile="hcw",
        scenario_labels={},
    ),
    "port-arden": dict(
        key="port-arden", code="PTA", rack_id=2, company="HCW Americas", enterprise="HCW",
        site="Port Arden", country="US", country_name="United States (Texas, Gulf Coast)", region="Gulf Coast",
        timezone="America/Chicago", unit="ASU7100", unit_label="Air Separation Unit, Train 71",
        scale=1.5, capacity="~1,500 t/d O₂",
        # Hot and humid: a bigger chiller keeps the air after the DCAC at ~12 degC, as at Haltenberg.
        ambient=(24.0, 5.0), dcac=(4.0, 0.33), ppu_cycle_s=10800.0,
        equipment={"lox_pump_spare": True, "gan_product": True},
        o2_pattern="steady",
        consumers={"o2": ["Refinery FCC", "POX unit", "Other"], "n2": "N2 pipeline"},
        customers="Refinery (FCC oxygen enrichment), partial-oxidation unit, N₂ pipeline to the chemical park",
        profile="us",
        scenario_labels={
            "eo_demand_step": ("Refinery O2 demand step (+20 %)",
                               "The refinery raises its FCC oxygen enrichment by 20 %. GOX header pressure drops "
                               "first; the ASU load must follow slowly."),
            "acetylene_burner_trip": ("POX burner trip",
                                      "One of three partial-oxidation burners trips: a sharp drop in O2 demand, "
                                      "restored after 15 minutes."),
            "n2_purge_campaign": ("N2 pipeline: customer purge campaign",
                                  "A turnaround at a pipeline customer draws extra GAN for purging."),
        },
    ),
    "vadhana": dict(
        key="vadhana", code="VDH", rack_id=3, company="HCW India", enterprise="HCW",
        site="Vadhana", country="IN", country_name="India (Gujarat)", region="Gujarat coast",
        timezone="Asia/Kolkata", unit="ASU2", unit_label="Air Separation Unit 2",
        scale=0.6, capacity="~600 t/d O₂",
        ambient=(30.0, 6.0), dcac=(2.0, 0.33), ppu_cycle_s=7200.0,
        # An oxygen plant for a steel works: no GAN product (no K-503, no LIN backup). All the
        # nitrogen leaves with the waste N2; LIN is only made for the tank.
        equipment={"lox_pump_spare": False, "gan_product": False},
        o2_pattern="eaf",
        consumers={"o2": ["Steel works", "Other"], "n2": None},
        customers="Electric-arc-furnace steel works (cyclic oxygen demand, one heat per hour)",
        profile="in",
        scenario_labels={
            "eo_demand_step": ("Steel works: extra furnace load (+20 % O2)",
                               "The steel works raises its oxygen use by 20 %. GOX header pressure drops first; "
                               "the ASU load must follow slowly."),
            "acetylene_burner_trip": ("EAF oxygen lance trip",
                                      "An oxygen lance at the furnace trips: a sharp drop in O2 demand, restored "
                                      "after 15 minutes."),
        },
    ),
}

DEFAULT = "haltenberg"


def current_key():
    key = os.getenv("HCW_SITE", DEFAULT) or DEFAULT
    if key not in SITES:
        raise ValueError(f"HCW_SITE={key!r}: unknown site (one of {', '.join(SITES)})")
    return key


def get(key=None):
    return SITES[key or current_key()]


def absent_points(site):
    """Field points this site doesn't use: those of equipment it doesn't have."""
    out = set()
    for feature, points in EQUIPMENT_POINTS.items():
        if not site["equipment"][feature]:
            out |= points
    return out
