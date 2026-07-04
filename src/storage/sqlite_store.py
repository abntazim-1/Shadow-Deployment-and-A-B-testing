import sqlite3
import json
from src.core.config import settings
from src.core.logging import logger
from typing import Dict, Any

def get_connection():
    # Parse the file path from the sqlite URL
    db_path = settings.sqlite_db_path.replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        # Evaluations table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT UNIQUE NOT NULL,
                prompt TEXT NOT NULL,
                control_model TEXT NOT NULL,
                control_response TEXT NOT NULL,
                control_latency_ms REAL NOT NULL,
                challenger_model TEXT NOT NULL,
                challenger_response TEXT NOT NULL,
                challenger_latency_ms REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Experiment events table for audit trail
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS experiment_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                details TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    except Exception as e:
        logger.error("Failed to initialize SQLite database", error=str(e))
    finally:
        conn.close()

def save_evaluation(eval_data: Dict[str, Any]):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO evaluations (
                trace_id, prompt, control_model, control_response, control_latency_ms,
                challenger_model, challenger_response, challenger_latency_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            eval_data.get('trace_id'),
            eval_data.get('prompt'),
            eval_data.get('control_model'),
            eval_data.get('control_response'),
            eval_data.get('control_latency_ms'),
            eval_data.get('challenger_model'),
            eval_data.get('challenger_response'),
            eval_data.get('challenger_latency_ms')
        ))
        conn.commit()
    except Exception as e:
        logger.error("Failed to save evaluation to SQLite", error=str(e))
    finally:
        conn.close()

def log_experiment_event(event_type: str, details: dict):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO experiment_events (event_type, details) VALUES (?, ?)
        """, (event_type, json.dumps(details)))
        conn.commit()
    except Exception as e:
        logger.error("Failed to log experiment event", error=str(e))
    finally:
        conn.close()

# Initialize schema
init_db()
