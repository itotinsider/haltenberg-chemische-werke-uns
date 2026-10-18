# Process behaviour

How the main parts of ASU1 behave and how they are controlled. For the overall picture, see
[The process](The-process).

## Oxygen product: internal compression

GOX leaves ASU1 at pipeline pressure without an oxygen compressor:

- **P-501 LOX pump** draws liquid oxygen from the LP sump and pumps it to ~33 bar(g) (PI-511).
- The pumped LOX **boils in E-301** against **booster air**: K-102 compresses ~30 % of the clean
  air to ~60 bar(a) (FIC-111, PI-112, JI-113, VI-114); it condenses on the other side and goes
  through the JT valve HV-116 into the HP column as liquid air.
- **Cross-limiting ratio control**, as for burner air/fuel: FIC-111's setpoint is the ratio (1.52)
  × the higher of LOX flow and LOX demand, so the booster leads on increase. The demand counts only
  up to the load limit FY-501: booster air for oxygen the pump won't deliver has no LOX to condense
  against. FIC-501's setpoint is capped at what the measured booster air can vaporize, so the LOX
  follows. FFI-111 shows the actual ratio; the operator sets it on the FIC-111 faceplate
  (1.35–2.0).
- **TI-512**, the GOX temperature leaving E-301, is the safety variable: too little booster air →
  cold (eventually liquid) oxygen towards the carbon-steel header → LL −25 °C trips **I-500**.
- **Load limit FY-501** = 0.208 × air (FIC-101) − LOX to storage (FI-408), plus an allowance for a
  sump above its setpoint. FIC-501's setpoint is capped there (status `CAP`, "LOAD LIMIT"): the pump
  can't take more oxygen than the air brings in. More demand → the backup vaporizer covers it and
  the operator raises the air. Without the limit, the EO demand step pumped the sump down and
  crashed purity to 94 %.
- **LIC-415**, a reboiler low-level override, limits FIC-501 below 45 %. The column can't be
  pumped empty when the header asks for more than it makes; the backup vaporizer covers the
  difference.

## Nitrogen product and backup supply

- **K-503 GAN compressor.** GAN leaves the HP column top at ~5.3 bar(a), is warmed in E-301 and
  compressed to the 8 bar(g) site grid. FIC-503 drives its inlet guide vanes (FV-503). Minimum flow
  10 kNm³/h (FIC-503 SP low limit); it surges below ~8 kNm³/h (VI-515). Also PI-513 discharge
  pressure and JI-514 motor power. It needs the MAC running and PI-401 ≥ 4 bar(a) to start.
- **Backup pumps P-505 (LOX) and P-506 (LIN)** lift tank liquid to header pressure for the
  vaporizers **E-507** (GOX header, FIC-507) and **E-508** (GAN header, FIC-508, up to 40 kNm³/h).
  They are *kept cold*: running all the time on minimum-flow recirculation (PI-516 / PI-517 show
  the shut-off head), so they deliver as soon as their valve opens. They are not tripped by I-100:
  the backup has to survive an ASU trip. I-505 / I-506 trip them on tank level LL (10 %).
- **Split range** on both headers: PIC-502 → FIC-501 (OP 0–80 %) / FIC-507 (80–100 %), and
  PIC-504 → FIC-503 (0–80 %) / FIC-508 (80–100 %).
- **Trip handover (feed-forward).** When I-500 or I-503 trips, the header master's OP jumps at
  once to where the backup vaporizer takes over the lost flow, instead of waiting for the pressure
  to fall far enough. GOX header minimum on a booster trip: 27.8 bar(g); GAN header on a K-503
  trip: ~7.6 bar(g).
- **Handback.** When FIC-501 / FIC-503 go back to CAS after a restart, the master is re-initialised
  so the restarted supply's setpoint equals the total flow now delivered; the vaporizer winds
  down as it takes over (no pressure bump).
- **Specific power** KPI-701 = (MAC + booster + GAN compressor power) ÷ GOX, ≈ 0.55 kWh/Nm³
  (H alarm 0.60).

## Main heat exchanger E-301: a simple heat balance

Every stream through E-301 has a temperature at both ends, and the expander X-303 has its inlet and
outlet temperature and pressure:

