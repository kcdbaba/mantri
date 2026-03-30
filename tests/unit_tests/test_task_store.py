"""
Unit tests for task_store M:N helpers:
  apply_item_extractions   — add / update / remove
  apply_node_data_extractions — merge semantics
  reconcile_order_ready    — completed / partial / no-change
  upsert_fulfillment_link  — auto-confirm at ≥0.92
All tests use an in-memory SQLite DB (tmp path via tmp_path fixture).
"""

import json
import time
import uuid
import pytest
import allure
from unittest.mock import patch
from pathlib import Path

from src.agent.update_agent import ItemExtraction, NodeDataExtraction


# ---------------------------------------------------------------------------
# Fixtures — spin up a fresh in-memory DB for each test
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path):
    p = tmp_path / "test.db"
    with patch("src.store.db.DB_PATH", str(p)):
        from src.store.db import init_schema
        init_schema()
        yield str(p)


def _seed_task(db_path, task_id, order_type="standard_procurement"):
    with patch("src.store.db.DB_PATH", db_path):
        from src.store.db import transaction
        import time
        now = int(time.time())
        with transaction() as conn:
            conn.execute(
                "INSERT INTO task_instances (id, order_type, client_id, supplier_ids, "
                "created_at, last_updated, stage, source) VALUES (?,?,?,?,?,?,?,?)",
                (task_id, order_type, "entity_sata", "[]", now, now, "active", "test"),
            )


def _seed_node(db_path, task_id, node_id, status="active", node_data=None):
    with patch("src.store.db.DB_PATH", db_path):
        from src.store.db import transaction
        now = int(time.time())
        with transaction() as conn:
            conn.execute(
                "INSERT INTO task_nodes (id, task_id, node_type, name, status, "
                "confidence, updated_at, updated_by, optional, requires_all, "
                "warns_if_incomplete, node_data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"{task_id}_{node_id}", task_id, "real_world_milestone", node_id,
                    status, 0.9, now, "seed", 0, "[]", "[]",
                    json.dumps(node_data) if node_data else None,
                ),
            )


def _seed_client_item(db_path, task_id, description, quantity, unit="bags"):
    with patch("src.store.db.DB_PATH", db_path):
        from src.store.db import transaction
        now = int(time.time())
        with transaction() as conn:
            conn.execute(
                "INSERT INTO client_order_items (id, task_id, description, unit, quantity, specs, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), task_id, description, unit, quantity, None, now),
            )


# ---------------------------------------------------------------------------
# apply_item_extractions
# ---------------------------------------------------------------------------

@allure.feature("Item Management")
@allure.story("Apply Item Extractions")
class TestApplyItemExtractions:

    def test_add_new_item(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "t1")
            from src.store.task_store import apply_item_extractions, get_client_order_items
            exts = [ItemExtraction(operation="add", description="atta 50kg", unit="bags", quantity=50)]
            apply_item_extractions("t1", "standard_procurement", exts)
            items = get_client_order_items("t1")
        assert len(items) == 1
        assert items[0]["description"] == "atta 50kg"
        assert items[0]["quantity"] == 50

    def test_add_multiple_items(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "t1")
            from src.store.task_store import apply_item_extractions, get_client_order_items
            exts = [
                ItemExtraction(operation="add", description="atta 50kg", unit="bags", quantity=50),
                ItemExtraction(operation="add", description="dal chana", unit="bags", quantity=30),
            ]
            apply_item_extractions("t1", "standard_procurement", exts)
            items = get_client_order_items("t1")
        assert len(items) == 2

    def test_update_existing_item(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "t1")
            _seed_client_item(db_path, "t1", "atta 50kg", 50)
            from src.store.task_store import apply_item_extractions, get_client_order_items
            exts = [ItemExtraction(
                operation="update", description="atta 70kg", unit="bags", quantity=70,
                existing_description="atta 50kg",
            )]
            apply_item_extractions("t1", "standard_procurement", exts)
            items = get_client_order_items("t1")
        assert len(items) == 1
        assert items[0]["description"] == "atta 70kg"
        assert items[0]["quantity"] == 70

    def test_remove_item(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "t1")
            _seed_client_item(db_path, "t1", "atta 50kg", 50)
            _seed_client_item(db_path, "t1", "dal chana", 30)
            from src.store.task_store import apply_item_extractions, get_client_order_items
            exts = [ItemExtraction(operation="remove", description="atta 50kg", existing_description="atta 50kg")]
            apply_item_extractions("t1", "standard_procurement", exts)
            items = get_client_order_items("t1")
        assert len(items) == 1
        assert items[0]["description"] == "dal chana"

    def test_supplier_order_writes_to_supplier_table(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "t1", order_type="supplier_order")
            from src.store.task_store import apply_item_extractions, get_supplier_order_items, get_client_order_items
            exts = [ItemExtraction(operation="add", description="steel rods", unit="kg", quantity=500)]
            apply_item_extractions("t1", "supplier_order", exts)
            supplier_items = get_supplier_order_items("t1")
            client_items = get_client_order_items("t1")
        assert len(supplier_items) == 1
        assert len(client_items) == 0


