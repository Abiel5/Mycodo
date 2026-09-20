# coding=utf-8
#
# grow_history.py - SQLite logger for sensor readings and dosing events.
#
# Tables are created automatically on first use.
# DB path: /opt/Mycodo/mycodo/databases/grow_history.db
#
import sqlite3
import threading
import time

DB_PATH = '/opt/Mycodo/mycodo/databases/grow_history.db'

_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _ensure_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sensor_readings (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ts           REAL    NOT NULL,
            ph           REAL,
            ec           REAL,
            water_temp_f REAL,
            air_temp_f   REAL,
            air_humidity REAL
        );
        CREATE TABLE IF NOT EXISTS dosing_events (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            ts             REAL    NOT NULL,
            trigger        TEXT    NOT NULL,
            pump_id        TEXT    NOT NULL,
            volume_ml      REAL    NOT NULL,
            grow_stage     TEXT,
            strength_factor REAL,
            ph_before      REAL,
            batch_id       TEXT
        );
    """)
    conn.commit()


def log_sensor_reading(ph=None, ec=None, water_temp_f=None, air_temp_f=None, air_humidity=None):
    """Record one sensor poll cycle. Pass None for any sensor that failed."""
    with _lock:
        conn = _conn()
        try:
            _ensure_schema(conn)
            conn.execute(
                "INSERT INTO sensor_readings "
                "(ts, ph, ec, water_temp_f, air_temp_f, air_humidity) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), ph, ec, water_temp_f, air_temp_f, air_humidity)
            )
            conn.commit()
        finally:
            conn.close()


def log_dosing_event(trigger, pump_id, volume_ml,
                     grow_stage=None, strength_factor=None,
                     ph_before=None, batch_id=None):
    """
    Record one pump dosing event.

    trigger       — 'nutrient', 'ph_auto', or 'manual'
    pump_id       — ESP32 pump ID string (e.g. 'phdown', 'micro')
    volume_ml     — volume dispensed
    grow_stage    — growth stage string, if known (nutrient doses)
    strength_factor — GH Flora strength multiplier, if applicable
    ph_before     — pH reading that triggered the dose (ph_auto doses)
    batch_id      — batch ID linking items in a nutrient batch
    """
    with _lock:
        conn = _conn()
        try:
            _ensure_schema(conn)
            conn.execute(
                "INSERT INTO dosing_events "
                "(ts, trigger, pump_id, volume_ml, grow_stage, strength_factor, ph_before, batch_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), trigger, pump_id, volume_ml,
                 grow_stage, strength_factor, ph_before, batch_id)
            )
            conn.commit()
        finally:
            conn.close()
