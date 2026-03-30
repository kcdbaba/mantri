# Integration Tests

End-to-end replay tests that feed real WhatsApp message sequences through the
production architecture (router → update_agent → linkage_agent) and verify
state evolution.

## Test types

### Dry replay (no LLM)

Feeds each message through the deterministic layers only:
- **Router** (`route()`) — verifies routing decisions (group map, entity match)
- **State assertions** — checks expected node states, items, and links at
  milestone messages without calling the LLM agents

The dry replay validates architecture plumbing: does data flow correctly through
the system? Are messages reaching the right tasks? Are DB writes structured
correctly?

Dry replay uses `expected_routing.json` as ground truth — a curated file listing
the expected routing outcome for each message (or at least for milestone
messages where routing matters).

### Live replay (with LLM)

Feeds each message through the full pipeline including `update_agent` and
`linkage_agent` LLM calls. Compares final task state against
`expected_output` from the eval case's `metadata.json`.

Live replay is non-deterministic (LLM variance) and costs API calls. Run it
deliberately, not in CI.

## Directory structure

```
tests/integration_tests/
├── README.md
├── <case_name>/
│   ├── replay_trace.json        # generated — message sequence for replay
│   ├── expected_routing.json    # curated — expected route() results per message
│   └── seed_tasks.json          # curated — task/entity DB state to seed before replay
```

## Scripts

All scripts live in `scripts/` at the project root. Run with `PYTHONPATH=.`.

### `scripts/build_replay_trace.py`

Builds `replay_trace.json` from an eval case's `metadata.json`. Parses raw
WhatsApp chat logs, interleaves messages across all threads by timestamp, and
resolves image attachment paths to actual files on disk.

```bash
python scripts/build_replay_trace.py --case tests/evals/R1-D-L3-01_sata_multi_item_multi_supplier/
```

**Input:** eval case directory with `metadata.json` (defines chat paths + time window)
**Output:** `tests/integration_tests/<case_name>/replay_trace.json` — JSON array
of message dicts in chronological order, each with:

| Field | Description |
|---|---|
| `message_id` | `{case_id}_{group_id}_{seq}` |
| `timestamp` | Unix epoch |
| `timestamp_raw` | Original WhatsApp format |
| `sender_jid` | Sender name from chat log |
| `group_id` | Chat directory name |
| `body` | Message text (empty for media-only) |
| `media_type` | `text`, `image`, or `document` |
| `image_path` | Absolute path to file on disk, or null |
| `thread_label` | Human-readable thread name |
| `thread_index` | 1-based thread number |

### `scripts/build_expected_routing.py`

Generates `expected_routing.json` by running `route()` against a seeded DB for
each message in a replay trace. The output must be **manually reviewed and
curated** before use as ground truth — the router's current behaviour is the
starting point, not necessarily correct.

```bash
python scripts/build_expected_routing.py \
    --trace tests/integration_tests/R1-D-L3-01_sata_multi_item_multi_supplier/replay_trace.json \
    --seed tests/integration_tests/R1-D-L3-01_sata_multi_item_multi_supplier/seed_tasks.json
```

### `scripts/case_extractor.py` (existing)

Generates `threads.txt` for eval cases from raw chat logs. Used upstream of the
replay trace builder — the eval case's `metadata.json` is the shared input.

## Running tests

```bash
# Dry replay only (no LLM, no API cost)
PYTHONPATH=. pytest tests/integration_tests/test_dry_replay.py -v

# Live replay (requires ANTHROPIC_API_KEY, costs money)
PYTHONPATH=. pytest tests/integration_tests/test_live_replay.py -v -s --run-live

# Live replay — update_agent only, skip linkage
PYTHONPATH=. pytest tests/integration_tests/test_live_replay.py -v -s --run-live --skip-linkage

# Live replay — first 20 messages only (quick iteration)
PYTHONPATH=. pytest tests/integration_tests/test_live_replay.py -v -s --run-live --max-messages 20
```

Live replay outputs:
- `replay_result.json` — full state snapshot + run statistics
- `replay_result.db` — SQLite DB for manual inspection

## Test methodology

1. **Select eval case** — pick a case with `metadata.json` (e.g. R1-D-L3-01)
2. **Build replay trace** — `build_replay_trace.py` parses raw chats into
   ingest-format messages with image paths
3. **Curate seed data** — create `seed_tasks.json` with the tasks/entities that
   should exist in the DB before replay starts (the router needs active tasks
   to match against)
4. **Generate expected routing** — `build_expected_routing.py` runs `route()`
   on each trace message and writes results; **manually review** to fix any
   routing errors before freezing as ground truth
5. **Run dry replay** — feed trace through router, assert against
   `expected_routing.json`
6. **Run live replay** — feed trace through full pipeline (router → update_agent
   → linkage_agent), snapshot final state (node_states, items, fulfillment_links,
   ambiguity_flags), compare against eval case `expected_output`

## Cases

### R1-D-L3-01: SATA multi-item multi-supplier

- **Source:** `tests/evals/R1-D-L3-01_sata_multi_item_multi_supplier/`
- **Window:** 3/1/26 – 3/24/26 (24 days)
- **Threads:** 4 (client group, Voltas supplier, LG supplier, internal tasks)
- **Messages:** 904 (13 with images on disk, 289 media-omitted)
- **Complexity:** Multiple items across multiple suppliers, GEM portal links,
  staff coordination, payment tracking
