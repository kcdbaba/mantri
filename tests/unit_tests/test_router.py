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
    # Config maps group → "task_001" → resolved to entity via client_id
    with patch("src.router.router._resolve_to_entity", return_value="entity_sata"):
        results = route(msg)
    assert len(results) == 1
    entity_id, conf = results[0]
    assert entity_id == "entity_sata"
    assert conf == pytest.approx(0.90)


# ---------------------------------------------------------------------------
# route() — Layer 2b (entity matching on shared group)
# ---------------------------------------------------------------------------

def test_layer2b_entity_match_on_shared_group():
    # All-staff group has no direct task mapping (None) → falls to 2b
    # Layer 2b now returns entity_ids directly (no task lookup)
    msg = {
        "media_type": "text",
        "body": "kapoor steel wale aa gaye",
        "group_id": "REPLACE_ALL_STAFF_JID@g.us",
    }
    results = route(msg)
    assert any(eid == "entity_kapoor_steel" for eid, _ in results)


def test_layer2b_no_entity_match_unrouted():
    with patch("src.router.router.get_active_tasks", return_value=[]):
        msg = {
            "media_type": "text",
            "body": "kuch bhi",
            "group_id": "REPLACE_ALL_STAFF_JID@g.us",
        }
        results = route(msg)
    assert results == []


# ---------------------------------------------------------------------------
# DB-aware alias matching
# ---------------------------------------------------------------------------

import allure

@allure.feature("Entity Resolution")
@allure.story("DB Alias Loading")
class TestDBAlias:

    def test_db_alias_matches(self):
        """Aliases from DB should be found by match_entities."""
        from src.router.alias_dict import match_entities, invalidate_alias_cache
        db_aliases = {"narmohan da": "entity_baishya_steel"}
        with patch("src.router.alias_dict._load_db_aliases", return_value=db_aliases):
            invalidate_alias_cache()
            results = match_entities("Narmohan Da se steel gate banwa rahe hain")
        assert any(eid == "entity_baishya_steel" for eid, _ in results)

    def test_db_alias_overrides_config(self):
        """DB alias should take precedence over config alias for same key."""
        from src.router.alias_dict import get_all_aliases, invalidate_alias_cache
        db_aliases = {"kapoor": "entity_kapoor_new"}
        with patch("src.router.alias_dict._load_db_aliases", return_value=db_aliases):
            invalidate_alias_cache()
            all_a = get_all_aliases()
        assert all_a["kapoor"] == "entity_kapoor_new"

    def test_config_alias_still_works(self):
        """Config aliases should still match when DB has no override."""
        from src.router.alias_dict import match_entities, invalidate_alias_cache
        with patch("src.router.alias_dict._load_db_aliases", return_value={}):
            invalidate_alias_cache()
            results = match_entities("Kapoor ji se maal aayega")
        assert any(eid == "entity_kapoor_steel" for eid, _ in results)

    def test_invalidate_cache_forces_reload(self):
        from src.router.alias_dict import invalidate_alias_cache, _load_db_aliases
        invalidate_alias_cache()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [("refresh", "entity_r")]
        with patch("src.store.db.get_connection", return_value=mock_conn):
            result = _load_db_aliases()
        assert result.get("refresh") == "entity_r"

    def test_cache_avoids_repeated_db_calls(self):
        """Repeated calls within TTL should not hit DB again."""
        from src.router.alias_dict import _load_db_aliases, invalidate_alias_cache
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [("test", "entity_test")]
        invalidate_alias_cache()
        with patch("src.store.db.get_connection", return_value=mock_conn):
            _load_db_aliases()
            _load_db_aliases()  # should use cache
        mock_conn.execute.assert_called_once()  # only 1 DB call


# ---------------------------------------------------------------------------
# Runtime group routing (Layer 2a+)
# ---------------------------------------------------------------------------

@allure.feature("Message Routing")
@allure.story("Runtime Task Routing")
class TestRuntimeRouting:

    def test_runtime_entity_returned_for_group(self):
        from src.router.router import route
        with patch("src.router.router.MONITORED_GROUPS", {"grp": None}), \
             patch("src.router.router._get_runtime_entities", return_value=["entity_live"]):
            results = route({"body": "test", "message_id": "m1", "group_id": "grp"})
        assert any(eid == "entity_live" for eid, _ in results)

    def test_runtime_plus_direct_both_returned(self):
        from src.router.router import route
        with patch("src.router.router.MONITORED_GROUPS", {"grp": "entity_seed"}), \
             patch("src.router.router._get_runtime_entities", return_value=["entity_live"]):
            results = route({"body": "test", "message_id": "m1", "group_id": "grp"})
        entity_ids = {eid for eid, _ in results}
        assert "entity_seed" in entity_ids
        assert "entity_live" in entity_ids

    def test_no_duplicates_in_results(self):
        from src.router.router import route
        with patch("src.router.router.MONITORED_GROUPS", {"grp": "entity_x"}), \
             patch("src.router.router._get_runtime_entities", return_value=["entity_x"]):
            results = route({"body": "test", "message_id": "m1", "group_id": "grp"})
        assert len(results) == 1

    def test_runtime_confidence_lower_than_direct(self):
        from src.router.router import route, RUNTIME_TASK_CONFIDENCE, DIRECT_GROUP_CONFIDENCE
        with patch("src.router.router.MONITORED_GROUPS", {"grp": "entity_seed"}), \
             patch("src.router.router._get_runtime_entities", return_value=["entity_live"]):
            results = route({"body": "test", "message_id": "m1", "group_id": "grp"})
        conf_map = {eid: conf for eid, conf in results}
        assert conf_map["entity_seed"] == DIRECT_GROUP_CONFIDENCE
        assert conf_map["entity_live"] == RUNTIME_TASK_CONFIDENCE
        assert conf_map["entity_live"] < conf_map["entity_seed"]
