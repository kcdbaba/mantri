"""
Unit tests for src/store/db — compute_cost, init_schema.
"""

import pytest
import allure
from unittest.mock import patch

from src.store.db import compute_cost


@allure.feature("Cost Tracking")
@allure.story("Compute Cost")
class TestComputeCost:

    def test_sonnet_cost(self):
        cost = compute_cost("claude-sonnet-4-6", 1000, 200)
        # (1000 * 3.00 + 200 * 15.00) / 1M = 0.006
        assert abs(cost - 0.006) < 0.0001

    def test_haiku_cost(self):
        cost = compute_cost("claude-haiku-4-5-20251001", 1000, 200)
        # (1000 * 0.80 + 200 * 4.00) / 1M = 0.0016
        assert abs(cost - 0.0016) < 0.0001

    def test_unknown_model_returns_zero(self):
        cost = compute_cost("unknown-model", 1000, 200)
        assert cost == 0.0

    def test_cache_write_tokens(self):
        cost_no_cache = compute_cost("claude-sonnet-4-6", 1000, 200)
        cost_with_cache = compute_cost("claude-sonnet-4-6", 1000, 200,
                                       cache_creation_tokens=500)
        assert cost_with_cache > cost_no_cache
        # 500 * 3.75 / 1M = 0.001875
        assert abs(cost_with_cache - cost_no_cache - 0.001875) < 0.0001

    def test_cache_read_tokens(self):
        cost_no_cache = compute_cost("claude-sonnet-4-6", 1000, 200)
        cost_with_cache = compute_cost("claude-sonnet-4-6", 1000, 200,
                                       cache_read_tokens=2000)
        assert cost_with_cache > cost_no_cache
        # 2000 * 0.30 / 1M = 0.0006
        assert abs(cost_with_cache - cost_no_cache - 0.0006) < 0.0001

    def test_zero_tokens_returns_zero(self):
        cost = compute_cost("claude-sonnet-4-6", 0, 0)
        assert cost == 0.0

    def test_gemini_flash_cost(self):
        cost = compute_cost("gemini-1.5-flash-8b", 4000, 150)
        # (4000 * 0.0375 + 150 * 0.15) / 1M = very small
        assert cost > 0
        assert cost < 0.001


@allure.feature("Database")
@allure.story("Init Schema")
class TestInitSchema:

    def test_init_schema_creates_tables(self, tmp_path):
        db_path = tmp_path / "test.db"
        with patch("src.store.db.DB_PATH", str(db_path)):
            from src.store.db import init_schema, get_connection
            init_schema()
            conn = get_connection()
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            conn.close()
        assert "task_instances" in tables
        assert "task_nodes" in tables
        assert "ambiguity_queue" in tables
        assert "node_owner_registry" in tables
        assert "usage_log" in tables

    def test_init_schema_populates_owner_registry(self, tmp_path):
        db_path = tmp_path / "test.db"
        with patch("src.store.db.DB_PATH", str(db_path)):
            from src.store.db import init_schema, get_connection
            init_schema()
            conn = get_connection()
            rows = conn.execute("SELECT * FROM node_owner_registry").fetchall()
            conn.close()
        assert len(rows) > 0

    def test_init_schema_idempotent(self, tmp_path):
        db_path = tmp_path / "test.db"
        with patch("src.store.db.DB_PATH", str(db_path)):
            from src.store.db import init_schema, get_connection
            init_schema()
            init_schema()  # second call should not fail
            conn = get_connection()
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            conn.close()
        assert "task_instances" in tables

    def test_co_owner_column_in_registry(self, tmp_path):
        db_path = tmp_path / "test.db"
        with patch("src.store.db.DB_PATH", str(db_path)):
            from src.store.db import init_schema, get_connection
            init_schema()
            conn = get_connection()
            cols = [r[1] for r in conn.execute("PRAGMA table_info(node_owner_registry)").fetchall()]
            conn.close()
        assert "co_owner" in cols

    def test_order_ready_has_co_owner(self, tmp_path):
        db_path = tmp_path / "test.db"
        with patch("src.store.db.DB_PATH", str(db_path)):
            from src.store.db import init_schema, get_connection
            init_schema()
            conn = get_connection()
            conn.row_factory = __import__("sqlite3").Row
            row = conn.execute(
                "SELECT co_owner, ownership_type FROM node_owner_registry "
                "WHERE node_id='order_ready' AND order_type='standard_procurement'"
            ).fetchone()
            conn.close()
        assert row["co_owner"] == "router_worker"
        assert row["ownership_type"] == "co_write"


@allure.feature("Database")
@allure.story("Seed Task")
class TestSeedTask:

    def test_seed_creates_task_and_nodes(self, tmp_path):
        db_path = tmp_path / "test.db"
        with patch("src.store.db.DB_PATH", str(db_path)):
            from src.store.db import init_schema, seed_task, get_connection
            init_schema()
            task = {"id": "t1", "order_type": "standard_procurement",
                    "client_id": "e_sata", "supplier_ids": ["e_kapoor"],
                    "stage": "active", "source": "test"}
            nodes = [
                {"id": "client_enquiry", "type": "real_world_milestone",
                 "name": "Client Enquiry", "optional": False},
                {"id": "filled_from_stock", "type": "real_world_milestone",
                 "name": "Filled from Stock", "optional": True},
            ]
            aliases = [{"alias": "sata", "entity_id": "e_sata", "entity_type": "client"}]
            seed_task(task, nodes, aliases)

            conn = get_connection()
            tasks = conn.execute("SELECT * FROM task_instances").fetchall()
            task_nodes = conn.execute("SELECT * FROM task_nodes").fetchall()
            ea = conn.execute("SELECT * FROM entity_aliases").fetchall()
            conn.close()

        assert len(tasks) == 1
        assert len(task_nodes) == 2
        assert len(ea) == 1

    def test_seed_optional_node_skipped(self, tmp_path):
        db_path = tmp_path / "test.db"
        with patch("src.store.db.DB_PATH", str(db_path)):
            from src.store.db import init_schema, seed_task, get_connection
            init_schema()
            task = {"id": "t1", "order_type": "standard_procurement",
                    "client_id": "e_sata", "supplier_ids": [],
                    "stage": "active", "source": "test"}
            nodes = [
                {"id": "filled_from_stock", "type": "real_world_milestone",
                 "name": "Stock", "optional": True},
            ]
            seed_task(task, nodes, [])

            conn = get_connection()
            row = conn.execute("SELECT status FROM task_nodes WHERE id='t1_filled_from_stock'").fetchone()
            conn.close()
        assert row[0] == "skipped"
