# ESPHome firmware

Firmware to drive two AiP33628 drivers, plus a config that puts
the panel in Home Assistant as a single light.

Mapping, scan timing and the current budget are in
`../../docs/display-map.md`. Every entity and action this exposes is in
`../../docs/home-assistant.md`.

## Start here

I recommend you build and flash the firmware onto the ESP32 before you wire anything to the clock board.

An ESP32 connected via a USB cable with nothing attached to it will still boot, join
WiFi and turn up in Home Assistant. Then when you connect the six wires the panel just
lights up.

1. `uv sync`, then copy `secrets.yaml.example` to `secrets.yaml` and fill in the WiFi entries.
2. `uv run flash.py` with the ESP32 on USB, from this directory.
3. Adopt the device in Home Assistant.
4. Build the clock board, `../../docs/wiring.md`.
5. Wire the six lines to the ESP32 and power it up.

Use the firmware in `../esp32/panel-test/` as it drives the panel with no WiFi and no Home Assistant, so if the display misbehaves it tells you whether the hardware is right before you start suspecting this
component.

## Requirements

[uv](https://docs.astral.sh/uv/) and Python 3.12 or newer. `uv sync` in this
directory installs ESPHome 2026.9.0 or newer, esptool and everything the
flashing tool needs into `.venv/`. There is nothing to activate; every command
in this README runs through `uv run`.

## Which ESP32

These are the boards that can be programmed to run the clock: the plain
**ESP32-WROOM-32** devkit family, the common 30 or 38 pin board that sells for
a few dollars. WROOM-32, WROVER, DevKitC and NodeMCU-32S all work unchanged. If
the listing says ESP32 with no letter after it, that is the one. It needs to be
a board that can be powered directly from 5V on the clock's USB port, and these
all have a 3.3V regulator on board for that.

Nothing else is supported. `flash.py` checks the chip before it writes
anything and refuses any board that is not a classic ESP32 with esptool's own
"This chip is X, not ESP32" message, so a mistake costs nothing. That includes
the kit's own ESP8266, which has none of the timer and GPIO machinery the scan
leans on, and the newer ESP32-S2, S3, C3 and C6, on which this driver has never
been built or timed. What a port to those would take is written up in
`../../possible-future-directions.md`.

The four display pins have to sit below GPIO32. A frame goes out as a single
store to the low GPIO output register, and that register only reaches GPIO0
through GPIO31. `__init__.py` checks this and refuses to build if you pick a
higher pin.

## Setup

```
uv sync
cp secrets.yaml.example secrets.yaml
```

On Windows that is `copy` instead of `cp`. `uv sync` installs ESPHome,
esptool and the flashing tool's dependencies into `.venv/`; every command
below is run through `uv run` so nothing needs activating.

Fill in the WiFi credentials and the timezone. Leave the per-device entries
alone, `flash.py` writes those.

### Where the timezone comes from

The clock has two time sources, both in the `time:` block of `clock-base.yaml`:

```yaml
time:
  - platform: homeassistant   # primary: time and timezone from Home Assistant
    id: ha_time
  - platform: sntp            # fallback: NTP time, timezone from secrets.yaml
    id: sntp_time
    timezone: !secret timezone
```

Once the clock is adopted in Home Assistant, **Home Assistant's own timezone
wins**, DST rules included. It is pushed to the clock on every time sync, so
changing the timezone in Home Assistant changes it on the clock. Nothing in
this repo needs editing for that.

The `timezone` entry in `secrets.yaml` only feeds the `sntp` fallback, and
the fallback is a startup value, not a standby. ESPHome keeps one global
timezone. The `sntp` block sets it from `secrets.yaml` at boot, and the first
Home Assistant time sync overwrites it. Nothing ever puts the `secrets.yaml`
value back, so if Home Assistant later drops off the network the clock keeps
the last timezone it was pushed. The secret is therefore in charge:

- from every power-on until Home Assistant first answers, adopted or not
- for as long as the clock is never adopted
- for as long as Home Assistant is older than 2026.3.0, which sends a
  timezone format current ESPHome no longer decodes

The example ships `Etc/UTC`, so a clock that reads several hours off is on
this startup value and has not heard from Home Assistant since it booted.
Set it to your zone anyway so the first seconds of every boot look right.

**Do not add a `timezone:` line under `platform: homeassistant`.** That key is
optional, and leaving it out is what enables the push. Adding one compiles the
push out and pins the clock to whatever you wrote, no matter what Home
Assistant says.

## Build and flash

```
uv run flash.py
```

Plug in one clock and run that. It reads the ESP32's factory MAC address over
USB and looks it up in `devices.yaml`:

- **A clock it has never seen** gets a name from the current time,
  `clock-YYYYMMDD-HHMM`, a fresh API key and OTA password appended to
  `secrets.yaml`, an entry in `devices.yaml`, and a `<name>.yaml` device file
  next to this README. Then it compiles and flashes.
- **A clock it has seen** gets exactly the identity it had, so reflashing
  never disturbs its pairing with Home Assistant.
- **Either way the entry records how the run ended**: `last_attempt` is the
  time and `last_result` is one of `registered`, `flashed`, `build-failed` or
  `flash-failed`. The build runs on its own before the upload so the two
  failures are told apart. One look at `devices.yaml` says which boards on
  the bench have a working clock on them and which were tried and did not.

That is how several clocks live side by side: each one is a separate ESPHome
node with its own secrets, all built from `clock-base.yaml`. The first clock
ever built, `wifi-clock`, predates the registry and was entered by hand.

`devices.yaml` and those minted `clock-YYYYMMDD-HHMM.yaml` files are both
gitignored. They are a record of the boards on this workstation, MAC addresses
included, and they describe nothing about the project itself, so they stay out
of the repo. `devices.yaml.example` shows what the registry holds; there is
nothing to copy, because `flash.py` creates the real one, header and all, the
first time it sees a clock. `clock-base.yaml` and `wifi-clock.yaml` are
tracked.

Back the two of them up with `secrets.yaml`, though, and keep all three
together. The registry is what stops `flash.py` minting a second identity for
a clock Home Assistant has already paired, and the secrets are the keys that
pairing uses. Lose the pair and every clock has to be re-added by hand.

`flash.py` keeps its own copy of both, so losing them takes more than one
mistake. Every flash writes `devices.yaml` and `secrets.yaml` into
`HU-058_clock_safety_backup_of_local_data.zip` in your user data directory
(`%LOCALAPPDATA%\HU-058_ESPHome\` on Windows,
`~/.local/share/HU-058_ESPHome/` elsewhere), along with a `README.md`
explaining what the archive is. If either file is missing when you next run
`flash.py`, it is restored from there before the registry is read, and the run
says so.

The archive is outside the repository on purpose. Both files are gitignored,
and git treats an untracked ignored file as expendable: it deletes one on a
`git pull` carrying the commit that untracked it, silently overwrites one on a
`git checkout` of an earlier commit and deletes it again on the way back, and
removes one with `git clean -xdf`. A copy kept in the working tree would go in
the same command as the original. It is one rolling copy, not a history, and it
is not a substitute for backing the pair up somewhere off this machine — and
because it contains `secrets.yaml`, it is not encrypted and should not be
synced or shared.

It checks the chip before it writes anything. A board that is not an ESP32
(the kit's own ESP8266, say) is refused with esptool's "This chip is ESP8266,
not ESP32" message and nothing is registered.

`--port COM7` picks the serial port when more than one USB adapter is
plugged in. `--register-only` writes the files without flashing. Anything
else on the command line goes straight to `esphome run`, so
`uv run flash.py --no-logs` skips the log tail after upload.

USB flashing on a WROOM-32 devkit may need to hold down BOOT while trying to
program. Auto-reset into the bootloader does not work on every board.

On Windows run these commands from PowerShell or cmd. A Git Bash / MSYS shell
compiles with no error and produces no build output, so the upload then fails;
`flash.py` refuses to build there rather than let it get that far.
`--register-only` compiles nothing and still works in any shell.

Each clock is its own ESPHome node, so a new MAC gets a new build directory and
would recompile the whole ESP-IDF framework. `flash.py` puts ccache on the
build's PATH to stop that. ESPHome installs ccache alongside the ESP-IDF tools
and already sets `CCACHE_BASEDIR`, so two nodes built from the same source share
compiled objects; it just looks for the binary on the calling shell's PATH,
where its own install is not, and so disables the cache it shipped. Measured
here, a second clock from a cold configure took 104 s instead of 400 s, with
1083 of 1089 compiles served from the cache. The six misses are the files that
genuinely differ per clock. The cache sits with the ESP-IDF tools and
`uv run esphome clean-all` removes it.

Once it is on the network, updates go over the air, and the API carries the
log stream. Use the clock's own device file:

```
uv run esphome run clock-20260928-1407.yaml --device clock-20260928-1407.local
uv run esphome logs clock-20260928-1407.yaml --device clock-20260928-1407.local
```

Two things to know before pushing an OTA build:

- **A bad build is not recoverable remotely.** If it fails to bring up WiFi
  the only way back is a USB cable, which means opening the case. Compile
  before uploading. The fallback access point in `clock-base.yaml` is the
  safety net, so look for its setup SSID before assuming a flash is dead.
- **Do not open the serial port for about a minute after an OTA.** Opening it
  asserts DTR, which resets the board before ESPHome marks the new partition
  valid, and the device rolls back to the previous image with no error
  anywhere.

## Adopt in Home Assistant

Home Assistant finds the device on its own. Look under Settings > Devices and
Services for a discovered ESPHome node.

It asks for an encryption key. That is the `api_key_<name>` entry for this
clock in your `secrets.yaml`; `flash.py` prints the exact entry name when it
registers a new clock. Every clock has its own, so pick the one that matches
the node Home Assistant discovered.

The clock's actions appear in Home Assistant as `esphome.<name_>_<action>`,
with the hyphens in the name turned into underscores, so a clock called
`clock-20260928-1407` exposes `esphome.clock_20260928_1407_show_number`.

Do this before you wire anything. Until the device is adopted the clock shows
the time and ignores everything else, because the light, the switches and
every effect entity live on the Home Assistant side.

## Wiring

Six wires into the empty DIP-16 MCU footprint. Four for the display, two for
the buttons, plus a ground.

Full build notes are in `../../docs/wiring.md`.

| ESP32 | Pin | Net |
| --- | --- | --- |
| GPIO22 | 14 on HU-058D; 16 on HU-058 / HU-058SE | CLK, driver 1 |
| GPIO21 | 5 | DATA, driver 1 |
| GPIO19 | 1 | CLK_1, driver 2 |
| GPIO18 | 2 | DATA_1, driver 2 |
| GPIO32 | 9 | S1, top button |
| GPIO33 | 10 | S2, bottom button |
| GND | 8 | GND |

## Configuration

```yaml
aip33628:
  id: panel
  clk_pin: GPIO22
  data_pin: GPIO21
  clk2_pin: GPIO19
  data2_pin: GPIO18
  time_id: ha_time
  twelve_hour: true
  blink_colon: true
  max_current: 15
```

| Key | Default | What |
| --- | --- | --- |
| `clk_pin`, `data_pin` | required | Driver 1 bus, below GPIO32 |
| `clk2_pin`, `data2_pin` | required | Driver 2 bus, below GPIO32 |
| `time_id` | none | A time source. Without one the panel shows dashes. |
| `max_current` | 15 | Ceiling on `IS[3:0]`, 0 to 15 |
| `twelve_hour` | true | 12h or 24h. Power on default, the switch owns it |
| `blink_colon` | true | Power on default, the switch owns it after that |

The light platform takes an `aip33628_id` and is otherwise a normal RGB light.

## What it does

- Shows the time
- Colon blinks once a second, and the upper dot drops while the network is
  down, so a glance at the panel says whether the time is still being kept
  accurately.
- Four dashes while there is no valid time. The board has no RTC, so a cold
  boot with no network means no time at all until the network comes back.
- One RGB "light" entity for the whole display, plus per digit color, per LED
  color, gradients, a color cycle and a flash effect.
- Temporary number displays light the degree mark when their unit is C or F.
- A lamp test lights every populated LED white for three seconds, then restores
  the previous display settings.

## Color resolution

Each COM slot is split into four binary weighted sub-frames, which gives 16
duty cycle levels per channel and 4096 colors.

The AiP33628 has no grayscale engine, so every one of those levels costs a
sub-frame. More levels would mean more sub-frames per COM pair, a shorter tick
and a narrower window. Four is where the flicker margin still looks
comfortable at 416Hz.

## Current

`max_current` is a fixed ceiling on `IS[3:0]`, 0 to 15. The Home Assistant
brightness slider maps onto that range and nothing else touches it.

It deliberately does not depend on what is on screen. Counting lit sinks to
hold a milliamp budget gives you a panel that dims itself once a second as the
colon blinks, gets brighter when you pick a saturated color because fewer
sinks are lit, and stalls part way through a fade.

The stock firmware never did this either. It held `IS` at 0xD across colon on
and colon off, and ran 0xF with a white digit lit, drawing 404mA against a
250mA label.

If the drivers run hotter than you want, lower `max_current`. That trades
brightness for heat without making the brightness depend on the content.

## Brightness

Sixteen current steps, spread across the whole slider.

Gamma correction lives in the component, and `gamma_correct` on the light is
set to 1.0 so it is not applied twice.

It has to live there because a plain gamma curve assumes the output can reach
zero, and this panel bottoms out at `IS` 0, which is 2.5mA. With a stock curve
the bottom 29 percent of the slider sits clamped against that floor doing
nothing. The component interpolates perceived output between the floor and
full instead, which uses all sixteen steps and leaves only the natural width
of one step at the bottom.

Color components go through the same exponent before they are rounded onto
duty cycle levels, because duty is linear light and Home Assistant sends gamma
encoded values. Skipping that step rounds every pastel up toward a saturated
color, most visibly by turning pink into white.

Both arrive linear from the light platform. The magnitude comes from
`current_values_as_brightness()` rather than `current_values.get_brightness()`,
because only the helper carries the transition state, without which a fade
never moves.

## Scan timing

The scan runs from a `gptimer` interrupt, not from `esp_timer`.

The esp_timer task dispatch path runs at task priority on core 0 next to the
WiFi task, which preempts it and stretches whichever COM slot is lit at the
time. A full duty slot rides that out. A 40us sub-frame does not, and the same
jitter reads as uneven digits and a visible pulse on any color that is not
saturated.
