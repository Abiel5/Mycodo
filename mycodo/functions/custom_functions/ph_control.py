# coding=utf-8
#
# ph_control.py - Mycodo Function: pH Control (Step-Dose with Cooldown)
#
# Reads pH from a sensor input on each loop cycle, doses pH Up or pH Down
# if outside the target range, then enforces a cooldown before dosing again.
#
# Drop this file in: /opt/Mycodo/mycodo/functions/
# Then add it in Mycodo UI: Setup → Function → Add Function
#
# Set the function Period in the Mycodo UI to your desired check interval
# (e.g. 60 seconds). The cooldown option is the guard against overdosing.
#
import json
import time
from uuid import uuid4

from mycodo.databases.models import CustomController
from mycodo.functions.base_function import AbstractFunction
from mycodo.mycodo_client import DaemonControl
from mycodo.utils.database import db_retrieve_table_daemon
from mycodo.utils.influx import read_influxdb_single

FUNCTION_INFORMATION = {
    'function_name_unique': 'ph_control_step_dose',
    'function_name': 'pH Control: Step-Dose',

    'message': (
        'Automatic pH control via step-dosing. '
        'On each loop cycle the function reads pH from the selected sensor input '
        'and doses pH Up or pH Down if outside the setpoint ± deadband window. '
        'A cooldown timer prevents re-dosing before the solution has time to mix. '
        'Set the function Period (in the Mycodo UI) to your desired check interval '
        '(e.g. 60 s). Set Cooldown to the minimum soak time between doses (e.g. 600 s).'
    ),

    'custom_options': [
        # ── pH Sensor ─────────────────────────────────────────────────────────
        {
            'type': 'message',
            'default_value': '-- pH Sensor --'
        },
        {
            'id': 'measurement_ph',
            'type': 'select_measurement',
            'default_value': '',
            'required': True,
            'name': 'pH Measurement',
            'phrase': 'Select the pH sensor input measurement'
        },
        {
            'id': 'measurement_max_age',
            'type': 'integer',
            'default_value': 300,
            'required': True,
            'name': 'Measurement Max Age (sec)',
            'phrase': 'Reject pH readings older than this many seconds'
        },
        # ── MQTT Output ───────────────────────────────────────────────────────
        {
            'type': 'message',
            'default_value': (
                '-- MQTT Output --\n'
                'Select the Atlas EZO-PMP MQTT Output that owns the broker connection.'
            )
        },
        {
            'id': 'mqtt_output',
            'type': 'select_device',
            'default_value': '',
            'required': True,
            'options_select': [
                'Output'
            ],
            'name': 'MQTT Output',
            'phrase': 'The Atlas EZO-PMP MQTT Output instance to publish through'
        },
        # ── pH Target ─────────────────────────────────────────────────────────
        {
            'type': 'message',
            'default_value': '-- pH Target --'
        },
        {
            'id': 'ph_setpoint',
            'type': 'float',
            'default_value': 6.0,
            'required': True,
            'name': 'pH Setpoint',
            'phrase': 'Target pH value'
        },
        {
            'id': 'ph_deadband',
            'type': 'float',
            'default_value': 0.2,
            'required': True,
            'name': 'Deadband (±)',
            'phrase': (
                'Acceptable pH range around setpoint. '
                'E.g. setpoint=6.0, deadband=0.2 → acceptable range is 5.8–6.2'
            )
        },
        # ── Pump Assignments ──────────────────────────────────────────────────
        {
            'type': 'message',
            'default_value': '-- Pump Assignments (must match ESP32 pump IDs) --'
        },
        {
            'id': 'pump_id_phup',
            'type': 'text',
            'default_value': 'phup',
            'required': True,
            'name': 'pH Up Pump ID',
            'phrase': 'ESP32 pump ID for pH Up solution'
        },
        {
            'id': 'pump_id_phdown',
            'type': 'text',
            'default_value': 'phdown',
            'required': True,
            'name': 'pH Down Pump ID',
            'phrase': 'ESP32 pump ID for pH Down solution'
        },
        # ── Dose Sizes ────────────────────────────────────────────────────────
        {
            'type': 'message',
            'default_value': (
                '-- Dose Sizes --\n'
                'Use small, conservative doses. The system will re-dose on the next '
                'eligible cycle if pH is still out of range after the cooldown elapses.'
            )
        },
        {
            'id': 'dose_ml_phup',
            'type': 'float',
            'default_value': 1.0,
            'required': True,
            'name': 'pH Up Dose (ml)',
            'phrase': 'Volume to dose per correction cycle when pH is too low'
        },
        {
            'id': 'dose_ml_phdown',
            'type': 'float',
            'default_value': 1.0,
            'required': True,
            'name': 'pH Down Dose (ml)',
            'phrase': 'Volume to dose per correction cycle when pH is too high'
        },
        # ── Cooldown ──────────────────────────────────────────────────────────
        {
            'type': 'message',
            'default_value': (
                '-- Cooldown --\n'
                'Minimum time between any two doses. Gives the solution time to mix '
                'and the sensor time to stabilize before the next correction.'
            )
        },
        {
            'id': 'cooldown_sec',
            'type': 'integer',
            'default_value': 600,
            'required': True,
            'name': 'Cooldown Between Doses (sec)',
            'phrase': 'Minimum seconds between any two doses (e.g. 600 = 10 min)'
        },
        # ── Batch Options ─────────────────────────────────────────────────────
        {
            'type': 'message',
            'default_value': '-- Batch Options --'
        },
        {
            'id': 'batch_id_prefix',
            'type': 'text',
            'default_value': 'ph',
            'required': False,
            'name': 'Batch ID Prefix',
            'phrase': 'Prefix for generated batch IDs (e.g. "ph" → "ph-a1b2c3d4")'
        },
    ],
}


