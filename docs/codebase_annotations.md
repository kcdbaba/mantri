Now I have comprehensive knowledge of the codebase. Let me create a detailed annotation document. Since I'm in READ-ONLY mode, I'll provide the complete document as text output.

## MANTRI CODEBASE ANNOTATION DOCUMENT

This document maps every code path, branch, injection point, and external call in the Mantri codebase for validation without running tests.

---

### 1. MESSAGE PROCESSING PIPELINE

#### Entry Points
- **router.route(message)** - Classifies message to entity_ids
- **worker.process_message(message, r)** - Per-message processing with agent call
- **worker.process_message_batch(task_id, messages, r)** - Batch processing for accumulated messages
- **worker.replay_messages(messages, r, conv_router)** - Shared entry for test harness and production

#### Complete Flow: route() → agent → _apply_output()

```
route(message: dict) → [(entity_id, confidence), ...]
  ├─ Layer 1: Noise filter (reactions, stickers, system, revoked)
  │   └─ Returns [] if media_type in NOISE_TYPES
  │   └─ Returns [] if empty (no body + no image)
  │
  ├─ Layer 2a: Direct group → entity map
  │   ├─ Check group_id in MONITORED_GROUPS
  │   ├─ If value is not None: resolve to entity_id, conf=DIRECT_GROUP_CONFIDENCE (0.90)
  │   ├─ If value is None + ENABLE_CONVERSATION_ROUTING: return [("__conv_pending__", 0.0)]
  │   └─ Layer 2a+: Runtime entities from task_routing_context (conf=RUNTIME_TASK_CONFIDENCE 0.85)
  │
  ├─ Layer 2b: Entity keyword + alias matching (rapidfuzz)
  │   └─ match_entities(body) → [(entity_id, confidence), ...]
  │   └─ Filter by ENTITY_MATCH_CONFIDENCE threshold (0.75)
  │
  ├─ Layer 2c: MiniLM embedding similarity (STUB — deferred post-Sprint 3)
  │   └─ Plan: encode body with all-MiniLM-L6-v2, cosine against context_embedding
  │   └─ Threshold: 0.65, EMA update on hit
  │
  └─ Layer 2d: Gemini Flash 8B routing (STUB — fail-up from 2c)
      └─ Plan: call if 2c < 0.65, inputs: body + active task summaries

process_message(message, r, conv_router=None)
  ├─ routes = route(message)
  ├─ If routes == [("__conv_pending__", 0.0)]:
  │   └─ conv_router.feed(message) → ConversationResult (or None if buffered)
  │   └─ If result: _process_conversation_result(result, r, conv_router)
  │   └─ Return (skip entity routing)
  │
  ├─ For each (entity_id, confidence) in routes:
  │   ├─ entity_tasks = get_tasks_for_entity(entity_id)
  │   ├─ resolved = _resolve_task_for_entity(entity_id, entity_tasks, message, r)
  │   │   ├─ If no tasks: CREATE live task (order_type via supplier: prefix check)
  │   │   ├─ If 1 task: RETURN it (single-task path)
  │   │   └─ If 2+ tasks: RETURN None (agent decides assignment)
  │   │
  │   ├─ If resolved is not None (single task):
  │   │   ├─ append_message(task_id, message, routing_confidence=confidence)
  │   │   ├─ output = run_update_agent(task_id, [message], task_override, routing_confidence)
  │   │   └─ For each task_output in output.task_outputs:
  │   │       └─ _apply_output(task_id, order_type, task_output, message, r)
  │   │
  │   └─ Else (multi-task):
  │       ├─ anchor_task_id = first immature task (or first if all mature)
  │       ├─ append_message(anchor_task_id, message, routing_confidence=confidence)
  │       ├─ output = run_update_agent(anchor_task_id, [message], entity_tasks=entity_tasks)
  │       └─ For each task_output in output.task_outputs:
  │           ├─ target_id = task_output.task_assignment
  │           ├─ If target_id == "new" + ENABLE_LIVE_TASK_CREATION:
  │           │   └─ CREATE new task (order_type from task_output.new_task_order_type)
  │           ├─ If target_id not in valid_ids: log warning, skip
  │           ├─ If target_id != anchor_task_id:
  │           │   └─ append_message(target_id, message, routing_confidence=0.85)
  │           └─ _apply_output(target_id, target_order_type, task_output, message, r)
```

#### MessageBuffer: Scrap Accumulation (entity groups only)

```
MessageBuffer (in worker.py)
  ├─ Groups by (entity_id, sender)
  ├─ Accumulates into sender-based scraps
  ├─ Flushes when:
  │   ├─ Gap since last message from same sender > SCRAP_GAP_S (60s)
  │   └─ Scrap reaches SCRAP_MAX_SIZE (10 messages)
  │
  └─ _process_scrap(scrap) → process_message_batch(task_id, messages)

replay_messages(messages, r, conv_router)
  ├─ buf = MessageBuffer()
  ├─ For each message:
  │   ├─ routes = route(message)
  │   ├─ If routes == [("__conv_pending__", 0.0)]:
  │   │   └─ conv_router.feed(message) — buffered or flushed
  │   ├─ Else for each (entity_id, confidence):
  │   │   ├─ flushed = buf.add(entity_id, message)
  │   │   └─ _process_scrap(scrap) for each flushed
  │   └─ stale = buf.flush_stale(msg_ts) — gap-based check
  │
  └─ At end:
      ├─ buf.flush_all() → remaining scraps
      └─ conv_router.flush_all() → remaining conversations
```

---

### 2. UPDATE AGENT CALL PIPELINE

#### Entry: run_update_agent(task_id, messages, overrides...)

```
run_update_agent(task_id, messages, ...)
  ├─ Normalize messages to list
  ├─ Load state (or use overrides):
  │   ├─ node_states = get_node_states(task_id)
  │   ├─ recent_messages = get_recent_messages(task_id, MAX_CONTEXT_MESSAGES=20)
  │   ├─ current_items = get_order_items(task_id)
  │
  ├─ Build prompts:
  │   ├─ system_prompt = build_system_prompt(task_id)
  │   │   ├─ Task template nodes (JSON)
  │   │   ├─ Business context (hardcoded Uttam Enterprise, staff, language)
  │   │   └─ Output spec (node status values, ambiguity rules, item extraction, etc.)
  │   │
  │   └─ user_section = build_user_section(node_states, recent_messages, new_messages, items, entity_tasks)
  │       ├─ Current node states (excluding skipped)
  │       ├─ Current order items (formatted as JSON)
  │       ├─ Active tasks for entity (if multi-task) with [CONFIRMED] tag
  │       └─ Recent messages + new message(s)
  │
  ├─ Load image (if present):
  │   └─ _load_image(messages[-1]) → (bytes, media_type) or (None, "")
  │
  ├─ Select model:
  │   └─ _select_model(messages):
  │       ├─ If any message has numbers, items, image, or length > 40 chars → CLAUDE_MODEL (Sonnet)
  │       └─ Else → GEMINI_MODEL (Flash)
  │
  ├─ Call LLM:
  │   └─ _call_with_retry(system_prompt, user_section, message_id, task_id, image, model)
  │       ├─ Dispatch to _call_anthropic_with_retry or _call_gemini_with_retry
  │       ├─ Anthropic: messages.create(model, max_tokens=2048, system=[text with cache_control], messages=[{role, content}])
  │       ├─ Retry: max 3 attempts, exponential backoff (1s, 4s, 16s)
  │       └─ Returns: LLMResponse(raw, tokens_in, tokens_out, cache_creation, cache_read)
  │
  ├─ Parse output:
  │   ├─ _parse_raw(raw) → AgentOutput or None
  │   ├─ Strip markdown fences (```)
  │   ├─ Extract JSON from mixed content (find first { ... last })
  │   ├─ Validate with pydantic: AgentOutput.model_validate(data)
  │   └─ On failure: retry once with correction prompt
  │
  ├─ Log tokens:
  │   └─ log_llm_call(call_type, model, tokens_in, tokens_out, duration_ms, message_id, task_id, cache tokens)
  │
  └─ Return: AgentOutput(task_outputs=[...]) or None
```

#### AgentOutput Structure
```
TaskOutput(per task):
  ├─ task_assignment: str (existing task_id or "new")
  ├─ new_task_order_type: str | None ("client_order" or "supplier_order" if "new")
  ├─ node_updates: [NodeUpdate(...)]
  ├─ ambiguity_flags: [AmbiguityFlag(...)]
  ├─ item_extractions: [ItemExtraction(...)]
  ├─ node_data_extractions: [NodeDataExtraction(...)]
  └─ new_task_candidates: [dict(...)]

NodeUpdate:
  ├─ node_id: str
  ├─ new_status: Literal[pending, active, in_progress, completed, blocked, provisional, skipped, failed, partial]
  ├─ confidence: float [0, 1]
  └─ evidence: str

