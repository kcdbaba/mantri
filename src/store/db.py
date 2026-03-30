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
    node_type       TEXT,           -- 'agent_action' | 'real_world_milestone' | 'auto_trigger' | 'time_trigger' | 'decision' | 'human_review'
    name            TEXT,
    status          TEXT DEFAULT 'pending',  -- 'pending' | 'active' | 'in_progress' | 'completed' | 'blocked' | 'provisional' | 'skipped' | 'failed' | 'partial'
    confidence      REAL,
    last_message_id TEXT,
    updated_at      INTEGER,
    updated_by      TEXT,           -- 'agent' | actor_id for manual corrections
    optional        INTEGER DEFAULT 0,  -- 1 = node is skipped by default until subgraph activated
    requires_all    TEXT,           -- JSON array of node IDs that must be completed; violation → failure alert
    warns_if_incomplete TEXT,       -- JSON array of node IDs; incompleteness → warning only
    node_data       TEXT,           -- JSON: accumulated structured extractions (items, prices, dates, etc.)
    linked_task_ids TEXT            -- JSON array of task IDs spawned from this node (e.g. client_notification)
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

-- Node ownership registry — makes agent→node ownership explicit and queryable
CREATE TABLE IF NOT EXISTS node_owner_registry (
    node_id         TEXT NOT NULL,
    order_type      TEXT NOT NULL,
    owner_agent     TEXT NOT NULL,   -- 'update_agent' | 'linkage_agent'
    ownership_type  TEXT NOT NULL,   -- 'exclusive_write' | 'read_only'
    PRIMARY KEY (node_id, order_type)
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
    id              TEXT PRIMARY KEY,   -- uuid
    message_id      TEXT,
    task_id         TEXT,
    node_id         TEXT,               -- node blocked by this ambiguity (if any)
    group_id        TEXT,
    body            TEXT,               -- original message body
    description     TEXT,               -- agent's description of the ambiguity
    severity        TEXT,               -- 'high' | 'medium' | 'low'
    category        TEXT,               -- 'entity' | 'quantity' | 'status' | 'timing' | 'linkage'
    escalation_target TEXT,             -- JSON array: ['ashish', 'senior_staff']
    blocking        INTEGER DEFAULT 0,  -- 1 = node_id is blocked until this resolves
    status          TEXT DEFAULT 'pending',  -- 'pending' | 'escalated' | 'resolved' | 'expired'
    escalated_at    INTEGER,
    re_escalation_count INTEGER DEFAULT 0,
    resolved_by     TEXT,
    resolution_note TEXT,
    created_at      INTEGER,
    resolved_at     INTEGER
);

-- Tracks which time_trigger alert instances have already fired (prevents duplicates)
CREATE TABLE IF NOT EXISTS task_alerts_fired (
    id              TEXT PRIMARY KEY,   -- uuid
    task_id         TEXT REFERENCES task_instances(id),
    node_id         TEXT,               -- e.g. 'supplier_predelivery_enquiry'
    alert_key       TEXT,               -- e.g. 'days_before_7' | 'elapsed_48h'
    fired_at        INTEGER
);

-- M:N item linkage tables

CREATE TABLE IF NOT EXISTS client_order_items (
    id              TEXT PRIMARY KEY,
    task_id         TEXT REFERENCES task_instances(id),
    description     TEXT,           -- informal natural language (Hindi/Hinglish)
    unit            TEXT,           -- e.g. 'kg', 'pcs', 'bags'
    quantity        REAL,
    specs           TEXT,           -- additional spec notes
    created_at      INTEGER
);

CREATE TABLE IF NOT EXISTS supplier_order_items (
    id              TEXT PRIMARY KEY,
    task_id         TEXT REFERENCES task_instances(id),
    description     TEXT,
    unit            TEXT,
    quantity        REAL,
    specs           TEXT,
    created_at      INTEGER
);

