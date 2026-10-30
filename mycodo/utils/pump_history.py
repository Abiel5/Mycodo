# coding=utf-8
import datetime
import json
import os
import sqlite3
import threading

from mycodo.config import DATABASE_PATH


PUMP_HISTORY_DIR = os.path.join(DATABASE_PATH, 'pump_history')
PUMP_HISTORY_FILE = os.path.join(PUMP_HISTORY_DIR, 'pump_history.jsonl')
PUMP_HISTORY_DB_FILE = os.path.join(PUMP_HISTORY_DIR, 'pump_history.db')

_WRITE_LOCK = threading.Lock()


def pump_history_file_path():
    return PUMP_HISTORY_FILE


def pump_history_db_path():
    return PUMP_HISTORY_DB_FILE


def append_batch_intent_record(batch_id,
                               function_id,
                               output_id,
                               gallons_added,
                               growth_stage,
                               strength_factor,
                               doses,
                               payload,
                               stop_on_error):
    return append_pump_history_record(
        record_type='batch_intent',
        batch_id=batch_id,
        event_type='batch_intent',
        source='mycodo_function',
        payload={
            'function_id': function_id,
            'output_id': output_id,
            'gallons_added': gallons_added,
            'growth_stage': growth_stage,
            'strength_factor': strength_factor,
            'doses': doses,
            'stop_on_error': stop_on_error,
            'batch_payload': payload
        }
    )


def append_mqtt_record(topic, payload, source='mqtt_input'):
    event_type = None
    batch_id = None

    if isinstance(payload, dict):
        event_type = payload.get('type') or payload.get('event') or payload.get('state')
        batch_id = payload.get('batch_id')

        if batch_id is None and isinstance(payload.get('batch'), dict):
            batch_id = payload['batch'].get('batch_id')

        # Batch events identify the batch by the request_id of the request that started it.
        if batch_id is None and event_type in ('batch_event', 'batch_item_event'):
            batch_id = payload.get('request_id')

    return append_pump_history_record(
        record_type='mqtt_event',
        batch_id=batch_id,
        topic=topic,
        event_type=event_type,
        source=source,
        payload=payload
    )


def append_pump_history_record(record_type,
                               payload,
                               batch_id=None,
                               topic=None,
                               event_type=None,
                               source=None):
    safe_payload = _json_safe(payload)
    record = {
        'recorded_at': datetime.datetime.utcnow().replace(microsecond=0).isoformat() + 'Z',
        'record_type': record_type,
        'batch_id': batch_id,
        'topic': topic,
        'event_type': event_type,
        'source': source,
        'payload': safe_payload
    }

    with _WRITE_LOCK:
        _ensure_history_dir()

        with open(PUMP_HISTORY_FILE, 'a', encoding='utf-8') as history_file:
            history_file.write(json.dumps(record, sort_keys=True))
            history_file.write('\n')

        _write_sqlite_record(record)

    return record


def _ensure_history_dir():
    if not os.path.isdir(PUMP_HISTORY_DIR):
        os.makedirs(PUMP_HISTORY_DIR)


def _write_sqlite_record(record):
    connection = sqlite3.connect(PUMP_HISTORY_DB_FILE, timeout=30)

    try:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA foreign_keys=ON')
        _ensure_database_schema(connection)
        _insert_raw_event(connection, record)

        if record['record_type'] == 'batch_intent':
            _upsert_batch_intent(connection, record)
        elif record['record_type'] == 'mqtt_event':
            _apply_mqtt_event(connection, record)

        connection.commit()
    finally:
        connection.close()