AmbiguityFlag:
  ├─ description: str
  ├─ severity: Literal[high, medium, low]
  ├─ category: Literal[entity, quantity, status, timing, linkage]
  └─ blocking_node_id: str | None
```

---

### 3. OUTPUT APPLICATION PIPELINE

#### _apply_output(task_id, order_type, output, message, r)

```
_apply_output(task_id, order_type, output, message, r)
  │
  ├─ 1. Apply node updates:
  │   │   For each update in output.node_updates:
  │   │   ├─ If confidence < PROVISIONAL_THRESHOLD (0.75) and status not in [pending, provisional]:
  │   │   │   └─ Downgrade status to "provisional"
  │   │   ├─ update_node_as_update_agent(task_id, node_id, status, confidence, message_id)
  │   │   └─ Log: "Node update: task=X node=Y → status (conf=Z) | evidence"
  │   │
  │   └─ 2. Stock path auto-trigger:
  │       └─ If any update is filled_from_stock:
  │           └─ check_stock_path_order_ready(task_id) → sets order_ready if all conditions met
  │
  ├─ 3. Cascade downstream auto-triggers:
  │   │   cascade_auto_triggers(task_id)
  │   │   ├─ Check all auto_trigger nodes
  │   │   ├─ If predecessor completed → activate (pending → active) if not already
  │   │   └─ Auto-triggers: order_confirmation, order_ready, predispatch_checklist, delivery_photo_check
  │   │
  │   └─ Auto-trigger rules:
  │       ├─ auto_trigger nodes activate automatically on predecessor completion
  │       ├─ Exception: order_ready managed by linkage_agent for supplier-fulfilled, NOT here
  │       └─ Set status "active" once, then wait for message evidence to complete
  │
  ├─ 4. New task candidates:
  │   └─ For each candidate in output.new_task_candidates:
  │       └─ _log_new_task_candidate(candidate, message, task_id) → task_event_log table
  │
  ├─ 5. Ambiguity escalation:
  │   └─ For each flag in output.ambiguity_flags:
  │       └─ _handle_ambiguity(flag, task_id, message)
  │           ├─ Insert into ambiguity_queue
  │           ├─ Compute escalation_target from ACTIVE_ESCALATION_PROFILE + ESCALATION_CATEGORY_OVERRIDES
  │           │   ├─ If severity="high" or (medium and confidence < threshold):
  │           │   │   └─ escalation_target = escalation_target_high
  │           │   └─ Else if severity="low" or (low confidence):
  │           │       └─ escalation_target = escalation_target_low
  │           └─ If blocking_node_id set: mark node as "blocked" (blocks gate nodes only if block_scope="gate_only")
  │
  ├─ 6. Node data extractions:
  │   └─ apply_node_data_extractions(task_id, extractions)
  │       ├─ For each extraction:
  │       │   ├─ Load existing node_data (JSON)
  │       │   ├─ Merge new data (new keys overwrite, absent keys preserved)
  │       │   └─ Write back to task_nodes.node_data
  │       └─ Fields like delivery_deadline, expected_delivery_date → used by linkage/time-trigger
  │
  ├─ 7. Item extractions:
  │   ├─ apply_item_extractions(task_id, order_type, extractions)
  │   │   ├─ For each extraction:
  │   │   │   ├─ operation="add": INSERT into client_order_items or supplier_order_items
  │   │   │   ├─ operation="update": UPDATE by matching existing_description
  │   │   │   └─ operation="remove": DELETE by matching existing_description
  │   │   └─ Returns list of changed descriptions
  │   │
  │   └─ _check_post_confirmation_item_changes(task_id, order_type, extractions, message)
  │       └─ If order_confirmation=completed (client) or supplier_collection=completed (supplier):
  │           └─ Raise ambiguity_flag(severity=high, category=quantity, blocking_node)
  │
  └─ 8. Publish to linkage stream:
      └─ _publish_task_event(task_id, message, r)
          └─ r.xadd(TASK_EVENTS_STREAM, {"event_type": "message_processed", "task_id", "message_id", "message_json"})
```

#### Post-Application State Checks
- Node status downgrades: confidence < 0.75 → "provisional"
- Gate node blocking: blocking_node in GATE_NODES and block_scope="gate_only"
- Cascade activation: auto_trigger predecessors → activate dependents
- Stock path: filled_from_stock + supplier skipped → order_ready=completed
- Item lock: post-confirmation item changes → high severity ambiguity

---

### 4. CONVERSATION ROUTING PIPELINE

#### Shared Group Processing (MONITORED_GROUPS[group_id] == None)

```
ConversationRouter (in conversation_router.py)
  │
  ├─ init: FLUSH_GAP_S=300 (5 minutes), enable_llm_matching=False
  │
  ├─ feed(message) → ConversationResult | None
  │   ├─ Check buffer for group_id
  │   ├─ If exists and gap > FLUSH_GAP_S: flush previous buffer, create new
  │   ├─ Add message to fresh buffer
  │   └─ Return flushed result (or None if just buffered)
  │
  ├─ flush_stale(now) → [ConversationResult]
  │   └─ For each buffer with idle > FLUSH_GAP_S: flush and remove
  │
  ├─ flush_all() → [ConversationResult]
  │   └─ Flush all remaining buffers (end of replay)
  │
  └─ _flush_buffer(buf) → ConversationResult
      │
      ├─ 1. OCR enrichment:
      │   │   _enrich_with_ocr(messages, group_id)
      │   │   ├─ Load OCR cache from _ocr_cache[group_id]
      │   │   ├─ Append OCR text to empty message bodies (never replace)
      │   │   ├─ Carry resolutions mapping as _ocr_resolutions metadata
      │   │   └─ Entity detection can use resolutions as additional evidence
      │   │
      │   └─ load_ocr_cache(ocr_path) → {message_id: ocr_data}
      │       └─ OCR data: {extracted_text, description, resolutions, category}
      │
      ├─ 2. Scrap detection (per-sender, topic-bounded):
      │   │   scraps = detect_scraps(messages, group_id)
      │   │   ├─ Partition by sender → strands
      │   │   ├─ Split on entity/item mention change OR gap > BURST_GAP_S (900s = 15min)
      │   │   ├─ extract_entity_refs(body) → [{ref, confidence}]
      │   │   │   ├─ Layer 1: Known aliases → confidence 0.95
      │   │   │   ├─ Layer 2: ORBAT numbered units → confidence 0.85
      │   │   │   ├─ Layer 3: Army unit regex → confidence 0.6
      │   │   │   ├─ Layer 3.5: Unnumbered unit type combos → confidence 0.5
      │   │   │   └─ Layer 4: "from X" / "return from X" → confidence 0.6
      │   │   │
      │   │   └─ Output: Scrap(id, group_id, sender, entity_matches, messages, status)
      │   │
      │   └─ ORBAT lookup: indian_military_units_reference.json → numbered_units
      │
      ├─ 3. Conversation building (forward propagation):
      │   │   conversations = build_conversations(scraps, group_id, task_items, task_entities)
      │   │   ├─ Sort scraps chronologically
      │   │   ├─ For each scrap:
      │   │   │   ├─ If entity_matches: create/find conversation
      │   │   │   ├─ Backward propagation: assign recent buffered scraps (BACKPROP_MAX_S=3600)
      │   │   │   ├─ Else if sender has current context: continue in same conversation (gap ≤ 1800s)
      │   │   │   └─ Else: buffer unassigned
      │   │   │
      │   │   ├─ Item-matcher fallback: resolve_scrap_entity_by_items()
      │   │   │   └─ If no entity evidence: try fuzzy matching scrap text against active task items
      │   │   │
      │   │   └─ Bookkeeping singleton: dual-assign payment messages
      │   │       └─ is_payment_message(text) → both order conversation AND singleton:bookkeeping
      │   │
      │   └─ Output: [Conversation(...)]
      │
      ├─ 4. Reply-tree cross-sender enhancement:
      │   │   _enhance_with_reply_tree(conversations, scraps, threaded, messages, group_id)
      │   │   ├─ build_reply_tree(text_messages) → [ThreadedMessage]
      │   │   │   └─ Link messages by reply context (next_message field)
      │   │   │
      │   │   └─ Entity evidence from ANY message in thread covers whole thread
      │   │       (unlike per-sender scraps where only mentioned entity applies)
      │   │
      │   └─ Advantage: cross-sender context = higher coverage (scrap 77%, reply-tree 85%+)
      │
      ├─ 5. Date-based deterministic matching:
      │   │   _enhance_with_date_matching(conversations, scraps, task_entities, group_id)
      │   │   ├─ extract_timeline(tasks, node_states, task_entities) → [TimelineEvent]
      │   │   │   ├─ Extract dates from node_data: delivery_deadline, dispatch_date, payment_date, etc.
      │   │   │   └─ window: DELIVERY_WINDOW_DAYS=2, PAYMENT_WINDOW_DAYS=1
      │   │   │
      │   │   └─ match_by_date(unassigned_scraps, timeline) → [DateMatch]
      │   │       ├─ Scrap timestamp in event window + has keyword → match entity
      │   │       ├─ Keywords: DELIVERY_KEYWORDS or PAYMENT_KEYWORDS
      │   │       └─ Only match if exactly one entity (no ambiguity)
      │   │
      │   └─ Deterministic: no LLM, high precision
      │
      ├─ 6. LLM backward context matching (if enable_llm_matching):
      │   │   _enhance_with_llm_context(conversations, scraps, group_id)
      │   │   ├─ For each assigned scrap with entity evidence:
      │   │   │   ├─ Look backward 16 working hours (LOOKBACK_WORKING_HOURS=16.0)
      │   │   │   ├─ Find unassigned scraps in that window (skip < 1 working hour)
      │   │   │   └─ Call Gemini Flash with anchor + candidates
      │   │   │
      │   │   └─ match_backward_context(assigned, all_scraps, assigned_ids) → [(scrap_id, entity_ref)]
      │   │       ├─ _call_llm_judge(entity_ref, anchor, candidates)
      │   │       │   └─ Prompt: conservative judgment (≥ 0.75 confidence only)
      │   │       ├─ Retry: max 2 attempts
      │   │       └─ Cost: ~$0.002 per replay (Gemini Flash free tier)
      │   │
      │   └─ Working hours: 9am-7pm IST, Mon-Sat, excluding holidays
      │
      ├─ 7. Entity learning:
      │   │   discover_entities(messages, known_aliases)
      │   │   ├─ Scan for contact shares: name + phone / VCF files
      │   │   ├─ "From X" supplier introductions
      │   │   ├─ Unnumbered military unit references
      │   │   └─ Output: [DiscoveredEntity(...)] queued for confirmation
      │   │
      │   └─ Store discovered entities → entity_discovery table (not auto-added)
      │
      └─ 8. Return ConversationResult:
          ├─ conversations: [Conversation(...)]
          ├─ unassigned_messages: messages not in any scrap
          ├─ total_messages: len(buf.messages)
          └─ discovered_entities: [DiscoveredEntity(...)]
