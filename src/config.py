"""
Sprint 3 MVP — hardcoded config for single seeded order.

Replace GROUP_JID_* values with real JIDs after Baileys connects.
To discover JIDs: run Baileys, send a test message to each group,
and read the group_id printed to stdout by the ingestion handler.
"""

# ---------------------------------------------------------------------------
# WhatsApp group JIDs → task routing
# ---------------------------------------------------------------------------

# Groups where messages route directly to a known task (Layer 2a)
# Value: task_id if dedicated group, None if shared group (use Layer 2b)
MONITORED_GROUPS: dict[str, str | None] = {
    "REPLACE_SATA_CLIENT_JID@g.us":    "task_001",   # SATA client group → task_001
    "REPLACE_ALL_STAFF_JID@g.us":      None,          # All-Staff → Layer 2b
    "REPLACE_KAPOOR_SUPPLIER_JID@g.us": None,         # Kapoor Steel supplier → Layer 2b
}

# ---------------------------------------------------------------------------
# Seeded task instance (manually inserted on first run)
# ---------------------------------------------------------------------------

SEED_TASK = {
    "id": "task_001",
    "order_type": "standard_procurement",
    "client_id": "entity_sata",
    "supplier_ids": ["entity_kapoor_steel"],
    "stage": "quote_requested",
    "source": "manual_seed",
}

# ---------------------------------------------------------------------------
# Entity alias dictionary (Layer 2b matching)
# ---------------------------------------------------------------------------

# Maps lowercased alias → canonical entity_id
ENTITY_ALIASES: dict[str, str] = {
    # SATA / Eastern Command variants
    "sata":                 "entity_sata",
    "51 sub area":          "entity_sata",
    "51 sa":                "entity_sata",
    "eastern command":      "entity_sata",
    "army":                 "entity_sata",

    # Kapoor Steel variants
    "kapoor":               "entity_kapoor_steel",
    "kapoor steel":         "entity_kapoor_steel",
    "kapoor ji":            "entity_kapoor_steel",
    "kapoor bhai":          "entity_kapoor_steel",
    "kapoor sahab":         "entity_kapoor_steel",
}

# Minimum rapidfuzz partial_ratio score to accept an alias match
ENTITY_MATCH_THRESHOLD = 80

# ---------------------------------------------------------------------------
# Routing confidence thresholds
# ---------------------------------------------------------------------------

DIRECT_GROUP_CONFIDENCE = 0.90     # Layer 2a: dedicated group → task
ENTITY_MATCH_CONFIDENCE = 0.75     # Layer 2b: entity keyword match
PROVISIONAL_THRESHOLD = 0.75       # Update agent: below this → provisional node update
AMBIGUITY_THRESHOLD = 0.50         # Below this → dead letter queue

# ---------------------------------------------------------------------------
# Update agent
# ---------------------------------------------------------------------------

CLAUDE_MODEL = "claude-sonnet-4-6"
MAX_CONTEXT_MESSAGES = 20          # last N messages per task passed to update agent
AGENT_MAX_TOKENS = 1024

# ---------------------------------------------------------------------------
# Alert engine
# ---------------------------------------------------------------------------

CRON_INTERVAL_SECONDS = 900        # 15 minutes
ALERT_LOG_PATH = "logs/alerts.log"
UNROUTED_LOG_PATH = "logs/unrouted.log"
AGENT_ERROR_LOG_PATH = "logs/agent_errors.log"
NEW_TASK_LOG_PATH = "logs/new_task_candidates.log"

# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------

REDIS_URL = "redis://localhost:6379"
INGEST_QUEUE_KEY = "ingest_queue"

# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

DB_PATH = "data/mantri.db"
