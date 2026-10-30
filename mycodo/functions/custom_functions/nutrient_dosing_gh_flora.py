# coding=utf-8
#
# nutrient_dosing_gh_flora.py - Mycodo Function: GH Flora Series Nutrient Dosing
#
# Computes Cal-Mag + FloraMicro + FloraGro + FloraBloom doses from gallons added
# and growth stage, then publishes a single batch MQTT command to the
# ESP32 pump controller.
#
# Drop this file in: /opt/Mycodo/mycodo/functions/
# Then add it in Mycodo UI: Setup → Function → Add Function
#
import json
from uuid import uuid4

from mycodo.databases.models import CustomController
from mycodo.functions.base_function import AbstractFunction
from mycodo.mycodo_client import DaemonControl
from mycodo.utils.database import db_retrieve_table_daemon
from mycodo.utils.pump_history import append_batch_intent_record

# GH Flora Basic Applications Table — full-strength ml/gal (1 tsp = 4.93 ml)
# strength_factor scales these down at dose time (default 0.33 = 1/3 strength)
STAGE_RECIPES = {
    'seedling':       {'gro': 1.25, 'micro': 1.25, 'bloom': 1.25},  # 1/4 tsp each
    'early_veg':      {'gro': 5.0,  'micro': 5.0,  'bloom': 5.0 },  # 1 tsp each
    'late_veg':       {'gro': 15.0, 'micro': 10.0, 'bloom': 10.0},  # 3/2/2 tsp
    'early_bloom':    {'gro': 10.0, 'micro': 10.0, 'bloom': 10.0},  # 2/2/2 tsp
    'mid_late_bloom': {'gro': 5.0,  'micro': 10.0, 'bloom': 15.0},  # 1/2/3 tsp
}

