"""
db/database.py — SQLite schema and async initialisation via aiosqlite.

All tables use INTEGER PRIMARY KEY AUTOINCREMENT so SQLite auto-generates IDs.
JSON blobs are stored as TEXT — cheap, schema-less, and perfectly readable.
"""
import aiosqlite
from config import settings

DB_PATH: str = settings.database_url

# ── DDL ──────────────────────────────────────────────────────────────────────

_CREATE_OBSERVATIONS = """
CREATE TABLE IF NOT EXISTS observations (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT    NOT NULL,
    data      TEXT    NOT NULL          -- JSON telemetry snapshot
)
"""

_CREATE_ACTIONS = """
CREATE TABLE IF NOT EXISTS actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    action_type TEXT    NOT NULL,
    params      TEXT    NOT NULL,       -- JSON
    result      TEXT,                   -- JSON
    success     INTEGER DEFAULT 1       -- 0/1 bool
)
"""

_CREATE_DECISIONS = """
CREATE TABLE IF NOT EXISTS decisions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TEXT    NOT NULL,
    intention         TEXT    NOT NULL,
    reason            TEXT    NOT NULL,
    actions           TEXT    NOT NULL, -- JSON list of {tool, args, result}
    reflection        TEXT    DEFAULT '',
    reflection_score  REAL    DEFAULT NULL,
    telemetry_snapshot TEXT   DEFAULT '{}'
)
"""

_CREATE_EPISODES = """
CREATE TABLE IF NOT EXISTS episodes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT    NOT NULL,
    summary   TEXT    NOT NULL,
    outcome   TEXT,
    score     REAL    DEFAULT 0.5
)
"""

_CREATE_PATTERNS = """
CREATE TABLE IF NOT EXISTS patterns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    UNIQUE NOT NULL,
    description TEXT    NOT NULL,
    confidence  REAL    DEFAULT 0.5,
    occurrences INTEGER DEFAULT 1,
    last_seen   TEXT
)
"""

_CREATE_GOALS = """
CREATE TABLE IF NOT EXISTS goals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT    NOT NULL,
    priority    INTEGER DEFAULT 5,
    active      INTEGER DEFAULT 1,      -- 0/1 bool
    created_at  TEXT    NOT NULL
)
"""

_CREATE_POWER_STATS = """
CREATE TABLE IF NOT EXISTS daily_power_stats (
    date        TEXT PRIMARY KEY,       -- YYYY-MM-DD
    max_power_w REAL DEFAULT 0.0        -- Peak watts recorded that day
)
"""

_DEFAULT_GOALS = [
    ("Maximize solar utilisation while ensuring backup reliability", 8),
    ("Maintain battery health by avoiding deep discharge below 20% SOC", 9),
    ("Predict and prepare for power outages proactively", 7),
    ("Minimise unnecessary relay switches to reduce hardware wear", 6),
]

# ── PUBLIC API ────────────────────────────────────────────────────────────────

async def init_db() -> None:
    """Create all tables and seed default goals (idempotent)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        
        await db.execute(_CREATE_OBSERVATIONS)
        await db.execute(_CREATE_ACTIONS)
        await db.execute(_CREATE_DECISIONS)
        await db.execute(_CREATE_EPISODES)
        await db.execute(_CREATE_PATTERNS)
        await db.execute(_CREATE_GOALS)
        await db.execute(_CREATE_POWER_STATS)

        # Only seed goals if the table is empty
        async with db.execute("SELECT COUNT(*) FROM goals") as cur:
            (count,) = await cur.fetchone()

        if count == 0:
            await db.executemany(
                "INSERT INTO goals (description, priority, active, created_at) "
                "VALUES (?, ?, 1, datetime('now'))",
                _DEFAULT_GOALS,
            )

        await db.commit()
