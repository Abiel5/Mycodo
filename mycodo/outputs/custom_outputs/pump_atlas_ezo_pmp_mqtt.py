# coding=utf-8
#
# pump_atlas_ezo_pmp_mqtt.py - Output for Atlas Scientific EZO-PMP via MQTT
#
# Mycodo publishes raw ASCII command strings to an MQTT broker.
# An ESP32 bridge subscribes to the command topic and forwards commands
# to the Atlas EZO-PMP over I2C. No direct hardware connection is used
# from the Raspberry Pi.
#
import json
from uuid import uuid4

from flask_babel import lazy_gettext

from mycodo.databases.models import OutputChannel
from mycodo.outputs.base_output import AbstractOutput
from mycodo.utils.constraints_pass import constraints_pass_positive_or_zero_value
from mycodo.utils.constraints_pass import constraints_pass_positive_value
from mycodo.utils.database import db_retrieve_table_daemon
from mycodo.utils.utils import random_alphanumeric

# Measurements recorded to InfluxDB
measurements_dict = {
    0: {
        'measurement': 'volume',
        'unit': 'ml'
    },
    1: {
        'measurement': 'duration_time',
        'unit': 's'
    }
}

channels_dict = {
    0: {
        'types': ['volume', 'on_off'],
        'measurements': [0, 1]
    }
}

