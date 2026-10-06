# Possible future directions

Ideas that were explored for this project and set aside on 2026-10-06, kept
here so the thinking is not lost. None of this is planned. The clock runs on
the classic ESP32 (the WROOM-32 devkit family) and `flash.py` flashes that
board; see `firmware/esphome/README.md`.

## Why this was set aside

Making the clock run on the ESP32-S2, S3, C3 and C6 looked like a few days
of work, and most of it would have gone into a tool that is only loosely
about this clock: identifying the attached board and fitting a project's pin
needs onto it. Building that for one clock and eight boards is a platform
with no second user. The honest scope of this repository is one clock, one
board family, and a registry of the boards that have been tried.

## The idea: a generic board-fitting tool

What was actually being circled is a tool in two halves that would sit
between any ESP32 project and any ESP32 board.

### Half one: identify the board

Only part of this is possible, and knowing which part is the whole lesson.

- **The chip is detectable.** esptool's `detect_chip()` (a public export in
  esptool 5.3.1; `esp.CHIP_NAME`, `esp.get_chip_description()`,
  `esp.get_chip_features()`) gives the chip family, the silicon revision and
  in-package flash and PSRAM from eFuse, so an S3 N8R8 is distinguishable from
  an N8. Do not parse esptool's stdout for this: "Chip is ESP32-S3" is
  esptool 4 phrasing and 5 prints `Chip type:` and `Features:` instead. For
  this project, detection is nothing more than deleting the `--chip esp32`
  hardcode in `read_mac()`.
- **The board is not detectable, by anyone.** Nothing over USB tells an
  ESP32-S3-DevKitC-1 from a Lolin S3 Mini from an unbranded AliExpress module:
  same chip, same flash ID, different pin headers, different onboard LED,
  different PSRAM wiring. The USB bridge VID/PID (already available as
  `p.vid` in `find_port()`) separates a devkit's native USB from a CH340 clone
  but does not close the gap. The classic ESP32 has the widest board spread
  of all (DevKitC, NodeMCU-32S, WROVER, Lolin32, bare WROOM) and they do not
  agree on where the LED is. ESP Web Tools keys its builds by chip family and
  has the human pick the manifest for exactly this reason.
