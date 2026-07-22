import sqlite3
import json
from src.core.config import settings
from src.core.logging import logger
from typing import Dict, Any

def get_connection():
    # Parse the file path from the sqlite URL
    db_path = settings.sqlite_db_path.replace("sqlite:///", "")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL mode: readers never block writers and writers never block readers.
    # Critical for concurrent API gateway + evaluation worker access.
    # NORMAL sync is safe with WAL and significantly faster than FULL.
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
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
                judge_score REAL DEFAULT 0.0,
                semantic_equivalence REAL DEFAULT 0.0,
                judge_reasoning TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Migrations for existing DB schema
        try:
            cursor.execute("ALTER TABLE evaluations ADD COLUMN judge_score REAL DEFAULT 0.0")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE evaluations ADD COLUMN semantic_equivalence REAL DEFAULT 0.0")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE evaluations ADD COLUMN judge_reasoning TEXT")
        except Exception:
            pass

        # Experiment events table for audit trail
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS experiment_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                details TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Experiment lifecycle table: tracks start, stop, and promotion outcome
        # for each named experiment. Answers: "when did we start? what happened?"
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS experiments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                control_model TEXT NOT NULL,
                challenger_model TEXT NOT NULL,
                status TEXT DEFAULT 'running',
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP,
                outcome TEXT
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
                challenger_model, challenger_response, challenger_latency_ms,
                judge_score, semantic_equivalence, judge_reasoning
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            eval_data.get('trace_id'),
            eval_data.get('prompt'),
            eval_data.get('control_model'),
            eval_data.get('control_response'),
            eval_data.get('control_latency_ms'),
            eval_data.get('challenger_model'),
            eval_data.get('challenger_response'),
            eval_data.get('challenger_latency_ms'),
            eval_data.get('judge_score', 0.0),
            eval_data.get('semantic_equivalence', 0.0),
            eval_data.get('judge_reasoning', '')
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

# Initialize schema is now done via FastAPI lifespan in main.py

def get_recent_dead_letters(limit: int = 50) -> list:
    """Fetch recent items from the dead-letter queue for the admin endpoint."""
    from src.storage.redis_store import redis_client
    import asyncio
    import json
    async def _fetch():
        raw = await redis_client.lrange("llm_shadow_queue:dead_letter", 0, limit - 1)
        return [json.loads(r) for r in raw]
    # Run in a new event loop if called from sync context
    try:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(_fetch())
    except RuntimeError:
        return asyncio.run(_fetch())
