#!/usr/bin/env python3
"""
build_seed_tasks.py

Generates seed_tasks.json for an integration test case by analyzing the eval
case's metadata.json and sampling raw chat messages.

Heuristics:
- Chat labels containing "client", "jobs", or army unit names → client task + dedicated group
- Chat labels containing "supplier" → supplier task + dedicated group
- Chat labels containing "task", "staff", "internal" → shared group (null mapping)
- Entity names inferred from chat labels and sender names
- Aliases extracted from sender name patterns

The output is a best-effort scaffold — review and adjust before use.

Usage:
    python scripts/build_seed_tasks.py --case tests/evals/R1-D-L3-01_sata_multi_item_multi_supplier/
    python scripts/build_seed_tasks.py --case tests/evals/R1-D-L3-01_sata_multi_item_multi_supplier/ \
        --output tests/integration_tests/R1-D-L3-01_sata_multi_item_multi_supplier/seed_tasks.json
"""

import json
import re
import argparse
from pathlib import Path
from collections import Counter

from case_extractor import parse_log, parse_ts, filter_window, ChatInput, load_chat_inputs


def _slugify(name: str) -> str:
    """Convert a label to a snake_case ID."""
    s = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
    return s[:40]


def _is_supplier_chat(label: str) -> bool:
    return bool(re.search(r'supplier|vendor', label, re.IGNORECASE))


def _is_internal_chat(label: str) -> bool:
    return bool(re.search(r'task|staff|internal|all.staff', label, re.IGNORECASE))


def _is_client_chat(label: str) -> bool:
    """Anything that's not supplier or internal is assumed to be a client chat."""
    return not _is_supplier_chat(label) and not _is_internal_chat(label)


def _extract_entity_name(label: str) -> str:
    """Extract a short entity name from a chat label."""
    # Remove common suffixes
    name = re.sub(r'\s*(group|chat|jobs|supplier|client|internal|staff)\s*', ' ', label, flags=re.IGNORECASE)
    name = re.sub(r'\s*\(.*?\)\s*', ' ', name)  # remove parentheticals
    return name.strip() or label


def _extract_aliases(entity_name: str, senders: list[str], label: str) -> list[str]:
    """Generate aliases from entity name and sender patterns."""
    aliases = set()

    # From entity name: split into words, add individual words >3 chars
    words = entity_name.lower().split()
    for w in words:
        w = re.sub(r'[^a-z0-9]', '', w)
        if len(w) > 3:
            aliases.add(w)
    if len(words) > 1:
        aliases.add(entity_name.lower())

    # From senders: common patterns
    for sender in senders:
        # Skip generic staff names
        if re.search(r'ashish|samita|abhisha|mousumi|rahul|samsul|pramod|staff', sender, re.IGNORECASE):
            continue
        # Extract meaningful parts of sender names
        parts = re.sub(r'[^a-z\s]', '', sender.lower()).split()
        for p in parts:
            if len(p) > 3 and p not in ('this', 'message', 'deleted', 'media', 'omitted', 'new', 'sir'):
                aliases.add(p)

    # From label
    label_words = re.sub(r'[^a-z\s]', '', label.lower()).split()
    for w in label_words:
        if len(w) > 3 and w not in ('group', 'chat', 'jobs', 'supplier', 'client', 'internal', 'staff', 'shared'):
            aliases.add(w)

    return sorted(aliases)


