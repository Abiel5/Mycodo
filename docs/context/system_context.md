# system_context.md
## Shared System Context for the Mycodo MQTT Doser Project

## Purpose

This file is the shared source of truth for the Mycodo MQTT doser project.

It should give another engineer enough context to understand:

- what the system does
- how the parts fit together
- what each layer is responsible for
- how commands and events flow
- how nutrient dosing is supposed to work
- what is already decided
- what is still open

If code and this file disagree, either:
- update this file, or
- document why the code intentionally differs

---

## Project Overview

This project is an automated hydroponic nutrient dosing system.

### High-level goal

Allow Mycodo to send nutrient dosing jobs to an ESP32-based pump controller so that:

- the user specifies fresh water added
- the system computes nutrient amounts
- nutrients are dosed in a safe order
- only one pump motor runs at a time
- the ESP32 executes the batch
- Mycodo/Pi keeps the history and logs

This is designed for RDWC / hydroponic reservoir top-offs using:

- Cal-Mag
- GH FloraMicro
- GH FloraGro
- GH FloraBloom

pH adjustment is a separate workflow and is **not part of the nutrient batch function**.

---

## System Architecture

```text
User / Mycodo UI
    ->
Mycodo Function
    ->
Custom Mycodo MQTT Output
    ->
MQTT Broker (Mosquitto on Pi)
    ->
ESP32 Feather V2
    ->
Atlas EZO-PMP pumps over I2C
    +
PROFET high-side outputs for pump motor power

ESP32
    ->
MQTT events
    ->
Mycodo / Pi-side subscriber / logger
    ->
logs + nutrient history database
```

---

## Main Components

### Raspberry Pi
Runs:
- Mycodo
- Mosquitto MQTT broker
- future nutrient history / event persistence

### Mycodo Function
Responsible for nutrient math and batch creation.

### Mycodo Output
Responsible for MQTT transport.

### ESP32 Feather V2
Acts as:
- MQTT client
- command parser
- pump router
- motor scheduler
- batch executor
- event publisher

### Atlas EZO-PMP pumps
Actual dosing hardware using I2C ASCII commands.

### Infineon PROFET board(s)
Control 12V motor power to each pump.

---

## Responsibility Split

This is one of the most important design decisions.

### Mycodo Function owns:
- user inputs
- nutrient math
- stage selection
- strength factor
- pump mapping
- batch payload construction

### Mycodo Output owns:
- MQTT broker settings
- MQTT topics
- authentication
- publishing payloads

### ESP32 owns:
- resolving `pump_id`
- hardware mapping
- turning pump motor power on/off
- one-motor-at-a-time enforcement
- command execution
- batch sequencing
- inter-step delays
- publishing execution events

### Mycodo / Pi side logger owns:
- persistent nutrient history
- event correlation by `batch_id`
- dashboards / reports / totals
- failure history
- controller status history if desired

### Not owned by ESP32:
- long-term storage
- nutrient totals database
- high-level nutrient recipe logic

---

## Core Design Rules

1. Mycodo stays the orchestration layer.
2. The output is transport only.
3. The ESP32 is the execution controller.
4. The ESP32 publishes real-time facts.
5. The Pi/Mycodo side stores history.
6. Nutrient dosing is deterministic math.
7. pH adjustment is separate from nutrient batching.
8. Only one pump motor may run at a time.

---

## MQTT Contract

### Topics

Command topic:
```text
esp32/pump/cmd
```

Response/event topic:
```text
esp32/pump/resp
```

Controller status topic:
```text
esp32/pump/status
```

### General message flow

```text
Mycodo Function
  -> builds batch JSON

Mycodo Output
  -> publishes JSON to esp32/pump/cmd

ESP32
  -> parses command
  -> executes items
  -> publishes events to esp32/pump/resp
  -> publishes heartbeat/status to esp32/pump/status
```

---

## Pump Identity Model

Mycodo should think in **logical nutrients**.

The ESP32 should expose logical `pump_id`s that match nutrient meaning where possible.

### Recommended pump IDs

```python
pump_map = {
    "calmag": "calmag",
    "micro": "micro",
    "gro": "gro",
    "bloom": "bloom",
}
```

This is preferred over only using `pump_1`, `pump_2`, etc. because it makes:

- logs easier to read
- event history easier to read
- future debugging easier

### Important rule

Mycodo logic should use nutrient names.
The ESP32 should resolve `pump_id` to hardware details.

---

## Hardware Mapping Concept

The ESP32 internally maps:

```text
pump_id -> (i2c address + board index + channel index)
```

Example idea:

```json
{
  "calmag": { "i2c": "0x64", "board": 0, "channel": 1 },
  "micro":  { "i2c": "0x65", "board": 0, "channel": 2 },
  "gro":    { "i2c": "0x66", "board": 0, "channel": 3 },
  "bloom":  { "i2c": "0x68", "board": 0, "channel": 4 }
}
```

Mycodo should never need to know those hardware details.

---

## Nutrient Dosing Model

## Inputs

The nutrient dosing workflow should use:

- `gallons_added`
- `growth_stage`
- optional `strength_factor` override

