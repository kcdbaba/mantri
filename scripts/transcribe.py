#!/usr/bin/env python3
"""
transcribe.py

Transcribes a meeting recording using OpenAI Whisper (local, free).
Outputs a timestamped transcript .txt file alongside the input file.

SETUP (one-time):
    pip install openai-whisper
    brew install ffmpeg

USAGE:
    python scripts/transcribe.py path/to/recording.mp4
    python scripts/transcribe.py path/to/recording.mp4 --model large-v3
    python scripts/transcribe.py path/to/recording.mp4 --language hi

OUTPUT:
    A .txt file next to the recording with timestamped lines:
        [00:00:05 --> 00:00:12]  haan toh basically main jo karta hoon...
        [00:00:12 --> 00:00:18]  so when Ashish sends a task on WhatsApp...

MODELS (accuracy vs speed trade-off):
    tiny     — fastest, least accurate
    base     — good for clear English
    small    — decent Hinglish handling
    medium   — handles Hinglish well
    large-v3 — best accuracy for mixed Hindi/English (recommended, ~3GB download on first run)

LANGUAGE:
    Omit --language for auto-detect (works well for Hinglish).
    Use --language en to force English.
    - Model weights are downloaded once and cached (~/.cache/whisper/)

NOTES:
    Use --language hi to force Hindi if auto-detect struggles.
    - Works directly on .mp4, .m4a, .mp3, .wav — no need to extract audio first
    - If a segment comes out garbled, try --language hi to force Hindi mode
"""

import argparse
import sys
from pathlib import Path


def transcribe(audio_path: Path, model_name: str, language: str | None) -> Path:
    try:
        import whisper
    except ImportError:
        print("Whisper not installed. Run: pip install openai-whisper")
        sys.exit(1)

    print(f"Loading model: {model_name} (first run downloads weights)...")
    model = whisper.load_model(model_name)

    print(f"Transcribing: {audio_path}")
    options = {"verbose": True}
    options["initial_prompt"] = "Transcribe in Hinglish, using Latin characters."
    if language:
        options["language"] = language

    result = model.transcribe(str(audio_path), **options)

    out_path = audio_path.with_suffix(".txt")
    with open(out_path, "w", encoding="utf-8") as f:
        for segment in result["segments"]:
            start = _fmt_time(segment["start"])
            end   = _fmt_time(segment["end"])
            text  = segment["text"].strip()
            f.write(f"[{start} --> {end}]  {text}\n")

    print(f"\nTranscript saved: {out_path}")
    return out_path


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", help="Path to audio or video file")
    parser.add_argument("--model", default="large-v3",
                        choices=["tiny", "base", "small", "medium", "large-v3"],
                        help="Whisper model (default: large-v3)")
    parser.add_argument("--language", default=None,
                        help="Language code e.g. 'hi' for Hindi, 'en' for English. "
                             "Omit for auto-detect (recommended for Hinglish).")
    args = parser.parse_args()

    audio_path = Path(args.file)
    if not audio_path.exists():
        print(f"File not found: {audio_path}")
        sys.exit(1)

    transcribe(audio_path, args.model, args.language)