# ---------------------------------------------------------------------------
# apply_node_data_extractions
# ---------------------------------------------------------------------------

@allure.feature("Node Data")
@allure.story("Apply Node Data Extractions")
class TestApplyNodeDataExtractions:

    def test_writes_new_data(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "t1")
            _seed_node(db_path, "t1", "supplier_indent")
            from src.store.task_store import apply_node_data_extractions, get_node_data
            exts = [NodeDataExtraction(node_id="supplier_indent", data={"expected_delivery_date": "2026-04-10", "supplier_name": "Kapoor Steel"})]
            apply_node_data_extractions("t1", exts)
            nd = get_node_data("t1", "supplier_indent")
        assert nd["expected_delivery_date"] == "2026-04-10"
        assert nd["supplier_name"] == "Kapoor Steel"

    def test_merges_preserves_existing_keys(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "t1")
            _seed_node(db_path, "t1", "supplier_indent", node_data={"supplier_name": "Kapoor Steel"})
            from src.store.task_store import apply_node_data_extractions, get_node_data
            exts = [NodeDataExtraction(node_id="supplier_indent", data={"expected_delivery_date": "2026-04-10"})]
            apply_node_data_extractions("t1", exts)
            nd = get_node_data("t1", "supplier_indent")
        # existing key preserved, new key added
        assert nd["supplier_name"] == "Kapoor Steel"
        assert nd["expected_delivery_date"] == "2026-04-10"

    def test_new_key_overwrites_old(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "t1")
            _seed_node(db_path, "t1", "supplier_indent", node_data={"expected_delivery_date": "2026-04-05"})
            from src.store.task_store import apply_node_data_extractions, get_node_data
            exts = [NodeDataExtraction(node_id="supplier_indent", data={"expected_delivery_date": "2026-04-10"})]
            apply_node_data_extractions("t1", exts)
            nd = get_node_data("t1", "supplier_indent")
        assert nd["expected_delivery_date"] == "2026-04-10"

    def test_nonexistent_node_skipped_silently(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "t1")
            from src.store.task_store import apply_node_data_extractions
            exts = [NodeDataExtraction(node_id="ghost_node", data={"foo": "bar"})]
            # should not raise
            apply_node_data_extractions("t1", exts)

    def test_get_node_data_missing_returns_empty(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "t1")
            from src.store.task_store import get_node_data
            assert get_node_data("t1", "no_such_node") == {}


# ---------------------------------------------------------------------------
# upsert_fulfillment_link — auto-confirm threshold
# ---------------------------------------------------------------------------

