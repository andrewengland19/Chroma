# Lighting board — first build steps (board + faders)

Hands-on guide for building the controller **before** the Playbill lights. It follows the prototype
spec ([lighting-board-prototype-p0.svg](../assets/lighting-board-prototype-p0.svg)) and feeds the full
spec ([lighting-board-final-spec.svg](../assets/lighting-board-final-spec.svg)); the layout it builds toward is
[lighting-board-proposal.svg](../assets/lighting-board-proposal.svg).

You don't need the WLED/Playbill hardware to see results: the faders can drive a LIFX bulb through the hub
that already exists. The Playbill bars converge with the board at the end.

> Nothing here is bench-verified. Items marked **⚠** must be checked against the real part's datasheet or on
> the bench. Prices are rough.

## Parts to order (about $80–100 at US-distributor prices)

| What | Search for / buy | Qty | About |
|---|---|---|---|
| Dev board | **ESP32-S3-DevKitC-1** (plain "-N8" avoids reserved pins; N16R8 also works with our pin map). USB-C data cable. | 1 | $12–15 |
| Faders, 60 mm | Bourns PTA-series 60 mm, 10 kΩ linear, or an equivalent B10K 60 mm slide pot | 4 | $20–25 |
| Faders, 100 mm | "100 mm slide potentiometer 10k linear". Read the datasheet for slot size and mounting first. | 2 | $10–14 |
| Flash buttons | 24 mm arcade button, translucent cap if possible | 2 | $6–10 |
| Assign buttons | 12 mm momentary pushbutton | 2 | $4 |
| Encoders | EC11-style with push switch, plus knobs | 2 | $6 |
| Backlight pixels | SK6812 mini-e strip or breakouts | ~10 | $3 |
| Level shifter | 74AHCT125 (breakout or DIP) | 2 | $4 |
| I/O expanders | MCP23017, DIP-28 or breakout module | 2 | $4 |
| Passives | Kit: 1 kΩ, 4.7 kΩ, 330 Ω, 100 nF, 10 µF, 470–1000 µF; a few ferrite beads | 1 | $8 |
| Breadboards + jumpers | **Two** 830-point boards and a jumper kit (the S3 DevKitC is wide) | — | $12 |
| Perfboard + headers | for when it works | — | $5 |

Lab gear: scope, multimeter, soldering iron. A photodiode (BPW34-type) with a 10 kΩ resistor to 3.3 V lets
you measure latency on the scope.

**Don't order yet:** MX switches and keycaps, face plate, PCBs, OLED, case. The prototype tells you what
those should be.

## Build steps

### Step 1: one fader, serial only (first evening)
1. Install PlatformIO (the VS Code extension) or Arduino IDE with ESP32 support. Get a blink and a serial
   "hello" working.
2. Wire the fader: one end to 3V3, the other end to GND, the wiper through 1 kΩ to **GPIO1**, with 100 nF
   from that node to GND.
3. Print the raw ADC value. Move the fader, then let go.
   - **You should see:** a clean sweep from near 0 to near 4095, stable when you let go.
   - **Watch for:** the S3's ADC is nonlinear near both rails. Plan to calibrate the endpoints in software.
   - **Pin rule:** use GPIO1–9 only. ADC2 pins misbehave once wifi is on.

### Step 2: make it clean
1. Average 16 samples per read, then smooth with an exponential moving average.
2. Add a deadband of about ±2 counts so a still fader sends nothing.
3. Plot raw vs filtered in the Serial Plotter.
   - **Pass:** at rest no more than ±2 counts of wander; sweeps are smooth.
   - If it's noisy it is usually wiring: shorten wires and add a 10 µF cap near the fader's 3V3 pin before
     blaming the chip.

### Step 3: several faders, check for crosstalk
1. Add 3 more faders on GPIO2–4, sharing 3V3 and GND.
2. Move one while holding the others still. **Pass:** the still ones stay within ±2 counts.
3. Try powering from a USB charger instead of your laptop to see how much power-source noise matters.

### Step 4: buttons and encoder
1. Wire FLASH, ASSIGN and an encoder with internal pull-ups, as in the schematic.
2. Debounce in software (about 5 ms). Add a long-press (500 ms) detector.
3. Read the encoder with the S3's hardware pulse counter, not by polling.
   - **Pass:** a rapid button mash gives clean press/release edges; the encoder counts correctly spun fast.

### Step 5: get the board talking to your Mac
1. Turn wifi power-save off, join your wifi, and send fader values over WebSocket or UDP at up to 60 Hz. A
   throwaway Python script on the Mac can print what arrives.
