"""
FastAPI ingestion endpoint.

Receives messages from Baileys, enriches media (Sprint 3: text pass-through),
pushes to Redis queue.
"""

import json
import logging
from typing import Literal

import redis
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.config import REDIS_URL, INGEST_STREAM

log = logging.getLogger(__name__)
app = FastAPI()
redis_client = redis.from_url(REDIS_URL, decode_responses=True)


class IncomingMessage(BaseModel):
    message_id: str
    group_id: str
    sender_jid: str
    timestamp: int
    body: str | None = None
    media_type: Literal["text", "image", "audio", "sticker", "reaction", "system"] = "text"
    media_url: str | None = None


@app.post("/ingest")
def ingest(msg: IncomingMessage):
    enriched = msg.model_dump()

    # Media enrichment (Sprint 3: text only — stubs for image/audio)
    if msg.media_type == "image":
        # TODO: download + PaddleOCR / Gemini Flash captioning
        enriched["body"] = "[image: enrichment not yet implemented]"
        log.debug("Image message %s — enrichment deferred", msg.message_id)

    elif msg.media_type == "audio":
        # TODO: download + ffmpeg + Whisper transcription
        enriched["body"] = "[voice note: transcription not yet implemented]"
        log.debug("Audio message %s — transcription deferred", msg.message_id)

    elif msg.media_type in ("sticker", "reaction", "system"):
        # Noise — still push so router can log/drop cleanly
        pass

    try:
        redis_client.xadd(
            INGEST_STREAM,
            {"message_json": json.dumps(enriched)},
            maxlen=50_000,
            approximate=True,
        )
    except redis.RedisError as e:
        log.error("Redis unavailable: %s", e)
        raise HTTPException(status_code=503, detail="Queue unavailable")

    return {"status": "queued", "message_id": msg.message_id}


@app.get("/health")
def health():
    try:
        redis_client.ping()
        return {"status": "ok", "redis": "ok"}
    except redis.RedisError:
        return {"status": "degraded", "redis": "unreachable"}
