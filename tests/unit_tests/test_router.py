"""
Unit tests for router Layer 1 (noise filter), Layer 2a (group→task),
and Layer 2b (entity alias matching).
No LLM calls, no DB, no Redis.
"""

import pytest
from unittest.mock import patch, MagicMock

from src.router.alias_dict import match_entities, _normalise
from src.router.router import route


# ---------------------------------------------------------------------------
# _normalise
# ---------------------------------------------------------------------------

def test_normalise_lowercase():
    assert _normalise("KAPOOR STEEL") == "kapoor steel"

def test_normalise_strips_punctuation():
    assert _normalise("kapoor ji,") == "kapoor ji"

def test_normalise_collapses_whitespace():
    assert _normalise("kapoor  ji") == "kapoor ji"

def test_normalise_empty():
    assert _normalise("") == ""


# ---------------------------------------------------------------------------
# match_entities (Layer 2b)
# ---------------------------------------------------------------------------

def test_exact_alias_match():
    results = match_entities("kapoor steel ne maal bheja")
    entity_ids = [e for e, _ in results]
    assert "entity_kapoor_steel" in entity_ids

def test_fuzzy_alias_match():
    # "kapoor ji" is a registered alias
    results = match_entities("kapoor ji se baat karo")
    entity_ids = [e for e, _ in results]
    assert "entity_kapoor_steel" in entity_ids

def test_sata_alias_match():
    results = match_entities("51 sub area ka order confirm hua")
    entity_ids = [e for e, _ in results]
    assert "entity_sata" in entity_ids

def test_army_alias_match():
    results = match_entities("army wale aaj aayenge")
    entity_ids = [e for e, _ in results]
    assert "entity_sata" in entity_ids

def test_no_match_returns_empty():
    results = match_entities("aaj mausam accha hai")
    assert results == []

def test_empty_body_returns_empty():
    assert match_entities("") == []

def test_confidence_scaled_below_one():
    results = match_entities("kapoor steel")
    for _, conf in results:
        assert 0.0 < conf <= 0.90

def test_multiple_entities_in_one_message():
    results = match_entities("sata ka order kapoor steel se aayega")
    entity_ids = [e for e, _ in results]
    assert "entity_sata" in entity_ids
    assert "entity_kapoor_steel" in entity_ids


# ---------------------------------------------------------------------------
# route() — Layer 1 noise filter
# ---------------------------------------------------------------------------

def test_noise_reaction_dropped():
    msg = {"media_type": "reaction", "body": "👍", "group_id": ""}
    assert route(msg) == []

def test_noise_sticker_dropped():
    msg = {"media_type": "sticker", "body": "", "group_id": ""}
    assert route(msg) == []

def test_noise_system_dropped():
    msg = {"media_type": "system", "body": "John joined", "group_id": ""}
    assert route(msg) == []


# ---------------------------------------------------------------------------
# route() — Layer 2a (direct group→task)
# ---------------------------------------------------------------------------

def test_layer2a_direct_group():
    msg = {
        "media_type": "text",
        "body": "order ready hai",
        "group_id": "REPLACE_SATA_CLIENT_JID@g.us",
    }
    results = route(msg)
    assert len(results) == 1
    task_id, conf = results[0]
    assert task_id == "task_001"
    assert conf == pytest.approx(0.90)


# ---------------------------------------------------------------------------
# route() — Layer 2b (entity matching on shared group)
# ---------------------------------------------------------------------------

def test_layer2b_entity_match_on_shared_group():
    # All-staff group has no direct task mapping (None) → falls to 2b
    # Seed a matching active task via mock
    fake_task = {
        "id": "task_001",
        "client_id": "entity_sata",
        "supplier_ids": '["entity_kapoor_steel"]',
    }
    with patch("src.router.router.get_active_tasks", return_value=[fake_task]):
        msg = {
            "media_type": "text",
            "body": "kapoor steel wale aa gaye",
            "group_id": "REPLACE_ALL_STAFF_JID@g.us",
        }
        results = route(msg)
    assert any(tid == "task_001" for tid, _ in results)


def test_layer2b_no_entity_match_unrouted():
    with patch("src.router.router.get_active_tasks", return_value=[]):
        msg = {
            "media_type": "text",
            "body": "kuch bhi",
            "group_id": "REPLACE_ALL_STAFF_JID@g.us",
        }
        results = route(msg)
    assert results == []