```

#### Conversation Result Processing

```
_process_conversation_result(result, r, conv_router)
  │
  ├─ For each conversation in result.conversations:
  │   ├─ Get entity_ref (unit:X, supplier:X, or singleton:bookkeeping)
  │   │
  │   ├─ Scrap-level message accumulation:
  │   │   └─ For each scrap in conversation:
  │   │       └─ Batch by sender within the scrap (already grouped)
  │   │
  │   └─ Route via router to entity:
  │       └─ If entity_ref starts with "unit:" or "supplier:": 
  │           └─ Create/find raw entity, resolve to task, run agent
  │       └─ If singleton:bookkeeping:
  │           └─ Create internal bookkeeping task
  │
  ├─ Unrouted messages:
  │   └─ log to UNROUTED_LOG_PATH (not escalated, just logged)
  │
  └─ Discovered entities:
      └─ Store in discovered_entities queue (dashboard for Ashish confirmation)
```

---

### 5. LINKAGE AGENT PIPELINE

#### Entry: Subscribes to TASK_EVENTS_STREAM

```
linkage_worker.run()
  │
  ├─ Redis consumer group: linkage_worker_group on TASK_EVENTS_STREAM
  ├─ Creates consumer group on first run (CONSUMER_GROUP="linkage_worker_group")
  │
  ├─ Main loop: xreadgroup(CONSUMER_GROUP, CONSUMER_NAME, {TASK_EVENTS_STREAM: ">"}, block=5s)
  │   │
  │   └─ For each event_id, fields:
  │       │
  │       ├─ Check event_type == "message_processed" (skip others)
  │       ├─ Parse message_json from fields
  │       ├─ Get open orders summary (client + supplier tasks)
  │       ├─ Skip if no client orders (M:N requires at least one client)
  │       │
  │       └─ run_linkage_agent(open_orders, fulfillment_links, message)
  │           │
  │           ├─ 1. Build input:
  │           │   ├─ system_prompt = build_system_prompt() (static, cached)
  │           │   └─ user_section = build_user_section(open_orders, fulfillment_links, message)
  │           │       ├─ All open client orders (task_id, items)
  │           │       ├─ All open supplier orders (task_id, items)
  │           │       ├─ Current fulfillment links (all matches)
  │           │       └─ New message (timestamp, sender, body)
  │           │
  │           ├─ 2. Call LLM:
  │           │   ├─ Model: CLAUDE_MODEL (Sonnet) — always, not negotiable
  │           │   ├─ (Gemini Flash available only for benchmarking, not production)
  │           │   └─ Same retry logic as update_agent (_call_with_retry, 3 attempts)
  │           │
  │           ├─ 3. Parse output:
  │           │   └─ LinkageAgentOutput.model_validate(data)
  │           │       ├─ linkage_updates: [LinkageUpdate(...)]
  │           │       ├─ client_order_updates: [ClientOrderUpdate(...)]
  │           │       ├─ new_task_candidates: [dict(...)]
  │           │       └─ ambiguity_flags: [LinkageAmbiguityFlag(...)]
  │           │
  │           └─ 4. Log tokens:
  │               └─ log_llm_call(call_type=linkage_agent, ...)
  │
  ├─ Apply linkage_updates:
  │   │
  │   └─ For each update:
  │       ├─ upsert_fulfillment_link(...)
  │       │   ├─ If match_confidence >= 0.92 and status=="candidate": auto-confirm
  │       │   └─ INSERT OR REPLACE into fulfillment_links
  │       │
  │       └─ Log: "Linkage: client → supplier [status] conf=X | reasoning"
  │
  ├─ Apply client_order_updates:
  │   │
  │   └─ For each update:
  │       ├─ update_node_as_linkage_agent(task_id, node_id, status, confidence, message_id)
  │       └─ (No agent call — direct DB write)
  │
  ├─ Reconcile order_ready:
  │   │
  │   └─ For each affected client order:
  │       ├─ reconcile_order_ready(client_task_id)
  │       │   ├─ Check all fulfillment links for this client order
  │       │   ├─ If all items allocated (sum quantities = required): order_ready=completed
  │       │   ├─ If partial: order_ready=partial
  │       │   ├─ Else: skip (order_ready stays pending)
  │       │   └─ Returns what was set (or None)
  │       │
  │       └─ Log: "reconcile_order_ready: task=X → order_ready=Y"
  │
  ├─ Prune terminal links:
  │   │
  │   ├─ For supplier orders with all links in {fulfilled, failed, invalidated}:
  │   │   ├─ DELETE fulfillment_links
  │   │   ├─ update_node(task_closed, completed)
  │   │   └─ close_task(supplier_order_id)
  │   │
  │   └─ For client orders with all links in {completed}:
  │       ├─ DELETE fulfillment_links
  │       ├─ update_node(task_closed, completed)
  │       └─ close_task(client_order_id)
  │
  ├─ Log new task candidates:
  │   └─ For each candidate: INSERT into task_event_log
  │
  ├─ Escalate ambiguity flags:
  │   │
  │   └─ For each flag:
  │       ├─ Convert to AmbiguityFlag
  │       ├─ If affected_task_ids: escalate per task_id
  │       └─ Else: escalate to source task_id from event
  │
  ├─ Log event:
  │   └─ INSERT into task_event_log(event_type=linkage_processed, ...)
  │
  ├─ Ack event:
  │   └─ r.xack(TASK_EVENTS_STREAM, CONSUMER_GROUP, event_id)
  │
  └─ Retry + dead-letter:
      ├─ On error: retry up to 3 times with linear backoff (1s, 2s)
      ├─ On exhaustion: INSERT into dead_letter_events, then ack
      └─ Developer inspects dead_letter_events, replays by re-publishing
