# Mycodo-Side Context for ESP32 Pump Event Consumption and Nutrient Logging

## Goal

Define what the Mycodo / Raspberry Pi side should do in the overall dosing system.

The ESP32 is the real-time execution controller.
The Mycodo / Pi side is the orchestration and persistence layer.

This document focuses on the **Mycodo side responsibilities** now that the ESP32 is publishing:

- controller status
- command responses
- pump lifecycle events
- batch lifecycle events
- batch item lifecycle events
- requested / reported / estimated dose fields where applicable

---

## Core Architecture

```text
User / Function / Mycodo UI
    ->
Mycodo Function
    ->
Custom MQTT Output
    ->
MQTT broker
    ->
ESP32
    ->
Pumps

ESP32
    ->
MQTT events
    ->
Mycodo / Pi subscriber / logger
    ->
logs + nutrient history database
```

---

## Mycodo-Side Responsibilities

The Mycodo / Pi side should own:

1. nutrient math
2. user-entered batch intent
3. batch creation
4. MQTT publishing through the custom output
5. receiving ESP32 events
6. logging historical nutrient jobs
7. logging batch/item completion state
8. reporting totals / failures / history

The Mycodo / Pi side should **not**:

- directly drive pump hardware
- own motor sequencing timing
- pretend to know actual physical reservoir delivery beyond what was requested or reported
- duplicate ESP32 scheduler logic

---

## Recommended Division of Responsibilities

### Mycodo Function
Responsible for:

- gallons added
- growth stage
- strength factor
- pump mapping
- nutrient calculations
- creating one `pump_batch` payload
- assigning a `batch_id`
- sending the payload through the custom output

### Mycodo Output
Responsible for:

- MQTT broker connection settings
- command topic
- publishing payloads to MQTT
- acting as transport only

### ESP32
Responsible for:

- resolving `pump_id`
- controlling hardware
- enforcing one-motor-at-a-time
- executing batch steps
- publishing status/events/results

### Mycodo/Pi Event Consumer
Responsible for:

- subscribing to ESP32 event topics
- correlating responses to `batch_id`
- storing historical records
- surfacing errors
- allowing later dashboards/reports

---

## What Mycodo Should Log

Mycodo should log **both**:

### 1. Planned / intended dose data
This comes from the Function at batch creation time.

Examples:
- `batch_id`
- timestamp created
- gallons added
- growth stage
- strength factor
- target nutrient amounts
- target delays
- selected pump mapping

### 2. Execution result data
This comes from ESP32 MQTT events.

Examples:
- batch accepted/rejected
- item running/completed/failed
- requested_ml
- reported_ml
- estimated_ml
- result_source
- abort reason
- completion timestamps

This gives you both:
- what Mycodo asked for
- what the ESP32 says happened

---

## Source of Truth Rules

Mycodo should treat these fields differently:

### `requested_ml`
This is the Function-side intent.
This should always be stored.

### `reported_ml`
This is pump-reported data coming from the ESP32 event stream.
This is more trustworthy than estimation, but still not a true external flow measurement.

### `estimated_ml`
This is fallback data inferred by the ESP32 when no explicit pump-reported final amount exists.

### `result_source`
Mycodo should preserve this exactly as published by the ESP32.

Suggested values:
- `requested_only`
- `pump_reported`
- `estimated`
- `unknown`

### Important
Mycodo should not overwrite or reinterpret these fields in a way that hides uncertainty.

If the ESP32 says:
- `requested_ml = 19.00`
- `estimated_ml = 19.00`
- `result_source = "estimated"`

then Mycodo should store exactly that, not pretend it knows true actual delivery.

---

## MQTT Topics Mycodo Should Care About

### Publish to:
```text
esp32/pump/cmd
```

### Subscribe to:
```text
esp32/pump/resp
esp32/pump/status
```

---

## What Mycodo Should Do With `esp32/pump/status`

This is controller health / presence.

Mycodo should use it to know:
- controller online
- controller ready
- heartbeat alive
- offline/disconnected

### Recommended behavior
- show last-seen timestamp
- show online/offline state
- log disconnect/reconnect events
- optionally alert if heartbeat disappears

This data is operational, not dosing history.

---

## What Mycodo Should Do With `esp32/pump/resp`

This is where useful execution records come from.

Mycodo should handle these event types:

- `pump_response`
- `pump_event`
- `batch_response`
- `batch_event`
- `batch_item_event`

---

## Recommended Logging Model

## A. Batch-level table / record

Store one parent record per nutrient job.

Suggested fields:

- `batch_id`
- `created_at`
- `started_at`
- `completed_at`
- `status` (`created`, `accepted`, `running`, `completed`, `aborted`, `failed`, `rejected`)
- `gallons_added`
- `growth_stage`
- `strength_factor`
- `stop_on_error`
- `function_name`
- `output_id`
- `controller_id` (optional)
- `batch_payload_json`
- `error_reason` (nullable)

This record is primarily created on the Mycodo side when the Function sends the batch.

---

## B. Batch-item-level table / record

Store one child record per nutrient step.

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

These records are updated as ESP32 events arrive.

---

## C. Controller-status table / record (optional)

If you want operational history, store:

- `controller_id`
- `state`
- `timestamp`
- `wifi_rssi`
- `free_heap`
- `active_batch`
- `motor_busy`

This is optional, but nice for diagnostics.

---

## Mycodo Function Responsibilities in More Detail

The nutrient Function should:

1. accept user inputs
   - gallons added
   - growth stage
   - optional strength override

2. calculate nutrient target volumes
   - Cal-Mag
   - FloraMicro
   - FloraGro
   - FloraBloom

3. build a single `pump_batch` payload

4. create a `batch_id`

