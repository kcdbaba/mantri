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
# Ambiguity escalation config
# ---------------------------------------------------------------------------
#
# Profiles control how aggressively the system escalates ambiguity to humans.
# "ashish_current" reflects Ashish's stated preference from interview (2026-03-28):
#   medium+ always escalated, low highlighted to staff+Ashish, nothing silently hidden.
#
# Dimensions:
#   escalation_threshold_high  — agent confidence below this → medium ambiguity → escalate to Ashish
#   escalation_threshold_low   — agent confidence below this → low ambiguity → escalate to staff+Ashish
#   blocking_threshold         — agent confidence below this on a gate node → set node to blocked
#   silent_resolution_allowed  — if False, every ambiguity flag is surfaced regardless of confidence
#   escalation_rate_limit      — max ambiguity escalations per task per hour (None = unlimited)
#   resolution_timeout_high_s  — seconds before medium+ unresolved ambiguity re-escalates
#   resolution_timeout_low_s   — seconds before low unresolved ambiguity auto-resolves provisional
#   block_scope                — which node types can be blocked: "gate_only" | "all"
#
# Category-level overrides can supplement the profile for fine-grained control.
# Categories: entity | quantity | status | timing | linkage

ESCALATION_PROFILES = {
    "ashish_current": {
        "escalation_threshold_high":  0.85,   # medium+ ambiguity
        "escalation_threshold_low":   0.65,   # low ambiguity
        "blocking_threshold":         0.65,   # block gate nodes below this
        "silent_resolution_allowed":  False,  # never silently hide
        "escalation_rate_limit":      None,   # unlimited
        "resolution_timeout_high_s":  1800,   # 30 min before re-escalation
        "resolution_timeout_low_s":   14400,  # 4 hours before auto-provisional
        "block_scope":                "gate_only",
        "escalation_target_high":     ["ashish"],
        "escalation_target_low":      ["senior_staff", "ashish"],
    },
    "balanced": {
        "escalation_threshold_high":  0.80,
        "escalation_threshold_low":   0.60,
        "blocking_threshold":         0.55,
        "silent_resolution_allowed":  False,
        "escalation_rate_limit":      10,
        "resolution_timeout_high_s":  3600,
        "resolution_timeout_low_s":   28800,
        "block_scope":                "gate_only",
        "escalation_target_high":     ["ashish"],
        "escalation_target_low":      ["senior_staff", "ashish"],
    },
    "high_trust": {
        "escalation_threshold_high":  0.70,
        "escalation_threshold_low":   0.50,
        "blocking_threshold":         0.45,
        "silent_resolution_allowed":  True,
        "escalation_rate_limit":      3,
        "resolution_timeout_high_s":  7200,
        "resolution_timeout_low_s":   86400,
        "block_scope":                "gate_only",
        "escalation_target_high":     ["ashish"],
        "escalation_target_low":      ["senior_staff"],
    },
}

# Active profile — change this to switch system-wide escalation behaviour
ACTIVE_ESCALATION_PROFILE = "ashish_current"

# Per-category overrides applied on top of the active profile (optional)
# e.g. {"entity": {"blocking_threshold": 0.75}} to block harder on entity ambiguity
ESCALATION_CATEGORY_OVERRIDES: dict[str, dict] = {
    "entity":   {"blocking_threshold": 0.75},   # wrong entity = wrong order; block earlier
    "linkage":  {"blocking_threshold": 0.75},   # wrong M:N link = wrong delivery
}

# Gate nodes — ambiguity on these triggers blocking (when block_scope="gate_only")
GATE_NODES = {
    "order_confirmation",
    "order_ready",
    "dispatched",
    "supplier_QC",
}

# ---------------------------------------------------------------------------
# Update agent
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Task creation
# ---------------------------------------------------------------------------

ENABLE_LIVE_TASK_CREATION = True     # Per-order task creation active
ENABLE_CONVERSATION_ROUTING = False  # Conversation routing for shared groups (AS agent)

# API gate: controls whether LLM API calls are permitted.
# True in production. False in dev/staging/testing (default).
# Test infra sets True only for --run-live runs.
import os
PERMIT_API = os.environ.get("MANTRI_PERMIT_API", "false").lower() == "true"

# ---------------------------------------------------------------------------
# Update agent
# ---------------------------------------------------------------------------

CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_MODEL_FAST = "claude-haiku-4-5-20251001"
GEMINI_MODEL = "gemini-2.5-flash"

# Canonical model names for cache and reporting
CANONICAL_MODELS = {CLAUDE_MODEL, CLAUDE_MODEL_FAST, GEMINI_MODEL}
MAX_CONTEXT_MESSAGES = 20          # last N messages per task passed to update agent
AGENT_MAX_TOKENS = 2048

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
INGEST_QUEUE_KEY = "ingest_queue"       # Legacy list key (deprecated — use INGEST_STREAM)
INGEST_STREAM = "ingest_stream"         # Redis stream; router_worker reads from this
TASK_EVENTS_STREAM = "task_events"      # Redis stream; linkage_worker reads from this

# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

DB_PATH = "data/mantri.db"
