# Automatic board detection roadmap

Everything learned about running this firmware on ESP32 variants other than
the classic ESP32-WROOM-32, and a staged plan for letting `flash.py` and the
ESPHome config handle as many of them as possible without hand edits.

Findings below were verified against ESP-IDF 5.5.5 headers in the ESPHome
cache (`components/soc/<chip>/register/soc/gpio_struct.h`) and ESPHome
2026.9's pin validators (`esphome/components/esp32/gpio_esp32_*.py`). No
board other than the classic ESP32 has been built or tested. Every claim about
another chip is a header and validator inspection, not a flash.

## Current state

- `firmware/esphome/README.md` says S2, S3, C3 and C6 need nine `.val` edits in
  `send_pair_()` and a `board:` change, and nothing else. That is wrong for
  S2 and S3, and incomplete for all four. See "Per chip" below.
- `flash.py` hardcodes `--chip esp32` in `read_mac()`, so esptool refuses any
  other chip before the registry is touched. That is the first barrier on
  the flashing path, and it is deliberate. The board line and the default
  pins below are the next two.
- `clock-base.yaml` pins `board: esp32dev`, GPIO22/21/19/18 for the display,
  GPIO32/33 for the buttons and GPIO2 for the heartbeat LED. All of those are
  classic-ESP32 choices and several are illegal on other chips.
- `components/aip33628/__init__.py` accepts `PLATFORM_ESP32` without checking
  the variant, so config validation gives no early warning.
- `firmware/esp32/panel-test/platformio.ini` is also pinned to `esp32dev`.

## What the firmware depends on

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

## Per chip

### ESP32 (classic, WROOM-32, WROVER, DevKitC, NodeMCU-32S)

Supported. Reference platform. `out_w1ts` is `uint32_t`, GPIO0 to 31 are all
in the low bank, GPIO34 to 39 are input only, strapping pins are 0, 2, 5, 12
and 15. Devkit LED on GPIO2.

### ESP32-S3

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

### ESP32-S2

Compiles unchanged for the same reason as the S3: `out_w1ts` is `uint32_t`.
Single core, so the scan ISR and the WiFi task share one core; untested
whether the 40us sub-frame still holds. GPIO19 and 20 are USB. GPIO26 to 32
are flash and PSRAM. GPIO22 to 25 do not exist. Strapping pins 0, 45 and 46.
Same pin map as the S3 works. Board `esp32-s2-saola-1` or similar.

### ESP32-C3

Needs the code change. `out_w1ts` and `out_w1tc` are unions, so every bare
assignment in `send_pair_()` becomes `GPIO.out_w1ts.val = ...`. Nine lines.
Single RISC-V core. GPIO18 and 19 are USB. GPIO12 to 17 are flash on most
modules. Only 22 GPIOs, all below 32, so the low bank rule is never hit.
Strapping pins 2, 8, 9. Board `esp32-c3-devkitm-1`. The panel-test firmware
has the same bare assignments and needs the same edit.

### ESP32-C6

Needs the code change. `out_w1ts` is `gpio_out_w1ts_reg_t`, a union with
`.val`. Same nine lines. GPIO12 and 13 are USB. GPIO24 to 30 are flash.
Strapping pins 4, 5, 8, 9, 15. Board `esp32-c6-devkitc-1`. Requires an
ESPHome release with C6 support in the Arduino path, which is newer than the
IDF only support; verify before attempting.

### ESP8266

Not viable. No `gptimer`, a different GPIO register model, and a single
core with WiFi in the same context as user code. A port would be a new
driver, not a config change. `flash.py` already refuses it with esptool's
"This chip is ESP8266, not ESP32" message.

### ESP32-P4 and later

Not reviewed. The P4 has no WiFi of its own and is out of scope for a
network clock.

## Portable code change

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

## Roadmap

### Stage 1: correct the docs

- Split the README's "S2, S3, C3 and C6" section into "S2 and S3, config
  only" and "C3 and C6, code change". Drop "nothing else should need
  touching".
- List the S3 pin constraints and a suggested S3 pin map.
- State that `flash.py` refuses non-classic chips and why.
- Point at this file.

### Stage 2: make the code chip-agnostic

- Switch `send_pair_()` and `sendFrame()` to `REG_WRITE` on the `_W1TS` and
  `_W1TC` register addresses. Verify the ESP32 build still clocks the pair in
  6.4us by checking the compiled loop or timing a frame.
- Add a variant check to `__init__.py` using
  `esphome.components.esp32.get_esp32_variant()` so an unsupported variant
  fails config validation with a clear message instead of a compile error
  inside `aip33628.cpp`.

### Stage 3: let flash.py detect the chip

- Run esptool with `--chip auto` and parse the "Chip is ESP32-S3" line, or
  call esptool as a library and read `chip.CHIP_NAME`.
- Keep the refusal for ESP8266 and anything not in a supported list.
- Record the chip in `devices.yaml` next to the MAC. Reflashing a known MAC
  must keep the chip it was registered with; a mismatch is an error, not a
  silent update.

### Stage 4: per-chip config packages

- Move the chip specific parts of `clock-base.yaml` into
  `boards/esp32.yaml`, `boards/esp32s3.yaml` and so on: the `esp32:` block,
  the four display pins, the two button pins and the heartbeat output.
- Have `flash.py` write the right `packages:` entry into the generated
  `<name>.yaml` from the detected chip. `wifi-clock.yaml` and the existing
  clock file keep their current pins by pinning the `esp32.yaml` package
  explicitly.
- Update `docs/wiring.md` with a per-chip pin table, since the MCU socket
  wiring is fixed and only the ESP32 side moves.

### Stage 5: test on hardware

Each variant needs one physical build before it is listed as supported.
Check in order: config validates, compiles, frame timing on a scope or logic
analyser matches the classic ESP32, no visible flicker on an unsaturated
color, buttons read, OTA works. Single core parts (S2, C3, C6) get extra
attention on the flicker test since WiFi and the scan ISR share a core.

## Open questions

- Does the S2 or C3 single core keep the 40us sub-frame stable with WiFi
  active? Nobody has measured it.
- Is the 6.4us pair send preserved when the direct member writes become
  `REG_WRITE`? Expected yes, needs a build check.
- Which ESPHome release added C6 Arduino support? Needed before Stage 4
  claims C6.
- Should the heartbeat move to the S3 devkit's WS2812 via `neopixelbus` or
  stay a plain GPIO output the user wires themselves? Default to plain GPIO
  and document the LED difference.