### Optional saved defaults
- Cal-Mag ml/gal
- default strength factor
- mixing delays
- pump mapping
- batch behavior flags

---

## Growth Stages

Current planned stages:

- `seedling`
- `early_veg`
- `late_veg`
- `early_bloom`
- `mid_late_bloom`

These are simplified stage buckets, not full weekly chart automation.

---

## Recipe Basis

Current recipe model uses GH Flora Series **Light Feed** values as the base reference.

Example stage recipes in ml/gal:

```python
STAGE_RECIPES = {
    "seedling":       {"micro": 1.8, "gro": 1.8, "bloom": 1.8},
    "early_veg":      {"micro": 3.6, "gro": 3.4, "bloom": 2.6},
    "late_veg":       {"micro": 6.0, "gro": 5.6, "bloom": 4.2},
    "early_bloom":    {"micro": 5.3, "gro": 4.6, "bloom": 6.0},
    "mid_late_bloom": {"micro": 4.7, "gro": 4.7, "bloom": 6.6},
}
```

### Cal-Mag
Cal-Mag is treated separately and is not stage-based.

Formula:

```text
calmag_ml = gallons_added × calmag_ml_per_gal
```

---

## Strength Factor

A multiplier is applied to nutrient amounts.

Example:

```text
dose_ml = gallons_added × recipe_ml_per_gal × strength_factor
```

Typical default:
- `0.33` = about one-third strength

---

## Nutrient Order

Order matters.

### Required order
1. Cal-Mag
2. FloraMicro
3. FloraGro
4. FloraBloom

### Why
GH labels explicitly warn that FloraMicro should be added to fresh water first and never mixed undiluted with FloraGro or FloraBloom.

Since Cal-Mag is being treated as a separate first step for RO/top-off usage, the practical planned order is:

- Cal-Mag first
- then Micro
- then Gro
- then Bloom

---

## Mixing Delays

We want real mixing delays between nutrient steps.

### Planned defaults
- after Cal-Mag: 5–10 minutes
- after Micro: 2–3 minutes
- after Gro: 1–2 minutes
- after Bloom: 1–2 minutes trailing delay if desired

### Important
These delays should be owned by the **ESP32 batch executor**, not by Mycodo sending each step one at a time.

---

## Batch Command Model

The nutrient Function should build **one** batch payload.

Example shape:

```json
{
  "v": 1,
  "type": "pump_batch",
  "batch_id": "nutrient-abc123",
  "options": {
    "stop_on_error": true
  },
  "commands": [
    {
      "item_id": "step-1",
      "pump_id": "calmag",
      "cmd": "D,19.00",
      "post_delay_ms": 600000
    },
    {
      "item_id": "step-2",
      "pump_id": "micro",
      "cmd": "D,7.76",
      "post_delay_ms": 150000
    },
    {
      "item_id": "step-3",
      "pump_id": "gro",
      "cmd": "D,7.76",
      "post_delay_ms": 90000
    },
    {
      "item_id": "step-4",
      "pump_id": "bloom",
      "cmd": "D,10.89",
      "post_delay_ms": 90000
    }
  ]
}
```

---

## Batch Execution Rules

### Required behavior
- execute items strictly in order
- no parallel motor jobs
- if `stop_on_error = true`, abort remaining items on first failure
- after each motor item completes, optionally wait `post_delay_ms`
- then advance to the next item

### Why `post_delay_ms`
This is intended to represent **true post-completion mixing delay**.

It is different from:
- I2C response timing
- command acknowledgement delay

---

## Single Motor Constraint

Only one pump motor may run at a time.

Reason:
- limited PSU budget
- safety / predictability
- current design assumption

This must be enforced on the ESP32 side.

---

## Output / Function Relationship

### Correct planned architecture

The Function should **use the output**.

That means:

- Function computes nutrient amounts
- Function builds batch payload
- Function sends payload to the custom output
- Output publishes MQTT
- Function does not own broker/topic/auth config
- Function does not publish MQTT directly

### Important
The output should remain transport only.

It should not:
- recalculate nutrient amounts
- own the nutrient algorithm
- reinterpret the batch

---

## Event Publishing Model

The ESP32 should publish enough data for Mycodo/Pi to understand:

- controller online/offline/ready/alive
- batch accepted/rejected/completed/aborted
- item running/completed/failed/post_delay
- pump events for individual commands
- requested, reported, or estimated dose information where available

---

## Requested vs Reported vs Estimated Dose Data

This is a key concept.

The Atlas EZO-PMP is **not** a true external flow meter.
It can report useful internal status, but it does not independently verify actual physical reservoir delivery.

### Mycodo and ESP32 should distinguish between:

#### `requested_ml`
What Mycodo asked the pump to do.

#### `reported_ml`
What the pump explicitly reported back, if available.

#### `estimated_ml`
What the ESP32 inferred from the command/timing model when no explicit pump-reported final amount was available.

#### `result_source`
Where the best-known quantity came from.

Suggested values:
- `requested_only`
- `pump_reported`
- `estimated`
- `unknown`

### Important rule
Do not collapse these into one ambiguous “actual” field.

That would hide uncertainty.

