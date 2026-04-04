"""Unit tests for FastAPI ingestion endpoint behavior."""

import json
from unittest.mock import MagicMock, patch

import pytest
import redis
from fastapi.testclient import TestClient

from src.ingestion.ingest import INGEST_STREAM, app


@pytest.fixture
def client():
    return TestClient(app)


def _base_payload(**overrides):
    payload = {
        "message_id": "m1",
        "group_id": "grp@g.us",
        "sender_jid": "user@s.whatsapp.net",
        "timestamp": 1710000000,
        "body": "hello",
        "media_type": "text",
        "media_url": None,
    }
    payload.update(overrides)
    return payload


def test_ingest_valid_payload_queues_message(client):
    mock_redis = MagicMock()
    with patch("src.ingestion.ingest.redis_client", mock_redis):
        res = client.post("/ingest", json=_base_payload())

    assert res.status_code == 200
    assert res.json()["status"] == "queued"
    mock_redis.xadd.assert_called_once()
    args, kwargs = mock_redis.xadd.call_args
    assert args[0] == INGEST_STREAM
    message_json = json.loads(args[1]["message_json"])
    assert message_json["body"] == "hello"
    assert kwargs["maxlen"] == 50_000
    assert kwargs["approximate"] is True


def test_ingest_invalid_payload_returns_422(client):
    payload = _base_payload(media_type="invalid_media")
    res = client.post("/ingest", json=payload)
    assert res.status_code == 422


def test_ingest_redis_failure_returns_503(client):
    mock_redis = MagicMock()
    mock_redis.xadd.side_effect = redis.RedisError("down")

    with patch("src.ingestion.ingest.redis_client", mock_redis):
        res = client.post("/ingest", json=_base_payload())

    assert res.status_code == 503
    assert res.json()["detail"] == "Queue unavailable"


def test_health_reports_ok_and_degraded(client):
    ok_redis = MagicMock()
    with patch("src.ingestion.ingest.redis_client", ok_redis):
        ok = client.get("/health")
    assert ok.status_code == 200
    assert ok.json() == {"status": "ok", "redis": "ok"}

    bad_redis = MagicMock()
    bad_redis.ping.side_effect = redis.RedisError("down")
    with patch("src.ingestion.ingest.redis_client", bad_redis):
        degraded = client.get("/health")
    assert degraded.status_code == 200
    assert degraded.json() == {"status": "degraded", "redis": "unreachable"}