@allure.feature("Fulfillment Links")
@allure.story("Upsert Fulfillment Link")
class TestUpsertFulfillmentLink:

    def test_auto_confirms_at_high_confidence(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "c1", "client_order")
            _seed_task(db_path, "s1", "supplier_order")
            from src.store.task_store import upsert_fulfillment_link, get_fulfillment_links
            upsert_fulfillment_link({
                "id": "link1", "client_order_id": "c1",
                "client_item_description": "atta 50kg",
                "supplier_order_id": "s1",
                "supplier_item_description": "wheat flour 50kg",
                "quantity_allocated": 50, "match_confidence": 0.95,
                "match_reasoning": "same item", "status": "candidate",
            })
            links = get_fulfillment_links("c1")
        assert links[0]["status"] == "confirmed"

    def test_stays_candidate_below_threshold(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "c1", "client_order")
            _seed_task(db_path, "s1", "supplier_order")
            from src.store.task_store import upsert_fulfillment_link, get_fulfillment_links
            upsert_fulfillment_link({
                "id": "link1", "client_order_id": "c1",
                "client_item_description": "atta 50kg",
                "supplier_order_id": "s1",
                "supplier_item_description": "wheat flour",
                "quantity_allocated": 50, "match_confidence": 0.85,
                "match_reasoning": "possibly same", "status": "candidate",
            })
            links = get_fulfillment_links("c1")
        assert links[0]["status"] == "candidate"

    def test_exactly_at_threshold_confirms(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "c1", "client_order")
            _seed_task(db_path, "s1", "supplier_order")
            from src.store.task_store import upsert_fulfillment_link, get_fulfillment_links
            upsert_fulfillment_link({
                "id": "link1", "client_order_id": "c1",
                "client_item_description": "atta",
                "supplier_order_id": "s1",
                "supplier_item_description": "atta",
                "quantity_allocated": 10, "match_confidence": 0.92,
                "match_reasoning": "exact", "status": "candidate",
            })
            links = get_fulfillment_links("c1")
        assert links[0]["status"] == "confirmed"


# ---------------------------------------------------------------------------
# reconcile_order_ready
# ---------------------------------------------------------------------------

