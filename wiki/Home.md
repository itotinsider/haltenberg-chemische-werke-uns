# HCW Haltenberg — ASU1 simulator & DCS

> **Disclaimer.** This code is part of the [IT/OT Academy](https://itot.academy), offered by
> [The IT/OT Insider](https://itotinsider.com). It is a generated, vibe-coded system made **only for
> demo purposes**. It may not be used for anything other than demonstration or training: not in
> production, not connected to real equipment, and not as the basis for any real control, quality
> or maintenance decision.

**Haltenberg Chemische Werke (HCW)** is a fictional chemical site. Its first unit, **ASU1**, is a
Linde-type **Air Separation Unit** (~1,000 t/d oxygen from ~145,000 Nm³/h of air), simulated in
real time and controlled by a small but complete **DCS** with a **built-in historian**, which
exposes its data over **OPC UA**.

![Operator console overview](images/operator-overview.png)

## What it is for

HCW explains the **classic process-industry stack**, and gives you realistic plant data to work
with:

- field I/O over raw **Modbus TCP**: numbers in registers, no names, no units;
- a **DCS** that turns them into modelled controller tags, with control loops, alarms and
  interlocks;
- a **historian** that stores every value over time;
- **OPC UA**, the interface IT-side clients consume (here **Node-RED**).

**Three sites, one standard.** HCW also runs an ASU in the US (Port Arden, Texas) and one in India
(Vadhana, Gujarat). Same simulator engine, different size, equipment and customers, and each
site's DCS presents its data over OPC UA in its own way: other names, units, structure and even
timestamps. That is the starting point of a global **DataOps** demo: standardise all three onto
one ISA-95 [Standard data model](Standard-data-model). See [Global sites](Global-sites).

It is a training system for **plant data**, not a full operator training simulator. The process is
realistic enough that the data behaves like real plant data: trips, alarms, backups taking over,
slow purity recovery. An **instructor station** starts faults that the DCS can't see, only their
effects, just like in a real plant.

## Where to start

| You want to… | Read |
|---|---|
| Run it on your laptop | [Getting started](Getting-started) |
| Understand DCS, Modbus, interlocks, trips | [Plant automation in brief](Plant-automation-in-brief) |
| Know what the plant does | [The process: air separation](The-process) |
| See how the pieces fit | [Architecture](Architecture) |
| Compare the three sites, map their data | [Global sites](Global-sites), [Standard data model](Standard-data-model) |
| Learn how the plant and its data work | [Trainers](Trainers): Process, Engineering, IT/OT |
| Use the screens | [Operator console](Operator-console), [Instructor station](Instructor-station) |
| Get the data out | [Data access](Data-access) |
| Change the code | [Development](Development) |

Plans and work in progress are in
[ROADMAP.md](https://github.com/itotinsider/haltenberg-chemische-werke-uns/blob/main/ROADMAP.md).
The detailed process and control reference is the
[research document](https://github.com/itotinsider/haltenberg-chemische-werke-uns/blob/main/linde%20air%20seperation%20research.md).
