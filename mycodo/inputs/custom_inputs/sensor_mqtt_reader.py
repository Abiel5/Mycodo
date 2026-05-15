# coding=utf-8
import copy
import datetime
import json
import threading

from mycodo.inputs.base_input import AbstractInput
from mycodo.utils.actions import run_input_actions
from mycodo.utils.influx import add_measurements_influxdb

measurements_dict = {
    0: {'measurement': 'ion_concentration',       'unit': 'pH'},
    1: {'measurement': 'electrical_conductivity',  'unit': 'μS_cm'},
    2: {'measurement': 'temperature',              'unit': 'F'},   # water temp
    3: {'measurement': 'temperature',              'unit': 'F'},   # air temp
    4: {'measurement': 'humidity',                 'unit': 'percent'},
}

channels_dict = {
    0: {},
    1: {},
    2: {},
    3: {},
    4: {},
}

INPUT_INFORMATION = {
    'input_name_unique': 'sensor_mqtt_reader',
    'input_manufacturer': 'Mycodo',
    'input_name': 'Sensor ESP32 MQTT Reader',
    'input_name_short': 'Sensor MQTT',
    'input_library': 'paho-mqtt',
    'measurements_name': 'pH / EC / Temperature / Humidity',
    'measurements_dict': measurements_dict,
    'channels_dict': channels_dict,

    'measurements_use_same_timestamp': True,

    'message': (
        'On each poll cycle, sends a read_all command to the ESP32 sensor controller '
        'over MQTT, waits for the response on the status topic, and records pH, EC, '
        'water temperature, air temperature, and humidity. Any sensor that returns -1 '
        '(hardware failure) is skipped for that cycle.'
    ),

    'options_enabled': [
        'measurements_select',
        'period',
        'pre_output',
    ],
    'options_disabled': ['interface'],
    'interfaces': ['Mycodo'],

    'dependencies_module': [
        ('pip-pypi', 'paho', 'paho-mqtt==1.5.1')
    ],

    'custom_options': [
        {
            'id': 'hostname',
            'type': 'text',
            'default_value': '10.0.0.232',
            'required': True,
            'name': 'MQTT Broker Hostname',
            'phrase': 'Hostname or IP of the MQTT broker'
        },
        {
            'id': 'port',
            'type': 'integer',
            'default_value': 1883,
            'required': True,
            'name': 'MQTT Broker Port',
            'phrase': 'Port of the MQTT broker'
        },
        {
            'id': 'use_auth',
            'type': 'bool',
            'default_value': True,
            'name': 'Use Authentication',
            'phrase': 'Enable MQTT username/password authentication'
        },
        {
            'id': 'username',
            'type': 'text',
            'default_value': '',
            'required': False,
            'name': 'MQTT Username',
            'phrase': 'MQTT broker username (if authentication is enabled)'
        },
        {
            'id': 'password',
            'type': 'text',
            'default_value': '',
            'required': False,
            'name': 'MQTT Password',
            'phrase': 'MQTT broker password (if authentication is enabled)'
        },
        {
            'id': 'cmd_topic',
            'type': 'text',
            'default_value': 'esp32/sensors/cmd',
            'required': True,
            'name': 'Command Topic',
            'phrase': 'MQTT topic to publish read commands to'
        },
        {
            'id': 'status_topic',
            'type': 'text',
            'default_value': 'esp32/sensors/status',
            'required': True,
            'name': 'Status Topic',
            'phrase': 'MQTT topic to subscribe to for sensor readings'
        },
        {
            'id': 'read_timeout_sec',
            'type': 'integer',
            'default_value': 8,
            'required': True,
            'name': 'Read Timeout (seconds)',
            'phrase': 'How long to wait for the ESP32 response before giving up'
        },
    ],

    'custom_channel_options': [
        {
            'id': 'name',
            'type': 'text',
            'default_value': '',
            'required': False,
            'name': 'Channel Name',
            'phrase': 'A name for this channel'
        },
    ],
}

# Channel index → field name in the ESP32 status payload
_CHANNEL_FIELDS = {
    0: 'ph',
    1: 'ec',
    2: 'water_temp',
    3: 'air_temp',
    4: 'air_hum',
}


