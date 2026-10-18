# The process: air separation

ASU1 makes oxygen and nitrogen from air by cooling it until it liquefies and distilling it. Oxygen
boils at −183 °C and nitrogen at −196 °C, so in a column the nitrogen rises to the top and liquid
oxygen collects at the bottom. The plant follows the classic Linde double-column design.

**Size:** ~145,000 Nm³/h of air in, ~1,000 t/d (~29,000 Nm³/h) of gaseous oxygen out at 30 bar(g),
plus nitrogen for the site's 8 bar(g) grid.

## Six blocks plus energy

The process is divided into areas. The first digit of every tag number is its area, and so is
its block on the overview display.

| # | Block | What happens | Main equipment |
|---|---|---|---|
| 1 | Air intake and compression | Air is filtered, compressed to ~6 bar and cooled with water | K-101 main air compressor (MAC), FIC-101 air flow; K-102 booster compresses ~30 % of the air to ~60 bar for the oxygen product |
| 2 | Front-end purification (PPU) | Water and CO₂ would freeze in the cold box, so two mole-sieve beds adsorb them. One is online, the other is regenerated with hot waste nitrogen; they switch every 4 hours | Beds R-201 A/B, heater E-204, sequence KS-201 |
| 3 | Main heat exchanger and expander | The clean air is cooled against the cold products leaving the plant. An expander takes some air and makes the cold that keeps the process going | E-301, X-303 |
| 4 | Double column | The HP column pre-separates the air; the LP column makes pure oxygen at the bottom and nitrogen at the top. The main condenser couples them: HP nitrogen condenses while LP oxygen boils | C-401, E-404 |
| 5 | Products | **Internal compression:** the LOX pump P-501 pumps liquid oxygen to pipeline pressure; it boils in E-301 against the booster air. The GAN compressor K-503 sends nitrogen to the site | P-501, K-503 |
| 6 | Backup and storage | Liquid oxygen and nitrogen tanks. Backup pumps and vaporizers keep both headers supplied when ASU1 trips | T-505, P-505 / E-507, P-506 / E-508 |
| 7 | Energy | Specific power: compressor energy per unit of oxygen, ≈ 0.55 kWh/Nm³ | KPI-701 |

## The customers

- **Oxygen header (30 bar(g)):** an ethylene-oxide (EO) plant and an acetylene plant, plus other
  consumers. Their demand wanders, and the instructor can step it.
- **Nitrogen grid (8 bar(g)):** the whole site, for purging and blanketing. Purge jobs come and go.

## What makes it interesting for data

- **Slow and fast together:** flows respond in seconds, purity in tens of minutes, the cold box in
  hours.
- **Saw-tooth signals:** the mole-sieve cycle shows up in CO₂ and temperatures every 4 hours.
- **Coupled effects:** a trip in one block shows up in others, with delays.
- **Backups:** the customers barely notice a trip, but the data shows every step.

The process behaviour in detail (internal compression, nitrogen backups, the E-301 heat balance)
is on [Process behaviour](Process-behaviour). The full engineering reference is the
[research document](https://github.com/itotinsider/haltenberg-chemische-werke-uns/blob/main/linde%20air%20seperation%20research.md).
