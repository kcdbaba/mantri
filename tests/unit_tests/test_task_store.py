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