class InputModule(AbstractInput):
    """
    Mycodo-driven sensor reader for the ESP32 sensor controller.
    On each poll cycle, publishes a read_all command and waits for the response.
    """

    def __init__(self, input_dev, testing=False):
        super().__init__(input_dev, testing=testing, name=__name__)

        self.client = None
        self.log_level_debug = None

        self.hostname = None
        self.port = None
        self.use_auth = None
        self.username = None
        self.password = None
        self.cmd_topic = None
        self.status_topic = None
        self.read_timeout_sec = None

        self._pending_request_id = None
        self._pending_event = None
        self._pending_values = None
        self._lock = threading.Lock()

        if not testing:
            self.setup_custom_options(INPUT_INFORMATION['custom_options'], input_dev)
            self.try_initialize()

    def initialize(self):
        import paho.mqtt.client as mqtt

        self.log_level_debug = self.input_dev.log_level_debug
        self._load_options()

        self.client = mqtt.Client('sensor_mqtt_{}'.format(self.unique_id.split('-')[0]))
        if self.use_auth and self.username:
            self.client.username_pw_set(self.username, self.password)

        self.client.on_connect    = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message    = self._on_message

        self.client.connect(self.hostname, port=self.port, keepalive=60)
        self.client.loop_start()

        self.logger.info(
            "Sensor MQTT reader initialized. broker={}:{}, cmd={}, status={}".format(
                self.hostname, self.port, self.cmd_topic, self.status_topic
            )
        )

    def get_measurement(self):
        from uuid import uuid4

        if not self.client or not self.client.is_connected():
            self.logger.error("MQTT client is not connected")
            return None

        request_id = 'mycodo-{}'.format(uuid4().hex[:8])
        event = threading.Event()

        with self._lock:
            self._pending_request_id = request_id
            self._pending_event = event
            self._pending_values = None

        payload = json.dumps({'v': 1, 'cmd': 'read_all', 'request_id': request_id})
        self.client.publish(self.cmd_topic, payload)
        self.logger.debug("Sent read_all (request_id={})".format(request_id))

        got_response = event.wait(timeout=self.read_timeout_sec)

        with self._lock:
            values = self._pending_values
            self._pending_request_id = None
            self._pending_event = None
            self._pending_values = None

        if not got_response or values is None:
            self.logger.error(
                "Sensor read timed out after {}s (request_id={})".format(
                    self.read_timeout_sec, request_id
                )
            )
            return None

        self.return_dict = copy.deepcopy(measurements_dict)
        timestamp_utc = datetime.datetime.utcnow()
        any_stored = False

        for channel, field in _CHANNEL_FIELDS.items():
            if not self.is_enabled(channel):
                continue
            raw = values.get(field)
            if raw is None or float(raw) < 0:
                self.logger.debug("Channel {} ({}) returned failure value, skipping".format(channel, field))
                continue
            self.return_dict[channel]['value'] = float(raw)
            self.return_dict[channel]['timestamp_utc'] = timestamp_utc
            any_stored = True

        if not any_stored:
            self.logger.error("All sensors returned failure values for request_id={}".format(request_id))
            return None

        self.logger.debug("Sensor read ok: {}".format(
            {_CHANNEL_FIELDS[c]: self.return_dict[c].get('value') for c in _CHANNEL_FIELDS if 'value' in self.return_dict[c]}
        ))

        return self.return_dict

    def stop_input(self):
        self.running = False
        if self.client:
            self.client.loop_stop()
            try:
                self.client.disconnect()
            except Exception:
                self.logger.exception("Unable to disconnect MQTT client cleanly")

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe(self.status_topic)
            self.logger.info("Connected to MQTT broker, subscribed to {}".format(self.status_topic))
        else:
            self.logger.error("MQTT connection failed with code {}".format(rc))

    def _on_disconnect(self, client, userdata, rc):
        self.logger.warning("Disconnected from MQTT broker (rc={})".format(rc))

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except Exception as err:
            self.logger.error("Failed to decode MQTT message: {}".format(err))
            return

        # Ignore controller_status heartbeats — they have a 'type' field
        if payload.get('type') == 'controller_status':
            return

        with self._lock:
            if (self._pending_request_id is not None
                    and payload.get('request_id') == self._pending_request_id
                    and msg.topic == self.status_topic):
                self._pending_values = payload
                if self._pending_event:
                    self._pending_event.set()

    def _load_options(self):
        self.hostname        = self.get_custom_option('hostname') or '10.0.0.232'
        self.port            = int(self.get_custom_option('port') or 1883)
        self.use_auth        = bool(self.get_custom_option('use_auth'))
        self.username        = self.get_custom_option('username') or None
        self.password        = self.get_custom_option('password') or None
        self.cmd_topic       = self.get_custom_option('cmd_topic') or 'esp32/sensors/cmd'
        self.status_topic    = self.get_custom_option('status_topic') or 'esp32/sensors/status'
        self.read_timeout_sec = int(self.get_custom_option('read_timeout_sec') or 8)
