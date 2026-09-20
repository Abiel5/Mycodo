# CLAUDE.md

## Purpose

This repository contains the Mycodo-side customizations for the hydroponic dosing system running on the Raspberry Pi. It is the orchestration, UI-integration, and nutrient-logic side of the project.

At a high level, this repo is responsible for:

- exposing custom Mycodo outputs and functions
- publishing MQTT commands to the ESP32 pump controller
- converting user intent into pump actions
- computing nutrient amounts from gallons added and growth stage
- building sequential `pump_batch` requests
- integrating the workflow into the Mycodo UI and daemon model
- recording intended dosing history on the Mycodo side

This repo should be treated as the orchestration, operator, and nutrient-logic layer of the system.

## How This Repo Relates to the Other Repo

The companion repo is the ESP32 firmware repo. That repo is the hardware-facing execution side.

Responsibility split:

- Mycodo repo:
  - UI and operator interaction
  - nutrient recipe calculation
  - batch planning
  - command publishing
  - Mycodo plugin integration
- ESP32 repo:
  - hardware pump lookup
  - PROFET switching
  - I2C communication with pumps
  - motor scheduling
  - event and status publishing

Shared boundary between repos:

- MQTT topics
- JSON message schema
- `pump_id` naming
- batch semantics
- event/response expectations

This repo should assume the ESP32 is a smart execution controller, not a raw passthrough device. The documented shared topics are `esp32/pump/cmd`, `esp32/pump/resp`, and `esp32/pump/status`. fileciteturn5file3 fileciteturn5file5

## Tech Stack

- Python
- Mycodo custom output and custom function plugin architecture
- Flask-backed Mycodo environment
- `paho-mqtt`
- Mycodo database model access and daemon utilities

The current Mycodo-side source imports components such as:

- `AbstractOutput`
- `AbstractFunction`
- `flask_babel.lazy_gettext`
- `paho-mqtt`
- Mycodo database helpers
- Mycodo daemon control helpers fileciteturn5file18 fileciteturn5file19

Operational environment:

- Raspberry Pi running Mycodo
- local/LAN MQTT broker
- ESP32 controller on the network

## Project-Specific Architecture

### High-level flow

User / Mycodo UI → custom output or function → MQTT publish → ESP32 firmware
                                                            ↓
                                                pump execution + events

### Current repo responsibilities

This repo currently contains two main project pieces:

1. a custom output module for Atlas EZO-PMP control over MQTT
2. a custom function that calculates GH Flora nutrient doses and emits one sequential batch message

That separation is important:

- the output module owns MQTT broker connectivity and user-facing manual controls
- the function module owns nutrient math and batch construction

### Output architecture

The output module is designed for indirect control only:

Mycodo → MQTT broker → ESP32 bridge → EZO-PMP over I2C

It does not connect the Raspberry Pi directly to Atlas hardware. The current source explicitly describes that model. fileciteturn5file18

The output supports multiple payload styles:

- raw ASCII
- minimal JSON
- batch JSON

and exposes Mycodo UI actions for:

- raw command send
- dispense by volume
- stop
- info
- status
- calibration query
- batch actions
- rename pump at runtime fileciteturn5file18

### Function architecture

The nutrient dosing function computes a sequence for:

1. Cal‑Mag
2. FloraMicro
3. FloraGro
4. FloraBloom

based on:

- gallons added
- selected growth stage
- strength factor / override
- configured mixing delays
- configured pump IDs

It then publishes a single `pump_batch` request to the ESP32 and appends an intent record through Mycodo utilities. fileciteturn5file19

### GH Flora basis

The function’s stage recipes are based on a light-feed interpretation of the GH Flora program, and the current source encodes stage recipe values directly in `ml/gal`. The uploaded GH chart and labels support the general nutrient-stage model and the rule that FloraMicro should be added to fresh water before FloraGro and FloraBloom. fileciteturn5file19 fileciteturn5file12turn5file10turn5file9turn5file11

## Key Files

### `pump_atlas_ezo_pmp_mqtt.py`
Main Mycodo custom output module for Atlas EZO-PMP via MQTT.

Current responsibilities include:

- broker host/port/auth configuration
- MQTT topic configuration
- payload format selection
- manual command publishing
- Mycodo UI custom commands
- optional batch-style publishing
- pump label / pump ID / queue option configuration fileciteturn5file18

### `nutrient_dosing_gh_flora.py`
Main Mycodo custom function for GH Flora nutrient dosing.

Current responsibilities include:

- stage recipe definitions
- custom function options
- pump ID assignments
- strength factor handling
- gallons-added input
- mixing delay configuration
- `pump_batch` construction
- dose preview / dose send actions
- intent history append behavior fileciteturn5file19

### Supporting context/reference docs
Useful cross-repo design references include:

- `project-notes.md`
- `mqtt_pump_controller_context_v2.md`
- `esp32_motor_scheduler_context.md`
- `batch_commands.md`
- `Mycodo Nutrient Automation.txt` fileciteturn5file3 fileciteturn5file5 fileciteturn5file4 fileciteturn5file8

## Build / Run / Install Guidance

This repo is not a standalone service by itself; it plugs into an existing Mycodo installation.

### Development workflow used for this project

The working strategy for this project is:

- edit and iterate on a local Mycodo project/codebase on the development machine
- keep the custom output/function files under local version control there
- when changes are ready to test on hardware, copy the updated files into the Mycodo installation running on the Raspberry Pi
- then reload/restart Mycodo services on the Pi and validate behavior against the live ESP32 + pump setup

