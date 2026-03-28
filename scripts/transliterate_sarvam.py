#!/usr/bin/env python3
"""
transliterate_sarvam.py

Converts a Sarvam transcript file from Devanagari/mixed script to Roman (Latin) script
using the Sarvam AI transliteration API.

USAGE:
    python scripts/transliterate_sarvam.py interviews/sarvam/my_sarvam_transcribe.txt
    python scripts/transliterate_sarvam.py interviews/sarvam/my_sarvam_transcribe.txt --out interviews/sarvam/my_roman.txt

INPUT FORMAT (one line per segment):
    [00:00:05 --> 00:00:06]  Speaker 1: ओके।

OUTPUT FORMAT (same structure, text transliterated to Roman):
    [00:00:05 --> 00:00:06]  Speaker 1: Okay.

NOTES:
    - Source language auto-detected per line (use 'auto' for mixed files)
    - Processes one line at a time (Sarvam API limit: 1000 chars/call)
    - SARVAM_API_KEY must be set in .env
"""

import argparse
import os
import re
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

MAX_CHARS = 900  # Sarvam transliteration API limit is 1000; keep headroom


def has_devanagari(text: str) -> bool:
    return bool(re.search(r"[\u0900-\u097F]", text))


def transliterate_text(client, text: str) -> str:
    """Transliterate a single text string from Devanagari to Roman."""
    if not has_devanagari(text):
        return text
    # Truncate very long segments (e.g. hallucination loops) to API limit
    chunk = text[:MAX_CHARS]
    for source_lang in ("auto", "hi-IN"):
        try:
            response = client.text.transliterate(
                input=chunk,
                source_language_code=source_lang,
                target_language_code="en-IN",
            )
            result = response.transliterated_text
            # Append remaining as-is if truncated (better than losing it)
            if len(text) > MAX_CHARS:
                result += " [truncated]"
            return result
        except Exception:
            continue
    # If all attempts fail, return original text unchanged
    return text


# Regex to parse transcript lines
LINE_RE = re.compile(r"^(\[[\d:]{8} --> [\d:]{8}\]\s+Speaker \d+: )(.*)")


def transliterate_file(in_path: Path, out_path: Path) -> None:
    try:
        from sarvamai import SarvamAI
    except ImportError:
        print("sarvamai not installed. Run: pip install sarvamai")
        sys.exit(1)

    api_key = os.environ.get("SARVAM_API_KEY")
    if not api_key:
        print("SARVAM_API_KEY not set. Add it to .env.")
        sys.exit(1)

    client = SarvamAI(api_subscription_key=api_key)

    raw_lines = in_path.read_text(encoding="utf-8").splitlines()
    print(f"Read {len(raw_lines)} lines from {in_path.name}")

    # Split each line into prefix + text
    prefixes = []
    texts = []
    for line in raw_lines:
        m = LINE_RE.match(line)
        if m:
            prefixes.append(m.group(1))
            texts.append(m.group(2))
        else:
            prefixes.append("")
            texts.append(line)

    # Process line by line
    result_texts = []
    devanagari_count = sum(1 for t in texts if has_devanagari(t))
    processed = 0
    for i, text in enumerate(texts):
        result_texts.append(transliterate_text(client, text))
        if has_devanagari(text):
            processed += 1
            if processed % 50 == 0 or processed == devanagari_count:
                print(f"  Transliterated {processed}/{devanagari_count} segments ({i+1}/{len(texts)} lines total)")

    output_lines = [p + t for p, t in zip(prefixes, result_texts)]
    out_path.write_text("\n".join(output_lines), encoding="utf-8")
    print(f"Transliterated transcript saved: {out_path} ({len(output_lines)} lines)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("file", help="Path to Sarvam transcript .txt file")
    parser.add_argument(
        "--out",
        default=None,
        help="Output .txt path (default: same dir, _roman.txt suffix)",
    )
    args = parser.parse_args()

    in_path = Path(args.file)
    if not in_path.exists():
        print(f"File not found: {in_path}")
        sys.exit(1)

    if args.out:
        out_path = Path(args.out)
    else:
        stem = in_path.stem
        # Replace _transcribe or _roman suffix if present, or just append _roman
        stem = re.sub(r"_(transcribe|roman|codemix|verbatim)$", "", stem)
        out_path = in_path.parent / (stem + "_roman.txt")

    transliterate_file(in_path, out_path)
