#!/usr/bin/env python3
"""
transcribe_sarvam.py

Transcribes a meeting recording using Sarvam AI Saaras v3 (Batch API).
Better than Whisper/Gemini for Hinglish + Assamese code-mixing.

SETUP (one-time):
    pip install sarvamai
    Add SARVAM_API_KEY to .env (get free key + ₹1000 credits at sarvam.ai)

USAGE:
    python scripts/transcribe_sarvam.py temp/recording.mp4
    python scripts/transcribe_sarvam.py temp/recording.mp4 --out interviews/my_transcript.txt
    python scripts/transcribe_sarvam.py temp/recording.mp4 --mode codemix  # Devanagari Hindi + Latin English

MODES:
    transcribe  — full transcription in native script (default)
    codemix     — Hindi in Devanagari, English in Latin in same output
    verbatim    — includes filler words, false starts etc.

OUTPUT:
    A .txt file in interviews/ with timestamped, diarised lines:
        [00:00:05 --> 00:00:12]  Speaker 1: Haan toh basically main jo karta hoon...
        [00:00:12 --> 00:00:18]  Speaker 2: So when Ashish sends a task on WhatsApp...

NOTES:
    - Batch API supports up to 1 hour per file — no chunking needed
    - Diarisation up to 8 speakers included by default
    - Audio is extracted from video before upload (smaller file, faster)
    - SARVAM_API_KEY must be set in .env
    - Pricing: ~₹30/hour (~$0.35 USD). Free ₹1000 credits on signup.
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


def extract_audio(video_path: Path) -> tuple[Path, bool]:
    """Extract mono 16kHz MP3 audio from a video file, saved to interviews/audio/.
    Returns (audio_path, already_existed)."""
    audio_dir = INTERVIEWS_DIR / "audio"
    audio_dir.mkdir(exist_ok=True)
    audio_path = audio_dir / (video_path.stem + "_audio.mp3")
    if audio_path.exists():
        print(f"Reusing existing audio: {audio_path} ({audio_path.stat().st_size / 1e6:.1f} MB)")
        return audio_path, True
    print(f"Extracting audio from {video_path.name} ...")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path), "-vn",
         "-ar", "16000", "-ac", "1", "-b:a", "64k", str(audio_path)],
        check=True, capture_output=True,
    )
    print(f"Audio saved: {audio_path} ({audio_path.stat().st_size / 1e6:.1f} MB)")
    return audio_path, False


def format_timestamp(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def transcribe(input_path: Path, out_path: Path, mode: str, language: str) -> None:
    try:
        from sarvamai import SarvamAI
    except ImportError:
        print("sarvamai not installed. Run: pip install sarvamai")
        sys.exit(1)

    api_key = os.environ.get("SARVAM_API_KEY")
    if not api_key:
        print("SARVAM_API_KEY not set. Add it to .env and source it.")
        sys.exit(1)

    client = SarvamAI(api_subscription_key=api_key)

    # Extract audio if input is a video file
    audio_extensions = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}
    if input_path.suffix.lower() not in audio_extensions:
        audio_path, _ = extract_audio(input_path)
    else:
        audio_path = input_path

    try:
        print("Creating Sarvam batch job ...")
        job = client.speech_to_text_job.create_job(
            model="saaras:v3",
            mode=mode,
            language_code=language if language else None,
            with_diarization=True,
            num_speakers=3,  # Ashish + Kunal + occasional staff
        )

        print("Uploading audio ...")
        job.upload_files(file_paths=[str(audio_path)])
        job.start()

        print("Waiting for transcription to complete ...")
        job.wait_until_complete()

        results = job.get_file_results()
        successful = results.get("successful", [])
        if not successful:
            failed = results.get("failed", [])
            print(f"Transcription failed: {failed}")
            sys.exit(1)

        # Download output JSON files to a temp dir, then parse
        import tempfile, json
        with tempfile.TemporaryDirectory() as tmp_dir:
            job.download_outputs(output_dir=tmp_dir)
            json_files = list(Path(tmp_dir).glob("*.json"))
            if not json_files:
                print("No output JSON files downloaded")
                sys.exit(1)

            lines = []
            for jf in sorted(json_files):
                data = json.loads(jf.read_text(encoding="utf-8"))
                diarised = data.get("diarized_transcript", data.get("diarised_transcript", []))
                transcript = data.get("transcript", "")

                if diarised:
                    for segment in diarised:
                        speaker = segment.get("speaker_id", "Speaker ?")
                        start = format_timestamp(segment.get("start", 0))
                        end = format_timestamp(segment.get("end", 0))
                        text = segment.get("transcript", "").strip()
                        lines.append(f"[{start} --> {end}]  {speaker}: {text}")
                elif transcript:
                    lines.append(transcript)
                else:
                    # Dump raw so we can inspect
                    lines.append(json.dumps(data, ensure_ascii=False, indent=2))

        out_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"Transcript saved: {out_path} ({len(lines)} segments)")

    finally:
        pass  # Audio kept in interviews/audio/ for reuse


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("file", help="Path to video or audio file")
    parser.add_argument(
        "--out",
        default=None,
        help="Output .txt path (default: interviews/<filename>_sarvam.txt)",
    )
    parser.add_argument(
        "--mode",
        default="transcribe",
        choices=["transcribe", "codemix", "verbatim"],
        help="Transcription mode (default: transcribe)",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="BCP-47 language code e.g. hi-IN, as-IN. Omit for auto-detect.",
    )
    args = parser.parse_args()

    input_path = Path(args.file)
    if not input_path.exists():
        print(f"File not found: {input_path}")
        sys.exit(1)

    if args.out:
        out_path = Path(args.out)
    else:
        sarvam_dir = INTERVIEWS_DIR / "sarvam"
        sarvam_dir.mkdir(exist_ok=True)
        suffix = f"_sarvam_{args.mode}.txt"
        out_path = sarvam_dir / (input_path.stem + suffix)

    transcribe(input_path, out_path, args.mode, args.language)
