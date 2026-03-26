#!/usr/bin/env python3
"""
case_extractor.py

Extracts a time-windowed snippet from one or more WhatsApp chat export directories,
annotates attached images/PDFs within that window via Claude vision, and outputs a
multi-thread formatted log ready for evaluation against the testing prompt.

── Two modes ──────────────────────────────────────────────────────────────────

1. Case mode (recommended) — driven by a metadata.json file:

    python scripts/case_extractor.py --case data/cases/R3-C-L3-02_concurrent_flag_orders_same_supplier/

    Reads metadata.json from the case directory, writes threads.txt to the same directory.

2. Ad-hoc mode — useful for exploration before defining a case:

    python scripts/case_extractor.py \\
        --start "8/12/23, 18:00" --end "8/13/23, 23:59" \\
        --chats "data/raw_chats/dir1" "data/raw_chats/dir2" \\
        --output data/cases/some_case/threads.txt

── metadata.json schema ───────────────────────────────────────────────────────

{
  "id": "R3-C-L3-02",
  "name": "concurrent_flag_orders_same_supplier",
  "framework": "R3-C",
  "level": "L3",
  "sprint": "S2",
  "data_source": "real-incomplete",
  "description": "Two concurrent flag orders for different Army clients through same supplier.",
  "chat_inputs": {
    "start": "3/2/2026, 20:00",
    "end":   "3/18/2026, 23:59",
    "chats": [
      {"path": "data/raw_chats/full_chat_logs/Voltas_supplier", "label": "Voltas Supplier Group"},
      {"path": "data/raw_chats/full_chat_logs/est_div_jobs",    "label": "Est Div Client Group"}
    ]
  },
  "completeness": {
    "complete": false,
    "missing": ["Delivery confirmation", "Payment"]
  },
  "expected_output": "",
  "pass_criteria": "",
  "notes": ""
}

── Output format ──────────────────────────────────────────────────────────────

    === THREAD 1: Voltas Supplier Group ===
    [8/12/23, 18:52] Ashish Chhabra: message text
    [8/12/23, 18:53] Ashish Chhabra: [IMAGE:payment_ledger] ... | implied_event: ...
    ...
    === THREAD 2: Est Div Client Group ===
    ...
"""

import os
import re
import json
import base64
import argparse
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass

from dotenv import load_dotenv
load_dotenv()

import anthropic

# ── Constants ──────────────────────────────────────────────────────────────────

SUPPORTED_IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
SUPPORTED_DOC_EXTS   = {'.pdf'}
CONTEXT_LINES        = 5

MSG_LINE_RE = re.compile(r'^(\d{1,2}/\d{1,2}/\d{2,4}, \d{1,2}:\d{2})\s+-\s+(.+)$')
ATTACHMENT_RE = re.compile(
    r'([^:\n]+\.(jpg|jpeg|png|gif|webp|pdf))\s+\(file attached\)\s*$',
    re.IGNORECASE | re.MULTILINE
)

VISION_SYSTEM_PROMPT = """You are analyzing files shared in WhatsApp chats for an Army supply business in Guwahati, India.
The business (run by Ashish Chhabra) procures equipment, appliances, and supplies for Army clients.
Communications are in Hinglish (Hindi in Roman script + English).

Return a JSON object with:
- image_type: one of — handwritten_note, proforma_invoice, payment_confirmation, payment_ledger, courier_note, order_list, product_screenshot, product_photo, delivery_challan, other
- extracted_text: all visible text verbatim, preserving numbers exactly
- structured_data: key business data as a flat dict (items, quantities, amounts, dates, names, account numbers, tracking numbers, invoice numbers, etc.)
- implied_event: the business event this file represents (concise)
- chat_annotation: one line for insertion into a chat log — concise but complete

Return only valid JSON. No markdown fences."""

# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class ChatInput:
    path: Path
    label: str

@dataclass
class Message:
    timestamp: datetime
    ts_raw: str
    sender: str
    content: str
    has_attachment: bool = False
    attachment_name: str = ""

# ── Datetime parsing ───────────────────────────────────────────────────────────

def parse_ts(ts_str: str) -> datetime:
    ts_str = ts_str.strip()
    for fmt in ('%m/%d/%y, %H:%M', '%d/%m/%y, %H:%M',
                '%m/%d/%Y, %H:%M', '%d/%m/%Y, %H:%M'):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse timestamp: {ts_str!r}")

# ── Log parser ─────────────────────────────────────────────────────────────────

