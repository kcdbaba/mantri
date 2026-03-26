#!/usr/bin/env python3
# DEPRECATED — superseded by case_extractor.py
# Annotates an entire chat log regardless of case window. Kept for reference only.
"""
image_annotator.py

Enriches a WhatsApp chat export directory by processing all attached media files
(images and PDFs) and replacing "(file attached)" references in the .txt log
with structured annotations extracted via Claude vision.

Usage:
    python scripts/image_annotator.py <chat_directory> [output_file.txt]

Example:
    python scripts/image_annotator.py "data/raw_chats/voltas supplier full history/WhatsApp Chat with Voltas jobs uttam enterprise" data/enriched/voltas_enriched.txt

Output format for each annotated attachment:
    [IMAGE:proforma_invoice] Proforma PI/546 from Jiwan Ram Binod Kumar — 8x Water Dispenser + 2x Chest Freezer — Total: 1,07,120 incl. GST. Payment terms: 7 days. | implied_event: Proforma invoice received, awaiting approval and payment
"""

import os
import re
import base64
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import anthropic

# ── Constants ─────────────────────────────────────────────────────────────────

SUPPORTED_IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
SUPPORTED_DOC_EXTS   = {'.pdf'}
CONTEXT_LINES = 6  # Lines of chat history passed to model for context

SYSTEM_PROMPT = """You are analyzing files shared in WhatsApp chats for an Army supply business in Guwahati, India. The business (run by Ashish Chhabra) procures equipment, appliances, and supplies for Army clients. Communications are in Hinglish (Hindi in Roman script + English).

For each image or document, return a JSON object with these fields:
- image_type: one of — handwritten_note, proforma_invoice, payment_confirmation, payment_ledger, courier_note, order_list, product_screenshot, product_photo, delivery_challan, other
- extracted_text: all visible text verbatim, preserving numbers exactly
- structured_data: key business data as a flat dict (e.g. supplier, buyer, items, quantities, amounts, dates, account_numbers, tracking_numbers, gstin, invoice_number)
- implied_event: the business event this file represents (e.g. "Payment of 33,000 made to Jiwan Ram Binod Kumar on 12/08/23 — balance 36,500 outstanding")
- chat_annotation: one line suitable for insertion into a chat log — concise but complete enough that a reader understands the file without seeing it

Return only valid JSON. No markdown fences. No explanation."""

# ── File helpers ───────────────────────────────────────────────────────────────

def _media_type_for_image(path: Path) -> str:
    return {
        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
        '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp',
    }.get(path.suffix.lower(), 'image/jpeg')

def _b64(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode('utf-8')

# ── Claude calls ───────────────────────────────────────────────────────────────

def _call_claude(client: anthropic.Anthropic, content: list, context: str) -> dict:
    if context:
        content.append({
            "type": "text",
            "text": f"Recent chat context preceding this file:\n{context}\n\nAnalyze and return JSON."
        })
    else:
        content.append({"type": "text", "text": "Analyze and return JSON."})

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    raw = response.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith('```'):
        raw = re.sub(r'^```[a-z]*\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw.strip())
    return json.loads(raw)


def analyze_image(client: anthropic.Anthropic, path: Path, context: str = "") -> dict:
    content = [{
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": _media_type_for_image(path),
            "data": _b64(path),
        }
    }]
    return _call_claude(client, content, context)


def analyze_pdf(client: anthropic.Anthropic, path: Path, context: str = "") -> dict:
    content = [{
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": _b64(path),
        }
    }]
    return _call_claude(client, content, context)

# ── Annotation formatting ──────────────────────────────────────────────────────

def format_annotation(filename: str, result: dict) -> str:
    image_type   = result.get('image_type', 'unknown')
    annotation   = result.get('chat_annotation', f'[File: {filename}]')
    implied      = result.get('implied_event', '')
    return f"[IMAGE:{image_type}] {annotation} | implied_event: {implied}"

# ── Main enrichment pipeline ───────────────────────────────────────────────────

def enrich_chat_log(chat_dir: str, output_path: str = None) -> str:
    """
    Process a WhatsApp chat export directory.

    Reads the .txt log, replaces every '<filename> (file attached)' reference
    with a structured annotation from Claude vision, and returns the enriched log.
    '<Media omitted>' lines are left unchanged (files not available).
    """
    chat_dir = Path(chat_dir)
    if not chat_dir.is_dir():
        raise NotADirectoryError(f"Not a directory: {chat_dir}")

    txt_files = list(chat_dir.glob('*.txt'))
    if not txt_files:
        raise FileNotFoundError(f"No .txt log file found in {chat_dir}")
    txt_file = txt_files[0]
    print(f"Processing: {txt_file.name}")

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY not set. Run: export ANTHROPIC_API_KEY=your_key_here"
        )
    client = anthropic.Anthropic(api_key=api_key)
    lines    = txt_file.read_text(encoding='utf-8').splitlines()
    enriched = []
    context_window: list[str] = []

    # Pattern: filename.ext (file attached)
    # Filename may contain spaces (e.g. "Uttam Enterprise PI546.pdf (file attached)")
    # Exclude ':' from filename match — WhatsApp uses it as the message content separator
    attachment_re = re.compile(
        r'([^:\n]+\.(jpg|jpeg|png|gif|webp|pdf))\s+\(file attached\)\s*$',
        re.IGNORECASE
    )

    stats = {'processed': 0, 'missing': 0, 'failed': 0, 'skipped': 0}

    for line in lines:
        match = attachment_re.search(line)
        if match:
            filename = match.group(1).strip()
            file_path = chat_dir / filename
            ext = Path(filename).suffix.lower()

            if not file_path.exists():
                enriched.append(line.replace('(file attached)', '(file not found in export)'))
                stats['missing'] += 1
                print(f"  ✗ Missing: {filename}")
            elif ext in SUPPORTED_IMAGE_EXTS or ext in SUPPORTED_DOC_EXTS:
                context = '\n'.join(context_window[-CONTEXT_LINES:])
                try:
                    if ext in SUPPORTED_DOC_EXTS:
                        result = analyze_pdf(client, file_path, context)
                    else:
                        result = analyze_image(client, file_path, context)

                    annotation = format_annotation(filename, result)
                    enriched.append(attachment_re.sub(annotation, line))
                    stats['processed'] += 1
                    print(f"  ✓ {filename} → {result.get('image_type', '?')}")

                except Exception as e:
                    enriched.append(line)  # Keep original on failure
                    stats['failed'] += 1
                    print(f"  ✗ Failed ({filename}): {e}")
            else:
                # Unsupported file type (video, audio, etc.) — leave as-is
                enriched.append(line)
                stats['skipped'] += 1

        else:
            enriched.append(line)

        context_window.append(line)
        if len(context_window) > CONTEXT_LINES:
            context_window.pop(0)

    enriched_text = '\n'.join(enriched)

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(enriched_text, encoding='utf-8')
        print(f"\nEnriched log written to: {output_path}")

    print(f"\nDone — processed: {stats['processed']}, missing: {stats['missing']}, failed: {stats['failed']}, skipped: {stats['skipped']}")
    return enriched_text


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    chat_directory = sys.argv[1]
    out_file = sys.argv[2] if len(sys.argv) > 2 else None

    enrich_chat_log(chat_directory, out_file)
