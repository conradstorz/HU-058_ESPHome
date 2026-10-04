# Roadmap to multi-board compatibility

The short version of `automatic-board-detection-roadmap.md`: what stops a
non-classic ESP32 from running this clock today, and what has to change. The
long file has the per-chip header findings; this one is the checklist.

Only the classic ESP32 (WROOM-32, DevKitC) has ever been flashed. Everything
below about other chips comes from reading ESP-IDF headers and ESPHome's pin
validators, not from hardware.

## What is in the way

| # | Issue | Where | Affects |
| --- | --- | --- | --- |
| 1 | Default pins GPIO22/21/19/18 (display) and GPIO32/33 (buttons) are classic-ESP32 choices. On the S3, GPIO22 does not exist, GPIO19 is USB D-, GPIO32/33 are flash and PSRAM. | `firmware/esphome/clock-base.yaml` | S2, S3, C3, C6 |
| 2 | `board: esp32dev` is hardcoded. ESPHome infers the chip variant from this line. | `firmware/esphome/clock-base.yaml` | all |
| 3 | Heartbeat LED on GPIO2 is the classic devkit's LED. S3 devkits have a WS2812 on GPIO38 or 48; the output compiles and shows nothing. Decided: drive the WS2812, see below. | `firmware/esphome/clock-base.yaml` | S2, S3 |
| 4 | `flash.py` runs esptool with `--chip esp32`, so any other chip is refused before the registry is touched. | `firmware/esphome/flash.py`, `read_mac()` | all |
| 5 | The component accepts any ESP32 variant without checking it, so a bad pin fails at compile time with no useful message. | `firmware/esphome/components/aip33628/__init__.py` | all |
| 6 | `GPIO.out_w1ts` / `out_w1tc` are written as bare integers. That is correct on ESP32, S2 and S3 but a union on C3 and C6. | `components/aip33628/aip33628.cpp` `send_pair_()`, `panel-test/src/main.cpp` `sendFrame()` | C3, C6 only |
| 7 | `platformio.ini` in the panel-test project is also pinned to `esp32dev`. | `firmware/esp32/panel-test/` | all |
| 8 | `framework: type: arduino` is hardcoded, and ESPHome 2026.9 only allows Arduino on ESP32, C3, S2 and S3 (`esp32/__init__.py`, `ARDUINO_ALLOWED_VARIANTS`). | `firmware/esphome/clock-base.yaml` | C6 |

S2 and S3 are config-only ports once 1 to 5 are done. C3 also needs 6. C6
needs 6 and 8, which means a second framework. See the decisions below.

## The plan

1. **Make the driver chip-agnostic.** Replace the bare register writes with
   `REG_WRITE(GPIO_OUT_W1TS_REG, mask)` and the `_W1TC` twin, in both the
   component and the panel-test firmware. Confirm the classic ESP32 still
   clocks a pair in 6.4us. Add a variant check to `__init__.py` so an
   unsupported chip fails config validation with a plain message.
2. **Detect the chip in `flash.py`.** Use `--chip auto`, parse the chip name,
   keep refusing ESP8266 and anything not on the supported list. Record the
   chip in `devices.yaml` next to the MAC. A known MAC that shows up as a
   different chip is an error, not an update.
3. **Per-chip config packages.** Move the `esp32:` block, the four display
   pins, the two button pins and the heartbeat output out of `clock-base.yaml`
   into `boards/esp32.yaml`, `boards/esp32s3.yaml` and so on. `flash.py`
   writes the matching `packages:` entry into the generated device file. The
   existing `wifi-clock.yaml` pins the `esp32.yaml` package explicitly and
   keeps its wiring.
4. **Show the wiring after flashing.** Once `flash.py` knows the chip and the
   board package it chose, the pins are no longer the ones in the README. So
   after a successful flash it must print a pin-out guide for the person at
   the bench: one table mapping each ESP32 GPIO it just configured to the MCU
   socket pin and net on the clock board (CLK, DATA, CLK_1, DATA_1, S1, S2,
   GND), with the HU-058D vs HU-058/SE socket-pin difference for CLK called
   out. The same table goes into `docs/wiring.md` as a per-chip section, and
   the printed version names that section. A clock that is already registered
   gets the table for the chip it was registered with, not the default.
5. **Test on hardware, one variant at a time.** Config validates, compiles,
   frame timing on a logic analyser matches the classic ESP32, no visible
   flicker on an unsaturated color, both buttons read, OTA works. Single-core
   parts (S2, C3, C6) get extra time on the flicker test because WiFi and the
   scan ISR share a core. A chip is not listed as supported until this passes.

## Suggested S3 pin map

Legal under the GPIO0 to 31 rule, clear of USB, flash, PSRAM and strapping pins.