def _ensure_database_schema(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS batches (
            batch_id TEXT PRIMARY KEY,
            created_at TEXT,
            started_at TEXT,
            completed_at TEXT,
            status TEXT,
            gallons_added REAL,
            growth_stage TEXT,
            strength_factor REAL,
            stop_on_error INTEGER,
            function_id TEXT,
            output_id TEXT,
            controller_id TEXT,
            batch_payload_json TEXT,
            error_reason TEXT,
            last_updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS batch_items (
            batch_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            item_index INTEGER,
            pump_id TEXT,
            nutrient_name TEXT,
            cmd TEXT,
            state TEXT,
            requested_ml REAL,
            reported_ml REAL,
            estimated_ml REAL,
            result_source TEXT,
            raw_result TEXT,
            status_code TEXT,
            started_at TEXT,
            completed_at TEXT,
            failed_at TEXT,
            post_delay_ms INTEGER,
            error_reason TEXT,
            PRIMARY KEY (batch_id, item_id),
            FOREIGN KEY (batch_id) REFERENCES batches(batch_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS raw_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            batch_id TEXT,
            item_id TEXT,
            controller_id TEXT,
            topic TEXT,
            event_type TEXT,
            event_state TEXT,
            source TEXT,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS controller_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            controller_id TEXT,
            state TEXT,
            timestamp TEXT NOT NULL,
            wifi_rssi REAL,
            free_heap REAL,
            active_batch TEXT,
            motor_busy INTEGER,
            payload_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_raw_events_batch_id ON raw_events(batch_id);
        CREATE INDEX IF NOT EXISTS idx_raw_events_item_id ON raw_events(item_id);
        CREATE INDEX IF NOT EXISTS idx_batch_items_pump_id ON batch_items(batch_id, pump_id);
        CREATE INDEX IF NOT EXISTS idx_controller_status_timestamp ON controller_status(timestamp);
        """
    )


def _insert_raw_event(connection, record):
    payload = record['payload'] if isinstance(record['payload'], dict) else {}
    batch_id = record.get('batch_id') or _extract_value(
        payload,
        ['batch_id'],
        ['batch', 'batch_id'],
        ['data', 'batch_id']
    )
    item_id = _extract_value(
        payload,
        ['item_id'],
        ['item', 'item_id'],
        ['data', 'item_id']
    )
    controller_id = _extract_value(
        payload,
        ['controller_id'],
        ['controller', 'controller_id'],
        ['status', 'controller_id'],
        ['data', 'controller_id']
    )
    event_state = _extract_state(payload)

    connection.execute(
        """
        INSERT INTO raw_events (
            recorded_at,
            batch_id,
            item_id,
            controller_id,
            topic,
            event_type,
            event_state,
            source,
            payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record['recorded_at'],
            batch_id,
            item_id,
            controller_id,
            record.get('topic'),
            record.get('event_type'),
            event_state,
            record.get('source'),
            _dump_json(payload)
        )
    )


def _upsert_batch_intent(connection, record):
    payload = record['payload']
    batch_payload = payload.get('batch_payload', {})
    created_at = record['recorded_at']

    connection.execute(
        """
        INSERT INTO batches (
            batch_id,
            created_at,
            status,
            gallons_added,
            growth_stage,
            strength_factor,
            stop_on_error,
            function_id,
            output_id,
            batch_payload_json,
            last_updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(batch_id) DO UPDATE SET
            created_at = COALESCE(batches.created_at, excluded.created_at),
            gallons_added = COALESCE(batches.gallons_added, excluded.gallons_added),
            growth_stage = COALESCE(batches.growth_stage, excluded.growth_stage),
            strength_factor = COALESCE(batches.strength_factor, excluded.strength_factor),
            stop_on_error = COALESCE(batches.stop_on_error, excluded.stop_on_error),
            function_id = COALESCE(batches.function_id, excluded.function_id),
            output_id = COALESCE(batches.output_id, excluded.output_id),
            batch_payload_json = COALESCE(batches.batch_payload_json, excluded.batch_payload_json),
            status = COALESCE(batches.status, excluded.status),
            last_updated_at = excluded.last_updated_at
        """,
        (
            record['batch_id'],
            created_at,
            'created',
            _coerce_float(payload.get('gallons_added')),
            payload.get('growth_stage'),
            _coerce_float(payload.get('strength_factor')),
            _coerce_int(payload.get('stop_on_error')),
            payload.get('function_id'),
            payload.get('output_id'),
            _dump_json(batch_payload),
            created_at
        )
    )

    for item_index, command in enumerate(batch_payload.get('commands', []), start=1):
        item_id = command.get('item_id') or 'step-{}'.format(item_index)
        pump_id = command.get('pump_id')
        requested_ml = _requested_ml_from_command(command.get('cmd'))

        connection.execute(
            """
            INSERT INTO batch_items (
                batch_id,
                item_id,
                item_index,
                pump_id,
                nutrient_name,
                cmd,
                state,
                requested_ml,
                post_delay_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(batch_id, item_id) DO UPDATE SET
                item_index = COALESCE(batch_items.item_index, excluded.item_index),
                pump_id = COALESCE(batch_items.pump_id, excluded.pump_id),
                nutrient_name = COALESCE(batch_items.nutrient_name, excluded.nutrient_name),
                cmd = COALESCE(batch_items.cmd, excluded.cmd),
                state = COALESCE(batch_items.state, excluded.state),
                requested_ml = COALESCE(batch_items.requested_ml, excluded.requested_ml),
                post_delay_ms = COALESCE(batch_items.post_delay_ms, excluded.post_delay_ms)
            """,
            (
                record['batch_id'],
                item_id,
                item_index,
                pump_id,
                _nutrient_name_from_pump_id(pump_id),
                command.get('cmd'),
                'created',
                requested_ml,
                _coerce_int(command.get('post_delay_ms'))
            )
        )


def _apply_mqtt_event(connection, record):
    payload = record['payload'] if isinstance(record['payload'], dict) else {}
    event_type = (record.get('event_type') or '').strip()

    if event_type == 'batch_response':
        _update_batch_from_response(connection, record, payload)
    elif event_type == 'batch_event':
        _update_batch_from_event(connection, record, payload)
    elif event_type == 'batch_item_event':
        _update_batch_item_from_event(connection, record, payload)

    if _is_status_payload(record, payload):
        _insert_controller_status(connection, record, payload)


def _update_batch_from_response(connection, record, payload):
    batch_id = _extract_value(
        payload,
        ['batch_id'],
        ['batch', 'batch_id'],
        ['data', 'batch_id']
    ) or record.get('batch_id')
    if not batch_id:
        return

    accepted = _extract_value(payload, ['accepted'], ['ok'])
    status = _extract_state(payload)
    if status is None:
        if accepted is True:
            status = 'accepted'
        elif accepted is False:
            status = 'rejected'

    _upsert_batch_state(
        connection,
        batch_id=batch_id,
        status=status,
        event_time=record['recorded_at'],
        error_reason=_extract_error_reason(payload),
        controller_id=_extract_value(payload, ['controller_id'], ['controller', 'controller_id'])
    )


def _update_batch_from_event(connection, record, payload):
    batch_id = _extract_value(
        payload,
        ['batch_id'],
        ['batch', 'batch_id'],
        ['data', 'batch_id']
    ) or record.get('batch_id')
    if not batch_id:
        return

    _upsert_batch_state(
        connection,
        batch_id=batch_id,
        status=_extract_state(payload),
        event_time=record['recorded_at'],
        error_reason=_extract_error_reason(payload),
        controller_id=_extract_value(payload, ['controller_id'], ['controller', 'controller_id'])
    )


def _update_batch_item_from_event(connection, record, payload):
    batch_id = _extract_value(
        payload,
        ['batch_id'],
        ['batch', 'batch_id'],
        ['data', 'batch_id']
    ) or record.get('batch_id')
    if not batch_id:
        return

    item_id = _resolve_item_id(connection, batch_id, payload)
    if not item_id:
        item_id = 'unknown-item'

    pump_id = _extract_value(payload, ['pump_id'], ['item', 'pump_id'], ['data', 'pump_id'])
    cmd = _extract_value(payload, ['cmd'], ['item', 'cmd'], ['data', 'cmd'])
    state = _extract_state(payload)
    error_reason = _extract_error_reason(payload)
    requested_ml = _coerce_float(_extract_value(payload, ['requested_ml'], ['item', 'requested_ml']))
    reported_ml = _coerce_float(_extract_value(payload, ['reported_ml'], ['item', 'reported_ml']))
    estimated_ml = _coerce_float(_extract_value(payload, ['estimated_ml'], ['item', 'estimated_ml']))
    result_source = _extract_value(payload, ['result_source'], ['item', 'result_source'])
    raw_result = _extract_value(payload, ['raw_result'], ['item', 'raw_result'])
    status_code = _extract_value(payload, ['status_code'], ['item', 'status_code'])
    post_delay_ms = _coerce_int(_extract_value(payload, ['post_delay_ms'], ['item', 'post_delay_ms']))
    if requested_ml is None:
        requested_ml = _requested_ml_from_command(cmd)

    connection.execute(
        """
        INSERT INTO batch_items (
            batch_id,
            item_id,
            pump_id,
            nutrient_name,
            cmd,
            state,
            requested_ml,
            reported_ml,
            estimated_ml,
            result_source,
            raw_result,
            status_code,
            started_at,
            completed_at,
            failed_at,
            post_delay_ms,
            error_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(batch_id, item_id) DO UPDATE SET
            pump_id = COALESCE(excluded.pump_id, batch_items.pump_id),
            nutrient_name = COALESCE(batch_items.nutrient_name, excluded.nutrient_name),
            cmd = COALESCE(excluded.cmd, batch_items.cmd),
            state = COALESCE(excluded.state, batch_items.state),
            requested_ml = COALESCE(excluded.requested_ml, batch_items.requested_ml),
            reported_ml = COALESCE(excluded.reported_ml, batch_items.reported_ml),
            estimated_ml = COALESCE(excluded.estimated_ml, batch_items.estimated_ml),
            result_source = COALESCE(excluded.result_source, batch_items.result_source),
            raw_result = COALESCE(excluded.raw_result, batch_items.raw_result),
            status_code = COALESCE(excluded.status_code, batch_items.status_code),
            started_at = COALESCE(batch_items.started_at, excluded.started_at),
            completed_at = COALESCE(batch_items.completed_at, excluded.completed_at),
            failed_at = COALESCE(batch_items.failed_at, excluded.failed_at),
            post_delay_ms = COALESCE(excluded.post_delay_ms, batch_items.post_delay_ms),
            error_reason = COALESCE(excluded.error_reason, batch_items.error_reason)
        """,
        (
            batch_id,
            item_id,
            pump_id,
            _nutrient_name_from_pump_id(pump_id),
            cmd,
            state,
            requested_ml,
            reported_ml,
            estimated_ml,
            result_source,
            _dump_json(raw_result) if isinstance(raw_result, (dict, list)) else raw_result,
            status_code,
            _state_timestamp(state, 'started', record['recorded_at']),
            _state_timestamp(state, 'completed', record['recorded_at']),
            _state_timestamp(state, 'failed', record['recorded_at']),
            post_delay_ms,
            error_reason
        )
    )

    _update_batch_from_item_state(connection, batch_id, state, record['recorded_at'], error_reason)


def _upsert_batch_state(connection, batch_id, status, event_time, error_reason=None, controller_id=None):
    if not status and not error_reason and not controller_id:
        return

    started_at = event_time if status in ['accepted', 'running'] else None
    completed_at = event_time if status in ['completed', 'aborted', 'failed', 'rejected'] else None

    connection.execute(
        """
        INSERT INTO batches (
            batch_id,
            started_at,
            completed_at,
            status,
            controller_id,
            error_reason,
            last_updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(batch_id) DO UPDATE SET
            started_at = COALESCE(batches.started_at, excluded.started_at),
            completed_at = COALESCE(excluded.completed_at, batches.completed_at),
            status = COALESCE(excluded.status, batches.status),
            controller_id = COALESCE(excluded.controller_id, batches.controller_id),
            error_reason = COALESCE(excluded.error_reason, batches.error_reason),
            last_updated_at = excluded.last_updated_at
        """,
        (
            batch_id,
            started_at,
            completed_at,
            status,
            controller_id,
            error_reason,
            event_time
        )
    )


def _update_batch_from_item_state(connection, batch_id, item_state, event_time, error_reason=None):
    if item_state in ['running', 'post_delay']:
        _upsert_batch_state(connection, batch_id, 'running', event_time)
    elif item_state == 'failed':
        _upsert_batch_state(connection, batch_id, 'failed', event_time, error_reason=error_reason)


def _insert_controller_status(connection, record, payload):
    connection.execute(
        """
        INSERT INTO controller_status (
            controller_id,
            state,
            timestamp,
            wifi_rssi,
            free_heap,
            active_batch,
            motor_busy,
            payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _extract_value(payload, ['controller_id'], ['controller', 'controller_id'], ['status', 'controller_id']),
            _extract_state(payload),
            record['recorded_at'],
            _coerce_float(_extract_value(payload, ['wifi_rssi'], ['status', 'wifi_rssi'], ['data', 'wifi_rssi'])),
            _coerce_float(_extract_value(payload, ['free_heap'], ['status', 'free_heap'], ['data', 'free_heap'])),
            _extract_value(payload, ['active_batch'], ['status', 'active_batch'], ['data', 'active_batch']),
            _coerce_int(_coerce_bool(_extract_value(payload, ['motor_busy'], ['status', 'motor_busy'], ['data', 'motor_busy']))),
            _dump_json(payload)
        )
    )


def _resolve_item_id(connection, batch_id, payload):
    explicit_item_id = _extract_value(payload, ['item_id'], ['item', 'item_id'], ['data', 'item_id'])
    if explicit_item_id:
        return explicit_item_id

    pump_id = _extract_value(payload, ['pump_id'], ['item', 'pump_id'], ['data', 'pump_id'])
    if not pump_id:
        return None

    rows = connection.execute(
        """
        SELECT item_id
        FROM batch_items
        WHERE batch_id = ? AND pump_id = ?
        """,
        (batch_id, pump_id)
    ).fetchall()

    if len(rows) == 1:
        return rows[0][0]

    return None


def _is_status_payload(record, payload):
    topic = record.get('topic') or ''
    if topic.endswith('/status'):
        return True

    return any(
        _extract_value(payload, path) is not None
        for path in [
            ['wifi_rssi'],
            ['status', 'wifi_rssi'],
            ['free_heap'],
            ['status', 'free_heap'],
            ['motor_busy'],
            ['status', 'motor_busy']
        ]
    )


def _extract_state(payload):
    accepted = _extract_value(payload, ['accepted'], ['ok'])
    explicit_state = _extract_value(payload, ['state'], ['event'], ['status', 'state'], ['data', 'state'])
    if explicit_state not in [None, '']:
        return str(explicit_state)

    if accepted is True:
        return 'accepted'
    if accepted is False:
        return 'rejected'
    return None


def _extract_error_reason(payload):
    error_reason = _extract_value(
        payload,
        ['error_reason'],
        ['reason'],
        ['error'],
        ['message'],
        ['data', 'error_reason']
    )

    if isinstance(error_reason, (dict, list)):
        return _dump_json(error_reason)
    return error_reason


def _extract_value(payload, *paths):
    for path in paths:
        value = payload
        for key in path:
            if not isinstance(value, dict) or key not in value:
                value = None
                break
            value = value[key]

        if value is not None:
            return value

    return None


def _requested_ml_from_command(command):
    if not command:
        return None

    command_text = str(command).strip()
    if not command_text.upper().startswith('D,'):
        return None

    command_parts = command_text.split(',')
    if len(command_parts) < 2:
        return None

    return _coerce_float(command_parts[1])


def _nutrient_name_from_pump_id(pump_id):
    if pump_id in [None, '']:
        return None
    return str(pump_id)


def _state_timestamp(state, timestamp_type, recorded_at):
    state = (state or '').strip().lower()
    if timestamp_type == 'started' and state in ['accepted', 'running', 'post_delay']:
        return recorded_at
    if timestamp_type == 'completed' and state == 'completed':
        return recorded_at
    if timestamp_type == 'failed' and state == 'failed':
        return recorded_at
    return None


def _coerce_float(value):
    if value in [None, '']:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value):
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


def _coerce_int(value):
    if value in [None, '']:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dump_json(value):
    return json.dumps(_json_safe(value), sort_keys=True)


def _json_safe(value):
    if isinstance(value, dict):
        safe_dict = {}
        for key, item in value.items():
            safe_dict[str(key)] = _json_safe(item)
        return safe_dict

    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]

    if isinstance(value, datetime.datetime):
        return value.isoformat()

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    return str(value)