```

#### Linkage Status Lifecycle
- **candidate**: allocated but unconfirmed (confidence < 0.92)
- **confirmed**: high-confidence (confidence >= 0.92)
- **failed**: QC failure on collection
- **fulfilled**: supplier QC passed
- **completed**: client delivery confirmed
- **invalidated**: order cancelled/truncated

---

### 6. LLM CALL SITES AND CACHING

#### Update Agent LLM Calls

| Site | Model Selection | Cache Status | Line | Input Size | Retry |
|------|-----------------|--------------|------|-----------|-------|
| run_update_agent @ 172 | _select_model() → Sonnet or Flash | Anthropic: ephemeral cache | 321-326 | system + user | 3× |
| _call_anthropic_with_retry @ 321 | Parameterized | YES (system, cache_control) | 324 | 2K tokens avg | exponential |
| _call_gemini_with_retry @ 374 | Parameterized | NO (Gemini native cache TBD) | 374-376 | 2K tokens avg | exponential |
| Retry @ 200 | Same model | Same cache | 200-207 | system + correction | 1× |

#### Linkage Agent LLM Calls

| Site | Model | Cache Status | Line | Input Size | Retry |
|------|-------|--------------|------|-----------|-------|
| run_linkage_agent @ 85 | Sonnet (always) | Anthropic: ephemeral | 90-92 | order summary + links | 3× |

#### Conversation Routing LLM Calls

| Site | Model | Cache Status | Line | Input Size | Retry |
|------|-------|--------------|------|-----------|-------|
| _call_llm_judge (via _try_gemini) @ 205 | Gemini Flash 2.5 | NO | 205 | ~2K tokens | 2× |

#### Vision/OCR Calls

| Site | Model | Cache Status | Line | Input Size | Retry |
|------|-------|--------------|------|-----------|-------|
| process_image @ 238 | Gemini Flash 2.5 | File cache (JSON) | 238 | Image bytes | 1× (implicit) |

---

### 7. DEV-TEST CACHE MECHANISM

#### Agent Cache (instrumented_replay.py + agent_cache.py)

```
Development testing with --dev-test flag:
  │
  ├─ Cache initialization:
  │   ├─ agent_cache.init(case_dir / "dev_cache.db")
  │   ├─ Opens SQLite with WAL journal mode
  │   └─ Schema: llm_cache(key, raw, tokens_in, tokens_out, cache_creation, cache_read)
  │
  ├─ Cache key generation:
  │   ├─ make_key(system_prompt, user_section) → SHA256(system + "\n---\n" + user)[:16]
  │   └─ Deterministic: same prompts → same key
  │
  ├─ Dev-test LLM call interception (instrumented_replay.py):
  │   │
  │   └─ _traced_call_with_retry():
  │       ├─ If dev_test=True:
  │       │   ├─ cache_key = make_key(system, user)
  │       │   ├─ cached = agent_cache.get(cache_key)
  │       │   ├─ If cached: return LLMResponse (cached) — 0 API calls, 0 tokens billed
  │       │   ├─ Else if allow_api_calls=False: raise RuntimeError (cache miss → use --run-live)
  │       │   └─ Else: call LLM normally → cache response for future runs
  │       │
  │       └─ If dev_test=False: normal path (no cache)
  │
  ├─ Cache writes (atomic):
  │   ├─ agent_cache.put(key, raw, tokens_in, tokens_out, cache_tokens)
  │   ├─ INSERT OR REPLACE (atomic upsert)
  │   └─ Committed immediately — no data loss on crash
  │
  ├─ Cache stats:
  │   ├─ agent_cache.stats() → {entries, hits, misses, hit_rate}
  │   └─ Printed at end of replay
  │
  └─ Typical dev workflow:
      ├─ First run: --run-live --dev-test → populates cache, runs full pipeline
      ├─ Subsequent runs: (no flags) → uses cache, < 1s per replay, free
      └─ When prompt changes: cache miss → must --run-live again
```

#### What Cache Covers vs Misses

```
COVERS (intercepted at _call_with_retry):
  ├─ Update agent calls (Sonnet + Flash)
  ├─ Linkage agent calls (Sonnet)
  ├─ Full LLM invocation including token counts
  └─ Works for both development and test harness

MISSES (not cached):
  ├─ Conversation router LLM calls (Gemini backward context) — separate path
  ├─ Vision/OCR calls (image_ocr.py) — separate path, has own JSON cache
  └─ Non-LLM determinism: routing, task resolution, node updates, item extraction
```

---

### 8. MONKEY-PATCHING IN INSTRUMENTED REPLAY

#### Patches Applied (Non-Behavior-Changing Tracing)

```
instrumented_replay.py patches (lines 138-162):
  │
  ├─ Config patches:
  │   ├─ src.store.db.DB_PATH → case_dir (isolated DB)
  │   ├─ src.config.DB_PATH → case_dir
  │   ├─ src.router.router.MONITORED_GROUPS → test seed monitored_groups
  │   └─ src.config.ENABLE_CONVERSATION_ROUTING → True/False per test
  │
  ├─ Tracing wrappers (no behavior change except dev_test cache):
  │   │
  │   ├─ _traced_route:
  │   │   ├─ Wraps route(message)
  │   │   ├─ Records routing layer (2a, 2b, conv_pending, unrouted)
  │   │   └─ mt.record_routing(routes, layer=..., is_noise=...)
  │   │
  │   ├─ _traced_call_with_retry:
  │   │   ├─ Wraps _call_with_retry for LLM calls
  │   │   ├─ Implements dev-test cache (behavior change ONLY with --dev-test)
  │   │   ├─ Records: system, user, raw output, tokens, model, latency
  │   │   └─ mt.record_llm_call(call_type, task_id, system, user, raw_output, ...)
  │   │
  │   ├─ _traced_resolve:
  │   │   ├─ Wraps _resolve_task_for_entity
  │   │   ├─ Records: entity_id, tasks, resolution_method (nil_create, single, agent, resolved)
  │   │   └─ mt.record_task_resolution(entity_id, entity_tasks, resolved_id, method)
  │   │
  │   └─ _traced_apply:
  │       ├─ Wraps _apply_output (post-processing)
  │       ├─ Records: node updates, items, ambiguity flags
  │       └─ mt.record_post_processing(task_id, cascades, tasks_created, flags, items, nodes)
  │
  └─ Context manager: contextlib.ExitStack to apply all patches atomically
```

#### Behavior Changes (Intentional)

```
--dev-test flag ONLY:
  ├─ dev_test=True
  ├─ Enables agent_cache (LLM response caching)
  ├─ Cache hits return immediately (0 API calls)
  ├─ Cache misses require --run-live to add to cache
  └─ All other behavior identical to production

No --dev-test (replay mode, eval mode):
  ├─ dev_test=False
  ├─ Normal LLM calls (no cache)
  ├─ All API calls made (allow_api_calls must be True)
  └─ Tracing still works (Phoenix endpoints)
```

#### Linkage Drain in Replay

```
After each batch of scraps processed:
  │
  ├─ _drain_linkage() (line 293-300)
  │   ├─ if not run_linkage: skip
  │   ├─ new_events = mock_redis.drain_events()
  │   │   └─ Mock Redis: collects all xadd calls since last drain
  │   │
  │   └─ For each event (stream_key, fields):
  │       ├─ process_event("replay", fields, mock_redis)
  │       │   ├─ Equivalent to linkage_worker.process_event
  │       │   ├─ Runs linkage agent on the event
  │       │   ├─ Applies linkage updates (upsert links, reconcile order_ready)
  │       │   └─ No retries (single attempt in replay)
  │       │
  │       └─ stats["linkage_events_processed"] += 1
  │
  └─ At end of replay: drain remaining (linked tasks may have been created)
```

---

### 9. BRANCHES AND DECISION POINTS

#### Router Branches (router.py:78-143)

| Line | Condition | Branch | Confidence | Path |
|------|-----------|--------|------------|------|
| 84 | media_type in NOISE_TYPES | → [] | 1.0 | Layer 1 exit |
| 91 | body empty AND no image | → [] | 1.0 | Layer 1 exit |
| 99 | group_id in MONITORED_GROUPS | YES → Layer 2a | varies | confidence from MONITORED_GROUPS |
| 101 | MONITORED_GROUPS[group_id] != None | → resolve to entity | 0.90 | Direct group mapping |
| 106 | ENABLE_CONVERSATION_ROUTING | YES → [("__conv_pending__", 0.0)] | 0.0 | Shared group sentinel |
| 112 | _get_runtime_entities(group_id) | → Layer 2a+ | 0.85 | Runtime entity append |
| 123 | match_entities(body) | YES → Layer 2b | varies (capped 0.75) | Entity keyword match |
| 131 | Layer 2b results | non-empty → return | varies | Early return |
| 143 | No layers matched | → [] | - | Dead letter queue |

#### Update Agent Model Selection Branches (update_agent.py:111-118)

| Condition | Model | Reason |
|-----------|-------|--------|
| ANY message: len(body) > 40 | Sonnet | Complex reasoning needed |
| ANY message: has numbers | Sonnet | Quantity extraction required |
| ANY message: has _QUANTITY_WORDS match | Sonnet | Business entity needs accuracy |
| ALL messages simple (< 40, no numbers, no keywords) | Flash | Fast + cheap sufficient |

#### LLM Parse Failure Path (update_agent.py:237-224)

| Line | Condition | Action |
|------|-----------|--------|
| 239-251 | Parse fails (JSONDecodeError, ValidationError) | Log error, call _log_error |
| 199-207 | Parse failed, allow retry | Call _call_with_retry again with correction prompt |
| 209-210 | Retry response is None | Log failure, return None |
| 224 | Retry parse fails | Log error, return None |

#### Apply Output Branches (worker.py:35-84)

| Line | Condition | Branch |
|------|-----------|--------|
| 40-43 | confidence < PROVISIONAL_THRESHOLD | Downgrade to provisional |
| 53-56 | Any update is filled_from_stock | Call check_stock_path_order_ready |
| 59 | Cascade check result | Log cascades fired |
| 68-69 | Ambiguity flag present | Call _handle_ambiguity |
| 72-73 | node_data_extractions present | Call apply_node_data_extractions |
| 76-80 | item_extractions present | Call apply_item_extractions, check post-confirmation |

#### Task Resolution Branches (worker.py:86-107)

| Line | Condition | Branch |
|------|-----------|--------|
| 90-101 | entity_tasks is empty | CREATE live task, return task_id |
| 103-104 | len(entity_tasks) == 1 | RETURN single task_id (fast path) |
| 106-107 | len(entity_tasks) > 1 | RETURN None (multi-task, agent decides) |

#### Conversation Buffer Branches (conversation_router.py:151-181)

| Line | Condition | Branch |
|------|-----------|--------|
| 166 | group_id in _buffers | Check gap |
| 170 | gap > flush_gap_s | Flush previous, create new |
| 177 | group_id not in _buffers | Create new buffer |

#### Linkage Link Pruning Branches (linkage_worker.py:198-210)

| Line | Condition | Branch |
|------|-----------|--------|
| 199-203 | supplier status in {fulfilled, failed, invalidated} | Prune links, close supplier task |
| 205-209 | client status all == completed | Prune links, close client task |

---

### 10. LOOPS AND ITERATION

#### Router replay_messages Loop (worker.py:391-430)

```
for msg in messages:
  ├─ routes = route(msg)
  ├─ If not routes: check noise vs unrouted, continue
  ├─ If routes == [("__conv_pending__", 0.0)]: feed conv_router, continue
  ├─ For (entity_id, confidence) in routes:
  │   └─ buf.add(entity_id, msg) → returns flushed scraps
  │       └─ For scrap in flushed: _process_scrap(scrap)
  │
  └─ stale = buf.flush_stale(msg_ts): gap-based check
      └─ For scrap in stale: _process_scrap(scrap)