def parse_log(txt_path: Path) -> list[Message]:
    lines    = txt_path.read_text(encoding='utf-8').splitlines()
    messages: list[Message] = []
    current: Message | None = None

    for line in lines:
        m = MSG_LINE_RE.match(line)
        if m:
            ts_raw, rest = m.group(1), m.group(2)
            colon_idx = rest.find(': ')
            if colon_idx == -1:
                if current:
                    messages.append(current)
                current = None
                continue

            sender  = rest[:colon_idx].strip()
            content = rest[colon_idx + 2:]

            if current:
                messages.append(current)

            try:
                ts = parse_ts(ts_raw)
            except ValueError:
                current = None
                continue

            att = ATTACHMENT_RE.search(content)
            current = Message(
                timestamp=ts, ts_raw=ts_raw, sender=sender, content=content,
                has_attachment=bool(att),
                attachment_name=att.group(1).strip() if att else "",
            )
        else:
            if current and line.strip():
                current.content += '\n' + line

    if current:
        messages.append(current)
    return messages


def filter_window(messages: list[Message], start: datetime, end: datetime) -> list[Message]:
    return [m for m in messages if start <= m.timestamp <= end]

# ── Claude vision ──────────────────────────────────────────────────────────────

def _b64(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode('utf-8')

def _image_media_type(path: Path) -> str:
    return {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
            '.gif': 'image/gif', '.webp': 'image/webp'}.get(path.suffix.lower(), 'image/jpeg')

def annotate_file(client: anthropic.Anthropic, file_path: Path, context: str = "") -> dict:
    ext = file_path.suffix.lower()
    file_block = (
        {"type": "document",
         "source": {"type": "base64", "media_type": "application/pdf", "data": _b64(file_path)}}
        if ext in SUPPORTED_DOC_EXTS else
        {"type": "image",
         "source": {"type": "base64", "media_type": _image_media_type(file_path), "data": _b64(file_path)}}
    )
    text_block = {"type": "text",
                  "text": f"Recent chat context:\n{context}\n\nAnalyze and return JSON." if context
                          else "Analyze and return JSON."}

    response = client.messages.create(
        model="claude-opus-4-6", max_tokens=2048, system=VISION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": [file_block, text_block]}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```[a-z]*\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw.strip())
    return json.loads(raw)

# ── Thread builder ─────────────────────────────────────────────────────────────

def build_thread(thread_idx: int, label: str, messages: list[Message],
                 chat_dir: Path, client: anthropic.Anthropic) -> str:
    lines      = [f"=== THREAD {thread_idx}: {label} ==="]
    ctx_buf: list[str] = []

    for msg in messages:
        annotation = None

        if msg.has_attachment:
            file_path = chat_dir / msg.attachment_name
            ext       = Path(msg.attachment_name).suffix.lower()

            if file_path.exists() and (ext in SUPPORTED_IMAGE_EXTS or ext in SUPPORTED_DOC_EXTS):
                try:
                    result     = annotate_file(client, file_path, '\n'.join(ctx_buf[-CONTEXT_LINES:]))
                    annotation = (f"[IMAGE:{result.get('image_type','unknown')}] "
                                  f"{result.get('chat_annotation', msg.attachment_name)} "
                                  f"| implied_event: {result.get('implied_event','')}")
                    print(f"      ✓ {msg.attachment_name} → {result.get('image_type','?')}")
                except Exception as e:
                    annotation = f"[IMAGE:error — {msg.attachment_name}: {e}]"
                    print(f"      ✗ {msg.attachment_name}: {e}")
            elif not file_path.exists():
                annotation = f"[IMAGE:not_in_export — {msg.attachment_name}]"

        content   = ATTACHMENT_RE.sub(annotation, msg.content) if annotation and msg.has_attachment else msg.content
        formatted = f"[{msg.ts_raw}] {msg.sender}: {content}"
        lines.append(formatted)
        ctx_buf.append(formatted)

    return '\n'.join(lines)

# ── Metadata helpers ───────────────────────────────────────────────────────────

def load_chat_inputs(metadata: dict, base_dir: Path) -> tuple[str, str, list[ChatInput]]:
    """Extract start, end, and ChatInput list from metadata. Paths resolved relative to base_dir."""
    ci      = metadata['chat_inputs']
    start   = ci['start']
    end     = ci['end']
    chats: list[ChatInput] = []
    for entry in ci['chats']:
        if isinstance(entry, str):
            p = Path(entry) if Path(entry).is_absolute() else base_dir / entry
            chats.append(ChatInput(path=p, label=p.name))
        else:
            p = Path(entry['path']) if Path(entry['path']).is_absolute() else base_dir / entry['path']
            chats.append(ChatInput(path=p, label=entry.get('label', p.name)))
    return start, end, chats


METADATA_TEMPLATE = {
    "id": "",
    "name": "",
    "framework": "",
    "level": "",
    "sprint": "S2",
    "data_source": "real-incomplete",
    "description": "",
    "chat_inputs": {
        "start": "",
        "end": "",
        "chats": [
            {"path": "data/raw_chats/full_chat_logs/...", "label": "..."}
        ]
    },
    "completeness": {
        "complete": False,
        "missing": []
    },
    "expected_output": "",
    "pass_criteria": "",
    "notes": ""
}

# ── Core extraction ────────────────────────────────────────────────────────────

def run_extraction(start_str: str, end_str: str, chats: list[ChatInput],
                   output_path: Path, client: anthropic.Anthropic) -> str:
    start_dt = parse_ts(start_str)
    end_dt   = parse_ts(end_str)

    print(f"  Window : {start_str} → {end_str}")
    print(f"  Threads: {len(chats)}\n")

    thread_blocks = []
    for i, chat in enumerate(chats, start=1):
        txt_files = list(chat.path.glob('*.txt'))
        if not txt_files:
            print(f"  [SKIP] No .txt log in {chat.path}")
            continue

        print(f"  [{i}] {chat.label}")
        messages = parse_log(txt_files[0])
        windowed = filter_window(messages, start_dt, end_dt)
        n_att    = sum(1 for m in windowed if m.has_attachment)
        print(f"      {len(messages)} total → {len(windowed)} in window, {n_att} attachment(s)")

        block = build_thread(i, chat.label, windowed, chat.path, client)
        thread_blocks.append(block)

    if not thread_blocks:
        raise ValueError("No messages found in any thread for the given window.")

    output = '\n\n'.join(thread_blocks)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output, encoding='utf-8')

    n_imgs = output.count('[IMAGE:')
    print(f"\n  threads.txt written → {output_path}")
    print(f"  {len(thread_blocks)} thread(s), {n_imgs} image annotation(s)")
    return output

# ── Entry points ───────────────────────────────────────────────────────────────

def run_case_mode(case_dir: Path, client: anthropic.Anthropic) -> None:
    meta_path = case_dir / 'metadata.json'
    if not meta_path.exists():
        raise FileNotFoundError(f"No metadata.json in {case_dir}")

    metadata = json.loads(meta_path.read_text(encoding='utf-8'))
    print(f"Case: {metadata.get('id','')} — {metadata.get('name','')}")

    # Paths in metadata.json are relative to the project root (cwd when running the script)
    start, end, chats = load_chat_inputs(metadata, Path.cwd())
    run_extraction(start, end, chats, case_dir / 'threads.txt', client)


def run_adhoc_mode(start: str, end: str, chat_paths: list[str],
                   output: str, client: anthropic.Anthropic) -> None:
    chats = [ChatInput(path=Path(p), label=Path(p).name) for p in chat_paths]
    out   = Path(output) if output else Path('data/cases/adhoc/threads.txt')
    print("Ad-hoc extraction")
    run_extraction(start, end, chats, out, client)


def new_case(case_dir: Path) -> None:
    """Create a new case directory with a blank metadata.json."""
    case_dir.mkdir(parents=True, exist_ok=True)
    meta_path = case_dir / 'metadata.json'
    if meta_path.exists():
        print(f"metadata.json already exists at {meta_path}")
        return
    meta_path.write_text(json.dumps(METADATA_TEMPLATE, indent=2), encoding='utf-8')
    print(f"Created: {meta_path}")
    print("Fill in the fields and run: python scripts/case_extractor.py --case <case_dir>")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--case',  metavar='CASE_DIR',
                      help='Case directory containing metadata.json')
    mode.add_argument('--start', metavar='DATETIME',
                      help='Ad-hoc mode: start datetime (e.g. "8/12/23, 18:00")')
    mode.add_argument('--new',   metavar='CASE_DIR',
                      help='Create a new case directory with blank metadata.json')

    parser.add_argument('--end',    metavar='DATETIME', help='Ad-hoc end datetime')
    parser.add_argument('--chats',  nargs='+',          help='Ad-hoc chat directories')
    parser.add_argument('--output', metavar='FILE',     help='Ad-hoc output path')
    args = parser.parse_args()

    if args.new:
        new_case(Path(args.new))
    else:
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY not set. Add it to .env")
        client = anthropic.Anthropic(api_key=api_key)

        if args.case:
            run_case_mode(Path(args.case), client)
        else:
            if not args.end or not args.chats:
                parser.error("--start requires --end and --chats")
            run_adhoc_mode(args.start, args.end, args.chats, args.output, client)