-- Dual-description fulfillment links (no shared item_id; matching is LLM-reasoned)
CREATE TABLE IF NOT EXISTS fulfillment_links (
    id                          TEXT PRIMARY KEY,
    client_order_id             TEXT REFERENCES task_instances(id),
    client_item_description     TEXT,
    supplier_order_id           TEXT REFERENCES task_instances(id),
    supplier_item_description   TEXT,
    quantity_allocated          REAL,
    match_confidence            REAL,   -- 0.0–1.0; >= 0.92 → auto-confirmed
    match_reasoning             TEXT,
    status                      TEXT DEFAULT 'candidate',  -- 'confirmed' | 'candidate' | 'failed' | 'auto_allocated'
    resolution_note             TEXT,   -- set when Ashish resolves ambiguity
    created_at                  INTEGER,
    updated_at                  INTEGER
);

-- Redis stream event log (for debugging / audit; primary stream is Redis task_events)
CREATE TABLE IF NOT EXISTS task_event_log (
    id              TEXT PRIMARY KEY,
    task_id         TEXT,
    event_type      TEXT,   -- 'node_updated' | 'message_processed' | 'linkage_updated'
    payload         TEXT,   -- JSON
    ts              INTEGER
);

-- Dead-letter queue for linkage_worker events that fail after all retries.
-- Developer-facing: inspect payload, fix root cause, replay by re-publishing
-- fields_json back to the task_events stream.
CREATE TABLE IF NOT EXISTS dead_letter_events (
    id              TEXT PRIMARY KEY,
    stream_key      TEXT NOT NULL,
    event_id        TEXT NOT NULL,
    fields_json     TEXT NOT NULL,  -- full Redis stream fields as JSON
    failure_reason  TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 1,
    first_failed_at INTEGER NOT NULL,
    last_failed_at  INTEGER NOT NULL,
    resolved        INTEGER NOT NULL DEFAULT 0  -- 1 once replayed / dismissed
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
    "claude-sonnet-4-6":          {"input": 3.00, "output": 15.00,
                                    "cache_write": 3.75, "cache_read": 0.30},
    "claude-haiku-4-5-20251001":  {"input": 0.80, "output": 4.00,
                                    "cache_write": 1.00, "cache_read": 0.08},
    "gemini-1.5-flash-8b":        {"input": 0.0375, "output": 0.15},
}  # per 1M tokens


def compute_cost(model: str, tokens_in: int, tokens_out: int,
                 cache_creation_tokens: int = 0, cache_read_tokens: int = 0) -> float:
    if model not in PRICING:
        return 0.0
    p = PRICING[model]
    # input_tokens from the API excludes cached tokens — it's already uncached only.
    # Cache tokens are billed separately at write (1.25×) or read (0.1×) rates.
    cost = (
        tokens_in * p["input"]
        + tokens_out * p["output"]
        + cache_creation_tokens * p.get("cache_write", p["input"])
        + cache_read_tokens * p.get("cache_read", p["input"])
    ) / 1_000_000
    return cost


def init_schema():
    """Create all tables if they don't exist. Safe to call on every startup."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()

    # Populate node_owner_registry from templates (idempotent)
    from src.agent.templates import TEMPLATES
    for order_type, tmpl in TEMPLATES.items():
        for node in tmpl["nodes"]:
            conn.execute(
                """INSERT OR IGNORE INTO node_owner_registry
                   (node_id, order_type, owner_agent, ownership_type)
                   VALUES (?, ?, ?, ?)""",
                (node["id"], order_type, node["owner"], "exclusive_write"),
            )
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
            default_status = "skipped" if node.get("optional") else "pending"
            conn.execute(
                """INSERT OR IGNORE INTO task_nodes
                   (id, task_id, node_type, name, status, updated_at, updated_by,
                    optional, requires_all, warns_if_incomplete)
                   VALUES (?, ?, ?, ?, ?, ?, 'seed', ?, ?, ?)""",
                (
                    f"{task['id']}_{node['id']}", task["id"], node["type"], node["name"],
                    default_status, now,
                    1 if node.get("optional") else 0,
                    json.dumps(node.get("requires_all", [])),
                    json.dumps(node.get("warns_if_incomplete", [])),
                ),
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
