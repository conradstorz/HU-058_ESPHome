# AGENTS.md

## Project summary

This repo reverse-engineers the HU-058D / HU-058 clock panel and provides firmware to drive it from an ESP32.

- Main project overview: [README.md](README.md)
- ESPHome firmware: [firmware/esphome/README.md](firmware/esphome/README.md)
- Bare-metal panel test firmware: [firmware/esp32/panel-test/README.md](firmware/esp32/panel-test/README.md)
- Hardware and protocol docs: [docs/](docs/)

## Where to work

- ESPHome config and custom component live under [firmware/esphome/](firmware/esphome/)
- PlatformIO test harness lives under [firmware/esp32/panel-test/](firmware/esp32/panel-test/)
- Reverse-engineering docs and board-level definitions live under [docs/](docs/)
- Datasheet/reference material lives under [reference/](reference/)

## Working conventions

- Treat the docs as the source of truth for protocol, wiring, and board mapping.
- Before changing timing, GPIO mapping, or display logic, confirm the behavior in the relevant doc: [docs/aip33628-protocol.md](docs/aip33628-protocol.md), [docs/display-map.md](docs/display-map.md), and [docs/wiring.md](docs/wiring.md).
- Keep hardware assumptions aligned with the supported ESP32 variants described in [firmware/esphome/README.md](firmware/esphome/README.md).
- This project is intentionally hardware-focused; verify both software and wiring changes against the physical panel behavior.
- Keep secrets out of version control. Use `secrets.yaml` in the ESPHome firmware directory and avoid committing credentials or personal data. `flash.py` appends per-device `api_key_<name>` / `ota_password_<name>` entries there; never regenerate them for a clock that is already paired with Home Assistant.
- Shared ESPHome config lives in `clock-base.yaml`. Each clock has a small `<name>.yaml` device file that pulls it in as a package, and `devices.yaml` maps ESP32 MAC addresses to those names. The first clock, `wifi-clock`, is already in Home Assistant and must keep its name and secrets.

## Build and validation commands

From [firmware/esphome/](firmware/esphome/):

```bash
uv sync
cp secrets.yaml.example secrets.yaml
uv run pytest            # unit tests for flash.py
uv run flash.py          # register (if new) and flash the clock on USB
uv run esphome config wifi-clock.yaml   # validate one device file
```

Use `uv` only; do not create or activate a venv or `pip install`. On Windows run from PowerShell or cmd (Git Bash has produced empty builds) and use `copy` instead of `cp`. `flash.py` refuses anything that is not an ESP32 before writing any files.

From [firmware/esp32/panel-test/](firmware/esp32/panel-test/):

```bash
pio run -t upload
pio device monitor
```

## Safety notes

- The panel is driven from 3.3V logic on the ESP32 without a level shifter in normal setups, but wiring and latch timing are sensitive.
- Do not assume the stock firmware or any other platform is compatible without reviewing the reverse-engineering docs.
- The project warns against running the display at very high current for extended periods; keep brightness and current limits sensible.

## Preferred agent behavior

- Prefer targeted, minimal edits.
- Link to existing docs rather than duplicating protocol details in code comments when the documentation already covers the fact.
- When adding firmware features or board support, explain how they map to the documented hardware and timing constraints.
- If a task touches the display protocol or wiring, include a brief note citing the relevant docs and the likely impact on hardware behavior.