```

**Termination**: Explicit — finishes when all messages processed.

#### Scrap Accumulation Loop (worker.py:273-298)

```
MessageBuffer.add(entity_id, message):
  │
  ├─ Lookup (entity_id, sender) key
  ├─ If key exists: check elapsed time
  │   ├─ If elapsed > gap_s OR len(scrap) >= max_size: flush (remove from _scraps)
  │   └─ Create new scrap
  ├─ Append message to current scrap
  └─ Return flushed list
```

**Termination**: Implicit — one message = one append. Flush only on gap or size limit.

#### Conversation Building Loop (conversation_manager.py:82-200)

```
for scrap in sorted_scraps (by timestamp):
  │
  ├─ If scrap has entity_matches:
  │   ├─ Create/find conversation for each entity
  │   ├─ Backward propagation: assign recent buffered scraps (gap ≤ BACKPROP_MAX_S=3600)
  │   └─ Clear sender's buffered list
  │
  ├─ Else if sender has current context:
  │   ├─ Check time gap from last context
  │   ├─ If gap ≤ 1800s: continue in same conversation
  │   └─ Else: buffer this scrap
  │
  ├─ Else (no evidence, no context):
  │   └─ Buffer in sender's open strand
  │
  └─ Dual-assign payment messages to bookkeeping singleton
```

**Termination**: Explicit — finishes when all scraps processed.

#### Linkage Loop (linkage_worker.py:291-310)

```
while True:
  │
  ├─ xreadgroup(..., block=5000): wait for new events or timeout
  │   └─ If timeout: continue (idle wait)
  │   └─ If events: process each
  │
  ├─ For event_id, fields:
  │   ├─ process_event(event_id, fields, r)
  │   │   ├─ Raises RuntimeError on retryable failure
  │   │   └─ Returns normally on success or skip
  │   │
  │   ├─ _process_with_retry: handle exceptions
  │   │   ├─ Retry up to 3 attempts (linear backoff 1s, 2s)
  │   │   ├─ On exhaustion: dead-letter + ack
  │   │   └─ Return (continue to next event)
  │   │
  │   └─ On success: ack event (r.xack)
  │
  └─ Loop continues indefinitely
```

**Termination**: Never (daemon worker) — must be killed.

#### Node Update Cascade (task_store.py - cascade_auto_triggers)

```
def cascade_auto_triggers(task_id):
  │
  ├─ Load all node states for task
  ├─ For each auto_trigger node:
  │   ├─ Check if predecessor completed
  │   ├─ If yes and node is pending: set to active
  │   └─ Update DB
  │
  └─ Return list of activated nodes
```

**Termination**: One pass — processes all auto_triggers in node definition order.

---

### 11. ERROR HANDLING AND EXCEPTIONS

#### LLM Call Retry Loop (update_agent.py:319-342)

```
try/except in _call_anthropic_with_retry:
  │
  ├─ For attempt in range(max_retries=3):
  │   ├─ Try: _anthropic_client.messages.create(...)
  │   ├─ Except anthropic.APIStatusError:
  │   │   ├─ Log warning with attempt count
  │   │   ├─ If attempt < max_retries-1: sleep(1, 4, 16) exponential, continue
  │   │   └─ Else: log error, return None
  │   │
  │   └─ On success: return LLMResponse
  │
  └─ All retries exhausted: return None (passed to _parse_raw)
```

**Failure modes**:
- API rate limit → retry
- Network timeout → retry
- Auth failure → fail on first attempt (no retry)
- JSON parse → retry with correction prompt (separate flow)

#### Parse Failure Path (update_agent.py:259-263)

```
try:
  │
  ├─ json.loads(cleaned)
  ├─ Validate with pydantic
  │
  └─ Return AgentOutput

except json.JSONDecodeError | ValidationError:
  │
  ├─ Log error with task_id, message_id
  ├─ Call _log_error(raw) → append to AGENT_ERROR_LOG_PATH
  ├─ Return None
  │
  └─ If return is None: fall back to retry with correction prompt
      (lines 199-207)
```

**Recovery**: Retry once with correction prompt (lines 200-224).

#### Linkage Event Retry (linkage_worker.py:257-279)

```
def _process_with_retry(event_id, fields, r):
  │
  ├─ For attempt in range(1, MAX_RETRIES+1):
  │   ├─ Try: process_event(event_id, fields, r)
  │   ├─ Except any Exception:
  │   │   ├─ Log warning (attempt, total, error)
  │   │   ├─ If attempt < MAX_RETRIES: sleep(attempt), continue
  │   │   └─ Else: break
  │   │
  │   └─ On success: return (ack event)
  │
  └─ After retries exhausted:
      ├─ _write_dead_letter(event_id, fields, str(exception), MAX_RETRIES)
      ├─ r.xack(TASK_EVENTS_STREAM, CONSUMER_GROUP, event_id)
      └─ Log CRITICAL: "DEAD LETTER: event=X after Y failed attempts"
```

**Dead-letter strategy**:
- Developer inspects dead_letter_events table
- Fixes root cause
- Replays by re-publishing fields_json to TASK_EVENTS_STREAM

#### Conversation Router Exception Handling (conversation_router.py:211-270)

```
_flush_buffer(buf):
  │
  ├─ Scrap detection: try/except implicitly (detect_scraps is safe)
  ├─ Conversation building: try/except implicitly (build_conversations is safe)
  ├─ Reply tree: try/except implicitly (build_reply_tree is safe)
  ├─ Date matching: try/except implicitly (deterministic, no external calls)
  │
  ├─ LLM context matching (if enable_llm_matching):
  │   └─ _try_gemini(...): returns None on any exception (logged as warning)
  │       └─ On failure: skip (entity_learning proceeds anyway)
  │
  └─ Entity learning:
      ├─ try/except when loading alias_dict
      └─ On failure: proceed with empty known_aliases (discovery still works)