class CustomModule(AbstractFunction):
    """
    pH control function using step-dosing with a cooldown guard.

    On each loop cycle: reads pH, doses pH Up or pH Down if outside the
    setpoint ± deadband window, then waits cooldown_sec before allowing
    another dose. Publishes a single-item batch to the ESP32 via
    the configured MQTT Output.
    """

    def __init__(self, function, testing=False):
        super().__init__(function, testing=testing, name=__name__)

        self.control = DaemonControl()

        # Tracks the last dose time so cooldown is enforced across loop cycles.
        # Reset to 0 on daemon restart (safe — worst case is one early dose).
        self._last_dose_time = 0.0

        # Pre-declare custom_options attributes (populated by setup_custom_options)
        self.measurement_ph = None
        self.measurement_max_age = None
        self.mqtt_output_id = None
        self.ph_setpoint = None
        self.ph_deadband = None
        self.pump_id_phup = None
        self.pump_id_phdown = None
        self.dose_ml_phup = None
        self.dose_ml_phdown = None
        self.cooldown_sec = None
        self.batch_id_prefix = None

        custom_function = db_retrieve_table_daemon(
            CustomController, unique_id=self.unique_id)
        self.setup_custom_options(
            FUNCTION_INFORMATION['custom_options'], custom_function)

        if not testing:
            self.try_initialize()

    def initialize(self):
        self.logger.info(
            "pH control initialized: setpoint={}, deadband=±{}, "
            "phup_id={} ({:.1f}ml), phdown_id={} ({:.1f}ml), cooldown={}s".format(
                self.ph_setpoint, self.ph_deadband,
                self.pump_id_phup, float(self.dose_ml_phup or 0),
                self.pump_id_phdown, float(self.dose_ml_phdown or 0),
                self.cooldown_sec,
            )
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Loop (called by Mycodo daemon every Period seconds)
    # ──────────────────────────────────────────────────────────────────────────

    def loop(self):
        ph = self._read_ph()
        if ph is None:
            self.logger.warning("No valid pH reading available, skipping cycle")
            return

        setpoint = float(self.ph_setpoint)
        deadband = float(self.ph_deadband)
        low  = setpoint - deadband
        high = setpoint + deadband

        if low <= ph <= high:
            self.logger.debug(
                "pH {:.2f} within range [{:.2f}-{:.2f}], no action".format(ph, low, high)
            )
            return

        now     = time.time()
        elapsed = now - self._last_dose_time
        remaining = float(self.cooldown_sec) - elapsed

        if remaining > 0:
            self.logger.debug(
                "pH {:.2f} out of range [{:.2f}-{:.2f}] but cooldown active "
                "({:.0f}s remaining), skipping".format(ph, low, high, remaining)
            )
            return

        if ph < low:
            pump_id   = str(self.pump_id_phup)
            dose_ml   = float(self.dose_ml_phup)
            direction = 'up'
        else:
            pump_id   = str(self.pump_id_phdown)
            dose_ml   = float(self.dose_ml_phdown)
            direction = 'down'

        self.logger.info(
            "pH {:.2f} out of range [{:.2f}-{:.2f}], dosing {} {:.2f} ml via pump '{}'".format(
                ph, low, high, direction, dose_ml, pump_id
            )
        )

        try:
            payload = self._build_payload(pump_id, dose_ml)
            self._publish_via_output(payload)
            self._last_dose_time = now
            self.logger.info(
                "pH dose sent: direction={}, pump={}, volume={:.2f} ml, pH_before={:.2f}".format(
                    direction, pump_id, dose_ml, ph
                )
            )
            try:
                from mycodo.utils.grow_history import log_dosing_event
                log_dosing_event(
                    trigger='ph_auto',
                    pump_id=pump_id,
                    volume_ml=dose_ml,
                    ph_before=ph,
                )
            except Exception as _he:
                self.logger.warning("grow_history dosing log failed: {}".format(_he))
        except Exception as err:
            self.logger.error(
                "pH dose failed: direction={}, pump={}, volume={:.2f} ml, error={}".format(
                    direction, pump_id, dose_ml, err
                )
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _read_ph(self):
        """Return the latest pH float, or None if unavailable."""
        if not self.measurement_ph:
            self.logger.error("No pH measurement configured")
            return None

        parts = str(self.measurement_ph).split(',')
        if len(parts) != 3:
            self.logger.error(
                "Unexpected measurement selection format '{}' "
                "(expected device_id,unit,channel)".format(self.measurement_ph)
            )
            return None

        device_id = parts[0]
        unit      = parts[1]
        channel   = int(parts[2])
        max_age   = int(self.measurement_max_age)

        last = read_influxdb_single(
            device_id, unit, channel,
            duration_sec=max_age,
            value='LAST'
        )

        if last is None or last[1] is None:
            self.logger.warning(
                "No pH measurement found for device={} unit={} channel={} "
                "within the last {}s".format(device_id, unit, channel, max_age)
            )
            return None

        return float(last[1])

    def _selected_output_id(self):
        output_id = self.get_custom_option('mqtt_output', default_return=self.mqtt_output_id)
        if output_id in [None, '', 'None']:
            return None
        return str(output_id)

    def _build_payload(self, pump_id, dose_ml):
        prefix = self.batch_id_prefix or 'ph'
        payload_dict = {
            'v': 1,
            'type': 'batch',
            'request_id': '{}-{}'.format(prefix, uuid4().hex[:8]),
            'options': {
                'stop_on_error': True,
            },
            'commands': [
                {
                    'item_id': 'step-1',
                    'pump_id': pump_id,
                    'cmd': 'D,{:.2f}'.format(dose_ml),
                    'post_delay_ms': 0,
                }
            ]
        }
        self.logger.debug(
            "pH batch payload: request_id={}, pump={}, dose={:.2f} ml".format(
                payload_dict['request_id'], pump_id, dose_ml
            )
        )
        return json.dumps(payload_dict)

    def _publish_via_output(self, payload_json):
        """Publish a pre-built JSON payload through the selected Output controller."""
        output_id = self._selected_output_id()
        if not output_id:
            raise ValueError(
                "No MQTT Output selected. Configure the 'MQTT Output' option in the Function settings."
            )

        result = self.control.module_function(
            'Output',
            output_id,
            'send_batch_command',
            {'payload_json': payload_json},
            thread=False,
            return_from_function=True,
            timeout=60
        )

        if isinstance(result, tuple):
            if len(result) >= 2 and result[0]:
                raise ValueError(result[1])
            if len(result) >= 2:
                return result[1]

        if isinstance(result, dict):
            if 'message' in result:
                return result['message']
            return str(result)

        if result in [None, '']:
            return "Output send_batch_command completed without a return message"

        result = str(result)
        if result.lower().startswith('error'):
            raise ValueError(result)
        return result