FUNCTION_INFORMATION = {
    'function_name_unique': 'nutrient_dosing_gh_flora',
    'function_name': 'Nutrient Dosing: GH Flora Series',

    'message': (
        'Automated nutrient dosing using GH Flora Series (Light Feed chart). '
        'Computes Cal-Mag + Micro + Gro + Bloom doses based on gallons added '
        'and growth stage, then sends a single batch MQTT command to the '
        'ESP32 pump controller for sequential execution with mixing delays.'
    ),

    'custom_options': [
        # ── MQTT Output ───────────────────────────────────────────────────────
        {
            'type': 'message',
            'default_value': (
                '-- MQTT Output --\n'
                'Select the Atlas EZO-PMP MQTT Output that owns the broker connection. '
                'Broker host, port, topic, and auth are read from that output at dose time.'
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
        # ── Pump ID Assignments ───────────────────────────────────────────────
        {
            'type': 'message',
            'default_value': '-- Pump Assignments (must match ESP32 pump IDs) --'
        },
        {
            'id': 'pump_id_calmag',
            'type': 'text',
            'default_value': 'pump_1',
            'required': True,
            'name': 'Cal-Mag Pump ID',
            'phrase': 'ESP32 pump ID for Cal-Mag'
        },
        {
            'id': 'pump_id_micro',
            'type': 'text',
            'default_value': 'pump_2',
            'required': True,
            'name': 'FloraMicro Pump ID',
            'phrase': 'ESP32 pump ID for FloraMicro'
        },
        {
            'id': 'pump_id_gro',
            'type': 'text',
            'default_value': 'pump_3',
            'required': True,
            'name': 'FloraGro Pump ID',
            'phrase': 'ESP32 pump ID for FloraGro'
        },
        {
            'id': 'pump_id_bloom',
            'type': 'text',
            'default_value': 'pump_4',
            'required': True,
            'name': 'FloraBloom Pump ID',
            'phrase': 'ESP32 pump ID for FloraBloom'
        },
        # ── Dosing Defaults ───────────────────────────────────────────────────
        {
            'type': 'message',
            'default_value': '-- Dosing Defaults --'
        },
        {
            'id': 'calmag_ml_per_gal',
            'type': 'float',
            'default_value': 3.8,
            'required': True,
            'name': 'Cal-Mag (ml/gal)',
            'phrase': 'Cal-Mag dose in ml per gallon — not stage-dependent'
        },
        {
            'id': 'strength_factor',
            'type': 'float',
            'default_value': 0.33,
            'required': True,
            'name': 'Strength Factor',
            'phrase': 'Multiplier applied to all nutrient doses (0.33 = light, 1.0 = full strength)'
        },
        # ── Mixing Delays ─────────────────────────────────────────────────────
        {
            'type': 'message',
            'default_value': (
                '-- Mixing Delays --\n'
                'Time to wait after each nutrient is dispensed before the next one runs. '
                    'These become per-command post_delay_ms in the batch payload.'
            )
        },
        {
            'id': 'delay_calmag_sec',
            'type': 'integer',
            'default_value': 600,
            'required': True,
            'name': 'Cal-Mag Delay (sec)',
            'phrase': 'Seconds to wait after Cal-Mag finishes before adding Micro (recommended: 300-600)'
        },
        {
            'id': 'delay_micro_sec',
            'type': 'integer',
            'default_value': 150,
            'required': True,
            'name': 'FloraMicro Delay (sec)',
            'phrase': 'Seconds to wait after Micro finishes before adding Gro (recommended: 120-180)'
        },
        {
            'id': 'delay_gro_sec',
            'type': 'integer',
            'default_value': 90,
            'required': True,
            'name': 'FloraGro Delay (sec)',
            'phrase': 'Seconds to wait after Gro finishes before adding Bloom (recommended: 60-90)'
        },
        {
            'id': 'delay_bloom_sec',
            'type': 'integer',
            'default_value': 90,
            'required': True,
            'name': 'FloraBloom Delay (sec)',
            'phrase': 'Seconds to wait after Bloom finishes (trailing delay, recommended: 60-90)'
        },
        # ── Batch Options ─────────────────────────────────────────────────────
        {
            'type': 'message',
            'default_value': '-- Batch Options --'
        },
        {
            'id': 'stop_on_error',
            'type': 'bool',
            'default_value': True,
            'name': 'Stop Batch on Error',
            'phrase': 'Abort remaining steps if any pump command fails on the ESP32'
        },
        {
            'id': 'batch_id_prefix',
            'type': 'text',
            'default_value': 'nutrient',
            'required': False,
            'name': 'Batch ID Prefix',
            'phrase': 'Prefix for generated batch IDs (e.g. "nutrient" → "nutrient-a1b2c3d4")'
        },
    ],

    'custom_commands_message': (
        'Select a growth stage and enter the gallons of fresh water added. '
        'Press "Preview Dose" to see the calculated volumes without sending, '
        'or "Calculate & Dose" to compute and immediately send the batch command.'
    ),

    'custom_commands': [
        {
            'id': 'gallons_added',
            'type': 'float',
            'default_value': 1.0,
            'name': 'Gallons Added',
            'phrase': 'Volume of fresh water added to the reservoir'
        },
        {
            'id': 'dose_stage',
            'type': 'select',
            'default_value': 'early_veg',
            'options_select': [
                ('seedling',       'Seedling'),
                ('early_veg',      'Early Veg'),
                ('late_veg',       'Late Veg'),
                ('early_bloom',    'Early Bloom'),
                ('mid_late_bloom', 'Mid/Late Bloom'),
            ],
            'name': 'Growth Stage',
            'phrase': 'Current growth stage — selects the GH Flora recipe'
        },
        {
            'id': 'strength_override',
            'type': 'float',
            'default_value': 0.0,
            'name': 'Strength Override (0 = use saved default)',
            'phrase': 'Override the configured strength factor for this dose only. Set to 0 to use the saved default.'
        },
        {
            'id': 'preview_dose',
            'type': 'button',
            'wait_for_return': True,
            'name': 'Preview Dose (no send)'
        },
        {
            'id': 'calculate_and_dose',
            'type': 'button',
            'wait_for_return': True,
            'name': 'Calculate & Dose'
        },
    ]
}


class CustomModule(AbstractFunction):
    """
    Nutrient dosing function for GH Flora Series (Light Feed).

    Computes Cal-Mag + Micro + Gro + Bloom doses from gallons added and
    growth stage, then publishes a single batch MQTT payload to the
    ESP32 pump controller for sequential execution with per-step mixing delays.
    """

    def __init__(self, function, testing=False):
        super().__init__(function, testing=testing, name=__name__)

        self.control = DaemonControl()

        # Pre-declare all custom_options attributes (populated by setup_custom_options)
        self.mqtt_output_id = None
        self.pump_id_calmag = None
        self.pump_id_micro = None
        self.pump_id_gro = None
        self.pump_id_bloom = None
        self.calmag_ml_per_gal = None
        self.strength_factor = None
        self.delay_calmag_sec = None
        self.delay_micro_sec = None
        self.delay_gro_sec = None
        self.delay_bloom_sec = None
        self.stop_on_error = None
        self.batch_id_prefix = None

        custom_function = db_retrieve_table_daemon(
            CustomController, unique_id=self.unique_id)
        self.setup_custom_options(
            FUNCTION_INFORMATION['custom_options'], custom_function)

        if not testing:
            self.try_initialize()

    def initialize(self):
        self.logger.info(
            "GH Flora dosing function initialized. "
            "Output ID: {}, pump assignments: Cal-Mag={}, Micro={}, Gro={}, Bloom={}".format(
                self.mqtt_output_id,
                self.pump_id_calmag, self.pump_id_micro,
                self.pump_id_gro, self.pump_id_bloom
            )
        )
        self.logger.info(
            "Dosing defaults: Cal-Mag={} ml/gal, strength={}, "
            "delays: Cal-Mag={}s, Micro={}s, Gro={}s, Bloom={}s".format(
                self.calmag_ml_per_gal, self.strength_factor,
                self.delay_calmag_sec, self.delay_micro_sec,
                self.delay_gro_sec, self.delay_bloom_sec
            )
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _selected_output_id(self):
        output_id = self.get_custom_option('mqtt_output', default_return=self.mqtt_output_id)
        if output_id in [None, '', 'None']:
            return None
        return str(output_id)

    def _publish_via_output(self, payload_json):
        """Publish a pre-built JSON payload through the selected Output controller."""
        output_id = self._selected_output_id()

        if not output_id:
            raise ValueError(
                "No MQTT Output selected. Configure the 'MQTT Output' option in the Function settings."
            )

        self.logger.debug(
            "Publishing prebuilt batch via Output controller {}".format(output_id)
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
                result = result['message']
            else:
                result = str(result)

        if result in [None, '']:
            return "Output send_batch_command completed without a return message"

        result = str(result)
        if result.lower().startswith('error'):
            raise ValueError(result)
        return result

    def _compute_doses(self, gallons, stage, strength):
        recipe = STAGE_RECIPES[stage]
        doses = {
            'calmag': round(gallons * self.calmag_ml_per_gal, 2),
            'micro':  round(gallons * recipe['micro'] * strength, 2),
            'gro':    round(gallons * recipe['gro']   * strength, 2),
            'bloom':  round(gallons * recipe['bloom']  * strength, 2),
        }
        total = round(sum(doses.values()), 2)
        self.logger.debug(
            "Dose computed: gallons={:.2f}, stage={}, strength={:.2f} | "
            "recipe ml/gal: Micro={}, Gro={}, Bloom={} | "
            "doses: Cal-Mag={:.2f}ml, Micro={:.2f}ml, Gro={:.2f}ml, Bloom={:.2f}ml | "
            "total={:.2f}ml".format(
                gallons, stage, strength,
                recipe['micro'], recipe['gro'], recipe['bloom'],
                doses['calmag'], doses['micro'], doses['gro'], doses['bloom'],
                total
            )
        )
        return doses

    def _build_payload(self, doses):
        prefix = self.batch_id_prefix or 'nutrient'
        payload_dict = {
            'v': 1,
            'type': 'batch',
            'request_id': '{}-{}'.format(prefix, uuid4().hex[:8]),
            'options': {
                'stop_on_error': bool(self.stop_on_error),
            },
            'commands': [
                {
                    'item_id': 'step-1',
                    'pump_id': self.pump_id_calmag,
                    'cmd': 'D,{:.2f}'.format(doses['calmag']),
                    'post_delay_ms': int(self.delay_calmag_sec) * 1000
                },
                {
                    'item_id': 'step-2',
                    'pump_id': self.pump_id_micro,
                    'cmd': 'D,{:.2f}'.format(doses['micro']),
                    'post_delay_ms': int(self.delay_micro_sec) * 1000
                },
                {
                    'item_id': 'step-3',
                    'pump_id': self.pump_id_gro,
                    'cmd': 'D,{:.2f}'.format(doses['gro']),
                    'post_delay_ms': int(self.delay_gro_sec) * 1000
                },
                {
                    'item_id': 'step-4',
                    'pump_id': self.pump_id_bloom,
                    'cmd': 'D,{:.2f}'.format(doses['bloom']),
                    'post_delay_ms': int(self.delay_bloom_sec) * 1000
                },
            ]
        }
        self.logger.debug(
            "Batch payload built: request_id={}, stop_on_error={}, commands={}".format(
                payload_dict['request_id'],
                payload_dict['options']['stop_on_error'],
                    ['{pump_id}:{cmd} (post-delay {post_delay_ms}ms)'.format(**c)
                 for c in payload_dict['commands']]
            )
        )
        return json.dumps(payload_dict)

    def _format_preview(self, gallons, stage, strength, doses):
        return (
            "Dose preview\n"
            "  Gallons : {gal:.2f}\n"
            "  Stage   : {stage}\n"
            "  Strength: {str:.2f}\n"
            "\n"
            "  Cal-Mag : {calmag:.2f} ml  →  wait {dc}s\n"
            "  Micro   : {micro:.2f} ml  →  wait {dm}s\n"
            "  Gro     : {gro:.2f} ml  →  wait {dg}s\n"
            "  Bloom   : {bloom:.2f} ml  →  wait {db}s"
        ).format(
            gal=gallons,
            stage=stage,
            str=strength,
            calmag=doses['calmag'], dc=self.delay_calmag_sec,
            micro=doses['micro'],   dm=self.delay_micro_sec,
            gro=doses['gro'],       dg=self.delay_gro_sec,
            bloom=doses['bloom'],   db=self.delay_bloom_sec,
        )

    def _resolve_inputs(self, args_dict):
        """Parse and validate common action inputs. Returns (gallons, stage, strength) or raises ValueError."""
        gallons  = float(args_dict.get('gallons_added', 1.0))
        stage    = args_dict.get('dose_stage', 'early_veg')
        override = float(args_dict.get('strength_override', 0.0))
        strength = override if override > 0 else float(self.strength_factor)

        if gallons <= 0:
            raise ValueError("gallons_added must be greater than 0")
        if stage not in STAGE_RECIPES:
            raise ValueError("Unknown growth stage: '{}'".format(stage))

        return gallons, stage, strength

    # ──────────────────────────────────────────────────────────────────────────
    # Custom command handlers (wired to UI buttons)
    # ──────────────────────────────────────────────────────────────────────────

    def preview_dose(self, args_dict):
        """Show calculated dose volumes without sending anything."""
        try:
            gallons, stage, strength = self._resolve_inputs(args_dict)
        except ValueError as err:
            self.logger.warning("Preview rejected: {}".format(err))
            return "Error: {}".format(err)

        self.logger.info(
            "Dose preview requested: gallons={:.2f}, stage={}, strength={:.2f}".format(
                gallons, stage, strength
            )
        )
        doses = self._compute_doses(gallons, stage, strength)
        return self._format_preview(gallons, stage, strength, doses)

    def calculate_and_dose(self, args_dict):
        """Compute doses and publish a single batch MQTT command."""
        try:
            gallons, stage, strength = self._resolve_inputs(args_dict)
        except ValueError as err:
            return "Error: {}".format(err)

        doses   = self._compute_doses(gallons, stage, strength)
        preview = self._format_preview(gallons, stage, strength, doses)
        payload = self._build_payload(doses)
        payload_dict = json.loads(payload)
        output_id = self._selected_output_id()

        total_ml = round(sum(doses.values()), 2)
        total_delay_sec = (
            int(self.delay_calmag_sec) + int(self.delay_micro_sec) +
            int(self.delay_gro_sec) + int(self.delay_bloom_sec)
        )
        self.logger.info(
            "Nutrient dose starting: gallons={:.2f}, stage={}, strength={:.2f}, "
            "total_nutrients={:.2f}ml, estimated_duration={}s (delays only)".format(
                gallons, stage, strength, total_ml, total_delay_sec
            )
        )
        self.logger.info(
            "Dose breakdown: Cal-Mag={:.2f}ml, Micro={:.2f}ml, "
            "Gro={:.2f}ml, Bloom={:.2f}ml".format(
                doses['calmag'], doses['micro'], doses['gro'], doses['bloom']
            )
        )
        try:
            append_batch_intent_record(
                batch_id=payload_dict['request_id'],
                function_id=self.unique_id,
                output_id=output_id,
                gallons_added=gallons,
                growth_stage=stage,
                strength_factor=strength,
                doses=doses,
                payload=payload_dict,
                stop_on_error=payload_dict['options']['stop_on_error']
            )
        except Exception as err:
            self.logger.warning(
                "Unable to persist batch intent for {}: {}".format(
                    payload_dict['request_id'], err
                )
            )

        self.logger.debug("Publishing payload via output {}".format(output_id))

        try:
            publish_result = self._publish_via_output(payload)
            try:
                from mycodo.utils.grow_history import log_dosing_event
                for pid, ml in [
                    (self.pump_id_calmag, doses['calmag']),
                    (self.pump_id_micro,  doses['micro']),
                    (self.pump_id_gro,    doses['gro']),
                    (self.pump_id_bloom,  doses['bloom']),
                ]:
                    log_dosing_event(
                        trigger='nutrient',
                        pump_id=pid,
                        volume_ml=ml,
                        grow_stage=stage,
                        strength_factor=strength,
                        batch_id=payload_dict['request_id'],
                    )
            except Exception as _he:
                self.logger.warning("grow_history dosing log failed: {}".format(_he))
            self.logger.info(
                "Batch submitted via output {} successfully: {} | Cal-Mag={:.2f}ml, Micro={:.2f}ml, "
                "Gro={:.2f}ml, Bloom={:.2f}ml, total={:.2f}ml".format(
                    output_id,
                    publish_result,
                    doses['calmag'], doses['micro'],
                    doses['gro'], doses['bloom'], total_ml
                )
            )
        except Exception as err:
            self.logger.error(
                "Publish failed: {} | doses were: Cal-Mag={:.2f}ml, "
                "Micro={:.2f}ml, Gro={:.2f}ml, Bloom={:.2f}ml".format(
                    err, doses['calmag'], doses['micro'],
                    doses['gro'], doses['bloom']
                )
            )
            return "Error publishing batch: {}\n\n{}".format(err, preview)

        return "Batch sent via output {}.\n{}\n\n{}".format(
            output_id,
            publish_result,
            preview
        )