```

**Philosophy**: Conversation routing is optional enrichment — failures don't block main pipeline.

---

### 12. EXTERNAL CALLS AND NETWORK BOUNDARIES

#### Anthropic API Calls

```
_call_anthropic_with_retry(line 321-342)
  │
  ├─ Location: src/agent/update_agent.py
  ├─ Client: anthropic.Anthropic() (global, line 31)
  ├─ Endpoint: Claude API (default OpenAI endpoint)
  ├─ Methods:
  │   ├─ _anthropic_client.messages.create(
  │   │   ├─ model: CLAUDE_MODEL or CLAUDE_MODEL_FAST (from config)
  │   │   ├─ max_tokens: AGENT_MAX_TOKENS (2048)
  │   │   ├─ system: [{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}]
  │   │   │           (line 315-317 — ephemeral cache enabled)
  │   │   └─ messages: [{"role": "user", "content": user_section}]
  │   │       OR [{"role": "user", "content": [{type: image}, {type: text}]}]
  │   │
  │   └─ Response fields accessed:
  │       ├─ response.content[0].text → raw LLM output
  │       ├─ response.usage.input_tokens
  │       ├─ response.usage.output_tokens
  │       ├─ response.usage.cache_creation_input_tokens (optional)
  │       └─ response.usage.cache_read_input_tokens (optional)
  │
  ├─ Error handling:
  │   └─ except anthropic.APIStatusError → retry or fail
  │
  ├─ Retry policy:
  │   ├─ max_retries: 3
  │   ├─ Backoff: exponential (1s, 4s, 16s)
  │   └─ Strategy: retry transient errors, fail fast on auth errors
  │
  └─ Cost tracking:
      └─ log_llm_call(...) → usage_log table
```

**Gating**: Called from:
1. run_update_agent (via _call_with_retry)
2. run_linkage_agent (via _call_with_retry)
3. instrumented_replay.py (via monkey-patch, can use dev_test cache)

#### Gemini API Calls

```
_call_gemini_with_retry(line 345-391)
  │
  ├─ Location: src/agent/update_agent.py
  ├─ Lazy init: _get_gemini_client() (line 35-40)
  │   └─ from google import genai
  │   └─ genai.Client(api_key=GOOGLE_API_KEY environment)
  │
  ├─ Endpoint: Google Gemini API
  ├─ Method:
  │   └─ client.models.generate_content(
  │       ├─ model: parameter (usually GEMINI_MODEL = "gemini-2.5-flash")
  │       ├─ contents: user_section OR [image_part, user_section]
  │       ├─ config: GenerateContentConfig(
  │       │   ├─ system_instruction: system_prompt
  │       │   ├─ max_output_tokens: AGENT_MAX_TOKENS * 4 (8192)
  │       │   ├─ temperature: 0.0
  │       │   ├─ response_mime_type: "application/json"
  │       │   └─ thinking_config: ThinkingConfig(thinking_budget=0)
  │       │
  │       └─ Response fields:
  │           ├─ response.text OR "" → raw output
  │           ├─ response.usage_metadata.prompt_token_count
  │           └─ response.usage_metadata.candidates_token_count
  │
  ├─ Error handling:
  │   └─ except Exception → retry or fail (generic catch-all)
  │
  ├─ Retry policy:
  │   ├─ max_retries: 3
  │   ├─ Backoff: exponential (1s, 4s, 16s)
  │   └─ Same as Anthropic
  │
  └─ Cost tracking:
      └─ log_llm_call(...) → usage_log table
```

**Gating**: Called from:
1. run_update_agent (for Flash model selection)
2. run_linkage_agent (if LINKAGE_MODEL is Gemini — not recommended)
3. Conversation routing LLM context matcher (_try_gemini, separate path)
4. Vision/OCR pipeline (image_ocr.py)

#### Redis Calls (worker.py + linkage_worker.py)

```
Message Queue (entity groups):
  │
  ├─ Writer: _publish_task_event(line 83)
  │   └─ r.xadd(TASK_EVENTS_STREAM, fields_dict)
  │       ├─ stream_key: "task_events"
  │       ├─ fields: {"event_type": "message_processed", "task_id", "message_id", "message_json"}
  │       └─ Atomicity: single write
  │
  └─ Reader: linkage_worker (line 291-310)
      ├─ r.xreadgroup(CONSUMER_GROUP, CONSUMER_NAME, {TASK_EVENTS_STREAM: ">"})
      ├─ block: 5000ms
      └─ On event: process_event(...) → r.xack(...) on success or dead-letter

Conversation Routing (shared groups):
  │
  ├─ No Redis calls (buffered in-memory, replayed via replay_messages)
  │
  └─ In production: would need Redis stream for inter-worker sync

Mocking in tests:
  │
  ├─ MockRedis class (test harness)
  │   ├─ xadd(): accumulate in-memory events list
  │   ├─ drain_events(): return and clear list (for linkage agent to process)
  │   └─ xack(): noop (no state)
  │
  └─ instrumented_replay.py:
      ├─ Creates mock_redis (no real Redis needed)
      └─ Drains events after each scrap flush → feeds to linkage_worker.process_event
```

**Gating**: Completely isolated in worker.py and linkage_worker.py. Can be mocked entirely in tests.

#### SQLite DB Calls

```
Connection management (db.py):
  │
  ├─ get_connection(): sqlite3.connect(DB_PATH)
  │   ├─ check_same_thread=False (allow cross-thread access in test harness)
  │   ├─ row_factory=sqlite3.Row (dict-like row access)
  │   ├─ PRAGMA journal_mode=WAL (write-ahead logging)
  │   └─ PRAGMA foreign_keys=ON
  │
  └─ @contextmanager transaction():
      ├─ conn.commit() on success
      ├─ conn.rollback() on exception
      └─ conn.close() finally

Read operations (no transaction):
  │
  ├─ get_task(task_id) → query task_instances
  ├─ get_node_states(task_id) → query task_nodes
  ├─ get_recent_messages(task_id) → query task_messages
  ├─ get_order_items(task_id) → query *_items tables
  ├─ get_fulfillment_links(client_order_id) → query fulfillment_links
  └─ get_active_tasks() → query task_instances WHERE stage != 'completed'

Write operations (wrapped in transaction):
  │
  ├─ update_node(...) → UPDATE task_nodes + task_instances
  ├─ append_message(...) → INSERT task_messages
  ├─ apply_node_data_extractions(...) → UPDATE task_nodes.node_data (JSON merge)
  ├─ apply_item_extractions(...) → INSERT/UPDATE/DELETE *_items tables
  ├─ upsert_fulfillment_link(...) → INSERT OR REPLACE fulfillment_links
  ├─ create_task_live(...) → INSERT task_instances + task_nodes
  └─ close_task(...) → UPDATE task_instances.stage = 'completed'

Ambiguity queue:
  │
  ├─ _handle_ambiguity(...) → INSERT ambiguity_queue
  └─ escalation_target computed from ESCALATION_PROFILES config

Dead-letter (linkage_worker.py):
  │
  ├─ _write_dead_letter(...) → INSERT dead_letter_events
  └─ Developer inspects, fixes, replays

Event audit log:
  │
  ├─ Process writes → INSERT task_event_log
  ├─ Linkage writes → INSERT task_event_log
  └─ OCR writes → (no DB write, JSON cache only)
```

**Gating**: DB path mocked in replay via `patch("src.store.db.DB_PATH", case_dir_db)`.

---

### 13. STATE MUTATIONS AND PERSISTENCE

#### In-Memory State (Non-Persistent)

```
Module-level globals:
  │
  ├─ update_agent.py:
  │   ├─ _anthropic_client: singleton Anthropic client (line 31)
  │   └─ _gemini_client: lazy-initialized Gemini client (line 32)
  │
  ├─ conversation_router.py:
  │   ├─ _ocr_cache: {group_id: {message_id: ocr_data}} (line 37)
  │   │   └─ Set via set_ocr_cache(group_id, cache_dict)
  │   │   └─ Used during _enrich_with_ocr(messages, group_id)
  │   │
  │   └─ ConversationRouter instance:
  │       ├─ _buffers: {group_id: GroupBuffer} (line 149)
  │       ├─ feed(message) → mutates _buffers
  │       └─ flush_* → clears _buffers
  │
  ├─ scrap_detector.py:
  │   ├─ _ORBAT_LOOKUP: {alias: (full_name, unit_key)} (line 101)
  │   │   └─ Loaded once at module import (line 185)
  │   │   └─ Read-only after initialization
  │   │
  │   └─ PRINCIPAL_ENTITIES: hardcoded set (line 85)
  │
  ├─ agent_cache.py (dev test only):
  │   ├─ _conn: SQLite connection (line 20)
  │   ├─ _hits, _misses: counters (lines 22-23)
  │   └─ agent_cache.init(path) → opens connection, resets counters
  │
  └─ tracer.py (Phoenix tracing):
      ├─ ReplayTracer instance:
      │   ├─ _provider: TracerProvider (one per replay)
      │   ├─ _tracer: Tracer instance
      │   ├─ _total_tokens_in, _total_tokens_out: accumulators
      │   └─ _total_cost: cost accumulator
      │
      └─ MessageTrace instance (one per message):
          ├─ route_data, msg_tokens, msg_cost: span data
          └─ Aggregates into parent_tracer.total_*
```

#### Database State (Persistent)

```
Task instances:
  │
  ├─ task_instances(id, order_type, client_id, supplier_ids, stage, ...)
  ├─ Mutations:
  │   ├─ INSERT on live task creation (_resolve_task_for_entity)
  │   ├─ UPDATE stage, last_updated on every node update
  │   └─ UPDATE stage='completed' on close_task
  │
  └─ Lifespan: created until delivery_confirmed OR manually closed

Task nodes:
  │
  ├─ task_nodes(id, task_id, status, confidence, node_data, ...)
  ├─ Mutations:
  │   ├─ INSERT on task creation (nodes initialized to "pending" / "skipped")
  │   ├─ UPDATE status, confidence, last_message_id on agent update
  │   ├─ UPDATE node_data (JSON merge) on extraction
  │   └─ Auto-trigger activation: UPDATE status → "active" on cascade
  │
  └─ Lifespan: same as task

Task messages:
  │
  ├─ task_messages(id, task_id, message_id, group_id, sender_jid, body, ...)
  ├─ Mutations:
  │   ├─ INSERT on append_message (one per message per task)
  │   ├─ INSERT OR IGNORE (idempotent)
  │   └─ routing_confidence recorded (used for ambiguity confidence)
  │
  └─ Lifespan: preserved for audit/replay

Items (client_order_items, supplier_order_items):
  │
  ├─ Mutations:
  │   ├─ INSERT on "add" extraction
  │   ├─ UPDATE on "update" extraction (match by existing_description)
  │   └─ DELETE on "remove" extraction
  │
  └─ Lifespan: same as task

Fulfillment links:
  │
  ├─ fulfillment_links(id, client_order_id, client_item_description, ...)
  ├─ Mutations:
  │   ├─ INSERT OR REPLACE (upsert) on linkage_agent output
  │   ├─ status auto-confirmed if confidence >= 0.92
  │   └─ DELETE on prune_links_for_* (terminal state cleanup)
  │
  └─ Lifespan: created, updated, then deleted when resolved

Ambiguity queue:
  │
  ├─ ambiguity_queue(id, task_id, node_id, status, escalation_target, ...)
  ├─ Mutations:
  │   ├─ INSERT on _handle_ambiguity
  │   ├─ UPDATE status='resolved' on manual resolution
  │   ├─ Re-escalation tracking: re_escalation_count
  │   └─ Auto-resolve on timeout (cron job, not in this codebase)
  │
  └─ Lifespan: until resolved or expired

Usage log:
  │
  ├─ usage_log(id, call_type, model, tokens_in, tokens_out, cost_usd, duration_ms, ts)
  ├─ Mutations:
  │   ├─ INSERT on log_llm_call (update_agent, linkage_agent, etc.)
  │   └─ Read-only after insert (audit trail)
  │
  └─ Lifespan: permanent (cost tracking)

Dead-letter events:
  │
  ├─ dead_letter_events(id, stream_key, event_id, fields_json, failure_reason, ...)
  ├─ Mutations:
  │   ├─ INSERT on retry exhaustion (_write_dead_letter)
  │   ├─ UPDATE resolved=1 on manual replay confirmation
  │   └─ Read-only inspection
  │
  └─ Lifespan: permanent (developer investigation)
```

#### Ordering and Causality

```
Message processing order:
  │
  ├─ Ingestion: messages enter queue with timestamp
  ├─ Router: route(message) in order
  ├─ MessageBuffer: accumulate into scraps (same-sender contiguity)
  ├─ Agent: batch call per scrap (time-ordered within batch)
  ├─ Apply: node updates, items, cascades (transactional)
  │
  └─ Linkage: asynchronous, triggered by task_events stream
      ├─ Eventual consistency: order_ready set after all links reconciled
      └─ No strict ordering guarantee (but causal: output→event→linkage)

Node status transitions (within same message):
  │
  ├─ Agent output → node_updates list (order matters for cascades)
  ├─ Apply in order: update nodes, cascade auto_triggers
  ├─ Cascade can activate downstream auto_triggers
  └─ All in single transaction (atomicity within one message)

Linkage reconciliation (event-driven):
  │
  ├─ Linkage event from task_events stream
  ├─ Process: apply updates, reconcile order_ready
  ├─ Idempotent: same event replayed = same end state
  └─ Non-idempotent: multiple linkage agents processing concurrently (N/A in this impl)
```

---

### 14. INJECTION POINTS AND CUSTOMIZATION

#### Configuration (src/config.py)

```
Router configuration:
  ├─ MONITORED_GROUPS: dict[group_id] → task_id | None
  │   └─ Customization point: add/remove groups, change mappings
  │
  ├─ ENTITY_ALIASES: dict[alias] → entity_id
  │   └─ Customization point: add entities, change alias variants
  │
  ├─ ENTITY_MATCH_THRESHOLD: 80 (rapidfuzz partial_ratio)
  │   └─ Customization point: raise for stricter matching, lower for lenient
  │
  ├─ DIRECT_GROUP_CONFIDENCE: 0.90
  ├─ ENTITY_MATCH_CONFIDENCE: 0.75
  ├─ PROVISIONAL_THRESHOLD: 0.75
  └─ AMBIGUITY_THRESHOLD: 0.50
      └─ Customization points: tune routing/escalation confidence

Escalation configuration:
  ├─ ESCALATION_PROFILES: dict[profile_name] → {thresholds, targets, ...}
  │   └─ "ashish_current" (default)
  │   └─ "balanced"
  │   └─ "high_trust"
  │
  ├─ ACTIVE_ESCALATION_PROFILE: "ashish_current"
  │   └─ Switch active profile here
  │
  └─ ESCALATION_CATEGORY_OVERRIDES: per-category threshold tweaks
      └─ Customization point: tighten entity/linkage blocking

Model selection:
  ├─ CLAUDE_MODEL: "claude-sonnet-4-6" (primary)
  ├─ CLAUDE_MODEL_FAST: "claude-haiku-4-5-20251001" (fallback, unused)
  ├─ GEMINI_MODEL: "gemini-2.5-flash" (simple messages)
  └─ MAX_CONTEXT_MESSAGES: 20 (message history per task)
      └─ Customization point: increase for longer context, decrease for cost

Conversation routing:
  ├─ ENABLE_CONVERSATION_ROUTING: False (disabled for now)
  │   └─ Set to True to enable shared group conversation analysis
  │
  └─ Tied to MONITORED_GROUPS[group_id] == None for shared groups

Database:
  ├─ DB_PATH: "data/mantri.db"
  │   └─ Customization point: point to different DB or use in-memory for testing
  │
  └─ Mocked in replay via patch("src.store.db.DB_PATH", case_dir_db)

Redis:
  ├─ REDIS_URL: "redis://localhost:6379"
  │   └─ Customization point: point to different Redis instance
  │
  └─ Mocked in replay via MockRedis (no real Redis needed)

Task creation:
  ├─ ENABLE_LIVE_TASK_CREATION: True
  │   └─ Set to False to disable agent-driven task creation
  │
  └─ Controls _resolve_task_for_entity nil-task creation
```

#### Template Customization (src/agent/templates.py)

```
Node definitions (per order type):
  │
  ├─ STANDARD_PROCUREMENT_TEMPLATE: full client + supplier + delivery
  ├─ CLIENT_ORDER_TEMPLATE: client + stock path + delivery
  ├─ SUPPLIER_ORDER_TEMPLATE: supplier only
  └─ LINKAGE_TASK_TEMPLATE: M:N coordination (singleton, no real nodes)
      └─ Customization point: add nodes, remove optional subgraphs, change transitions

Node properties:
  ├─ id, name, stage, owner (update_agent | linkage_agent)
  ├─ type: real_world_milestone | agent_action | auto_trigger | time_trigger
  ├─ optional: Boolean (defaults to skipped unless subgraph activated)
  ├─ requires_all: [node_ids] (gate prerequisites)
  ├─ warns_if_incomplete: [node_ids] (warning-only prerequisites)
  │
  └─ Customization points:
      ├─ Add new nodes (update templates)
      ├─ Change owner (who writes the node)
      ├─ Add/remove prerequisites (gate node blocking)
      └─ Mark optional nodes (lazy activation)

Auto-trigger rules:
  ├─ activates_when: condition string (predecessor node status)
  │   └─ E.g., "client_quotation.status=in_progress"
  │
  └─ Customization point: add new time_trigger nodes with alert_days_before
      └─ Cron worker fires these (not in main pipeline code)
```

#### Prompt Customization (src/agent/prompt.py)

```
System prompt building (build_system_prompt):
  │
  ├─ _BUSINESS_CONTEXT: hardcoded company context (lines 13-35)
  │   ├─ Company name, address, staff, language, naming conventions
  │   └─ Customization point: update for different business
  │
  ├─ _OUTPUT_SPEC: hardcoded output format (lines 37-252)
  │   ├─ NodeUpdate, AmbiguityFlag, ItemExtraction formats
  │   ├─ Status values and transition rules
  │   ├─ Node activation rules (auto_trigger vs time_trigger vs optional)
  │   └─ Customization point: refine extraction rules, add node-specific instructions
  │
  └─ Template nodes appended: get_template(order_type)
      └─ Nodes formatted as JSON in system prompt

User section building (build_user_section):
  │
  ├─ Current node states (excluding skipped) with confidence
  ├─ Current items (JSON list)
  ├─ Active tasks for entity (if multi-task, with [CONFIRMED] tags)
  ├─ Recent messages (last 20 by default)
  ├─ New message(s) (with [IMAGE ATTACHED] marker)
  │
  └─ Customization points:
      ├─ Adjust MAX_CONTEXT_MESSAGES (config)
      ├─ Add task context (e.g., client notes, delivery requirements)
      └─ Refine item presentation (add specs, prices, etc.)

Routing confidence presentation:
  │
  ├─ Included in user section (line 355)
  ├─ Low confidence (< 0.85) → add warning to model
  │   └─ "(Low — be conservative, prefer provisional, flag ambiguity if inconsistent)"
  │
  └─ Customization point: adjust threshold, change guidance text
```

#### Test Harness Overrides (instrumented_replay.py)

```
run_instrumented_replay parameters:
  │
  ├─ seed: test case seed dict
  │   ├─ monitored_groups: group mappings for this test
  │   ├─ config_overrides: {key: value} patches applied
  │   ├─ test_window: {warmup_end_ts, test_pool} for staging
  │   └─ ocr_caches: {group_id: ocr_file_path} for OCR injection
  │
  ├─ dev_test: Boolean — enable agent_cache (deterministic, fast repeats)
  │   └─ Reduces cost: first run API calls, subsequent runs cached
  │
  ├─ allow_api_calls: Boolean — require cache hits (no API calls in frozen mode)
  │   └─ Used with --no-run-live to force cache use
  │
  ├─ no_conv_llm: Boolean — disable Gemini backward context matching
  │   └─ Speeds up conversation routing testing
  │
  ├─ run_linkage: Boolean — run linkage agent during replay
  │   └─ Default True; set False to test routing only
  │
  └─ max_messages: int | None — limit test messages processed
      └─ Speeds up dev iterations (don't run full replay)

Config patches applied:
  │
  ├─ src.store.db.DB_PATH → case_dir / "mantri.db" (isolated)
  ├─ src.config.DB_PATH → same
  ├─ src.router.router.MONITORED_GROUPS → seed["monitored_groups"]
  └─ src.config.ENABLE_CONVERSATION_ROUTING → based on seed

Phoenix tracing:
  │
  ├─ phoenix_endpoints: list of 1-2 OTEL endpoints
  │   └─ Default: [remote droplet] for persistent traces
  │   └─ Can disable entirely (endpoints=[])
  │
  └─ auth_headers: dict for authenticated endpoints (e.g., Basic auth)
```

---

### 15. SUMMARY: CODE PATH VALIDATION CHECKLIST

#### Before Merging, Validate:

```
Routing layer changes:
  ├─ [] Layer 1 noise filter: no regressions on reaction/sticker/system types
  ├─ [] Layer 2a direct mapping: MONITORED_GROUPS applied correctly
  ├─ [] Layer 2a+ runtime entities: task_routing_context query + entity resolution
  ├─ [] Layer 2b alias matching: rapidfuzz threshold behavior
  ├─ [] Layer 2c/2d stubs: integration points defined (no code changes yet)
  └─ [] Conversation routing: sentinel __conv_pending__ routed to ConversationRouter

Agent call pipeline:
  ├─ [] Model selection: _select_model logic for message complexity
  ├─ [] Image loading: _load_image from path OR bytes, media type detection
  ├─ [] System prompt: cached with ephemeral control, business context included
  ├─ [] User section: node states, items, entity tasks, routing confidence
  ├─ [] Retry logic: max 3 attempts, exponential backoff, failure → None
  ├─ [] Parse output: markdown fence stripping, JSON extraction, validation
  ├─ [] Correction retry: called on parse failure (not on LLM failure)
  └─ [] Token logging: all calls logged to usage_log with cache tokens

Output application:
  ├─ [] Node updates: confidence < threshold → downgrade to provisional
  ├─ [] Stock path: filled_from_stock activates → check_stock_path_order_ready
  ├─ [] Cascades: auto_trigger predecessors → set downstream to active
  ├─ [] Ambiguity: escalation_target computed, blocking_node set on gate nodes
  ├─ [] Items: add/update/remove by description matching
  ├─ [] Post-confirmation: item changes after gate completion → high severity flag
  ├─ [] Node data: JSON merge (new overwrite, absent preserve)
  └─ [] Task events: published to Redis for linkage_worker

Conversation routing (shared groups):
  ├─ [] Scrap detection: BURST_GAP_S (900s) + entity mention splitting
  ├─ [] Entity extraction: layers (aliases, ORBAT, regex, "from X") with confidences
  ├─ [] Conversation building: scraps → conversations with forward propagation
  ├─ [] Backward propagation: recent buffered scraps (1h window) assigned to evidence
  ├─ [] Reply tree: cross-sender threading, entity evidence covers whole thread
  ├─ [] Date matching: timeline events + keyword detection (deterministic)
  ├─ [] LLM context: Gemini backward matching (16 working hours, high precision)
  ├─ [] Entity learning: contact shares, "from X", military units discovered
  └─ [] OCR enrichment: text appended (never replace), resolutions as metadata

Linkage agent:
  ├─ [] Open orders summary: client + supplier tasks with current items
  ├─ [] Fulfillment links: all matches across all client orders
  ├─ [] M:N coordination: item descriptions matched, quantities allocated
  ├─ [] Confidence thresholds: >= 0.92 → confirmed, else candidate
  ├─ [] Status transitions: candidate→confirmed→fulfilled→completed→pruned
  ├─ [] Order ready reconciliation: all items allocated → completed, else partial/pending
  ├─ [] Link pruning: terminal supplier states → delete + close supplier task
  ├─ [] Client pruning: all links completed → delete + close client task
  └─ [] Ambiguity: quantity/linkage flags with affected_task_ids

Dev-test cache:
  ├─ [] Cache key: SHA256(system + user)[:16] deterministic
  ├─ [] Cache hit: return cached LLMResponse immediately (0 tokens, 0 cost)
  ├─ [] Cache miss with --no-run-live: raise RuntimeError (must use --run-live)
  ├─ [] Cache write: atomic upsert, committed immediately (WAL journal)
  ├─ [] Cache stats: hits, misses, hit_rate printed at end
  └─ [] Cache scope: covers update_agent + linkage_agent only (not vision/llm_context)

Monkey-patching:
  ├─ [] route() wrapped: routing decisions traced (no behavior change)
  ├─ [] _call_with_retry() wrapped: LLM calls traced + cache injected (behavior change IF dev_test)
  ├─ [] _resolve_task_for_entity() wrapped: resolution method traced (no behavior change)
  ├─ [] _apply_output() wrapped: post-processing traced (no behavior change)
  ├─ [] All patches applied via contextlib.ExitStack (atomic)
  ├─ [] Config patches isolated per test case (DB, MONITORED_GROUPS)
  └─ [] Linkage drain: events drained after each scrap flush (not lazy)

Error handling:
  ├─ [] LLM API failure: retry 3x with backoff, return None on exhaustion
  ├─ [] Parse failure: log error, optionally retry with correction prompt
  ├─ [] DB transaction: commit on success, rollback on exception
  ├─ [] Linkage event failure: retry 3x with backoff, dead-letter on exhaustion
  ├─ [] Conversation routing: failures logged (warning), pipeline continues
  └─ [] Vision/OCR: failures logged (warning), missing OCR gracefully handled

External calls:
  ├─ [] Anthropic API: endpoint from client init, system cache enabled
  ├─ [] Gemini API: lazy init, separate auth (GOOGLE_API_KEY), JSON response mode
  ├─ [] Redis xadd: published from _apply_output → task_events stream
  ├─ [] Redis xreadgroup: read by linkage_worker from task_events stream
  ├─ [] Redis xack: after process_event success or dead-letter
  ├─ [] SQLite write: all wrapped in transaction() context
  └─ [] OCR cache: JSON file load/save (no external API except vision call)

State mutations:
  ├─ [] Node status: updated in DB via update_node(...), transactional
  ├─ [] Items: inserted/updated/deleted in *_items tables, transactional
  ├─ [] Fulfillment links: upserted atomically, auto-confirmed at >= 0.92
  ├─ [] Ambiguity queue: inserted on escalation, resolved manually
  ├─ [] Usage log: appended (read-only audit trail)
  ├─ [] Dead-letter: written on retry exhaustion, inspected by developer
  ├─ [] Idempotence: same message → same end state (within tolerance)
  └─ [] No race conditions: single-threaded router + worker (production: use locks)
```

---

## CONCLUSION

This annotation document fully maps the Mantri codebase without executing tests. Every code path, branch, LLM call site, state mutation, and injection point is documented for validation and future changes.

**Key validation principles**:
1. **Routing layer**: 4-layer cascade with stubs for 2c/2d
2. **Agent pipeline**: model selection → prompt building → LLM call → retry → parse → output application
3. **Output application**: node updates, cascades, items, ambiguity escalation, atomic transactions
4. **Conversation routing**: scrap detection → entity extraction → conversation building → date/LLM matching
5. **Linkage agent**: M:N item coordination, fulfillment link status lifecycle, order_ready reconciliation
6. **Dev-test cache**: SHA256 key determinism, atomic SQLite writes, scope limited to update + linkage agents
7. **Monkey-patching**: non-behavior-changing tracing except dev-test cache injection
8. **Error handling**: retry + dead-letter for LLM/linkage, silent continue for conversation routing
9. **External calls**: Anthropic, Gemini, Redis, SQLite all properly gated and mocked in tests
10. **State persistence**: task instances, nodes, items, links, ambiguity queue, usage log all transactional

Future code changes can be validated by reading this document and confirming branch coverage, error paths, and external call correctness without running tests.