Claude should assume that the local project is the main editing workspace, while the Raspberry Pi is the runtime/test environment.


### Typical file locations
The nutrient function source itself documents that it should be placed under:

```bash
/opt/Mycodo/mycodo/functions/
```

The output module belongs in the Mycodo outputs area in the local Mycodo codebase. fileciteturn5file19

### Typical workflow
After editing files in the Mycodo codebase:

- place/update the custom output file
- place/update the custom function file
- reload or restart the relevant Mycodo services/processes
- verify the output/function appears in the Mycodo UI
- configure broker settings, topics, and pump IDs in the UI
- test with simple MQTT-driven actions before running nutrient jobs

### MQTT validation from the Pi
Watch ESP32 responses:

```bash
mosquitto_sub -h <broker-host> -t 'esp32/pump/resp' -v
```

Watch status:

```bash
mosquitto_sub -h <broker-host> -t 'esp32/pump/status' -v
```

### Manual send validation
Send a basic command to verify end-to-end connectivity:

```bash
mosquitto_pub -h <broker-host> -t 'esp32/pump/cmd' -m '{"v":1,"pump_id":"calmag","cmd":"i","wait_ms":300,"request_id":"test-1"}'
```

### Mycodo-side prerequisites
Before using the modules, make sure:

- Mycodo is installed and running on the Pi
- the MQTT broker is reachable
- the ESP32 firmware is online and subscribed
- the configured `pump_id` values match what the ESP32 firmware knows
- the output module’s broker/topic/auth settings are correct

The project notes document the current default broker and topics used by the system. fileciteturn5file3

## Current Message Contract Used By This Repo

### Topics
Current defaults used across the repo and project notes:

- `esp32/pump/cmd`
- `esp32/pump/resp`
- `esp32/pump/status` fileciteturn5file3 fileciteturn5file18

### Output payload modes
The output supports:

- `raw_ascii`
- `json_minimal`
- `json_batch` fileciteturn5file18

### Batch behavior assumptions
The function expects the ESP32 to support:

- `type = "pump_batch"`
- ordered command execution
- `stop_on_error`
- per-item wait values
- one active batch at a time
- item and batch events on the response topic fileciteturn5file8 fileciteturn5file19

### Important note about compatibility
This repo should not invent new payload fields casually. Any change to batch shape, event naming, or `pump_id` conventions can break the ESP32 repo unless both repos are updated together.

## Codebase-Specific Gotchas and Conventions

### 1. This repo should stay hardware-agnostic
Do not put GPIO numbers, I2C addresses, or PROFET board/channel mapping in this repo. Those belong in the ESP32 firmware repo. This repo should speak in terms of logical `pump_id` values only. That separation is a core design principle in the messaging context. fileciteturn5file5

### 2. The output module is not a direct pump driver
It is easy to accidentally think of the Mycodo output as the pump driver. It is not. It is an MQTT publisher and UI layer.

### 3. Keep `pump_id` names aligned with the ESP32 firmware
The current nutrient function defaults to named roles such as `calmag`, `micro`, `gro`, and `bloom` in practice. If the firmware mapping changes, this repo must be updated to match. fileciteturn5file19turn5file20

### 4. Mixing order matters
The GH labels explicitly instruct adding FloraMicro to fresh water first, then FloraGro and/or FloraBloom, and not mixing concentrated parts together undiluted. The current function’s sequence follows that principle and should keep doing so unless intentionally redesigned. fileciteturn5file10turn5file9turn5file11

### 5. Batch construction is a first-class pattern here
The nutrient function is intentionally designed to emit one `pump_batch` instead of trying to orchestrate individual step timing from Mycodo. That keeps execution sequencing on the ESP32 side, where the motor scheduler already exists. fileciteturn5file19turn5file8

### 6. This repo owns nutrient math, not execution timing internals
This repo should decide amounts and desired step delays, but it should not try to reproduce the ESP32’s scheduler state machine in Python.

### 7. Preserve Mycodo plugin style
The modules follow Mycodo’s custom output/function metadata style with dictionaries like `OUTPUT_INFORMATION` and `FUNCTION_INFORMATION`. New features should fit that pattern rather than bypassing it. fileciteturn5file18turn5file19

### 8. Treat observed ESP32 events as integration truth
If the ESP32 runtime event payloads differ from older context docs, this repo should ultimately integrate against the actual firmware behavior and then update docs for consistency.

### 9. Do not place secrets in portable context files
Broker credentials or Wi‑Fi details may appear in project notes, but portable repo context files should describe where settings live, not hardcode live secrets. fileciteturn5file3

## Domain Context

This project automates hydroponic nutrient dosing for a system where fresh water is added and then nutrients are added in sequence.

The current GH Flora dosing function uses stage-based recipes for:

- seedling
- early veg
- late veg
- early bloom
- mid/late bloom

and combines:

- Cal‑Mag
- FloraMicro
- FloraGro
- FloraBloom

The function also supports a strength multiplier so the project can operate below full GH chart strength. The current source defaults that factor to `0.33`. fileciteturn5file19

## What Claude Should Keep In Mind In This Repo

- This is the orchestration/UI/nutrient-logic repo.
- Favor stability in the shared MQTT contract.
- Keep hardware details out of this codebase.
- Preserve `pump_id` compatibility with the ESP32 repo.
- Keep GH dosing order and dilution assumptions intact unless intentionally changing the nutrient model.
- When behavior changes, update the shared project docs so this repo and the ESP32 repo stay aligned.