2. Watch the rate and the gaps. **Pass:** steady updates, no gap over about 200 ms across a 30-minute soak.
3. Connect it to a real light: feed fader values to the hub's existing `/lights/set` endpoint to drive one
   LIFX bulb's brightness. Expect 100–250 ms of lag; LIFX is the slow path and this measures exactly how slow.

### Step 6: scale the inputs
1. Add an MCP23017 on I²C (SDA GPIO17, SCL GPIO18, address 0x20) and move the buttons onto it. This is how the
   final board handles ~40 inputs.
2. Add the interrupt line and the backlight chain: a couple of SK6812 pixels through the 74AHCT125 and a 330 Ω
   resistor.
3. Press 8 buttons at once. **Pass:** no missed edges, no wifi dropouts.

### Step 7: cardboard-and-acrylic mockup
1. Laser-cut the 72 × 230 mm test plate from the P0 sheet. Cut a cardboard version first to check the fit of the
   real parts.
2. Mount a fader, button and encoder in it, plus a second channel at the real 32 mm pitch.
3. Use it for an hour. This step is about feel: 60 mm vs 100 mm faders, flash-button distance, whether the
   layout works under your hand.

### Step 8: only then, PCBs
Once steps 1–7 are stable on the breadboard, design the channel module in KiCad. The final spec has the pinout
and footprint list.

## What can go wrong
- **Noise** is the most common problem: wifi bursts, long wires and a noisy USB supply all show up in the ADC.
  The final spec reserves a DNP MCP3208 footprint as the fallback.
- **Part dimensions** differ between manufacturers. Measure the part before cutting metal.
- **The S3 DevKitC covers a lot of breadboard.** Use two boards side by side or a breakout.

## Order review: AliExpress list (pre-purchase, 2026-10-09)

Checked line by line against the P0 spec. Total as listed is about **$7.40** before shipping.

| Listing | Verdict | Notes |
|---|---|---|
| ESP32-S3-DevKitC-1 N16R8, CP2102, $1.09 ×1 | OK, buy 3 | N16R8 has octal PSRAM, which uses GPIO35–37; our pin map doesn't touch those. $1.09 is likely the cheapest variant, so confirm the final price and variant. Extra boards cover a spare and the later WLED node. |
| 60 mm slide pot B10K, 5-pack, $0.99 | OK for P0 | Listing says "dual channel": 6 pins, two tracks. Use one wiper (or average both for less noise). Measure slot and mounting before cutting. **No 100 mm fader ordered** (see add list). |
| 24/30 mm LED arcade button ("copy Sanwa OBSC"), free promo | Use with care | Has its own single-color 5 V/12 V LED. Pick **24 mm, 5 V, clear/white cap**. Our design puts an RGB SK6812 behind the cap, so the stock LED unit gets removed or bypassed. ⚠ Check mount style (snap vs screw) against plate thickness. |
| 12×12 mm tactile switch ×20, $0.99 | Bench only | These are PCB switches, not panel pushbuttons. Choose a tall-stem variant (9–12 mm) and buy matching caps. Fine for the prototype; the final ASSIGN part is still to decide. |
| EC11 encoder 20-detent, free promo | OK | Choose **20 mm shaft**. **Knobs are not included**; buy them separately and match the shaft type (knurled vs flatted). |
| SN74AHCT125N, 5-pack, $0.99 | OK | DIP-14, breadboard friendly. The spec needs 2. 3.3 V inputs with 5 V Vcc work (tie OE low). |
| MCP23017 module ×2, $3.33 | OK | Module version is easy to breadboard. Check that A0–A2 are broken out and that the headers are soldered. Runs at 3.3 V. |

### Add to the order
- ESP32-S3 dev board: **3** instead of 1.
- **100 mm** slide pots ×2, for the 60-vs-100 mm decision.
- Arcade buttons: **2–3 × 24 mm** (5 V, clear/white) and 1 × 30 mm, same listing.
- **Encoder knobs** ×4 (match the shaft).
- **SK6812 mini-e** ×20 (strip or breakouts) for the backlight chain.
- Resistor and capacitor kits, ferrite beads, **two 830-point breadboards**, jumper wires.
- Optional and cheap: 0.96" SSD1306 I²C OLED ×2, a 5-pack of MX switches with translucent keycaps for a
  feel test, BPW34 photodiodes ×2 for latency measurement.
- Buy any **mains-powered 5 V supply locally**, from a listed brand, not on AliExpress.