| | Warm end (top) | Cold end (bottom) |
|---|---|---|
| Main air | TI-307 in, ≈ 15 °C | TI-301 out, ≈ −174 °C → HP column |
| Booster air (~60 bar) | TI-308 in, ≈ 22 °C | TI-311 out (liquefied), ≈ −172 °C → HV-116 |
| Pumped LOX → GOX | TI-512 out, ≈ 10 °C | TI-312 in, ≈ −178 °C |
| GAN | TI-309 out, ≈ 12 °C → K-503 | TI-313 in, ≈ −177 °C |
| Waste N₂ | TI-310 out, ≈ 12 °C → PPU regeneration | TI-314 in, ≈ −192 °C |
| Expander side draw | | TI-315 / PI-316 in ≈ −110 °C, 5.6 bar(a); TI-317 / PI-318 out ≈ −178 °C, 1.4 bar(a) |

The plant model closes an **enthalpy balance** over E-301 at every step, with deliberately simple
assumptions: ideal gases with one constant cp (1.30 kJ/Nm³·K), constant heats of vaporisation, no
heat leak into the exchanger, a clean exchanger.
- **Inlets** come from upstream: the PPU, the booster aftercooler, the column streams at their
  boiling points.
- **Outlets** follow from design approaches: 3 K at the warm end, −174 °C at the cold end.
- **What doesn't balance** shifts the whole temperature profile, through the exchanger's metal
  (~10 min). Too much warm flow (booster air without LOX, no expander draw) warms it: the warm-end
  ΔT closes and the cold end warms. Too much cold flow cools it: the warm-end ΔT opens.
- **One calibrated number:** the heat the 60 bar booster air releases while it liquefies
  (~200 kJ/Nm³), chosen so the design point balances.
- **The expander:** an isentropic drop with 85 % efficiency, fitted to −110 → −178 °C. A loss of
  loading extracts less work, so the outlet is warmer.

So the data is consistent: TDI-302 = TI-307 − TI-310, and the balance recomputed from the OPC UA
tags closes within 2 % (a test checks both). Try it yourself as a data exercise.

Alarms (no trips): TI-315 H (warm expander inlet), TI-317 L (close to the dew point: liquid in the
expander) and H (too little work extracted).

## Restart after a trip

1. Interlocks tab: **Reset I-100**, then I-300, I-110, I-503 and I-500. A child interlock can only
   be reset after its parent (I-100 → I-300 / I-110 / I-503, I-110 → I-500). The backup pumps
   P-505 / P-506 keep running through an ASU trip; the vaporizers hold both headers meanwhile.
2. **Start** the main air compressor. It starts in MAN on minimum guide vanes.
3. Once it is RUNNING, press **Normal line-up**, then raise the FIC-101 setpoint to 145.
   - It ramps at 1.45 kNm³/h per minute by default; the ramp rate is set on the faceplate.
4. Start the expander. Once it is RUNNING, press **Normal line-up** again and set the FIC-303
   setpoint to 18.
5. Start the **booster K-102** (needs the MAC running). Once it is RUNNING, press **Normal line-up**:
   FIC-111 goes to CAS and follows the pumped LOX in ratio.
6. Start the **LOX pump P-501** (needs the booster running and LIC-404 ≥ 40 %). It starts on part
   flow in MAN. Once it is RUNNING, press **Normal line-up**: FIC-501 returns to CAS under the GOX
   header pressure. Line-up skips loops whose equipment isn't running yet and says so.
7. Start the **GAN compressor K-503** (needs the MAC running and PI-401 ≥ 4 bar(a)). Once it is
   RUNNING (~25 s), press **Normal line-up**: FIC-503 takes the GAN grid back from the LIN backup.
8. Purity takes ~1 h to recover.

After a booster or LOX pump trip alone, only steps 5–6 are needed, but be quick: with the pump
down the oxygen stays in the reboiler sump, which floods in ~10 min (LIC-404 HH trips the ASU).
After a K-503 trip alone, only step 7. After a backup pump trip: reset I-505 / I-506, start the
pump (tank level above 10 %) and press **Normal line-up**.
