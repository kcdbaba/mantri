# Interviews Directory

## Structure

```
interviews/
  README.md                        ← this file
  user_research_plan.md            ← interview guide and recruitment plan
  audio/                           ← extracted MP3 audio (16kHz mono 64kbps)
  whisper/                         ← Whisper large-v3 transcripts (original)
  gemini/                          ← Gemini 2.5 Pro transcripts + analyses
  sarvam/                          ← Sarvam AI Saaras v3 transcripts
  analysis_ashish_part1.md         ← Whisper-based analysis of Ashish Part 1
  analysis_staff_interview.md      ← Whisper-based analysis of Staff session
  SATA_BTY_case_agent_output.txt   ← agent output used in prototype walkthrough
  staff_interview_presentation.md  ← slide deck source for staff session
  staff_interview.pptx             ← compiled slide deck
```

---

## Recordings

| File | Session | Length | Date |
|---|---|---|---|
| `Part 1 Ashish interview_Mar_27_2026-07_41-PM.mp4` (in temp/) | Ashish Chhabra, business owner | ~44 min | 2026-03-27 |
| `Mantri project User interview_Mar_27_2026-11_17-AM.mp4` (in temp/) | Staff session (Mousami + Samita + Kunal; Ashish briefly present) | ~67 min | 2026-03-27 |

---

## Transcript Quality Notes

### Whisper (`whisper/`)

**Ashish Part 1** (`Part 1 Ashish interview_Mar_27_2026-07_41-PM.txt`):
- ⚠️ **Heavy hallucination in first ~30 minutes** (lines 6–300 approx). 30-second loops appear at 00:04:18–00:05:48 and 00:29:13–00:30:53.
- English from ~00:31:00 onward is clean and reliable.
- Substantive interview content begins clearly around 00:10:00.
- **Do not use for content before 00:10:00.** Use Gemini transcript for 00:00–00:14.

**Staff session** (`Mantri project User interview_Mar_27_2026-11_17-AM.txt`):
- Moderate Hinglish quality — Hindi segments partially garbled, English clean.
- Overall usable. Gemini transcript improves Hinglish fidelity significantly.

---

### Gemini (`gemini/`)  — model: `gemini-2.5-pro`, audio-only input

**Ashish Part 1** (`Part 1 Ashish interview_Mar_27_2026-07_41-PM_gemini.txt`):
- ✅ Clean and reliable for **00:00–00:14** — fills the gap Whisper could not cover.
- ⚠️ **Severe hallucination loop starting at ~00:15:51** (line ~284). The neighbourhood shop passage repeats identically for the rest of the file (~750+ lines). Root cause: Gemini latched onto a repeated passage in the original recording where Ashish re-described the scenario during a screen-share interruption.
- **Do not use for content after 00:14.** Use Whisper + existing analysis for 00:14–00:44.
- Combined coverage: Gemini 00:00–00:14 + Whisper 00:14–00:44 = full session.

**Staff session** (`Mantri project User interview_Mar_27_2026-11_17-AM_gemini.txt`):
- ✅ Clean throughout. 641 lines, good speaker diarisation (Speaker A/B).
- Better Hinglish fidelity than Whisper. Preferred transcript for this session.
- Identified third staff member: Abisha (not present, referenced by Ashish).

---

### Sarvam (`sarvam/`) — model: `saaras:v3`, batch API, with diarisation

**Ashish Part 1** (`Part 1 Ashish interview_Mar_27_2026-07_41-PM_sarvam_roman.txt`):
- ✅ 720 segments, full 44 min, no hallucination. Roman/Latin script throughout.
- Auto-detect chose `en-IN` (Kunal speaks English), so output is already Roman.
- **Definitive transcript for Ashish Part 1.** Supersedes Whisper + Gemini.

**Staff session** (`Mantri project User interview_Mar_27_2026-11_17-AM_sarvam_roman.txt`):
- ✅ 776 segments, full 67 min, no hallucination. Roman/Latin script throughout.
- Originally output in Devanagari (`_sarvam_transcribe.txt`) because Hindi content dominated auto-detect → converted to Roman using `scripts/transliterate_sarvam.py` (Sarvam transliteration API, `hi-IN → en-IN`).
- **Definitive transcript for Staff session.** Supersedes Whisper + Gemini.

---

## Analyses

| File | Source transcript | Notes |
|---|---|---|
| `analysis_ashish_part1.md` | Whisper | Complete for 00:14–00:44; blind for 00:00–00:14 |
| `analysis_staff_interview.md` | Whisper | Usable; some Hindi segments missing |
| `gemini/analysis_ashish_part1_gemini.md` | Gemini | Covers 00:00–00:14 only (loop after that) |
| `gemini/analysis_staff_interview_gemini.md` | Gemini | Full session; adds 3 new findings vs Whisper analysis |

**Definitive analyses** (to be written after Sarvam transcripts complete):
- Merge `analysis_ashish_part1.md` + `gemini/analysis_ashish_part1_gemini.md` + Sarvam transcript
- Update `reports/user_research_synthesis.md` with any new findings

---

## Transcription Scripts

| Script | Model | Best for |
|---|---|---|
| `scripts/transcribe.py` | Whisper large-v3 (local) | Quick local transcription, no API key needed |
| `scripts/transcribe_gemini.py` | Gemini 2.5 Pro | Clean English/Hinglish; avoid for >40 min recordings (hallucination risk) |
| `scripts/transcribe_sarvam.py` | Sarvam Saaras v3 | Hindi/Hinglish/Assamese; best for this project's language profile |
