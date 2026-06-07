<div align="center">

# yap

**AI-powered transcript editor. Upload a video → get a tighter cut.**

Upload → Transcribe → Edit transcript → Export MP4

</div>

---

## What it does

Yap transcribes your video with word-level timestamps, then an AI pipeline decides what to cut. You see a transcript — flip segments on/off, restore individual words, preview in real time, and export a rendered MP4 with J/L-cuts and audio ducking baked in.

No timeline scrubbing. No manual razor cuts. Just edit the text.

## Features

- **Verbatim transcription** — MLX-Whisper with word-level timestamps, de-hallucinated across segment boundaries
- **Holistic AI editing** — feeds the full transcript to Gemini 2.5 Flash as continuous prose; cuts land at word boundaries, never forced to chunk edges
- **Iterative critic loop** — editor → judge → revision notes → re-edit; always returns the best-scoring round
- **Word-level cuts** — click any word to cut it; filler words (um, uh, like) removed automatically
- **J/L-cuts & audio ducking** — true overlapping audio via `adelay + amix`; B-roll volume control
- **Seekable video preview** — scrub to any word in the transcript; the video jumps with you
- **Audio enhancement** — optional pre-processing pass (noise reduction, normalization)
- **Export to MP4** — ffmpeg `filter_complex` render; no re-encoding the parts you keep
- **Project save/load** — persisted to Supabase; import/export `.yap` project files
- **Native desktop app** — Electron wrapper with Google OAuth, system tray, and macOS dock integration

## Stack

| Layer | Tech |
|---|---|
| Frontend | Next.js 15 App Router, TypeScript, Tailwind CSS |
| Desktop | Electron 41 (Next.js on port 3241) |
| Transcription | MLX-Whisper (Apple Silicon, word-level) |
| AI | Gemini 2.5 Flash (cloud) · Gemma 4B via LM Studio (local fallback) |
| Video | ffmpeg — `filter_complex`, not concat demuxer |
| Auth & DB | Supabase (PKCE OAuth, Postgres) |
| Audio analysis | librosa, numpy |

## AI Pipeline

```
Upload video
    │
    ├─ analyze.py       — motion, RMS, pitch, silence, optional Gemma visual scan
    └─ transcribe.py    — MLX-Whisper word-level timestamps
            │
        tighten.py      — full-transcript prose edit (Gemini) → difflib alignment
            │               → iterative critic loop → best-scored round
        filler.py       — um/uh/like/you know cuts
        surgeon.py      — acoustic boundary snapping (librosa onset + energy envelope)
        rhythmist.py    — J/L-cut decisions, audio ducking, zoom hints
        linter.py       — deterministic QA (duration, overlaps, clipping)
            │
        export          — ffmpeg filter_complex render → MP4
```

The editing model receives the full transcript as continuous prose and returns it with words removed (deletion-only). A `difflib.SequenceMatcher` diff maps deletions back to Whisper timestamps. The model can only under-cut, never cut the wrong words.

## Installation

### Desktop app (macOS, Apple Silicon)

```bash
git clone https://github.com/your-username/yap
cd yap
npm install
python3 -m venv .venv && .venv/bin/pip install -e .
cp .env.local.example .env.local   # add GEMINI_API_KEY and Supabase credentials
npm run electron:dev
```

Requires: Node 20+, Python 3.11+, ffmpeg in PATH.

### Web only

```bash
npm run dev
# → http://localhost:3241
```

## Environment

```bash
# .env.local
GEMINI_API_KEY=...
NEXT_PUBLIC_SUPABASE_URL=...
NEXT_PUBLIC_SUPABASE_ANON_KEY=...
```

### Local LLM fallback (no Gemini key)

Point the pipeline at any OpenAI-compatible server:

```bash
export LLM_BASE_URL=http://127.0.0.1:1234   # LM Studio default
export LLM_MODEL=google/gemma-4-e4b
npm run electron:dev
```

Load the model with at least **8192 context** in LM Studio settings.

## Pipeline CLI

```bash
source .venv/bin/activate

# Run full pipeline on a video
python3 scripts/orchestrator.py video.mp4

# Save a fixture for eval
python3 scripts/orchestrator.py video.mp4 --save-fixture

# Run evals against all fixtures
GEMINI_API_KEY=<key> python3 scripts/eval.py

# Skip visual scan (faster)
python3 scripts/orchestrator.py video.mp4 --no-vision
```

## Eval system

Fixture-based LLM-as-judge scoring: **coherence**, **preservation**, **conciseness** (0–100 each). Results saved to `eval_results/`. The tighten pipeline iterates until coherence ≥ 90 or the round cap, returning the best-scoring round.

Current baseline (5 fixtures): coherence 85 · preservation 92 · conciseness 90.

## Project structure

```
app/                  Next.js app (API routes + pages)
  api/
    transcribe/       Calls orchestrator.py subprocess
    export/           ffmpeg render, streams MP4
    video/            Byte-range video serving
components/
  TranscriptEditor    Segment editor — keep/drop, word cuts, badges
  ExportPanel         Export trigger + download
electron/
  main.js             Electron entry — OAuth deep link, splash, dock icon
scripts/
  orchestrator.py     Pipeline entry point
  tighten.py          Holistic transcript editor + critic loop
  transcribe.py       MLX-Whisper wrapper
  surgeon.py          Acoustic boundary snapping
  rhythmist.py        J/L-cuts, ducking, zoom
  eval.py             Fixture eval runner
  gen-icon.js         macOS dock icon PNG generator
```

## License

MIT