| Net | S3 GPIO | MCU socket pin |
| --- | --- | --- |
| CLK, driver 1 | GPIO4 | 14 on HU-058D; 16 on HU-058 / HU-058SE |
| DATA, driver 1 | GPIO5 | 5 |
| CLK_1, driver 2 | GPIO6 | 1 |
| DATA_1, driver 2 | GPIO7 | 2 |
| S1, top button | GPIO8 | 9 |
| S2, bottom button | GPIO9 | 10 |
| GND | GND | 8 |

Unverified on hardware. Step 5 decides whether it stays.

## Decisions

### Heartbeat: WS2812 (decided)

The S3 package replaces the `gpio` output and its 4s interval with a one-LED
`esp32_rmt_led_strip` light, `chipset: WS2812`, `rgb_order: GRB`,
`internal: true`, and the interval calls `light.turn_on` at a low brightness
then `light.turn_off`. The RMT peripheral has its own interrupt and DMA-free
path; it does not touch the gptimer that scans the panel.

The pin depends on the devkit revision and ESPHome's board table does not
record it: ESP32-S3-DevKitC-1 v1.0 uses GPIO48, v1.1 uses GPIO38. Make it a
substitution in `boards/esp32s3.yaml` with GPIO48 as the default, and have the
post-flash pin-out guide print which one it chose so the person at the bench
can see whether the LED actually blinks. The classic ESP32 package keeps the
plain GPIO2 output.

### C6: needs ESP-IDF, so defer it

Resolved from the installed ESPHome, not from the old roadmap's guess. ESPHome
2026.9 refuses `framework: type: arduino` for the C6; it is only allowed for
ESP32, C3, S2 and S3. A C6 package would have to set `type: esp-idf` and the
rest of `clock-base.yaml` would need to be checked under that framework. The
driver itself uses only IDF calls (`gptimer`, `soc/gpio_struct.h`), so it is
probably fine, but nothing else in the config has been tried there.

Recommendation: leave C6 out of the first pass. The C3 covers the RISC-V
register change, and C6 can be added as `boards/esp32c6.yaml` with its own
framework block once a board is on the bench.

### Register writes: `REG_WRITE` is the same store

Settled by the definitions, not by disassembly (the Xtensa objdump is not in
the local toolchain cache). On ESP32, S2 and S3 `GPIO.out_w1ts` is a
`volatile uint32_t` member; `REG_WRITE(addr, v)` expands to
`*(volatile uint32_t *)addr = v`. Both are one volatile 32-bit store to the
same address and compile to the same instruction, so the 6.4us pair send
does not change. The stage 2 build still gets a sanity check: a logic
analyser on CLK, or a one-time cycle count around `send_pair_()` logged at
boot.

### Single core: unknown, and here is how to find out before buying

Why it is a real question. On the classic ESP32 the scan ISR lives on core 1
(`CONFIG_ARDUINO_RUNNING_CORE=1`, where `setup()` creates the gptimer) and
WiFi is pinned to core 0 (`CONFIG_ESP_WIFI_TASK_PINNED_TO_CORE_0=y`). The ISR
never waits behind a WiFi critical section today. The S3 has the same split.
On S2, C3 and C6 one core runs everything, and every critical section in the
WiFi driver masks the timer interrupt until it ends.

What it would look like. CPU load is not the problem: the ISR runs 25,000
times a second, most ticks are a decrement and a return, and the total is well
under a tenth of one core. The problem is latency. A late tick stretches the
sub-frame that is lit, and the 40us sub-frame is the brightness LSB, so the
damage lands on unsaturated colors as a low-level shimmer. Saturated colors
collapse to one 600us step per COM pair and barely notice.

Raising `intr_priority` on the gptimer does not help: on Xtensa a critical
section masks everything up to level 3, which is as high as a C handler can
go, and on RISC-V it masks all of them. If the measurement fails, the honest
fix is a coarser schedule (a larger `UNIT_US`, so fewer color levels on that
chip), not a priority tweak.

How to measure, on the classic ESP32 first so there is a baseline:

1. In `scan_tick_()`, read `esp_timer_get_time()` (IRAM-safe), subtract the
   previous value, and keep the largest excess over `UNIT_US` seen since the
   last read. Debug build only, behind a config flag.
2. Expose it as a template sensor, "scan jitter us", polled every second,
   which also resets the maximum.
3. Watch it with Home Assistant connected, during an OTA, and with the light
   being changed from a dashboard. That is the worst case the clock sees.
4. Repeat on the first S2 or C3 board.

Threshold, as a judgment call: a maximum under 10us, a quarter of the LSB
sub-frame, is invisible. Over 20us, drop that chip to 8 levels per channel
or do not list it.

One related fact that applies to every chip, found while checking this:
`CONFIG_GPTIMER_ISR_IRAM_SAFE` is off in the current build, so each flash
write (ESPHome saving a switch or light state) suspends the scan ISR for the
write and freezes the panel for a few milliseconds. Turning it on through
`sdkconfig_options` would remove that. It is a separate fix, not a multi-board
one, and is noted here so it is not mistaken for single-core jitter when the
measurement runs.