# Output information
OUTPUT_INFORMATION = {
    'output_name_unique': 'atlas_ezo_pmp_mqtt',
    'output_name': "{}: Atlas Scientific (MQTT)".format(lazy_gettext('Peristaltic Pump')),
    'output_manufacturer': 'Atlas Scientific',
    'output_library': 'paho-mqtt',
    'measurements_dict': measurements_dict,
    'channels_dict': channels_dict,
    'output_types': ['volume', 'on_off'],

    'url_manufacturer': 'https://atlas-scientific.com/peristaltic/',
    'url_datasheet': 'https://www.atlas-scientific.com/files/EZO_PMP_Datasheet.pdf',
    'url_product_purchase': 'https://atlas-scientific.com/peristaltic/ezo-pmp/',

    'message': (
        'Atlas Scientific EZO-PMP dosing pump controlled indirectly over MQTT. '
        'Mycodo publishes raw ASCII command strings to the configured MQTT command topic. '
        'An ESP32 (or similar) bridge subscribes to that topic and forwards commands '
        'to the EZO-PMP over I2C. No direct hardware connection is required from the Pi.'
    ),

    'options_enabled': [
        'button_send_volume',
        'button_send_duration'
    ],
    'options_disabled': ['interface'],

    'interfaces': ['IP'],

    'dependencies_module': [
        ('pip-pypi', 'paho', 'paho-mqtt==1.5.1')
    ],

    'custom_channel_options': [
        # ── Broker connection ──────────────────────────────────────────
        {
            'id': 'hostname',
            'type': 'text',
            'default_value': 'localhost',
            'required': True,
            'name': lazy_gettext('Hostname'),
            'phrase': 'Hostname or IP address of the MQTT broker'
        },
        {
            'id': 'port',
            'type': 'integer',
            'default_value': 1883,
            'required': True,
            'name': lazy_gettext('Port'),
            'phrase': 'Port of the MQTT broker'
        },
        {
            'id': 'keepalive',
            'type': 'integer',
            'default_value': 60,
            'required': True,
            'constraints_pass': constraints_pass_positive_or_zero_value,
            'name': lazy_gettext('Keep Alive'),
            'phrase': 'MQTT keepalive timeout in seconds. Set to 0 to disable.'
        },
        {
            'id': 'clientid',
            'type': 'text',
            'default_value': 'mycodo_ezo_pmp_{}'.format(random_alphanumeric(6)),
            'required': True,
            'name': 'Client ID',
            'phrase': 'Unique MQTT client ID for this output'
        },
        # ── Topics ────────────────────────────────────────────────────
        {
            'id': 'cmd_topic',
            'type': 'text',
            'default_value': 'esp32/pump/cmd',
            'required': True,
            'name': 'Command Topic',
            'phrase': 'MQTT topic to publish pump commands to'
        },
        {
            'id': 'payload_format',
            'type': 'select',
            'default_value': 'json_minimal',
            'options_select': [
                ('raw_ascii', 'Raw ASCII'),
                ('json_minimal', 'JSON (minimal schema)'),
                ('json_batch', 'JSON (batch schema)')
            ],
            'name': 'Payload Format',
            'phrase': 'Publish raw ASCII, single-pump JSON, or a single batch JSON payload covering all selected pumps'
        },
        {
            'id': 'pump_ids',
            'type': 'text',
            'default_value': 'pump_1',
            'required': False,
            'name': 'Pump IDs',
            'phrase': 'Comma-separated pump IDs (e.g. pump_1,pump_2). Used for JSON payload routing.'
        },
        {
            'id': 'wait_ms',
            'type': 'integer',
            'default_value': 300,
            'required': True,
            'constraints_pass': constraints_pass_positive_or_zero_value,
            'name': 'Wait (ms)',
            'phrase': 'Delay in milliseconds the ESP32 bridge should wait before reading a response'
        },
        {
            'id': 'stop_on_error',
            'type': 'bool',
            'default_value': True,
            'name': 'Stop Batch on Error',
            'phrase': 'Abort remaining batch items if any item fails (used with JSON batch schema)'
        },
        {
            'id': 'batch_id_prefix',
            'type': 'text',
            'default_value': 'batch',
            'required': False,
            'name': 'Batch ID Prefix',
            'phrase': 'Prefix used when generating batch_id values (e.g. "batch" → "batch-a1b2c3d4")'
        },
        {
            'id': 'resp_topic',
            'type': 'text',
            'default_value': 'esp32/pump/resp',
            'required': False,
            'name': 'Response Topic',
            'phrase': 'MQTT topic where the pump bridge publishes responses and execution events'
        },
        {
            'id': 'status_topic',
            'type': 'text',
            'default_value': 'esp32/pump/status',
            'required': False,
            'name': 'Status Topic',
            'phrase': 'MQTT topic where the pump bridge publishes status and heartbeat messages'
        },
        # ── Authentication ─────────────────────────────────────────────
        {
            'id': 'use_auth',
            'type': 'bool',
            'default_value': False,
            'name': 'Use Authentication',
            'phrase': 'Enable MQTT broker username/password authentication'
        },
        {
            'id': 'username',
            'type': 'text',
            'default_value': '',
            'required': False,
            'name': lazy_gettext('Username'),
            'phrase': 'Username for MQTT broker authentication'
        },
        {
            'id': 'password',
            'type': 'text',
            'default_value': '',
            'required': False,
            'name': lazy_gettext('Password'),
            'phrase': 'Password for MQTT broker authentication. Leave blank to disable.'
        },
        # ── Pump identity ──────────────────────────────────────────────
        {
            'id': 'pump_label',
            'type': 'text',
            'default_value': 'pump_1',
            'required': False,
            'name': 'Pump Label',
            'phrase': 'Optional label used in log messages to identify this pump'
        },
        # ── Scheduler behavior ───────────────────────────────────────
        {
            'id': 'queue_motor_if_busy',
            'type': 'bool',
            'default_value': True,
            'name': 'Queue Motor Commands if Busy',
            'phrase': 'When enabled, motor commands include queue_if_busy=true in JSON payloads by default'
        },
        # ── Flow control ───────────────────────────────────────────────
        {
            'id': 'flow_mode',
            'type': 'select',
            'default_value': 'fastest_flow_rate',
            'options_select': [
                ('fastest_flow_rate', 'Fastest Flow Rate'),
                ('specify_flow_rate', 'Specify Flow Rate')
            ],
            'name': 'Flow Rate Method',
            'phrase': 'Flow rate method used when dispensing a fixed volume'
        },
        {
            'id': 'flow_rate',
            'type': 'float',
            'default_value': 10.0,
            'constraints_pass': constraints_pass_positive_value,
            'name': 'Desired Flow Rate (ml/min)',
            'phrase': 'Target flow rate in ml/min when "Specify Flow Rate" is selected (EZO-PMP range: 0.5–105 ml/min)'
        },
        {
            'id': 'amps',
            'type': 'float',
            'default_value': 0.0,
            'required': True,
            'name': "{} ({})".format(lazy_gettext('Current'), lazy_gettext('Amps')),
            'phrase': 'The current draw of the device being controlled'
        }
    ],

    'custom_commands': [
        # ── Raw command ────────────────────────────────────────────────
        {
            'type': 'message',
            'default_value': (
                'Send a raw ASCII command string directly to the EZO-PMP via MQTT. '
                'Examples: D,5  |  D,*  |  X  |  i  |  Status  |  Cal,?  |  D,?'
            )
        },
        {
            'id': 'raw_command',
            'type': 'text',
            'default_value': 'i',
            'name': 'Raw Command',
            'phrase': 'Raw ASCII command string to publish to the pump command topic'
        },
        {
            'id': 'send_raw_command',
            'type': 'button',
            'wait_for_return': True,
            'name': 'Send Raw Command'
        },
        {
            'type': 'new_line'
        },
        # ── Volume dispense ────────────────────────────────────────────
        {
            'id': 'dispense_volume_ml',
            'type': 'float',
            'default_value': 5.0,
            'name': 'Volume to Dispense (ml)',
            'phrase': 'Volume in ml to instruct the pump to dispense'
        },
        {
            'id': 'dispense_volume',
            'type': 'button',
            'wait_for_return': True,
            'name': 'Dispense Volume'
        },
        {
            'type': 'new_line'
        },
        # ── Quick-action buttons ───────────────────────────────────────
        {
            'id': 'stop_pump',
            'type': 'button',
            'wait_for_return': True,
            'name': 'Stop Pump (X)'
        },
        {
            'id': 'pump_info',
            'type': 'button',
            'wait_for_return': True,
            'name': 'Pump Info (i)'
        },
        {
            'id': 'pump_status',
            'type': 'button',
            'wait_for_return': True,
            'name': 'Pump Status (Status)'
        },
        {
            'id': 'calibration_query',
            'type': 'button',
            'wait_for_return': True,
            'name': 'Calibration Query (Cal,?)'
        },
        {
            'type': 'new_line'
        },
        # ── Batch command ──────────────────────────────────────────────
        {
            'type': 'message',
            'default_value': (
                'Send a single batch command to all selected pump IDs at once. '
                'Requires "JSON (batch schema)" payload format to be selected above.'
            )
        },
        {
            'id': 'batch_action',
            'type': 'select',
            'default_value': 'dispense',
            'options_select': [
                ('dispense', 'Dispense Volume'),
                ('stop', 'Stop'),
                ('info', 'Info'),
                ('status', 'Status'),
                ('calibration_query', 'Calibration Query')
            ],
            'name': 'Batch Action',
            'phrase': 'Action to apply to all selected pumps in order'
        },
        {
            'id': 'batch_volume_ml',
            'type': 'float',
            'default_value': 5.0,
            'name': 'Batch Volume (ml)',
            'phrase': 'Volume in ml used when Batch Action is "Dispense Volume"'
        },
        {
            'id': 'send_batch_command',
            'type': 'button',
            'wait_for_return': True,
            'name': 'Send Batch Command'
        },
        {
            'type': 'new_line'
        },
        # ── Calibration ────────────────────────────────────────────────
        {
            'type': 'message',
            'default_value': (
                'Calibration: pump fluid through the line to remove air, then instruct '
                'the pump to dispense a known amount. Measure the actual volume dispensed, '
                'enter it below, and press Calibrate to Dispensed Amount.'
            )
        },
        {
            'id': 'calibrate_volume_ml',
            'type': 'float',
            'default_value': 10.0,
            'name': 'Actual Volume Dispensed (ml)',
            'phrase': 'The actual measured volume dispensed during calibration'
        },
        {
            'id': 'calibrate_pump',
            'type': 'button',
            'wait_for_return': True,
            'name': 'Calibrate to Dispensed Amount'
        },
        {
            'id': 'clear_calibration',
            'type': 'button',
            'wait_for_return': True,
            'name': 'Clear Calibration'
        },
        {
            'type': 'new_line'
        },
        # ── Pump rename ────────────────────────────────────────────────
        {
            'type': 'message',
            'default_value': (
                'Rename a pump ID on the ESP32 at runtime (resets on reboot). '
                'Example: rename "calmag" to "water" if you want to use that pump slot for plain water. '
                'from_id must match an existing pump ID on the ESP32 exactly.'
            )
        },
        {
            'id': 'rename_from_id',
            'type': 'text',
            'default_value': 'calmag',
            'name': 'Current Pump ID (from)',
            'phrase': 'The existing pump ID on the ESP32 to rename'
        },
        {
            'id': 'rename_to_id',
            'type': 'text',
            'default_value': 'water',
            'name': 'New Pump ID (to)',
            'phrase': 'The new pump ID to assign (max 23 characters, must not already be in use)'
        },
        {
            'id': 'rename_pump',
            'type': 'button',
            'wait_for_return': True,
            'name': 'Rename Pump'
        }
    ]
}


