#!/usr/bin/env python3
"""
build_replay_trace.py

Builds a replay trace from an eval case's metadata.json — a JSON array of
message dicts in chronological order across all threads, ready to feed into
process_message() for dry-run or live replay testing.

Unlike case_extractor.py (which merges threads into a human-readable
threads.txt with vision annotations), this script produces machine-readable
ingest-format messages with image_path pointers to actual files on disk.

Usage:

    python scripts/build_replay_trace.py --case tests/evals/R1-D-L3-01_sata_multi_item_multi_supplier/

    # Override output path:
    python scripts/build_replay_trace.py --case tests/evals/R1-D-L3-01_sata_multi_item_multi_supplier/ \
        --output /tmp/trace.json

Output format — JSON array, each element:

    {
        "message_id": "sata_jobs_0042",
        "timestamp": 1740820500,
        "sender_jid": "Sata Bty Bn Sub Arvind Sir Sep 22",
        "group_id": "sata_jobs",
        "body": "Price chahiye",
        "media_type": "text",
        "image_path": null,
        "thread_label": "SATA Artillery Regiment Client Group",
        "thread_index": 1
    }

When an attachment exists on disk, image_path is the absolute path to the file
and media_type is "image" or "document". When the attachment is missing or the
message has no attachment, image_path is null and media_type is "text".
"""

import json
import argparse
from pathlib import Path
from datetime import datetime

from case_extractor import (
    parse_log, parse_ts, filter_window, load_chat_inputs,
    Message, ChatInput,
    SUPPORTED_IMAGE_EXTS, SUPPORTED_DOC_EXTS,
)


def _media_type(ext: str) -> str:
    if ext in SUPPORTED_IMAGE_EXTS:
        return "image"
    if ext in SUPPORTED_DOC_EXTS:
        return "document"
    return "text"


def _to_unix_ts(dt: datetime) -> int:
    return int(dt.timestamp())


def build_trace(metadata_path: Path) -> list[dict]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    case_id = metadata.get("id", "unknown")

    start_str, end_str, chats = load_chat_inputs(metadata, Path.cwd())
    start_dt = parse_ts(start_str)
    end_dt = parse_ts(end_str)

    # Collect (message, thread_metadata) tuples across all threads
    all_messages: list[tuple[Message, int, str, Path]] = []

    for i, chat in enumerate(chats, start=1):
        txt_files = list(chat.path.glob("*.txt"))
        if not txt_files:
            print(f"  [SKIP] No .txt log in {chat.path}")
            continue

        messages = parse_log(txt_files[0])
        windowed = filter_window(messages, start_dt, end_dt)
        print(f"  [{i}] {chat.label}: {len(messages)} total -> {len(windowed)} in window")

        for msg in windowed:
            all_messages.append((msg, i, chat.label, chat.path))

    # Sort by timestamp across all threads (stable sort preserves intra-thread order)
    all_messages.sort(key=lambda x: x[0].timestamp)

    # Build trace
    trace: list[dict] = []
    group_counters: dict[str, int] = {}

    for msg, thread_idx, thread_label, chat_dir in all_messages:
        group_id = chat_dir.name
        group_counters[group_id] = group_counters.get(group_id, 0) + 1
        seq = group_counters[group_id]

        # Resolve attachment
        image_path = None
        media_type = "text"

        if msg.has_attachment and msg.attachment_name:
            file_path = chat_dir / msg.attachment_name
            ext = Path(msg.attachment_name).suffix.lower()
            if file_path.exists() and (ext in SUPPORTED_IMAGE_EXTS or ext in SUPPORTED_DOC_EXTS):
                image_path = str(file_path.resolve())
                media_type = _media_type(ext)

        # Strip attachment suffix from body for clean ingest
        body = msg.content
        if msg.has_attachment and msg.attachment_name:
            body = body.replace(f"{msg.attachment_name} (file attached)", "").strip()
        # Replace <Media omitted> with empty body (image-only message)
        if "<Media omitted>" in body:
            body = body.replace("<Media omitted>", "").strip()

        trace.append({
            "message_id": f"{case_id}_{group_id}_{seq:04d}",
            "timestamp": _to_unix_ts(msg.timestamp),
            "timestamp_raw": msg.ts_raw,
            "sender_jid": msg.sender,
            "group_id": group_id,
            "body": body,
            "media_type": media_type,
            "image_path": image_path,
            "thread_label": thread_label,
            "thread_index": thread_idx,
        })

    return trace


def main():
    parser = argparse.ArgumentParser(
        description="Build a replay trace from an eval case metadata.json"
    )
    parser.add_argument(
        "--case", required=True, metavar="CASE_DIR",
        help="Case directory containing metadata.json",
    )
    parser.add_argument(
        "--output", metavar="FILE",
        help="Output path (default: <case_dir>/replay_trace.json)",
    )
    args = parser.parse_args()

    case_dir = Path(args.case)
    metadata_path = case_dir / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"No metadata.json in {case_dir}")

    if args.output:
        output_path = Path(args.output)
    else:
        # Default: tests/integration_tests/<case_dir_name>/replay_trace.json
        integration_dir = Path("tests/integration_tests") / case_dir.name
        integration_dir.mkdir(parents=True, exist_ok=True)
        output_path = integration_dir / "replay_trace.json"

    print(f"Case: {json.loads(metadata_path.read_text()).get('id', '?')}")
    trace = build_trace(metadata_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(trace, indent=2, ensure_ascii=False), encoding="utf-8")

    # Summary stats
    n_images = sum(1 for m in trace if m["image_path"])
    n_media_omitted = sum(1 for m in trace if m["media_type"] == "text" and not m["body"])
    threads = sorted(set(m["thread_label"] for m in trace))

    print(f"\n  {len(trace)} messages across {len(threads)} thread(s)")
    print(f"  {n_images} with image/document on disk")
    print(f"  {n_media_omitted} empty-body (media omitted / no text)")
    print(f"  Written to: {output_path}")


if __name__ == "__main__":
    main()