@allure.feature("Fulfillment Links")
@allure.story("Reconcile Order Ready")
class TestReconcileOrderReady:

    def _setup(self, db_path, task_id):
        _seed_task(db_path, task_id, "client_order")
        _seed_node(db_path, task_id, "order_ready", status="pending")

    def test_all_items_allocated_sets_completed(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            self._setup(db_path, "c1")
            _seed_task(db_path, "s1", "supplier_order")
            _seed_client_item(db_path, "c1", "atta 50kg", 50)
            from src.store.task_store import upsert_fulfillment_link, reconcile_order_ready, get_node_data
            from src.store.db import get_connection
            upsert_fulfillment_link({
                "id": "l1", "client_order_id": "c1",
                "client_item_description": "atta 50kg",
                "supplier_order_id": "s1",
                "supplier_item_description": "atta 50kg",
                "quantity_allocated": 50, "match_confidence": 0.95,
                "match_reasoning": "exact", "status": "confirmed",
            })
            result = reconcile_order_ready("c1")
        assert result == "completed"

    def test_partial_allocation_sets_partial(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            self._setup(db_path, "c1")
            _seed_task(db_path, "s1", "supplier_order")
            _seed_client_item(db_path, "c1", "atta 50kg", 50)
            _seed_client_item(db_path, "c1", "dal 30kg", 30)
            from src.store.task_store import upsert_fulfillment_link, reconcile_order_ready
            # only atta confirmed, dal not
            upsert_fulfillment_link({
                "id": "l1", "client_order_id": "c1",
                "client_item_description": "atta 50kg",
                "supplier_order_id": "s1",
                "supplier_item_description": "atta 50kg",
                "quantity_allocated": 50, "match_confidence": 0.95,
                "match_reasoning": "exact", "status": "confirmed",
            })
            result = reconcile_order_ready("c1")
        assert result == "partial"

    def test_no_confirmed_links_returns_none(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            self._setup(db_path, "c1")
            _seed_client_item(db_path, "c1", "atta 50kg", 50)
            from src.store.task_store import reconcile_order_ready
            result = reconcile_order_ready("c1")
        assert result is None

    def test_no_items_returns_none(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            self._setup(db_path, "c1")
            from src.store.task_store import reconcile_order_ready
            result = reconcile_order_ready("c1")
        assert result is None


# ---------------------------------------------------------------------------
# prune_links_for_supplier_order / prune_links_for_client_order
# ---------------------------------------------------------------------------

@allure.feature("Fulfillment Links")
@allure.story("Prune Links")
class TestPruneLinks:

    def _seed_link(self, db_path, client_order_id, supplier_order_id, status, link_id=None):
        with patch("src.store.db.DB_PATH", db_path):
            from src.store.db import transaction
            now = int(time.time())
            with transaction() as conn:
                conn.execute(
                    """INSERT INTO fulfillment_links
                       (id, client_order_id, client_item_description,
                        supplier_order_id, supplier_item_description,
                        quantity_allocated, match_confidence, match_reasoning, status, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (link_id or str(uuid.uuid4()), client_order_id, "item",
                     supplier_order_id, "item", 10, 0.95, "test", status, now),
                )

    # --- prune_links_for_supplier_order ---

    def test_supplier_all_terminal_returns_true(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "c1", "client_order")
            _seed_task(db_path, "s1", "supplier_order")
            self._seed_link(db_path, "c1", "s1", "fulfilled")
            from src.store.task_store import prune_links_for_supplier_order
            result = prune_links_for_supplier_order("s1")
        assert result is True

    def test_supplier_partial_terminal_returns_false(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "c1", "client_order")
            _seed_task(db_path, "s1", "supplier_order")
            self._seed_link(db_path, "c1", "s1", "fulfilled", "lnk1")
            self._seed_link(db_path, "c1", "s1", "candidate", "lnk2")
            from src.store.task_store import prune_links_for_supplier_order
            result = prune_links_for_supplier_order("s1")
        assert result is False

    def test_supplier_no_links_returns_false(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "s1", "supplier_order")
            from src.store.task_store import prune_links_for_supplier_order
            result = prune_links_for_supplier_order("s1")
        assert result is False

    def test_supplier_all_terminal_statuses_accepted(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "c1", "client_order")
            _seed_task(db_path, "s1", "supplier_order")
            self._seed_link(db_path, "c1", "s1", "fulfilled",   "l1")
            self._seed_link(db_path, "c1", "s1", "failed",      "l2")
            self._seed_link(db_path, "c1", "s1", "invalidated", "l3")
            from src.store.task_store import prune_links_for_supplier_order
            result = prune_links_for_supplier_order("s1")
        assert result is True

    # --- prune_links_for_client_order ---

    def test_client_all_completed_returns_true(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "c1", "client_order")
            _seed_task(db_path, "s1", "supplier_order")
            self._seed_link(db_path, "c1", "s1", "completed")
            from src.store.task_store import prune_links_for_client_order
            result = prune_links_for_client_order("c1")
        assert result is True

    def test_client_partial_completed_returns_false(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "c1", "client_order")
            _seed_task(db_path, "s1", "supplier_order")
            self._seed_link(db_path, "c1", "s1", "completed", "l1")
            self._seed_link(db_path, "c1", "s1", "confirmed", "l2")
            from src.store.task_store import prune_links_for_client_order
            result = prune_links_for_client_order("c1")
        assert result is False

    def test_client_no_links_returns_false(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "c1", "client_order")
            from src.store.task_store import prune_links_for_client_order
            result = prune_links_for_client_order("c1")
        assert result is False


# ---------------------------------------------------------------------------
# close_task
# ---------------------------------------------------------------------------

@allure.feature("Task Lifecycle")
@allure.story("Close Task")
class TestCloseTask:

    def test_close_sets_stage_completed(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "t1")
            from src.store.task_store import close_task, get_task
            close_task("t1")
            task = get_task("t1")
        assert task["stage"] == "completed"

    def test_close_updates_last_updated(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "t1")
            from src.store.task_store import close_task, get_task
            before = get_task("t1")["last_updated"]
            import time; time.sleep(0.01)
            close_task("t1")
            after = get_task("t1")["last_updated"]
        assert after >= before

    def test_closed_task_excluded_from_active(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "t1")
            _seed_task(db_path, "t2")
            from src.store.task_store import close_task, get_active_tasks
            close_task("t1")
            active = get_active_tasks()
        active_ids = [t["id"] for t in active]
        assert "t1" not in active_ids
        assert "t2" in active_ids


# ---------------------------------------------------------------------------
# get_recent_messages
# ---------------------------------------------------------------------------

@allure.feature("Message Store")
@allure.story("Get Recent Messages")
class TestGetRecentMessages:

    def _seed_message(self, db_path, task_id, message_id, body, timestamp):
        with patch("src.store.db.DB_PATH", db_path):
            from src.store.task_store import append_message
            append_message(task_id, {
                "message_id": message_id, "group_id": "grp",
                "sender_jid": "sender", "body": body, "timestamp": timestamp,
            }, routing_confidence=0.9)

    def test_returns_messages_in_chronological_order(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "t1")
            self._seed_message(db_path, "t1", "m1", "first", 1000)
            self._seed_message(db_path, "t1", "m2", "second", 2000)
            self._seed_message(db_path, "t1", "m3", "third", 3000)
            from src.store.task_store import get_recent_messages
            msgs = get_recent_messages("t1")
        assert [m["body"] for m in msgs] == ["first", "second", "third"]

    def test_respects_limit(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "t1")
            for i in range(10):
                self._seed_message(db_path, "t1", f"m{i}", f"msg {i}", 1000 + i)
            from src.store.task_store import get_recent_messages
            msgs = get_recent_messages("t1", limit=3)
        assert len(msgs) == 3
        # Should be the 3 most recent
        assert msgs[-1]["body"] == "msg 9"

    def test_empty_task_returns_empty(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "t1")
            from src.store.task_store import get_recent_messages
            msgs = get_recent_messages("t1")
        assert msgs == []


# ---------------------------------------------------------------------------
# get_order_items — dispatches to client or supplier table
# ---------------------------------------------------------------------------

@allure.feature("Item Management")
@allure.story("Get Order Items")
class TestGetOrderItems:

    def _seed_supplier_item(self, db_path, task_id, description, quantity):
        with patch("src.store.db.DB_PATH", db_path):
            from src.store.db import transaction
            now = int(time.time())
            with transaction() as conn:
                conn.execute(
                    "INSERT INTO supplier_order_items (id, task_id, description, unit, quantity, specs, created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), task_id, description, "pcs", quantity, None, now),
                )

    def test_client_order_returns_client_items(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "c1", "standard_procurement")
            _seed_client_item(db_path, "c1", "atta 50kg", 50)
            from src.store.task_store import get_order_items
            items = get_order_items("c1")
        assert len(items) == 1
        assert items[0]["description"] == "atta 50kg"

    def test_supplier_order_returns_supplier_items(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "s1", "supplier_order")
            self._seed_supplier_item(db_path, "s1", "steel rods", 100)
            from src.store.task_store import get_order_items
            items = get_order_items("s1")
        assert len(items) == 1
        assert items[0]["description"] == "steel rods"

    def test_missing_task_returns_empty(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            from src.store.task_store import get_order_items
            items = get_order_items("nonexistent")
        assert items == []


# ---------------------------------------------------------------------------
# get_fulfillment_links_by_supplier
# ---------------------------------------------------------------------------

@allure.feature("Fulfillment Links")
@allure.story("Get Links By Supplier")
class TestGetFulfillmentLinksBySupplier(TestPruneLinks):

    def test_returns_links_for_supplier(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "c1", "client_order")
            _seed_task(db_path, "s1", "supplier_order")
            self._seed_link(db_path, "c1", "s1", "confirmed", "l1")
            self._seed_link(db_path, "c1", "s1", "candidate", "l2")
            from src.store.task_store import get_fulfillment_links_by_supplier
            links = get_fulfillment_links_by_supplier("s1")
        assert len(links) == 2

    def test_other_supplier_not_returned(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "c1", "client_order")
            _seed_task(db_path, "s1", "supplier_order")
            _seed_task(db_path, "s2", "supplier_order")
            self._seed_link(db_path, "c1", "s1", "confirmed", "l1")
            self._seed_link(db_path, "c1", "s2", "confirmed", "l2")
            from src.store.task_store import get_fulfillment_links_by_supplier
            links = get_fulfillment_links_by_supplier("s1")
        assert len(links) == 1
        assert links[0]["supplier_order_id"] == "s1"

    def test_no_links_returns_empty(self, db_path):
        with patch("src.store.db.DB_PATH", db_path):
            _seed_task(db_path, "s1", "supplier_order")
            from src.store.task_store import get_fulfillment_links_by_supplier
            links = get_fulfillment_links_by_supplier("s1")
        assert links == []


# ---------------------------------------------------------------------------
# compute_cost — including cache pricing
# ---------------------------------------------------------------------------

@allure.feature("Cost Tracking")
@allure.story("Compute Cost")
class TestComputeCost:

    def test_sonnet_basic_cost(self):
        from src.store.db import compute_cost
        # 1000 input * 3/1M + 500 output * 15/1M = 0.003 + 0.0075 = 0.0105
        cost = compute_cost("claude-sonnet-4-6", 1000, 500)
        assert abs(cost - 0.0105) < 1e-8

    def test_haiku_cheaper_than_sonnet(self):
        from src.store.db import compute_cost
        sonnet = compute_cost("claude-sonnet-4-6", 1000, 500)
        haiku = compute_cost("claude-haiku-4-5-20251001", 1000, 500)
        assert haiku < sonnet

    def test_cache_read_cheaper_than_uncached(self):
        from src.store.db import compute_cost
        uncached = compute_cost("claude-sonnet-4-6", 4000, 200)
        cached = compute_cost("claude-sonnet-4-6", 200, 200, cache_read_tokens=3800)
        assert cached < uncached

    def test_cache_write_more_expensive(self):
        from src.store.db import compute_cost
        uncached = compute_cost("claude-sonnet-4-6", 4000, 200)
        first_write = compute_cost("claude-sonnet-4-6", 200, 200, cache_creation_tokens=3800)
        assert first_write > uncached  # 1.25x on cached portion

    def test_unknown_model_returns_zero(self):
        from src.store.db import compute_cost
        assert compute_cost("unknown-model", 1000, 500) == 0.0

    def test_zero_tokens(self):
        from src.store.db import compute_cost
        assert compute_cost("claude-sonnet-4-6", 0, 0) == 0.0


# ---------------------------------------------------------------------------
# Template query helpers
# ---------------------------------------------------------------------------

@allure.feature("Templates")
@allure.story("Template Queries")
class TestTemplateQueries:

    def test_get_trigger_nodes_returns_triggers_only(self):
        from src.agent.templates import get_trigger_nodes
        nodes = get_trigger_nodes("standard_procurement")
        assert all(n["type"] in ("auto_trigger", "time_trigger") for n in nodes)
        assert len(nodes) > 0

    def test_get_time_trigger_nodes(self):
        from src.agent.templates import get_time_trigger_nodes
        nodes = get_time_trigger_nodes("standard_procurement")
        assert all(n["type"] == "time_trigger" for n in nodes)
        ids = {n["id"] for n in nodes}
        assert "quote_followup_48h" in ids
        assert "payment_followup_30d" in ids

    def test_get_auto_trigger_nodes(self):
        from src.agent.templates import get_auto_trigger_nodes
        nodes = get_auto_trigger_nodes("standard_procurement")
        assert all(n["type"] == "auto_trigger" for n in nodes)
        ids = {n["id"] for n in nodes}
        assert "order_confirmation" in ids
        assert "order_ready" in ids

    def test_get_trigger_nodes_supplier_order(self):
        from src.agent.templates import get_trigger_nodes
        nodes = get_trigger_nodes("supplier_order")
        ids = {n["id"] for n in nodes}
        assert "supplier_predelivery_enquiry" in ids

    def test_get_template_invalid_raises(self):
        from src.agent.templates import get_template
        with pytest.raises(ValueError, match="Unknown order type"):
            get_template("nonexistent")