class OutputModule(AbstractOutput):
    """
    Atlas Scientific EZO-PMP output controlled over MQTT.

    Publishes raw ASCII command strings to a configurable MQTT broker topic.
    An ESP32 (or similar) bridge subscribes to that topic and forwards the
    commands to the EZO-PMP over I2C. No direct hardware connection is
    required from the Raspberry Pi running Mycodo.

    Architecture:
        Mycodo → MQTT broker → ESP32 bridge → Atlas EZO-PMP (I2C)
    """

    def __init__(self, output, testing=False):
        super().__init__(output, testing=testing, name=__name__)

        self.publish = None
        self.currently_dispensing = False

        output_channels = db_retrieve_table_daemon(
            OutputChannel).filter(OutputChannel.output_id == self.output.unique_id).all()
        self.options_channels = self.setup_custom_channel_options_json(
            OUTPUT_INFORMATION['custom_channel_options'], output_channels)

    def initialize(self):
        import paho.mqtt.publish as publish

        self.publish = publish
        self.setup_output_variables(OUTPUT_INFORMATION)
        self.output_setup = True

        self.logger.debug(
            "[{}] EZO-PMP MQTT output initialized. Broker: {}:{}, command topic: {}".format(
                self.options_channels['pump_label'][0],
                self.options_channels['hostname'][0],
                self.options_channels['port'][0],
                self.options_channels['cmd_topic'][0]
            )
        )

    # ──────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────

    def _auth_dict(self):
        """Return a paho auth dict when broker authentication is enabled, else None."""
        if self.options_channels['use_auth'][0]:
            password = self.options_channels['password'][0] or None
            return {
                'username': self.options_channels['username'][0],
                'password': password
            }
        return None

    def _parse_pump_ids(self, pump_ids_text=None):
        if pump_ids_text is None:
            pump_ids_text = self.options_channels['pump_ids'][0]

        if pump_ids_text is None:
            return []

        if not isinstance(pump_ids_text, str):
            pump_ids_text = str(pump_ids_text)

        return [pump_id.strip() for pump_id in pump_ids_text.split(',') if pump_id.strip()]

    def _normalize_cmd(self, cmd):
        """Return a normalized uppercase command token prefix.

        Examples:
            'D,5' -> 'D'
            'cal,?' -> 'CAL'
            'Status' -> 'STATUS'
        """
        if cmd is None:
            return ''
        token = str(cmd).strip()
        if not token:
            return ''
        return token.split(',', 1)[0].strip().upper()

    def _is_motor_command(self, cmd):
        """Return True if cmd is considered motor/power-budgeted by scheduler policy."""
        token = self._normalize_cmd(cmd)
        return token in {'D', 'DC', 'DS', 'R', 'X', 'P'}

    def _is_non_motor_command(self, cmd):
        """Return True if cmd is considered non-motor by scheduler policy."""
        token = self._normalize_cmd(cmd)
        return token in {'I', 'PV', 'STATUS', 'CAL', 'NAME'}

    def _is_interrupt_command(self, cmd):
        """Return True for commands that should interrupt active motor work."""
        return self._normalize_cmd(cmd) == 'X'

    def _default_options_for_cmd(self, cmd):
        """Return default scheduler options for a command, or None if not needed."""
        if self._is_motor_command(cmd):
            # Motor commands should queue when busy by default, except interrupt stop.
            if self._is_interrupt_command(cmd):
                return {'queue_if_busy': False}
            queue_if_busy_default = self.options_channels.get('queue_motor_if_busy', [True])[0]
            return {'queue_if_busy': bool(queue_if_busy_default)}
        return None

    def _resolve_publish_options(self, cmd, options):
        """Merge default scheduler options with explicit caller options."""
        default_options = self._default_options_for_cmd(cmd)

        if default_options is None:
            return options

        if options is None:
            return default_options

        merged_options = dict(default_options)
        merged_options.update(options)
        return merged_options

    def _build_payload(self, cmd, pump_id, request_id=None, options=None): 
        payload_format = self.options_channels['payload_format'][0]

        if payload_format == 'json_minimal':
            if request_id is None:
                request_id = '{}-{}'.format(pump_id, uuid4().hex[:8])

            payload = {
                'v': 1,
                'pump_id': pump_id,
                'cmd': cmd,
                'wait_ms': int(self.options_channels['wait_ms'][0]),
                'request_id': request_id
            }

            if options is not None:
                payload['options'] = options

            return json.dumps(payload)

        return cmd

    def _build_batch_payload(self, cmd, pump_ids):
        """Build a pump_batch JSON payload for all pump_ids in order."""
        batch_prefix = self.options_channels['batch_id_prefix'][0] or 'batch'
        wait_ms = int(self.options_channels['wait_ms'][0])
        stop_on_error = bool(self.options_channels['stop_on_error'][0])

        payload = {
            'v': 1,
            'type': 'pump_batch',
            'batch_id': '{}-{}'.format(batch_prefix, uuid4().hex[:8]),
            'options': {
                'stop_on_error': stop_on_error,
                'default_wait_ms': wait_ms
            },
            'commands': []
        }

        for idx, pump_id in enumerate(pump_ids, start=1):
            payload['commands'].append({
                'item_id': 'step-{}'.format(idx),
                'pump_id': pump_id,
                'cmd': cmd
            })

        return json.dumps(payload)

    def _publish(self, cmd, pump_ids=None, request_id=None, options=None):
        """Publish a raw ASCII command string to the MQTT command topic.

        Args:
            cmd (str): Raw EZO-PMP command string, e.g. 'D,5', 'X', 'i'.
            pump_ids (list): Optional list of logical pump IDs. If omitted,
                comma-separated IDs from settings are used.
            request_id (str): Optional request ID to include in JSON payloads.
                If omitted, a per-pump request ID is generated.
            options (dict): Optional JSON options object to include in payloads
                when using JSON payload format.

        Returns:
            str: A short status message suitable for display in the Mycodo UI.
        """
        if not self.is_setup():
            msg = "Error 101: Device not set up. See https://kizniche.github.io/Mycodo/Error-Codes#error-101 for more info."
            self.logger.error(msg)
            return msg

        label = self.options_channels['pump_label'][0]
        topic = self.options_channels['cmd_topic'][0]
        payload_format = self.options_channels['payload_format'][0]
        effective_options = self._resolve_publish_options(cmd, options)

        if pump_ids is None:
            pump_ids = self._parse_pump_ids()

        if not pump_ids:
            # Fallback to legacy single-pump behavior
            pump_ids = [self.options_channels['pump_label'][0]]

        if payload_format == 'raw_ascii' and len(pump_ids) > 1:
            msg = "Error: Raw ASCII payload format supports only one target pump ID"
            self.logger.error("[{}] {}".format(label, msg))
            return msg

        self.logger.debug(
            "[{}] MQTT publish ({}) → {}: cmd={}, cmd_type={}, interrupt={}, options={}, targets={}".format(
                label,
                payload_format,
                topic,
                cmd,
                'motor' if self._is_motor_command(cmd) else 'non_motor' if self._is_non_motor_command(cmd) else 'unknown',
                self._is_interrupt_command(cmd),
                effective_options,
                ",".join(pump_ids)
            )
        )

        try:
            if payload_format == 'json_batch':
                payload = self._build_batch_payload(cmd, pump_ids)
                self.publish.single(
                    topic,
                    payload=payload,
                    hostname=self.options_channels['hostname'][0],
                    port=self.options_channels['port'][0],
                    client_id=self.options_channels['clientid'][0],
                    keepalive=self.options_channels['keepalive'][0],
                    auth=self._auth_dict()
                )
                return "Published batch '{}' to {} pump(s)".format(cmd, len(pump_ids))

            for pump_id in pump_ids:
                pump_request_id = request_id
                if pump_request_id is None:
                    pump_request_id = '{}-{}'.format(pump_id, uuid4().hex[:8])

                payload = self._build_payload(
                    cmd,
                    pump_id,
                    request_id=pump_request_id,
                    options=effective_options
                )
                self.publish.single(
                    topic,
                    payload=payload,
                    hostname=self.options_channels['hostname'][0],
                    port=self.options_channels['port'][0],
                    client_id=self.options_channels['clientid'][0],
                    keepalive=self.options_channels['keepalive'][0],
                    auth=self._auth_dict()
                )

            return "Published '{}' to {} pump(s)".format(cmd, len(pump_ids))
        except Exception as err:
            self.logger.error("[{}] MQTT publish error: {}".format(label, err))
            return "Error: {}".format(err)

    # ──────────────────────────────────────────────────────────────────
    # Output control (called by Mycodo framework)
    # ──────────────────────────────────────────────────────────────────

    def output_switch(self, state, output_type=None, amount=None, output_channel=None):
        if not self.is_setup():
            msg = "Error 101: Device not set up. See https://kizniche.github.io/Mycodo/Error-Codes#error-101 for more info."
            self.logger.error(msg)
            return msg

        if state == 'on' and output_type == 'sec':
            # Continuous dispense until explicitly stopped
            if self.currently_dispensing:
                self.logger.debug(
                    "[{}] Pump instructed to start while already dispensing.".format(
                        self.options_channels['pump_label'][0]))
            else:
                self.currently_dispensing = True
                if output_channel is not None:
                    self.output_states[output_channel] = True
                return self._publish('D,*')

        elif state == 'off' or (amount is not None and amount == 0):
            # Stop pump
            self.currently_dispensing = False
            if output_channel is not None:
                self.output_states[output_channel] = False
            return self._publish('X')

        elif state == 'on' and output_type in ['vol', None] and amount:
            # Dispense a fixed volume
            flow_mode = self.options_channels['flow_mode'][0]

            if flow_mode == 'fastest_flow_rate':
                # EZO-PMP maximum flow rate is 105 ml/min
                cmd = 'D,{:.2f}'.format(amount)

            elif flow_mode == 'specify_flow_rate':
                flow_rate = self.options_channels['flow_rate'][0]
                minutes_to_run = abs(amount) / flow_rate
                cmd = 'D,{:.2f},{:.2f}'.format(amount, minutes_to_run)

            else:
                self.logger.error("Invalid flow_mode: '{}'".format(flow_mode))
                return

            return self._publish(cmd)

        else:
            self.logger.error(
                "Invalid parameters: state={}, output_type={}, flow_mode={}, amount={}, flow_rate={}".format(
                    state,
                    output_type,
                    self.options_channels['flow_mode'][0],
                    amount,
                    self.options_channels['flow_rate'][0]
                )
            )

    def is_on(self, output_channel=None):
        if self.is_setup():
            return self.currently_dispensing

    def is_setup(self):
        return self.output_setup

    # ──────────────────────────────────────────────────────────────────
    # Custom command handlers (wired to UI buttons above)
    # ──────────────────────────────────────────────────────────────────

    def send_raw_command(self, args_dict):
        """Publish an arbitrary raw ASCII command string to the pump."""
        cmd = args_dict.get('raw_command', '').strip()
        if not cmd:
            self.logger.error("send_raw_command: no command provided")
            return "Error: no command provided"
        return self._publish(cmd)

    def dispense_volume(self, args_dict):
        """Dispense a configurable volume, respecting the current flow mode."""
        if 'dispense_volume_ml' not in args_dict:
            self.logger.error("dispense_volume: dispense_volume_ml not provided")
            return "Error: dispense_volume_ml not provided"

        amount = float(args_dict['dispense_volume_ml'])

        if self.options_channels['flow_mode'][0] == 'specify_flow_rate':
            flow_rate = self.options_channels['flow_rate'][0]
            minutes_to_run = abs(amount) / flow_rate
            cmd = 'D,{:.2f},{:.2f}'.format(amount, minutes_to_run)
        else:
            cmd = 'D,{:.2f}'.format(amount)

        return self._publish(cmd)

    def stop_pump(self, args_dict):
        """Send the EZO-PMP stop command (X)."""
        self.currently_dispensing = False
        return self._publish('X')

    def pump_info(self, args_dict):
        """Query pump firmware and device info (i)."""
        return self._publish('i')

    def pump_status(self, args_dict):
        """Query pump operating status (Status)."""
        return self._publish('Status')

    def calibration_query(self, args_dict):
        """Query current calibration state (Cal,?)."""
        return self._publish('Cal,?')

    def calibrate_pump(self, args_dict):
        """Calibrate using the actual measured dispensed volume (Cal,<ml>)."""
        if 'calibrate_volume_ml' not in args_dict:
            self.logger.error("calibrate_pump: calibrate_volume_ml not provided")
            return "Error: calibrate_volume_ml not provided"
        ml = float(args_dict['calibrate_volume_ml'])
        return self._publish('Cal,{:.2f}'.format(ml))

    def clear_calibration(self, args_dict):
        """Clear the pump calibration (Cal,clear)."""
        return self._publish('Cal,clear')

    def rename_pump(self, args_dict):
        """Send a bridge_command rename_pump to the ESP32.

        Renames a pump's logical ID at runtime. Resets on ESP32 reboot.
        """
        if not self.is_setup():
            msg = "Error 101: Device not set up. See https://kizniche.github.io/Mycodo/Error-Codes#error-101 for more info."
            self.logger.error(msg)
            return msg

        from_id = args_dict.get('rename_from_id', '').strip()
        to_id   = args_dict.get('rename_to_id', '').strip()

        if not from_id:
            return "Error: Current Pump ID (from) is required"
        if not to_id:
            return "Error: New Pump ID (to) is required"
        if len(to_id) > 23:
            return "Error: New Pump ID must be 23 characters or fewer"

        label = self.options_channels['pump_label'][0]
        topic = self.options_channels['cmd_topic'][0]

        payload = json.dumps({
            'v': 1,
            'type': 'bridge_command',
            'action': 'rename_pump',
            'from_id': from_id,
            'to_id': to_id
        })

        self.logger.info(
            "[{}] Sending rename_pump: {} → {}".format(label, from_id, to_id)
        )

        try:
            self.publish.single(
                topic,
                payload=payload,
                hostname=self.options_channels['hostname'][0],
                port=self.options_channels['port'][0],
                client_id=self.options_channels['clientid'][0],
                keepalive=self.options_channels['keepalive'][0],
                auth=self._auth_dict()
            )
            return "Sent rename_pump: '{}' → '{}' (check esp32/pump/resp for result, resets on reboot)".format(
                from_id, to_id
            )
        except Exception as err:
            self.logger.error("[{}] rename_pump publish error: {}".format(label, err))
            return "Error: {}".format(err)

    def send_batch_command(self, args_dict):
        """Build and publish a batch command to all selected pump IDs.

        Always publishes a pump_batch JSON payload regardless of the
        payload_format channel option.
        """
        if not self.is_setup():
            msg = "Error 101: Device not set up. See https://kizniche.github.io/Mycodo/Error-Codes#error-101 for more info."
            self.logger.error(msg)
            return msg

        prebuilt_payload = args_dict.get('payload_json')
        if prebuilt_payload:
            label = self.options_channels['pump_label'][0]
            topic = self.options_channels['cmd_topic'][0]

            try:
                if isinstance(prebuilt_payload, str):
                    payload_dict = json.loads(prebuilt_payload)
                else:
                    payload_dict = prebuilt_payload
            except Exception as err:
                self.logger.error("[{}] Invalid prebuilt batch payload: {}".format(label, err))
                return "Error: invalid prebuilt batch payload: {}".format(err)

            if payload_dict.get('type') != 'pump_batch':
                return "Error: prebuilt payload type must be 'pump_batch'"
            if not payload_dict.get('commands'):
                return "Error: prebuilt batch payload must contain at least one command"

            payload = json.dumps(payload_dict)

            self.logger.debug(
                "[{}] Prebuilt batch publish → {}: batch_id={}, commands={}".format(
                    label,
                    topic,
                    payload_dict.get('batch_id', 'unknown'),
                    len(payload_dict.get('commands', []))
                )
            )

            try:
                self.publish.single(
                    topic,
                    payload=payload,
                    hostname=self.options_channels['hostname'][0],
                    port=self.options_channels['port'][0],
                    client_id=self.options_channels['clientid'][0],
                    keepalive=self.options_channels['keepalive'][0],
                    auth=self._auth_dict()
                )
                return "Published prebuilt batch '{}' with {} command(s)".format(
                    payload_dict.get('batch_id', 'unknown'),
                    len(payload_dict.get('commands', []))
                )
            except Exception as err:
                self.logger.error("[{}] Prebuilt batch publish error: {}".format(label, err))
                return "Error: {}".format(err)

        action = args_dict.get('batch_action', 'dispense')

        if action == 'dispense':
            volume = float(args_dict.get('batch_volume_ml', 5.0))
            if self.options_channels['flow_mode'][0] == 'specify_flow_rate':
                flow_rate = self.options_channels['flow_rate'][0]
                minutes_to_run = abs(volume) / flow_rate
                cmd = 'D,{:.2f},{:.2f}'.format(volume, minutes_to_run)
            else:
                cmd = 'D,{:.2f}'.format(volume)
        elif action == 'stop':
            cmd = 'X'
        elif action == 'info':
            cmd = 'i'
        elif action == 'status':
            cmd = 'Status'
        elif action == 'calibration_query':
            cmd = 'Cal,?'
        else:
            return "Error: unknown batch action '{}'".format(action)

        pump_ids = self._parse_pump_ids()
        if not pump_ids:
            return "Error: no pump IDs configured"

        label = self.options_channels['pump_label'][0]
        topic = self.options_channels['cmd_topic'][0]

        try:
            payload = self._build_batch_payload(cmd, pump_ids)
        except Exception as err:
            self.logger.error("[{}] Batch publish error: {}".format(label, err))
            return "Error: {}".format(err)

        self.logger.debug(
            "[{}] Batch publish → {}: cmd={}, pumps={}".format(
                label, topic, cmd, ','.join(pump_ids)
            )
        )

        try:
            self.publish.single(
                topic,
                payload=payload,
                hostname=self.options_channels['hostname'][0],
                port=self.options_channels['port'][0],
                client_id=self.options_channels['clientid'][0],
                keepalive=self.options_channels['keepalive'][0],
                auth=self._auth_dict()
            )
            return "Published batch '{}' to {} pump(s): {}".format(
                cmd, len(pump_ids), ', '.join(pump_ids)
            )
        except Exception as err:
            self.logger.error("[{}] Batch publish error: {}".format(label, err))
            return "Error: {}".format(err)