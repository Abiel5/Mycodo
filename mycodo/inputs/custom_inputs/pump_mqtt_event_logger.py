# coding=utf-8
import datetime
import json

from mycodo.databases.models import Output
from mycodo.databases.models import OutputChannel
from mycodo.inputs.base_input import AbstractInput
from mycodo.utils.actions import run_input_actions
from mycodo.utils.database import db_retrieve_table_daemon
from mycodo.utils.influx import add_measurements_influxdb
from mycodo.utils.pump_history import append_mqtt_record
from mycodo.utils.pump_history import pump_history_db_path
from mycodo.utils.pump_history import pump_history_file_path


measurements_dict = {
    0: {
        'measurement': 'boolean',
        'unit': 'bool'
    },
    1: {
        'measurement': 'boolean',
        'unit': 'bool'
    },
    2: {
        'measurement': 'unitless',
        'unit': 'none'
    },
    3: {
        'measurement': 'unitless',
        'unit': 'none'
    }
}

channels_dict = {
    0: {},
    1: {},
    2: {},
    3: {}
}

INPUT_INFORMATION = {
    'input_name_unique': 'pump_mqtt_event_logger',
    'input_manufacturer': 'Mycodo',
    'input_name': 'Pump MQTT Event Logger',
    'input_name_short': 'Pump Events',
    'input_library': 'paho-mqtt',
    'measurements_name': 'Pump controller status',
    'measurements_dict': measurements_dict,
    'channels_dict': channels_dict,

    'measurements_use_same_timestamp': True,

    'message': (
        'Subscribes to the ESP32 pump response and status topics using the selected '
        'Atlas EZO-PMP MQTT Output settings. All received MQTT payloads are appended '
        'to an on-device JSONL history file, and controller status messages can also '
        'be stored as Mycodo measurements.'
    ),

    'options_enabled': [
        'measurements_select'
    ],
    'options_disabled': ['interface'],
    'interfaces': ['Mycodo'],

    'dependencies_module': [
        ('pip-pypi', 'paho', 'paho-mqtt==1.5.1')
    ],

    'custom_options': [
        {
            'type': 'message',
            'default_value': (
                'Select the Atlas EZO-PMP MQTT Output that owns the broker connection. '
                'This Input inherits hostname, port, authentication, and the response/status '
                'topics from that Output so the settings stay single-sourced.'
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
            'phrase': 'The Atlas EZO-PMP MQTT Output instance to subscribe through'
        }
    ],

    'custom_commands_message': 'Use these tools to verify which output is selected and where history records are stored.',
    'custom_commands': [
        {
            'id': 'show_history_path',
            'type': 'button',
            'wait_for_return': True,
            'name': 'Show History File Path'
        },
        {
            'id': 'reload_output_settings',
            'type': 'button',
            'wait_for_return': True,
            'name': 'Reload Output Settings'
        }
    ]
}


class InputModule(AbstractInput):
    """Subscribe to ESP32 pump response/status topics and persist received events."""

    def __init__(self, input_dev, testing=False):
        super().__init__(input_dev, testing=testing, name=__name__)

        self.client = None
        self.log_level_debug = None

        self.mqtt_output_id = None

        self.mqtt_hostname = None
        self.mqtt_port = None
        self.mqtt_keepalive = None
        self.mqtt_clientid = None
        self.mqtt_username = None
        self.mqtt_password = None
        self.resp_topic = None
        self.status_topic = None

        if not testing:
            self.setup_custom_options(INPUT_INFORMATION['custom_options'], input_dev)
            self.try_initialize()

    def initialize(self):
        import paho.mqtt.client as mqtt

        self.log_level_debug = self.input_dev.log_level_debug
        self._load_output_settings()

        self.client = mqtt.Client(self.mqtt_clientid)
        if self.mqtt_username:
            self.client.username_pw_set(self.mqtt_username, self.mqtt_password)

        self.logger.info(
            "Pump MQTT event logger initialized. Output={}, broker={}:{}, resp_topic={}, status_topic={}".format(
                self._selected_output_id(),
                self.mqtt_hostname,
                self.mqtt_port,
                self.resp_topic,
                self.status_topic
            )
        )

    def listener(self):
        self.callbacks_connect()
        self.connect()
        self.client.loop_start()

    def callbacks_connect(self):
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.client.on_subscribe = self.on_subscribe

    def connect(self):
        self.client.connect(
            self.mqtt_hostname,
            port=self.mqtt_port,
            keepalive=self.mqtt_keepalive
        )

    def on_connect(self, client, obj, flags, rc):
        self.logger.info("Connected to MQTT broker with code {}".format(rc))
        self._append_connection_record('connected', rc)

        subscribed_topics = []
        for topic in [self.resp_topic, self.status_topic]:
            if topic and topic not in subscribed_topics:
                self.client.subscribe(topic)
                subscribed_topics.append(topic)

    def on_disconnect(self, client, userdata, rc):
        self.logger.warning("Disconnected from MQTT broker with code {}".format(rc))
        self._append_connection_record('disconnected', rc)

    def on_subscribe(self, client, obj, mid, granted_qos):
        self.logger.debug(
            "Subscribed to pump MQTT topics: resp={}, status={}, qos={}".format(
                self.resp_topic,
                self.status_topic,
                granted_qos
            )
        )

    def on_message(self, client, userdata, msg):
        try:
            payload_text = msg.payload.decode()
        except Exception as err:
            self.logger.error("Unable to decode MQTT payload on {}: {}".format(msg.topic, err))
            append_mqtt_record(msg.topic, {
                'decode_error': str(err),
                'raw_payload': str(msg.payload)
            })
            return

        try:
            payload_dict = json.loads(payload_text)
        except ValueError as err:
            self.logger.error(
                "Unable to parse MQTT payload on {} as JSON: {} | payload={}".format(
                    msg.topic, err, payload_text
                )
            )
            append_mqtt_record(msg.topic, {
                'parse_error': str(err),
                'raw_payload': payload_text
            })
            return

        append_mqtt_record(msg.topic, payload_dict)

        if msg.topic == self.resp_topic:
            self._check_for_errors(payload_dict)

        if msg.topic == self.status_topic:
            self._store_status_measurements(payload_dict)

    def stop_input(self):
        self.running = False
        if self.client:
            self.client.loop_stop()
            try:
                self.client.disconnect()
            except Exception:
                self.logger.exception("Unable to disconnect MQTT client cleanly")

    def show_history_path(self, args_dict):
        return "JSONL: {}\nSQLite: {}".format(
            pump_history_file_path(),
            pump_history_db_path()
        )

    def reload_output_settings(self, args_dict):
        self._load_output_settings()
        return "Reloaded output settings from {}".format(self._selected_output_id())

    def _selected_output_id(self):
        output_id = self.get_custom_option('mqtt_output', default_return=self.mqtt_output_id)
        if output_id in [None, '', 'None']:
            return None
        return str(output_id)

    def _load_output_settings(self):
        output_id = self._selected_output_id()
        if not output_id:
            raise ValueError(
                "No MQTT Output selected. Configure the 'MQTT Output' option in the Input settings."
            )

        output = db_retrieve_table_daemon(Output, unique_id=output_id)
        if not output:
            raise ValueError("Selected MQTT Output '{}' was not found".format(output_id))

        channel = db_retrieve_table_daemon(OutputChannel).filter(
            OutputChannel.output_id == output_id
        ).first()
        channel_options = self._load_json(channel.custom_options if channel else '')

        self.mqtt_hostname = channel_options.get('hostname', 'localhost')
        self.mqtt_port = int(channel_options.get('port', 1883))
        self.mqtt_keepalive = 60
        self.resp_topic = channel_options.get('resp_topic', 'esp32/pump/resp')
        self.status_topic = channel_options.get('status_topic', 'esp32/pump/status')

        self.mqtt_clientid = 'pump_evt_{}'.format(self.unique_id.split('-')[0])

        use_auth = self._coerce_bool(channel_options.get('use_auth'))
        if use_auth:
            self.mqtt_username = channel_options.get('username') or None
            self.mqtt_password = channel_options.get('password') or None
        else:
            self.mqtt_username = None
            self.mqtt_password = None

    def _append_connection_record(self, state, rc):
        append_mqtt_record(
            topic=self.status_topic or 'esp32/pump/status',
            payload={
                'type': 'mqtt_connection',
                'state': state,
                'rc': rc,
                'input_id': self.unique_id,
                'output_id': self._selected_output_id()
            },
            source='mqtt_input_connection'
        )

    def _check_for_errors(self, payload):
        if not isinstance(payload, dict):
            return

        msg_type = payload.get('type', '')
        pump_id = payload.get('pump_id', '?')
        reason = payload.get('reason', '')

        # Request refused by the controller (bad pump, busy, invalid batch, ...)
        if msg_type == 'pump_response' and payload.get('ok') is False:
            self.logger.error(
                "Pump request failed: pump_id={}, request_id={}, error={}, message={}".format(
                    pump_id, payload.get('request_id', '?'),
                    payload.get('error', '?'), payload.get('message', '')
                )
            )

        # Single pump command rejected by the motor scheduler
        elif msg_type == 'pump_event' and payload.get('state') == 'rejected':
            self.logger.error(
                "Pump command rejected: pump_id={}, request_id={}, reason={}".format(
                    pump_id, payload.get('request_id', '?'), reason
                )
            )

        # A step inside a batch failed
        elif msg_type == 'batch_item_event' and payload.get('state') == 'failed':
            self.logger.error(
                "Batch step failed: request_id={}, item_id={}, pump_id={}, reason={}".format(
                    payload.get('request_id', '?'),
                    payload.get('item_id', '?'),
                    pump_id,
                    reason
                )
            )

        # Entire batch aborted mid-run
        elif msg_type == 'batch_event' and payload.get('state') == 'aborted':
            self.logger.error(
                "Batch aborted: request_id={}, reason={}, failed_index={}".format(
                    payload.get('request_id', '?'),
                    reason,
                    payload.get('failed_index', '?')
                )
            )

    def _store_status_measurements(self, payload):
        measurement = {}
        timestamp_utc = datetime.datetime.utcnow()

        channel_values = {
            0: self._online_value(payload),
            1: self._coerce_bool(self._extract_status_value(payload, 'motor_busy')),
            2: self._coerce_float(self._extract_status_value(payload, 'wifi_rssi')),
            3: self._coerce_float(self._extract_status_value(payload, 'free_heap')),
        }

        for channel, value in channel_values.items():
            if value is None:
                continue

            measurement[channel] = {
                'measurement': self.channels_measurement[channel].measurement,
                'unit': self.channels_measurement[channel].unit,
                'value': value,
                'timestamp_utc': timestamp_utc
            }

        if not measurement:
            return

        message, measurement = run_input_actions(
            self.unique_id,
            '',
            measurement,
            self.log_level_debug
        )

        add_measurements_influxdb(
            self.unique_id,
            measurement,
            use_same_timestamp=INPUT_INFORMATION['measurements_use_same_timestamp']
        )

    def _online_value(self, payload):
        online = self._extract_status_value(payload, 'online')
        if online is not None:
            return self._coerce_bool(online)

        state = self._extract_status_value(payload, 'state')
        if state is None:
            return None

        if isinstance(state, str):
            normalized_state = state.strip().lower()
            if normalized_state in ['online', 'ready', 'alive', 'connected']:
                return 1.0
            if normalized_state in ['offline', 'disconnected']:
                return 0.0

        return self._coerce_bool(state)

    def _extract_status_value(self, payload, key):
        for container_key in [None, 'status', 'controller', 'data']:
            if container_key is None:
                container = payload
            else:
                container = payload.get(container_key)

            if isinstance(container, dict) and key in container:
                return container[key]

        return None

    def _load_json(self, value):
        if not value:
            return {}
        if isinstance(value, dict):
            return value
        return json.loads(value)

    def _coerce_bool(self, value):
        if isinstance(value, bool):
            return 1.0 if value else 0.0

        if isinstance(value, (int, float)):
            return 1.0 if value else 0.0

        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in ['1', 'true', 'yes', 'on', 'busy']:
                return 1.0
            if normalized in ['0', 'false', 'no', 'off', 'idle']:
                return 0.0

        return None

    def _coerce_float(self, value):
        if value in [None, '']:
            return None

        try:
            return float(value)
        except (TypeError, ValueError):
            return None