5. log the intended job locally before publish

6. send the batch through the custom output

7. mark the batch as:
   - `created`
   - then wait for ESP32 event stream to move it to `accepted`, `completed`, etc.

### Important
The Function should not:
- wait and send each nutrient separately
- manage per-step timing
- directly publish MQTT itself
- consume status topics itself unless Mycodo architecture makes that convenient

---

## Mycodo Output Responsibilities in More Detail

The output should:

- own broker settings
- own topic settings
- publish the prebuilt batch payload
- not recalculate nutrient math
- not rewrite dosing values
- not own history persistence logic

The output is transport only.

---

## Recommended Mycodo Event Consumer / Subscriber

You will likely want one Pi-side subscriber/logger component that:

- subscribes to `esp32/pump/resp`
- subscribes to `esp32/pump/status`
- parses incoming JSON
- matches events to known `batch_id`
- updates stored history records

This may live:
- inside Mycodo if there is a clean integration point
- or as a small sidecar Python service on the Pi

### Current repo implementation
This repository now takes the integrated-Mycodo path first:

- `nutrient_dosing_gh_flora.py` records the planned batch intent before publish
- `pump_mqtt_event_logger.py` subscribes to `resp` and `status` using the selected MQTT Output settings
- both write through a shared `pump_history` utility
- the utility keeps an append-only JSONL audit log and a separate SQLite history database

This is a staging persistence layer, not the final schema.
It gives you one raw audit stream plus one queryable local database so payload shape, event correlation, and field naming can settle before deeper UI/reporting work.

### If integrated with Mycodo
Best outcome, because:
- unified logs
- easier UI integration
- one system of record

### If sidecar service
Still fine, especially if easier to build first.

---

## Event Handling Rules for Mycodo

### `batch_response`
Use this to immediately mark:
- accepted
- rejected

If rejected:
- update batch status
- store rejection reason
- stop waiting for completion

### `batch_event`
Use this to mark:
- accepted
- completed
- aborted

This is the high-level lifecycle signal.

### `batch_item_event`
Use this to update each nutrient step:

- running
- completed
- failed
- post_delay

This is where nutrient-specific result data should be stored.

### `pump_event`
Use this mainly for operational visibility unless tied to a tracked batch/item.

### `pump_response`
Useful for debugging, manual commands, and non-batch operations.

---

## Mapping Nutrient Name to Pump ID

Mycodo should preserve logical nutrient meaning.

For example:

```python
pump_map = {
    "calmag": "calmag",
    "micro": "micro",
    "gro": "gro",
    "bloom": "bloom",
}
```

If you keep pump IDs equal to nutrient names on the ESP32 side, Mycodo history becomes easier to read.

Then the database can store:
- `nutrient_name = "calmag"`
- `pump_id = "calmag"`

instead of only:
- `pump_id = "pump_1"`

This is cleaner for reports and debugging.

---

## Recommended Status Labels in Mycodo

For batch records:
- `created`
- `accepted`
- `running`
- `completed`
- `aborted`
- `rejected`
- `failed`

For item records:
- `running`
- `completed`
- `failed`
- `post_delay`

This matches the ESP32 event model cleanly.

---

## Recommended UI / Reporting Features Later

Once Mycodo stores nutrient history, useful views would be:

- most recent nutrient job
- all jobs for last 7 days
- total Cal-Mag dosed this week
- total FloraMicro / Gro / Bloom dosed this week
- failed jobs
- failed steps
- last known controller heartbeat
- last rejection reason
- requested vs reported values per nutrient step

These are Phase 3/4 items, not required to get basic operation working.

---

## Recommended Implementation Phases on the Mycodo Side

### Phase 1
Function + Output cleanup:
- Function uses output
- Output owns MQTT publish
- ESP32 executes batch
- no good persistence yet beyond logs

### Phase 2
Basic event consumer:
- subscribe to `resp` and `status`
- write logs
- correlate `batch_id`
- store batch/item execution records

### Current bridge status
Implemented now in this repo:
- append-only JSONL audit history at `DATABASE_PATH/pump_history/pump_history.jsonl`
- separate SQLite history DB at `DATABASE_PATH/pump_history/pump_history.db`
- function-side `batch_intent` records before publish
- MQTT-side `mqtt_event` records for `resp` and `status`
- batch, batch-item, raw-event, and controller-status SQLite writes
- basic controller-health measurements from `status` payloads

Still next:
- refine event-to-row mapping as the ESP32 payload contract settles
- add read/query helpers for latest batches and batch detail views
- expose recent job/failure views in the UI

### Phase 3
Structured nutrient history:
- SQLite schema
- totals and reports
- view recent jobs and failures

### Phase 4
UI polish:
- live controller health
- nutrient totals dashboard
- historical charts
- alerts

---

## Strong Recommendation About Storage

Use **SQLite on the Pi** first.

### Practical note about the current code
The current implementation deliberately keeps JSONL as the raw audit trail while using a separate SQLite database for queryable history.
That separate DB should still be treated as the low-risk integration layer, not yet the final first-class Mycodo schema.

Why:
- simple
- local
- good enough for this project
- no separate DB server required
- easy to query and export later

Do not try to store long-term nutrient history on the ESP32.

---

## Final Recommendation

The Mycodo / Pi side should be the place that answers:

- what nutrient batch was requested?
- when was it sent?
- what were the intended nutrient amounts?
- was the batch accepted?
- which steps completed?
- what did the ESP32 report?
- what was only estimated?
- which job failed?
- how much nutrient has been dosed over time?

That is the correct role for Mycodo in this system.

The ESP32 should remain:
- executor
- scheduler
- event publisher

The Pi/Mycodo side should remain:
- orchestrator
- historian
- reporter
