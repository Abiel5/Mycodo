# coding=utf-8
#
# pump_atlas_ezo_pmp_mqtt.py - Mycodo output for Atlas Scientific EZO-PMP via MQTT
#
# Mycodo publishes JSON commands to an MQTT broker.
# An ESP32 subscribes and forwards commands to the EZO-PMP over I2C.
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
        'Atlas Scientific EZO-PMP dosing pump controlled over MQTT. '
        'Mycodo publishes JSON commands to the configured broker. '
        'An ESP32 subscribes and forwards commands to the EZO-PMP over I2C. '
        'No direct hardware connection is required from the Pi.'
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
            'id': 'cmd_topic',
            'type': 'text',
            'default_value': 'esp32/pump/cmd',
            'required': True,
            'name': 'Command Topic',
            'phrase': 'MQTT topic to publish pump commands to'
        },
        {
            'id': 'resp_topic',
            'type': 'text',
            'default_value': 'esp32/pump/resp',
            'required': False,
            'name': 'Response Topic',
            'phrase': 'MQTT topic where the pump bridge publishes responses'
        },
        {
            'id': 'status_topic',
            'type': 'text',
            'default_value': 'esp32/pump/status',
            'required': False,
            'name': 'Status Topic',
            'phrase': 'MQTT topic where the pump bridge publishes status and heartbeat messages'
        },
        {
            'id': 'pump_id',
            'type': 'text',
            'default_value': 'calmag',
            'required': True,
            'name': 'Pump ID',
            'phrase': (
                'Logical pump ID sent in every command. '
                'Must match the ID configured on the ESP32. '
                'Examples: calmag, micro, gro, bloom, phup, phdown'
            )
        },
        {
            'id': 'wait_ms',
            'type': 'integer',
            'default_value': 300,
            'required': True,
            'constraints_pass': constraints_pass_positive_or_zero_value,
            'name': 'Wait (ms)',
            'phrase': 'Milliseconds the ESP32 waits before reading a response from the pump'
        },
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
            'phrase': 'Target flow rate when "Specify Flow Rate" is selected (EZO-PMP range: 0.5–105 ml/min)'
        },
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
            'phrase': 'MQTT broker username'
        },
        {
            'id': 'password',
            'type': 'text',
            'default_value': '',
            'required': False,
            'name': lazy_gettext('Password'),
            'phrase': 'MQTT broker password'
        },
        {
            'id': 'amps',
            'type': 'float',
            'default_value': 0.0,
            'required': True,
            'name': "{} ({})".format(lazy_gettext('Current'), lazy_gettext('Amps')),
            'phrase': 'Current draw of the device being controlled'
        },
    ],

    'custom_commands': [
        {
            'id': 'dispense_volume_ml',
            'type': 'float',
            'default_value': 5.0,
            'name': 'Volume to Dispense (ml)',
            'phrase': 'Volume in ml to dispense'
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
            'type': 'new_line'
        },
        {
            'type': 'message',
            'default_value': (
                'Calibration: prime the line first, dispense a known amount, '
                'measure the actual volume, enter it below, then press Calibrate.'
            )
        },
        {
            'id': 'calibrate_volume_ml',
            'type': 'float',
            'default_value': 10.0,
            'name': 'Actual Volume Dispensed (ml)',
            'phrase': 'The measured volume dispensed during calibration'
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
    ]
}


class OutputModule(AbstractOutput):
    """
    Atlas Scientific EZO-PMP output controlled over MQTT.

    Architecture:
        Mycodo → MQTT broker → ESP32 → EZO-PMP (I2C)
    """

    def __init__(self, output, testing=False):
        super().__init__(output, testing=testing, name=__name__)

        self.publish = None
        self.currently_dispensing = False
        self._client_id = 'mycodo_ezo_pmp_{}'.format(random_alphanumeric(6))

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
            "[{}] EZO-PMP MQTT output initialized. broker={}:{}, topic={}".format(
                self.options_channels['pump_id'][0],
                self.options_channels['hostname'][0],
                self.options_channels['port'][0],
                self.options_channels['cmd_topic'][0]
            )
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _auth(self):
        if self.options_channels['use_auth'][0]:
            return {
                'username': self.options_channels['username'][0],
                'password': self.options_channels['password'][0] or None
            }
        return None

    def _publish(self, cmd, pump_id=None, request_id=None):
        if not self.is_setup():
            msg = "Error 101: Device not set up."
            self.logger.error(msg)
            return msg

        if pump_id is None:
            pump_id = self.options_channels['pump_id'][0]
        if request_id is None:
            request_id = '{}-{}'.format(pump_id, uuid4().hex[:8])

        payload = json.dumps({
            'v': 1,
            'pump_id': pump_id,
            'cmd': cmd,
            'wait_ms': int(self.options_channels['wait_ms'][0]),
            'request_id': request_id
        })

        try:
            self.publish.single(
                self.options_channels['cmd_topic'][0],
                payload=payload,
                hostname=self.options_channels['hostname'][0],
                port=self.options_channels['port'][0],
                client_id=self._client_id,
                keepalive=60,
                auth=self._auth()
            )
            self.logger.debug("[{}] Published cmd={}".format(pump_id, cmd))
            return "Published '{}' to {}".format(cmd, pump_id)
        except Exception as err:
            self.logger.error("[{}] MQTT publish error: {}".format(pump_id, err))
            return "Error: {}".format(err)

    def _dose_cmd(self, amount):
        if self.options_channels['flow_mode'][0] == 'specify_flow_rate':
            flow_rate = self.options_channels['flow_rate'][0]
            minutes = abs(amount) / flow_rate
            return 'D,{:.2f},{:.2f}'.format(amount, minutes)
        return 'D,{:.2f}'.format(amount)

    # ── Mycodo output interface ───────────────────────────────────────────────

    def output_switch(self, state, output_type=None, amount=None, output_channel=None):
        if not self.is_setup():
            msg = "Error 101: Device not set up."
            self.logger.error(msg)
            return msg

        pump_id = self.options_channels['pump_id'][0]

        if state == 'on' and output_type == 'sec':
            self.currently_dispensing = True
            if output_channel is not None:
                self.output_states[output_channel] = True
            return self._publish('D,*', pump_id=pump_id)

        elif state == 'off' or (amount is not None and amount == 0):
            self.currently_dispensing = False
            if output_channel is not None:
                self.output_states[output_channel] = False
            return self._publish('X', pump_id=pump_id)

        elif state == 'on' and output_type in ['vol', None] and amount:
            self.currently_dispensing = True
            if output_channel is not None:
                self.output_states[output_channel] = True
            return self._publish(self._dose_cmd(amount), pump_id=pump_id)

        else:
            self.logger.error(
                "Invalid parameters: state={}, output_type={}, amount={}".format(
                    state, output_type, amount)
            )

    def is_on(self, output_channel=None):
        if self.is_setup():
            return self.currently_dispensing

    def is_setup(self):
        return self.output_setup

    # ── Custom command handlers ───────────────────────────────────────────────

    def dispense_volume(self, args_dict):
        amount = float(args_dict.get('dispense_volume_ml', 5.0))
        return self._publish(self._dose_cmd(amount))

    def stop_pump(self, args_dict):
        return self._publish('X')

    def pump_info(self, args_dict):
        return self._publish('i')

    def calibrate_pump(self, args_dict):
        ml = float(args_dict.get('calibrate_volume_ml', 10.0))
        return self._publish('Cal,{:.2f}'.format(ml))

    def clear_calibration(self, args_dict):
        return self._publish('Cal,clear')
