#!/usr/bin/env python3
"""
transcribe_gemini.py

Transcribes a meeting recording using Gemini 2.5 Pro (better Hinglish/Hindi support).
Uploads the file to the Gemini Files API, then requests a timestamped transcript.

SETUP (one-time):
    pip install google-generativeai
    export GOOGLE_API_KEY=<your key>   # or set in .env

USAGE:
    python scripts/transcribe_gemini.py temp/recording.mp4
    python scripts/transcribe_gemini.py temp/recording.mp4 --out interviews/my_transcript.txt

OUTPUT:
    A .txt file with timestamped lines:
        [00:00:05]  Haan toh basically main jo karta hoon...
        [00:00:12]  So when Ashish sends a task on WhatsApp...

NOTES:
    - Gemini Files API accepts up to 2 GB per file.
    - Upload time depends on file size and network speed.
    - The uploaded file is deleted from Gemini servers after 48 hours automatically.
    - GOOGLE_API_KEY must be set (from .env or environment).
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

INTERVIEWS_DIR = Path(__file__).parent.parent / "interviews"


PROMPT = """\
You are transcribing a business meeting recording for an Indian Army supply company based in Guwahati, India.

The conversation is in Hinglish — a natural mix of Hindi (written in Roman/Latin script) and English. \
Some segments may be in pure Hindi or pure English. Occasionally there may be Assamese words.

Please produce a full verbatim transcript with timestamps. Format each line as:
[HH:MM:SS]  <transcribed text>

Rules:
- Write Hindi words in Roman/Latin script (not Devanagari), exactly as spoken.
- Do not translate — preserve the original language as spoken.
- Keep speaker turns on separate lines. If you can identify different speakers, prefix with "Speaker A:" / "Speaker B:" etc.
- For unclear segments, write [unclear] rather than guessing.
- Do not summarise or paraphrase — full verbatim transcript only.
- Preserve filler words (haan, okay, so, basically, etc.) as they appear.
"""


def transcribe(video_path: Path, out_path: Path) -> None:
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print("google-genai not installed. Run: pip install google-genai")
        sys.exit(1)

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("GOOGLE_API_KEY not set. Source .env or export the variable.")
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    # Extract audio only — video token count (~258/s) exceeds 1M limit for long recordings.
    # Audio tokens (~32/s) are ~8× cheaper and fit comfortably within limits.
    audio_dir = INTERVIEWS_DIR / "audio"
    audio_dir.mkdir(exist_ok=True)
    audio_path = audio_dir / (video_path.stem + "_audio.mp3")
    print(f"Extracting audio from {video_path.name} ...")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-ar", "16000", "-ac", "1",
         "-b:a", "64k", str(audio_path)],
        check=True, capture_output=True,
    )
    print(f"Audio saved: {audio_path} ({audio_path.stat().st_size / 1e6:.1f} MB)")

    print(f"Uploading audio ...")
    uploaded = client.files.upload(file=str(audio_path), config={"mime_type": "audio/mpeg"})

    # Wait for processing
    print("Waiting for Gemini to process the file...")
    while uploaded.state.name == "PROCESSING":
        time.sleep(5)
        uploaded = client.files.get(name=uploaded.name)

    if uploaded.state.name != "ACTIVE":
        print(f"File processing failed: {uploaded.state.name}")
        sys.exit(1)

    print("Transcribing...")
    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=[uploaded, PROMPT],
        config=types.GenerateContentConfig(http_options=types.HttpOptions(timeout=600000)),
    )

    out_path.write_text(response.text, encoding="utf-8")
    print(f"Transcript saved: {out_path}")

    # Clean up remote file only — keep local audio for reuse
    client.files.delete(name=uploaded.name)
    print("Cleaned up uploaded file from Gemini servers.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("file", help="Path to video or audio file")
    parser.add_argument(
        "--out",
        default=None,
        help="Output .txt path (default: interviews/<filename>.txt)",
    )
    args = parser.parse_args()

    video_path = Path(args.file)
    if not video_path.exists():
        print(f"File not found: {video_path}")
        sys.exit(1)

    if args.out:
        out_path = Path(args.out)
    else:
        gemini_dir = INTERVIEWS_DIR / "gemini"
        gemini_dir.mkdir(exist_ok=True)
        out_path = gemini_dir / (video_path.stem + "_gemini.txt")

    transcribe(video_path, out_path)