---

## What the Pump Can and Cannot Tell Us

### Useful pump-side information
Potential useful commands / responses:
- `R`
- `D,?`
- `X -> *DONE,v`
- `TV,?`
- `ATV,?`
- calibration state / status codes

### Limitation
The pump does not prove:
- fluid actually reached the reservoir
- no leak happened
- no tubing issue happened
- no bottle-empty issue happened

So the system should publish **best-known truth**, not pretend it has an external verification sensor.

---

## Planned ESP32 Result Strategy

### Phase 1
Good enough:
- command accepted
- scheduler estimates completion
- publish `requested_ml`
- publish `estimated_ml = requested_ml`
- publish `result_source = "estimated"`

### Phase 2
Improved:
- after finite dose, query pump for status / last dispensed amount
- publish `reported_ml`
- publish `result_source = "pump_reported"`

### Phase 3
Optional:
- normalize more pump response patterns into structured numeric fields

---

## Mycodo / Pi Logging Model

The Pi side should be the long-term historian.

### It should store:

#### Planned / intended data
From the Function:
- `batch_id`
- created timestamp
- gallons added
- growth stage
- strength factor
- intended nutrient amounts
- payload JSON

#### Execution result data
From ESP32 events:
- batch accepted/rejected/completed/aborted
- item running/completed/failed/post_delay
- requested_ml
- reported_ml
- estimated_ml
- result_source
- raw_result
- error reasons
- timestamps

---

## Suggested Persistence Model

Start simple.

### Recommended first storage
- SQLite on the Pi

### Why
- simple
- local
- good enough
- easy to inspect/query/export
- no extra DB server needed

---

## Suggested Record Types

### Batch record
Suggested fields:
- `batch_id`
- `created_at`
- `started_at`
- `completed_at`
- `status`
- `gallons_added`
- `growth_stage`
- `strength_factor`
- `stop_on_error`
- `batch_payload_json`
- `error_reason`

### Batch item record
Suggested fields:
- `batch_id`
- `item_id`
- `index`
- `pump_id`
- `nutrient_name`
- `cmd`
- `state`
- `requested_ml`
- `reported_ml`
- `estimated_ml`
- `result_source`
- `raw_result`
- `status_code`
- `started_at`
- `completed_at`
- `failed_at`
- `post_delay_ms`
- `error_reason`

### Controller status record (optional)
Suggested fields:
- `controller_id`
- `state`
- `timestamp`
- `wifi_rssi`
- `free_heap`
- `active_batch`
- `motor_busy`

---

## pH Handling

pH should be a **separate workflow** from nutrient batching.

### Why
Nutrient dosing is deterministic.
pH adjustment is feedback/control-loop behavior.

### For now
- pH up/down should be separate manual or semi-manual functions
- use small increments
- wait for mixing
- later connect to a pH sensor for closed-loop correction

Do not merge pH dosing into the nutrient batch function.

---

## Known Assumptions

- Pi hosts Mycodo and the MQTT broker
- ESP32 is Wi-Fi connected and headless
- pump IDs can be logical names
- one motor at a time
- nutrient history belongs on the Pi side
- no true flow sensor yet
- pH is separate
- `post_delay_ms` is the correct model for inter-step mixing time

---

## Current State / Phase Notes

This project is evolving, but the intended progression is:

### Phase 1
ESP32 standalone control path works:
- Wi-Fi
- MQTT
- pump commands
- batch execution
- status/events

### Phase 2
Mycodo Function/Output cleanup:
- Function uses output
- output owns MQTT transport
- batch payload shape is stable

### Phase 3
Pi-side event logging / SQLite persistence

### Phase 4
Dashboard/reporting/history polish

---

## Open Decisions / To Revisit

These are the main things still worth refining over time:

1. exact SQLite schema
2. whether event consumer lives inside Mycodo or as a sidecar service
3. whether to query the pump after each finite dose for improved `reported_ml`
4. whether trailing Bloom delay is useful or unnecessary
5. how much controller health history to persist
6. whether future sensors (EC/pH) should be integrated into more advanced control logic

---

## Reference Files / Important Sources

Suggested key project files and docs to keep nearby:

- `main.cpp`
- `pump_atlas_ezo_pmp_mqtt.py`
- `nutrient_dosing_gh_flora.py`
- `project-notes.md`
- `batch_commands.md`
- EZO-PMP datasheet
- PROFET board user manual
- GH Flora feed chart / labels
- event publishing context
- Mycodo-side logging context

This section is not meant to replace the structured context above.
It just points to detailed source material.

---

## Practical Guidance for Future Edits

When changing the system, try to preserve these rules:

- do not move transport logic into the Function
- do not move scheduler logic into Mycodo
- do not let the ESP32 become the long-term database
- do not blur requested vs reported vs estimated dose information
- do not bundle pH control into nutrient batching
- do not break the one-motor-at-a-time rule unless the power design changes

---

## Summary

This project works best when treated as four clean layers:

### Mycodo Function
Thinks.

### Mycodo Output
Transports.

### ESP32
Executes.

### Pi-side logger / DB
Remembers.

That is the intended architecture and the basis for future work.
