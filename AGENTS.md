# Yap Editor — Agent & Codebase Guide

Transcript-based video editor. Upload a video → AI pipeline transcribes, plans cuts, and exports an edited MP4 via ffmpeg.

---

## Stack

- **Frontend**: Next.js 15 App Router, TypeScript, Tailwind CSS
- **Backend (Python)**: Multi-agent pipeline in `scripts/`, runs as a subprocess from Next.js API routes
- **AI**: Gemini 2.5 Flash (Director + Narrative Architect + eval judge), MLX-Whisper (transcription), Gemma 4 via mlx-vlm (optional visual scan)
- **Video**: ffmpeg for all rendering (filter_complex, not concat demuxer)

## Next.js

This version has breaking changes — APIs, conventions, and file structure may differ from training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.

---

## Python Pipeline

### Setup

```bash
source .venv/bin/activate
# or prefix commands with .venv/bin/python3
```

All deps are in `pyproject.toml`. Install with:
```bash
python3 -m venv .venv && .venv/bin/pip install -e .
```

### Entry point

```
scripts/orchestrator.py <video_path> [whisper_model] [--no-vision] [--save-fixture]
```

Outputs a single JSON edit plan to stdout. The Next.js `/api/transcribe` route calls this as a subprocess.

### Agent pipeline (in order)

| Step | File | Role |
|------|------|------|
| 1a | `analyze.py` | Sensor Array — motion, audio RMS, pitch, silence regions, optional Gemma visual tags |
| 1b | `transcribe.py` | MLX-Whisper word-level timestamps (runs parallel with analyze) |
| 2 | orchestrator (Gemini) | Director — reads sensor summary, emits per-agent config JSON |
| 3 | orchestrator (Gemini) | Narrative Architect — keep/drop decisions per segment |
| 4 | `surgeon.py` | Heuristic Surgeon — acoustic word boundary snapping, silence removal |
| 5 | `continuity.py` | Continuity Guard — restores short bridges around broken joins |
| 6 | `rhythmist.py` | The Rhythmist — J/L-cut decisions, audio ducking, zoom hints |
| 7 | `linter.py` | Integrity Linter — deterministic QA (min duration, overlaps, clipping) |
| 8 | `filler.py` | Filler word cuts (um, uh, like, etc.) |

### Key design decisions

- **Director config**: Gemini outputs a config JSON (`{surgeon.aggressiveness, narrative.cut_target_pct, rhythmist.j_cut_threshold, ducking_enabled}`) that downstream agents consume — thresholds are LLM-driven, not hardcoded.
- **Acoustic boundary snapping** (`surgeon.py`): Uses energy envelope (`_find_speech_end`) for cut starts and librosa onset detection with `backtrack=True` (`_find_speech_onset`) for cut ends. Replaces naive zero-crossing snap.
- **Word cut padding**: 3ms pre, 18ms post (asymmetric — trailing consonants need room).
- **`--no-vision`**: Skips the Gemma visual scan (mlx-vlm). Faster; no visual tags. Passed through from frontend toggle.
- **`--save-fixture`**: Wraps output in `{metadata, plan}` and saves to `fixtures/`. Used for eval.

---

## API Routes

| Route | What it does |
|-------|-------------|
| `/api/upload` | Streams video to `./tmp/{uuid}.mp4` |
| `/api/transcribe` | Calls `orchestrator.py`, returns full edit plan JSON |
| `/api/replan` | Re-runs Narrative Architect on an existing transcript (skips transcription) |
| `/api/finalize` | Saves approved edit plan |
| `/api/export` | ffmpeg render — builds `filter_complex`, streams output MP4 back |

---

## ffmpeg Export (`app/api/export/route.ts`)

**Critical**: video and audio are built as separate filter chains and combined at the end. This is what enables true J/L-cuts.

- **Video**: `trim → optional zoom/crop → concat` (v=1, a=0)
- **Audio**: `atrim → asetpts → volume → afade-in → afade-out → adelay → amix`
- `adelay` positions each audio stream at the correct output timestamp; `amix` with `normalize=0` sums them
- **Zoom**: only applied when ffprobe succeeds and `sourceW/sourceH` are known — prevents concat dimension mismatch
- **Fade constants**: `AUDIO_FADE = 0.025` (group edges), `AUDIO_FADE_INNER = 0.004` (word-cut seams, click prevention only)
- **Word cut padding**: `WORD_CUT_PAD_PRE = 0.003`, `WORD_CUT_PAD_POST = 0.018`
- Error reporting uses `stderr.slice(-1000)` (tail, not head — head is ffmpeg version banner)

---

## Frontend Components

| File | Role |
|------|------|
| `app/page.tsx` | Root — manages stage state, upload, transcription, export flow |
| `components/UploadStage.tsx` | Drop zone + vision toggle |
| `components/TranscriptEditor.tsx` | Segment editor — keep/drop, word cuts, badges (J-cut, L-cut, ducked, silence) |
| `components/ExportPanel.tsx` | Export trigger + download |

### Segment shape (TypeScript)

```ts
interface Segment {
  startSec: number;
  endSec: number;
  keep: boolean;
  wordCuts?: WordCut[];
  duckLevel?: number;        // 0–1 volume multiplier for B-roll ducking
  lCutTailSec?: number;      // extends outgoing audio past video end
  transition?: { type: "cut" | "j-cut" | "l-cut"; offsetSec: number };
  zoomHint?: { startScale: number; endScale: number; x: number; y: number } | null;
}
```

---

## Eval System

Fixture-based LLM-as-judge for edit plan quality.

```bash
# Capture a fixture during a pipeline run
.venv/bin/python3 scripts/orchestrator.py video.mp4 --save-fixture

# Run evals against all fixtures
GEMINI_API_KEY=<key> .venv/bin/python3 scripts/eval.py

# Target a specific fixture or model
.venv/bin/python3 scripts/eval.py fixtures/foo.json --model gemini-2.5-pro
```

Scores: **coherence**, **preservation**, **conciseness** (1–5 each) + false positives/negatives. Results saved to `eval_results/`.

---

## Environment

```bash
GEMINI_API_KEY=...   # required — used by orchestrator + eval judge
```

Set in `.env.local` for Next.js routes. Scripts read it from the environment directly.

## Ignored by git

`.venv/`, `fixtures/`, `eval_results/`, `videos/`, `tmp/`, `.env*`