- **So identity is chip plus one stored answer.** Detect what the wire can
  tell you, use it to shrink the question ("I see an ESP32-S3, 8MB flash, no
  PSRAM, CH343 bridge: DevKitC-1, Lolin S3 Mini, or other?"), ask once at
  first registration, store `chip` and `board` beside the MAC, never ask
  again. A known MAC reporting a different chip is an error, not an update.
  The registry fields have to be optional so every existing `devices.yaml`,
  and the copy inside the safety archive, still loads. A three-way prompt
  gets answered correctly; a 318-way list gets answered wrong.
- **Do not build a board database.** ESPHome ships one: 318 entries in
  `esphome/components/esp32/boards.py`, mapping board id to chip variant.
  Emit a `board:` string into that table and own nothing else.

### Half two: fit the project to the board

This half mostly exists already, under a different name.

- **The "generic connector that reports incompatibility" is ESPHome.** Its
  per-chip pin validators (`esphome/components/esp32/gpio_esp32_s3.py` and
  siblings) reject GPIO22 on an S3, warn on USB and strapping pins, and refuse
  flash and PSRAM pins, with real messages, at config time. A project's job
  is to declare its pin needs to it, not to replace it.
- **Nothing is "transcoded".** Once the register writes are made portable
  (see the appendix), the driver is one source for every chip. What varies per
  board is pin assignment plus two feature gates: the kind of indicator LED,
  and whether the Arduino framework is allowed at all (it is not, for the C6,
  in ESPHome 2026.9). That is pin-fitting and a capability check, not code
  transformation.
- **The one genuinely new artifact** would be a small pin requirements
  manifest and a fitter: "four outputs below GPIO32, writable in a single
  register store, two inputs, one indicator", solved against a given board's
  free pins, keyed by board rather than by chip because a chip-family default
  can only use pins legal on every board of that family. Neither esptool nor
  ESPHome has this. It is reusable across projects and it is small. It is
  also the part that turns a flashing script into a platform, which is why it
  stopped here.

### What it would have taken for this clock

For the record, the staged plan that was written and then set aside:

1. Make the driver chip-agnostic: `REG_WRITE(GPIO_OUT_W1TS_REG, mask)` and the
   `_W1TC` twin in `send_pair_()` and in the panel-test `sendFrame()`, and a
   variant check in `__init__.py` so an unsupported chip fails config
   validation with a plain message.
2. Identify the board in `flash.py` as above; store `chip` and `board` in the
   registry; an explicit `--board` override to change it later.
3. Per-board config packages in `boards/`, one file per ESPHome board id,
   holding only the `esp32:` block, the six pins and the heartbeat output.
   The "other" answer writes no package and the user supplies the pins.
4. Print the wiring after flashing: the GPIO-to-MCU-socket table for the
   board that was just configured, since the pins are no longer the ones in
   the README.
5. One physical board per variant on the bench before it is called
   supported, with the scan timing checked on a logic analyser.

Rough cost: two or three days for 1 to 4; open-ended for 5, which is hardware
purchases and bench time, not code.

## Appendix: what was learned about the other chips

Verified against ESP-IDF 5.5.5 headers in the ESPHome cache and ESPHome
2026.9's pin validators. No board other than the classic ESP32 has been built
or tested; every claim below about another chip is a header and validator
inspection, not a flash.

### What the firmware depends on

These are the hardware assumptions in `aip33628.cpp`. Anything that keeps all
of them is a config change; anything that breaks one is a code change.

| Dependency | Where | Why it matters |
| --- | --- | --- |
| `GPIO.out_w1ts` / `out_w1tc` as bare `uint32_t` | `send_pair_()` | One store clocks both buses; the loop has no padding because a store costs more than the 16ns the AiP33628 needs |
| All four display pins below GPIO32 | `_low_bank_pin()` in `__init__.py` | The low output register only reaches GPIO0 to 31 |
| `gptimer` with 1MHz resolution and a fixed auto-reload alarm | `setup()` | Sub-frames are 40us; `esp_timer` jitters too much beside WiFi |
| `IRAM_ATTR` on the tick and the frame send | both | Flash cache misses inside the ISR would stretch a sub-frame |
| 3.3V push-pull outputs driving the panel directly | wiring | No level shifter in normal setups |
| A 5V input with an onboard 3.3V regulator | devkit | The clock's USB port is the power source in the case |

### Per chip

#### ESP32 (classic, WROOM-32, WROVER, DevKitC, NodeMCU-32S)

Supported. Reference platform. `out_w1ts` is `uint32_t`, GPIO0 to 31 are all
in the low bank, GPIO34 to 39 are input only, strapping pins are 0, 2, 5, 12
and 15. Devkit LED on GPIO2.

#### ESP32-S3

Compiles unchanged. `out_w1ts` and `out_w1tc` are plain `uint32_t` in IDF
5.5.5, so the README's `.val` note does not apply. `gptimer` and `IRAM_ATTR`
carry over. GPIO store speed and interrupt latency are at least as good as the
classic part, so the timing margins hold.

What blocks it, in order:

1. **GPIO22 to 25 do not exist.** ESPHome rejects them outright
   (`gpio_esp32_s3.py`). The default `clk_pin: GPIO22` fails validation.
2. **GPIO19 and 20 are USB D- and D+.** ESPHome warns. On a devkit with the
   native USB port wired these cannot drive the panel.
3. **GPIO26 to 32 are the SPI flash bus.** `SPICS1`, `SPIHD`, `SPIWP`,
   `SPICS0`, `SPICLK`, `SPIQ`, `SPID`. ESPHome rejects them. The default
   button pin GPIO32 is `SPID`.
4. **GPIO33 to 37 are octal PSRAM** on R8 modules. ESPHome only warns, but
   the default button pin GPIO33 is one of them.
5. **Strapping pins are 0, 3, 45 and 46.** ESPHome warns.
6. **The devkit LED is a WS2812 on GPIO38 or GPIO48**, not a plain LED on
   GPIO2. The heartbeat output on GPIO2 compiles and does nothing visible.
7. **`flash.py` refuses it** because of `--chip esp32`.
8. **`board: esp32dev`** must become `esp32-s3-devkitc-1` or a matching
   variant. ESPHome infers `variant: ESP32S3` from the board name.

Usable display pins under the GPIO31 rule: 1, 2, 4 to 18, 21. Avoid 0 and 3.
A suggested map is CLK GPIO4, DATA GPIO5, CLK_1 GPIO6, DATA_1 GPIO7, buttons
GPIO8 and GPIO9, heartbeat left as GPIO2 or moved to the RGB LED.

#### ESP32-S2

Compiles unchanged for the same reason as the S3: `out_w1ts` is `uint32_t`.
Single core, so the scan ISR and the WiFi task share one core; untested
whether the 40us sub-frame still holds. GPIO19 and 20 are USB. GPIO26 to 32
are flash and PSRAM. GPIO22 to 25 do not exist. Strapping pins 0, 45 and 46.
Same pin map as the S3 works. Board `esp32-s2-saola-1` or similar.

#### ESP32-C3

Needs the code change. `out_w1ts` and `out_w1tc` are unions, so every bare
assignment in `send_pair_()` becomes `GPIO.out_w1ts.val = ...`. Nine lines.
Single RISC-V core. GPIO18 and 19 are USB. GPIO12 to 17 are flash on most
modules. Only 22 GPIOs, all below 32, so the low bank rule is never hit.
Strapping pins 2, 8, 9. Board `esp32-c3-devkitm-1`. The panel-test firmware
has the same bare assignments and needs the same edit.

#### ESP32-C6

Needs the code change. `out_w1ts` is `gpio_out_w1ts_reg_t`, a union with
`.val`. Same nine lines. GPIO12 and 13 are USB. GPIO24 to 30 are flash.
Strapping pins 4, 5, 8, 9, 15. Board `esp32-c6-devkitc-1`. Requires an
ESPHome release with C6 support in the Arduino path, which is newer than the
IDF only support; verify before attempting.

#### ESP8266

Not viable. No `gptimer`, a different GPIO register model, and a single
core with WiFi in the same context as user code. A port would be a new
driver, not a config change. `flash.py` already refuses it with esptool's
"This chip is ESP8266, not ESP32" message.

#### ESP32-P4 and later

Not reviewed. The P4 has no WiFi of its own and is out of scope for a
network clock.

### Portable code change

To stop caring which chips have union registers, replace the direct member
writes with a macro:

```cpp
#if defined(CONFIG_IDF_TARGET_ESP32) || defined(CONFIG_IDF_TARGET_ESP32S2) || defined(CONFIG_IDF_TARGET_ESP32S3)
#define GPIO_SET(mask) (GPIO.out_w1ts = (mask))
#define GPIO_CLR(mask) (GPIO.out_w1tc = (mask))
#else
#define GPIO_SET(mask) (GPIO.out_w1ts.val = (mask))
#define GPIO_CLR(mask) (GPIO.out_w1tc.val = (mask))
#endif
```

Or write through the register address with `REG_WRITE(GPIO_OUT_W1TS_REG,
mask)`, which is a plain 32-bit store on every chip and compiles to the same
instruction. That removes the `#if` entirely and is the cleaner fix. Apply the
same to `sendFrame()` in `firmware/esp32/panel-test/src/main.cpp`.

### Suggested S3 pin map

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

Unverified on hardware. Item 5 decides whether it stays. These are the
defaults for an `other` S3; a recognised board gets its own package.

### Decisions that were reached

#### Heartbeat: WS2812 (decided)

The S3 package replaces the `gpio` output and its 4s interval with a one-LED
`esp32_rmt_led_strip` light, `chipset: WS2812`, `rgb_order: GRB`,
`internal: true`, and the interval calls `light.turn_on` at a low brightness
then `light.turn_off`. The RMT peripheral has its own interrupt and DMA-free
path; it does not touch the gptimer that scans the panel.

The pin depends on the devkit revision and ESPHome's board table does not
record it: ESP32-S3-DevKitC-1 v1.0 uses GPIO48, v1.1 uses GPIO38. That is
the board-not-chip problem from "Half one" in miniature - the chip cannot
tell you, so the prompt has to. When
the answer is an S3-DevKitC-1, ask which revision and store it with the board.
Keep the pin a substitution in the board package, with GPIO48 as the default
for an `other` S3, and have the post-flash pin-out guide print which one it
chose so the person at the bench can see whether the LED actually blinks. The
classic ESP32 package keeps the plain GPIO2 output.

#### C6: needs ESP-IDF, so defer it

Resolved from the installed ESPHome, not from an earlier unchecked guess. ESPHome
2026.9 refuses `framework: type: arduino` for the C6; it is only allowed for
ESP32, C3, S2 and S3. A C6 package would have to set `type: esp-idf` and the
rest of `clock-base.yaml` would need to be checked under that framework. The
driver itself uses only IDF calls (`gptimer`, `soc/gpio_struct.h`), so it is
probably fine, but nothing else in the config has been tried there.

Recommendation: leave C6 out of the first pass. The C3 covers the RISC-V
register change, and C6 can be added as `boards/esp32c6.yaml` with its own
framework block once a board is on the bench.

#### Register writes: `REG_WRITE` is the same store

Settled by the definitions, not by disassembly (the Xtensa objdump is not in
the local toolchain cache). On ESP32, S2 and S3 `GPIO.out_w1ts` is a
`volatile uint32_t` member; `REG_WRITE(addr, v)` expands to
`*(volatile uint32_t *)addr = v`. Both are one volatile 32-bit store to the
same address and compile to the same instruction, so the 6.4us pair send
does not change. Item 1's register change still gets a sanity check: a logic
analyser on CLK, or a one-time cycle count around `send_pair_()` logged at
boot.

#### Single core: unknown, and here is how to find out before buying

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

### Open questions

- Does the S2 or C3 single core keep the 40us sub-frame stable with WiFi
  active? Nobody has measured it.
- Is the 6.4us pair send preserved when the direct member writes become
  `REG_WRITE`? Expected yes, needs a build check.
- Which ESPHome release added C6 Arduino support? Needed before item 3
  claims C6.
- The heartbeat LED is settled in this file (a one-LED `esp32_rmt_led_strip`
  light on the S3, plain GPIO2 on the classic ESP32), but its pin is the
  clearest case of board-not-chip: DevKitC-1 v1.0 uses GPIO48 and v1.1 uses
  GPIO38, and ESPHome's board table records neither. The revision becomes one
  more thing the board prompt under Half one has to ask.
- How many boards should the prompt offer per chip family, and on what
  evidence are they listed? Starting from the boards actually on the bench and
  adding one per confirmed report keeps every option honest; guessing a
  shortlist from ESPHome's 318 puts untested pin maps in front of the user.
