# CLAUDE.md — Mycodo (hydroponics fork)

Fork of `kizniche/Mycodo` (`origin` = `Abiel5/Mycodo`, `upstream` = `kizniche/Mycodo`).
Work happens on the **`mycodo-mqtt-doser`** branch; `master` is a mirror of upstream and
must never take commits.

## Role in the system

Mycodo is currently the **data-collection and logging side**. Day-to-day nutrient dosing
is done from the `grow-ui` web app; event-driven dosing from Mycodo (starting with pH
control) is planned. What Mycodo does today:

- polls the sensor ESP32 and stores readings (Mycodo Influx graphs + `grow_history.db`)
- listens to the pump ESP32 and records pump events
- provides a pump output (manual controls in the Mycodo UI, and the publish path for the
  pH function)

## Shared interfaces

- **MQTT** (JSON, every message has `"v": 1`): `esp32/pump/{cmd,resp,status}` and
  `esp32/sensors/{cmd,resp,status}`. Commands carry a `request_id` that the firmware echoes.
- **Names:** pumps are `calmag`, `micro`, `gro`, `bloom`, `phup`, `phdown`; sensors are
  `ph`, `ec`, `water_temp`, `air`. These are defined by the firmware and used as-is here.
- **Database:** `/opt/Mycodo/mycodo/databases/grow_history.db` (`dosing_events`,
  `sensor_readings`), also written by grow-ui. The schema is defined in both codebases, so
  a new column must be added in both, plus a manual `ALTER TABLE` on the Pi.
- **Never run a batch from Mycodo and grow-ui at the same time.** The pump ESP32 accepts
  one batch at a time.

## Custom modules (the whole diff against upstream)

Everything else in the repo is upstream's. Do not edit core Mycodo files.

| File | Type | Status | What it does |
|---|---|---|---|
| `mycodo/inputs/custom_inputs/sensor_mqtt_reader.py` | Input | **active** | Publishes `read_all` to `esp32/sensors/cmd` each poll, reads `esp32/sensors/status`, writes Influx and `grow_history.sensor_readings`. This is the **only writer of `sensor_readings`** — grow-ui only reads it. |
| `mycodo/inputs/custom_inputs/pump_mqtt_event_logger.py` | Input | **active** | Subscribes to `esp32/pump/resp` and `esp32/pump/status`, records pump events (Influx + legacy `pump_history`). Passive. |
| `mycodo/outputs/custom_outputs/pump_atlas_ezo_pmp_mqtt.py` | Output | active | Owns the MQTT broker connection. Manual commands from the Mycodo UI (dispense, stop, info, status, calibration, batch). Publisher only — not a pump driver. |
| `mycodo/functions/custom_functions/ph_control.py` | Function | **not running yet** | Step-dose pH loop with cooldown: reads pH from Influx, publishes pH Up/Down through the pump output, logs to `grow_history`. Planned for later. |
| `mycodo/functions/custom_functions/nutrient_dosing_gh_flora.py` | Function | **unused** | Computes GH Flora doses and publishes one `pump_batch`. Superseded by grow-ui; kept for reference. |
| `mycodo/utils/grow_history.py` | Util | active | Shared SQLite logger (`log_sensor_reading`, `log_dosing_event`). Schema is duplicated in `grow-ui/history.py` — keep the two in step. |
| `mycodo/utils/pump_history.py` | Util | legacy | Older JSONL + SQLite pump history, still imported by the event logger and the nutrient function. New code should use `grow_history`. |

## Rules

- **Recipe drift:** `nutrient_dosing_gh_flora.py` has its own `STAGE_RECIPES`. grow-ui's
  `config.py` is the source of truth. If the Mycodo function is ever re-enabled, the two
  must be reconciled first.
- **Hardware-agnostic:** speak only in logical `pump_id` / `sensor_id`. No GPIO, I2C
  addresses or PROFET channels here.
- **Don't invent MQTT fields.** Shape changes must land in the firmware, grow-ui and here
  together (see the workspace playbook).
- **Plugin style:** keep the `INPUT_INFORMATION` / `OUTPUT_INFORMATION` /
  `FUNCTION_INFORMATION` dict pattern; new features go through it.
- **pH is a feedback loop**, not batch math. Keep it in its own function.
- **No secrets** in files. Broker credentials are set in the Mycodo UI.

## Deploy

Windows is the editing machine; the Pi is the runtime.

1. Edit and commit here (branch `mycodo-mqtt-doser`).
2. Copy the changed file onto the Pi:
   - functions → `/opt/Mycodo/mycodo/functions/custom_functions/`
   - inputs → `/opt/Mycodo/mycodo/inputs/custom_inputs/`
   - outputs → `/opt/Mycodo/mycodo/outputs/custom_outputs/`
   - utils → `/opt/Mycodo/mycodo/utils/`
3. Restart Mycodo, then confirm the module appears and loads in the UI.

`/opt/Mycodo` on the Pi is **not a git checkout** (no commits), so the Pi can drift from
this repo. The Pi keeps `.bak` copies of the two functions.

Runtime facts on the Pi: the daemon is `mycodo.service` (the grow-ui unit is separate);
`grow_history.db` is in `/opt/Mycodo/mycodo/databases/`; the legacy pump history is in
`/opt/Mycodo/databases/pump_history/`. The sensor reader logs
`Sensor read timed out after 8s` to the daemon journal when the sensor ESP32 doesn't
answer a `read_all` in time — occasional timeouts are expected, a steady stream is not.

Watch traffic while testing:

```bash
mosquitto_sub -h <broker-host> -u <user> -P <pass> -t 'esp32/#' -v
```

## Syncing with upstream

```bash
git fetch upstream
git checkout master && git merge --ff-only upstream/master && git push origin master
git checkout mycodo-mqtt-doser && git rebase master   # then: git push --force-with-lease
```

The diff is additions only, so rebases are normally conflict-free.