def build_seed(metadata_path: Path) -> dict:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    case_id = metadata.get("id", "unknown")

    ci = metadata.get("chat_inputs", {})
    start_str = ci.get("start", "")
    end_str = ci.get("end", "")

    base_dir = Path.cwd()
    _, _, chats = load_chat_inputs(metadata, base_dir)

    # Parse date window
    try:
        start_dt = parse_ts(start_str)
        end_dt = parse_ts(end_str)
    except ValueError:
        print(f"Warning: could not parse date window ({start_str} - {end_str}), using all messages")
        start_dt = end_dt = None

    monitored_groups = {}
    entities = []
    tasks = []
    entity_ids_seen = set()

    for chat in chats:
        group_id = chat.path.name
        label = chat.label

        # Parse and sample messages
        txt_files = list(chat.path.glob("*.txt"))
        senders = []
        if txt_files:
            messages = parse_log(txt_files[0])
            if start_dt and end_dt:
                messages = filter_window(messages, start_dt, end_dt)
            # Collect unique senders
            sender_counts = Counter(m.sender for m in messages)
            senders = [s for s, _ in sender_counts.most_common(10)]
            print(f"  [{group_id}] {label}: {len(messages)} msgs, top senders: {senders[:5]}")
        else:
            print(f"  [{group_id}] {label}: no .txt file found")

        # Classify chat type and build seed entries
        if _is_internal_chat(label):
            monitored_groups[group_id] = None
            print(f"    → shared group (null mapping)")
        elif _is_supplier_chat(label):
            entity_name = _extract_entity_name(label)
            entity_id = f"entity_{_slugify(entity_name)}"
            task_id = f"task_{_slugify(entity_name)}_supplier"
            aliases = _extract_aliases(entity_name, senders, label)

            monitored_groups[group_id] = task_id

            if entity_id not in entity_ids_seen:
                entities.append({
                    "id": entity_id,
                    "name": entity_name,
                    "aliases": aliases,
                })
                entity_ids_seen.add(entity_id)

            tasks.append({
                "id": task_id,
                "order_type": "supplier_order",
                "client_id": "FILL_CLIENT_ENTITY_ID",
                "supplier_ids": [entity_id],
                "stage": "active",
            })
            print(f"    → supplier task: {task_id} (entity: {entity_id}, aliases: {aliases})")
        else:
            # Client chat
            entity_name = _extract_entity_name(label)
            entity_id = f"entity_{_slugify(entity_name)}"
            task_id = f"task_{_slugify(entity_name)}_client"
            aliases = _extract_aliases(entity_name, senders, label)

            monitored_groups[group_id] = task_id

            if entity_id not in entity_ids_seen:
                entities.append({
                    "id": entity_id,
                    "name": entity_name,
                    "aliases": aliases,
                })
                entity_ids_seen.add(entity_id)

            tasks.append({
                "id": task_id,
                "order_type": "standard_procurement",
                "client_id": entity_id,
                "supplier_ids": [],
                "stage": "active",
            })
            print(f"    → client task: {task_id} (entity: {entity_id}, aliases: {aliases})")

    # Link supplier tasks to client entities
    client_entity_ids = [e["id"] for e in entities
                         if any(t["client_id"] == e["id"] for t in tasks)]
    if client_entity_ids:
        for task in tasks:
            if task["order_type"] == "supplier_order" and task["client_id"] == "FILL_CLIENT_ENTITY_ID":
                task["client_id"] = client_entity_ids[0]  # best guess: first client
                task["_note"] = "auto-linked to first client — verify"

    # Deduplicate tasks that map to the same group
    seen_task_ids = set()
    deduped_tasks = []
    for t in tasks:
        if t["id"] not in seen_task_ids:
            deduped_tasks.append(t)
            seen_task_ids.add(t["id"])

    seed = {
        "_comment": f"Auto-generated seed for {case_id}. Review aliases, client_id linkages, and group mappings.",
        "monitored_groups": monitored_groups,
        "entities": entities,
        "tasks": deduped_tasks,
    }

    return seed


def main():
    parser = argparse.ArgumentParser(description="Generate seed_tasks.json from eval case metadata")
    parser.add_argument("--case", required=True, help="Eval case directory with metadata.json")
    parser.add_argument("--output", help="Output path (default: integration_tests/<case>/seed_tasks.json)")
    args = parser.parse_args()

    case_dir = Path(args.case)
    metadata_path = case_dir / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"No metadata.json in {case_dir}")

    if args.output:
        output_path = Path(args.output)
    else:
        integration_dir = Path("tests/integration_tests") / case_dir.name
        integration_dir.mkdir(parents=True, exist_ok=True)
        output_path = integration_dir / "seed_tasks.json"

    meta = json.loads(metadata_path.read_text())
    print(f"Case: {meta.get('id', '?')} — {meta.get('name', '')}")

    seed = build_seed(metadata_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(seed, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWritten to: {output_path}")
    print(f"Tasks: {len(seed['tasks'])}, Entities: {len(seed['entities'])}, Groups: {len(seed['monitored_groups'])}")
    print("\n⚠  Review the output — check aliases, client_id linkages, and group mappings.")


if __name__ == "__main__":
    main()
