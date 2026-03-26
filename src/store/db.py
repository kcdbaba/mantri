"""
SQLite schema initialisation and connection management.
"""

import sqlite3
import json
from pathlib import Path
from contextlib import contextmanager

from src.config import DB_PATH


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def transaction():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
-- Core task store

CREATE TABLE IF NOT EXISTS task_instances (
    id              TEXT PRIMARY KEY,
    template_id     TEXT,
    order_type      TEXT,
    client_id       TEXT,
    supplier_ids    TEXT,           -- JSON array
    created_at      INTEGER,
    last_updated    INTEGER,
    stage           TEXT,
    history_partial INTEGER DEFAULT 0,
    source          TEXT            -- 'bootstrap' | 'live' | 'manual_seed'
);

CREATE TABLE IF NOT EXISTS task_nodes (
    id              TEXT PRIMARY KEY,
    task_id         TEXT REFERENCES task_instances(id),
    node_type       TEXT,           -- 'agent_action' | 'real_world_milestone' | 'cadence' | 'decision' | 'human_review'
    name            TEXT,
    status          TEXT DEFAULT 'pending',  -- 'pending' | 'active' | 'completed' | 'blocked' | 'provisional'
    confidence      REAL,
    last_message_id TEXT,
    updated_at      INTEGER,
    updated_by      TEXT            -- 'agent' | actor_id for manual corrections
);

CREATE TABLE IF NOT EXISTS task_messages (
    id              TEXT PRIMARY KEY,
    task_id         TEXT REFERENCES task_instances(id),
    message_id      TEXT,
    group_id        TEXT,
    sender_jid      TEXT,
    body            TEXT,
    media_type      TEXT,
    timestamp       INTEGER,
    routing_confidence REAL
);

-- Router tables

CREATE TABLE IF NOT EXISTS entity_aliases (
    alias           TEXT,
    entity_id       TEXT,
    entity_type     TEXT,           -- 'client' | 'supplier' | 'officer' | 'item'
    confidence      REAL,
    source          TEXT,           -- 'bootstrap' | 'live' | 'manual'
    PRIMARY KEY (alias, entity_id)
);

CREATE TABLE IF NOT EXISTS task_routing_context (
    task_id         TEXT PRIMARY KEY REFERENCES task_instances(id),
    source_groups   TEXT,           -- JSON array of group JIDs
    entity_ids      TEXT,           -- JSON array
    delivery_location TEXT,
    key_dates       TEXT,           -- JSON
    item_types      TEXT,           -- JSON array
    officer_refs    TEXT,           -- JSON array
    context_text    TEXT,
    context_embedding BLOB          -- serialised numpy float32 array
);

-- Audit + improvement

CREATE TABLE IF NOT EXISTS audit_log (
    id              TEXT PRIMARY KEY,
    event_type      TEXT,           -- 'correction' | 'ambiguity_resolution' | 'alert_fired' | 'node_update'
    actor_id        TEXT,
    task_id         TEXT,
    node_id         TEXT,
    before_state    TEXT,           -- JSON
    after_state     TEXT,           -- JSON
    reason          TEXT,
    timestamp       INTEGER
);

CREATE TABLE IF NOT EXISTS ambiguity_queue (
    message_id      TEXT PRIMARY KEY,
    group_id        TEXT,
    body            TEXT,
    candidates      TEXT,           -- JSON: [{task_id, label}, ...]
    status          TEXT DEFAULT 'pending',  -- 'pending' | 'resolved' | 'expired'
    resolved_task_id TEXT,
    created_at      INTEGER,
    resolved_at     INTEGER
);

-- Usage / cost tracking

CREATE TABLE IF NOT EXISTS usage_log (
    id              TEXT PRIMARY KEY,
    call_type       TEXT,           -- 'update_agent' | 'vision_gemini' | 'whisper'
    message_id      TEXT,
    task_id         TEXT,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    cost_usd        REAL,
    duration_ms     INTEGER,
    model           TEXT,
    ts              INTEGER
);
"""

PRICING = {
    "claude-sonnet-4-6":   {"input": 3.00,   "output": 15.00},  # per 1M tokens
    "gemini-1.5-flash-8b": {"input": 0.0375, "output": 0.15},
}


def compute_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    if model not in PRICING:
        return 0.0
    p = PRICING[model]
    return (tokens_in * p["input"] + tokens_out * p["output"]) / 1_000_000


def init_schema():
    """Create all tables if they don't exist. Safe to call on every startup."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    print(f"Schema initialised at {DB_PATH}")


def seed_task(task: dict, nodes: list[dict], entity_aliases: list[dict]):
    """Insert the hardcoded MVP task instance, nodes, and entity aliases."""
    import time
    now = int(time.time())

    with transaction() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO task_instances
               (id, order_type, client_id, supplier_ids, created_at, last_updated, stage, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task["id"], task["order_type"], task["client_id"],
                json.dumps(task["supplier_ids"]), now, now, task["stage"], task["source"],
            ),
        )
        for node in nodes:
            conn.execute(
                """INSERT OR IGNORE INTO task_nodes
                   (id, task_id, node_type, name, status, updated_at, updated_by)
                   VALUES (?, ?, ?, ?, 'pending', ?, 'seed')""",
                (f"{task['id']}_{node['id']}", task["id"], node["type"], node["name"], now),
            )
        for alias in entity_aliases:
            conn.execute(
                """INSERT OR IGNORE INTO entity_aliases
                   (alias, entity_id, entity_type, confidence, source)
                   VALUES (?, ?, ?, ?, 'manual')""",
                (alias["alias"], alias["entity_id"], alias["entity_type"], 1.0),
            )

    print(f"Seeded task {task['id']} with {len(nodes)} nodes and {len(entity_aliases)} aliases")


if __name__ == "__main__":
    init_schema()

    # Seed the MVP task instance
    from src.config import SEED_TASK, ENTITY_ALIASES
    from src.agent.templates import STANDARD_PROCUREMENT_TEMPLATE

    nodes = STANDARD_PROCUREMENT_TEMPLATE["nodes"]
    aliases = [
        {"alias": alias, "entity_id": entity_id,
         "entity_type": "client" if "sata" in entity_id else "supplier"}
        for alias, entity_id in ENTITY_ALIASES.items()
    ]
    seed_task(SEED_TASK, nodes, aliases)
