import { NextRequest, NextResponse } from "next/server";
import { existsSync, createReadStream, unlinkSync, statSync } from "fs";
import { execFileSync, execFile } from "child_process";
import { promisify } from "util";
import path from "path";
import { randomUUID } from "crypto";
import { Readable } from "stream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const execFileAsync = promisify(execFile);

function resolveFfmpegPath(): string {
  try {
    const p = execFileSync("which", ["ffmpeg"], { encoding: "utf8" }).trim();
    if (p && existsSync(p)) return p;
  } catch {}
  return "ffmpeg";
}

const FFMPEG = resolveFfmpegPath();

interface WordCut {
  id?: string;
  startSec: number;
  endSec: number;
  word?: string;
  source?: "filler" | "silence" | "manual";
  renderStartSec?: number;
  renderEndSec?: number;
}
interface Transition { type: "cut" | "j-cut" | "l-cut"; offsetSec: number; }
interface ZoomHint { startScale: number; endScale: number; x: number; y: number; }

interface WordTimestamp { word: string; start: number; end: number; }

interface Segment {
  startSec: number;
  endSec: number;
  keep: boolean;
  words?: WordTimestamp[];
  wordCuts?: WordCut[];
  duckLevel?: number;
  lCutTailSec?: number;
  transition?: Transition;
  zoomHint?: ZoomHint | null;
}

const WORD_CUT_PAD_PRE  = 0.003; // pre-cut margin before renderStartSec
const WORD_CUT_PAD_POST = 0.030; // post-cut margin after renderEndSec
const MIN_SPAN        = 0.15;  // minimum keep-span duration — anything shorter is inaudible
const COALESCE_GAP    = 0.12;  // merge cut zones separated by less than this (inter-word silence)
const PRE_ROLL      = 0.08;
const POST_ROLL     = 0.25;
const AUDIO_FADE    = 0.025; // fade at segment group edges
const AUDIO_FADE_INNER = 0.025; // audible crossfade at word-cut seams (was 4 ms click-only)

function computeKeepSpans(
  rangeStart: number,
  rangeEnd: number,
  wordCuts: WordCut[]
): Array<{ startSec: number; endSec: number }> {
  // No word cuts — keep the whole range if it clears MIN_SPAN, else drop it.
  // (Previously this was an unconditional early return, which let tiny groups
  // sneak through without the MIN_SPAN gate.)
  if (wordCuts.length === 0) {
    return (rangeEnd - rangeStart >= MIN_SPAN) ? [{ startSec: rangeStart, endSec: rangeEnd }] : [];
  }

  const rawZones = wordCuts
    .map((wc) => ({
      // renderStart/End already absorb up to half the surrounding silence gap
      // (bounded in filler.py); the pads add a small fixed margin. Clamp only to
      // the group range so the cut can extend into the silence around the word.
      s: Math.max((wc.renderStartSec ?? wc.startSec) - WORD_CUT_PAD_PRE, rangeStart),
      e: Math.min((wc.renderEndSec ?? wc.endSec)   + WORD_CUT_PAD_POST, rangeEnd),
    }))
    .filter((z) => z.e > z.s)
    .sort((a, b) => a.s - b.s);

  // Coalesce cut zones separated by less than COALESCE_GAP (inter-word silences).
  // Multiple consecutive manual word-cuts each span one Whisper word timestamp,
  // leaving tiny acoustic gaps between them. A gap under 120 ms is inter-word
  // silence — not content worth keeping. Merging the zones eliminates those gaps.
  const zones: typeof rawZones = [];
  for (const z of rawZones) {
    const last = zones[zones.length - 1];
    if (last && z.s - last.e < COALESCE_GAP) {
      last.e = Math.max(last.e, z.e);
    } else {
      zones.push({ ...z });
    }
  }

  const spans: Array<{ startSec: number; endSec: number }> = [];
  let cursor = rangeStart;

  for (const z of zones) {
    if (z.s - cursor >= MIN_SPAN) spans.push({ startSec: cursor, endSec: z.s });
    cursor = Math.max(cursor, z.e);
  }
  if (rangeEnd - cursor >= MIN_SPAN) spans.push({ startSec: cursor, endSec: rangeEnd });

  // Return spans as-is, even if empty. An empty result means the user's cuts left
  // only sub-MIN_SPAN remnants — those are dropped, not restored to the full range.
  // (The old fallback `spans.length > 0 ? spans : [fullRange]` was causing cut
  // content to reappear when all remaining keep-spans were individually < MIN_SPAN.)
  return spans;
}

/**
 * Build separate video and audio ffmpeg filter chains.
 *
 * Video: trim → optional zoom → concat (video-only).
 * Audio: trim → volume → afade-in → afade-out → adelay → amix.
 *
 * Separating video and audio is what enables true J/L-cuts: each audio span
 * is positioned at an independent output time via adelay, then all streams are
 * summed with amix. Adjacent audio streams can overlap at cut boundaries,
 * which is how J/L-cuts actually work.
 *
 * Word-cut fades: afade is applied at EVERY sub-span boundary (not just group
 * edges) so word-level splices don't click.
 *
 * Zoom: if a segment carries a zoomHint, its video is scaled up and cropped
 * back to original size for a static punch-in effect.
 */
function buildFilters(
  keptSegments: Segment[],
  allSegments: Segment[],
  videoDuration: number,
  sourceW = 0,
  sourceH = 0,
): { filterComplex: string; vMap: string; aMap: string; spanCount: number } {
  const vParts: string[] = [];
  const aParts: string[] = [];
  const vOutputs: string[] = [];
  const aOutputs: string[] = [];

  let spanIdx = 0;
  let videoOutputSec = 0; // cumulative output video duration

  // Padded bounds per kept segment
  const paddedKept = keptSegments.map((seg) => {
    const idx = allSegments.indexOf(seg);
    const prevEnd   = idx > 0                     ? allSegments[idx - 1].endSec   : 0;
    const nextStart = idx < allSegments.length - 1 ? allSegments[idx + 1].startSec : videoDuration;
    return {
      seg,
      vStart: Math.max(seg.startSec - PRE_ROLL,  prevEnd),
      vEnd:   Math.min(seg.endSec   + POST_ROLL, nextStart, videoDuration),
    };
  });

  // Merge overlapping padded ranges into groups
  const merged: Array<{ vStart: number; vEnd: number; segs: typeof paddedKept }> = [];
  for (const p of paddedKept) {
    const last = merged.at(-1);
    if (last && p.vStart <= last.vEnd) {
      last.vEnd = Math.max(last.vEnd, p.vEnd);
      last.segs.push(p);
    } else {
      merged.push({ vStart: p.vStart, vEnd: p.vEnd, segs: [p] });
    }
  }

  for (const group of merged) {
    const wordCutsInRange: WordCut[] = [];
    for (const { seg } of group.segs) {
      for (const wc of (seg.wordCuts ?? [])) {
        if (wc.startSec >= group.vStart && wc.endSec <= group.vEnd) {
          wordCutsInRange.push(wc);
        }
      }
    }

    const allSpans = computeKeepSpans(group.vStart, group.vEnd, wordCutsInRange);
    // Drop spans that contain no spoken word. These are silent slivers left
    // behind when a silence cut only removes the RMS-silent middle of an
    // inter-word gap, leaving room-tone on either side that clears MIN_SPAN
    // and would otherwise render as a wordless clip. Skip the guard when the
    // group has no word timestamps at all, so plans lacking word-level data
    // (e.g. older/fallback plans) don't lose real speech.
    const groupWords = group.segs.flatMap(({ seg }) => seg.words ?? []);
    const spans =
      groupWords.length === 0
        ? allSpans
        : allSpans.filter((span) =>
            groupWords.some((w) => w.end > span.startSec && w.start < span.endSec),
          );

    for (const [spanInGroupIdx, span] of spans.entries()) {
      const isFirstInGroup = spanInGroupIdx === 0;
      const spanVideoDur = span.endSec - span.startSec;

      // Pick properties from the segment with most overlap with this span
      let bestOverlap = 0;
      let duckLevel = 1.0;
      let zoomHint: ZoomHint | null = null;
      let matchedSeg: Segment | null = null;
      for (const { seg } of group.segs) {
        const overlap = Math.min(seg.endSec, span.endSec) - Math.max(seg.startSec, span.startSec);
        if (overlap > bestOverlap) {
          bestOverlap = overlap;
          duckLevel   = seg.duckLevel ?? 1.0;
          zoomHint    = seg.zoomHint ?? null;
          matchedSeg  = seg;
        }
      }

      // ── Video ─────────────────────────────────────────────────────────────
      let vChain = `trim=start=${span.startSec}:end=${span.endSec},setpts=PTS-STARTPTS`;
      // Only apply zoom if we have confirmed source dimensions — without them the
      // crop can't guarantee exact original size, causing the video concat to fail.
      if (zoomHint && sourceW > 0 && sourceH > 0) {
        const s  = zoomHint.endScale;
        const cx = zoomHint.x;
        const cy = zoomHint.y;
        // Crop to EXACTLY sourceW×sourceH so all video spans fed into concat are
        // identical in size. After scale, `iw`/`ih` = scaled dims, so
        // (iw - sourceW) is the total horizontal margin to distribute.
        vChain += `,scale=trunc(iw*${s}/2)*2:trunc(ih*${s}/2)*2` +
                  `,crop=${sourceW}:${sourceH}:(iw-${sourceW})*${cx}:(ih-${sourceH})*${cy}`;
      }
      vParts.push(`[0:v]${vChain}[v${spanIdx}]`);
      vOutputs.push(`[v${spanIdx}]`);

      // ── Audio ─────────────────────────────────────────────────────────────
      // Default: audio window matches video window
      let aStart = span.startSec;
      let aEnd   = span.endSec;

      // Apply J/L-cut offsets only on the first sub-span of a group.
      // J-cut: pull audio start earlier so it precedes the video cut.
      // L-cut (outgoing): lCutTailSec on THIS segment extends its audio past video end.
      //   The *incoming* segment's transition.type === "l-cut" is UI metadata only —
      //   its audio starts normally; no extension needed here.
      if (isFirstInGroup && matchedSeg) {
        const t = matchedSeg.transition;
        if (t?.type === "j-cut") {
          aStart = Math.max(0, span.startSec - t.offsetSec);
        }
        const lTail = matchedSeg.lCutTailSec ?? 0;
        if (lTail > 0) aEnd = Math.min(videoDuration, span.endSec + lTail);
      }

      // Where this audio stream should begin in the OUTPUT timeline.
      // For a J-cut, audioOutputStart < videoOutputSec (audio precedes the cut).
      const audioOutputStart = videoOutputSec + (aStart - span.startSec);

      // Can't go before t=0; clip and adjust if needed
      let effectiveAStart = aStart;
      if (audioOutputStart < 0) effectiveAStart = aStart - audioOutputStart;
      const delayMs = Math.max(0, Math.round(audioOutputStart * 1000));

      const aDur = Math.max(0, aEnd - effectiveAStart);
      // Group edges get a full 25 ms fade; word-cut seams get 4 ms (click prevention only,
      // not long enough to audibly fade trailing consonants).
      const fadeIn  = Math.min(isFirstInGroup ? AUDIO_FADE : AUDIO_FADE_INNER, aDur / 4);
      const fadeOut = Math.min(spanInGroupIdx === spans.length - 1 ? AUDIO_FADE : AUDIO_FADE_INNER, aDur / 4);
      const fadeOutSt = Math.max(0, aDur - fadeOut).toFixed(4);

      const aChain: string[] = [
        `atrim=start=${effectiveAStart}:end=${aEnd}`,
        `asetpts=PTS-STARTPTS`,
      ];
      // Ducking: constant volume multiplier; fades handle the ramp in/out
      if (duckLevel !== 1.0) aChain.push(`volume=${duckLevel.toFixed(3)}`);
      aChain.push(`afade=t=in:st=0:d=${fadeIn.toFixed(4)}`);
      aChain.push(`afade=t=out:st=${fadeOutSt}:d=${fadeOut.toFixed(4)}`);
      // Delay positions this stream at the correct output time
      aChain.push(`adelay=${delayMs}:all=1`);

      aParts.push(`[0:a]${aChain.join(",")}[a${spanIdx}]`);
      aOutputs.push(`[a${spanIdx}]`);

      videoOutputSec += spanVideoDur;
      spanIdx++;
    }
  }

  if (spanIdx === 0) {
    return { filterComplex: "", vMap: "", aMap: "", spanCount: 0 };
  }

  const n = spanIdx;
  // Video: pure concat, no audio
  const vConcat = `${vOutputs.join("")}concat=n=${n}:v=1:a=0[outv]`;
  // Audio: mix all time-positioned streams; normalize=0 lets levels sum naturally
  const aMix    = `${aOutputs.join("")}amix=inputs=${n}:duration=longest:normalize=0[outa]`;

  return {
    filterComplex: [...vParts, ...aParts, vConcat, aMix].join(";"),
    vMap: "[outv]",
    aMap: "[outa]",
    spanCount: n,
  };
}

export async function POST(req: NextRequest) {
  const { filePath, plan, segments: legacySegments } = (await req.json()) as {
    filePath: string;
    plan?: { version?: number; segments?: Segment[] };
    segments?: Segment[];
  };
  const segments = plan?.segments ?? legacySegments;

  if (!filePath || !existsSync(filePath)) {
    return NextResponse.json({ error: "Source file not found" }, { status: 400 });
  }

  if (plan && plan.version !== 1) {
    return NextResponse.json({ error: "Unsupported edit plan version" }, { status: 400 });
  }

  if (!segments?.length) {
    return NextResponse.json({ error: "Edit plan not found" }, { status: 400 });
  }

  const keptSegments = segments.filter((s) => s.keep);
  if (keptSegments.length === 0) {
    return NextResponse.json({ error: "No segments selected" }, { status: 400 });
  }

  const videoDuration = Math.max(...segments.map((s) => s.endSec));
  const tmpDir = path.join(process.cwd(), "tmp");
  const outputId = randomUUID();
  const outputPath = path.join(tmpDir, `${outputId}_edited.mp4`);

  // Probe source dimensions so the zoom crop can target exact original size,
  // preventing concat from failing on dimension mismatches between spans.
  let sourceW = 0, sourceH = 0;
  try {
    const { stdout: probeOut } = await execFileAsync("ffprobe", [
      "-v", "quiet", "-print_format", "json",
      "-show_streams", "-select_streams", "v:0", filePath,
    ]);
    const stream = JSON.parse(probeOut).streams?.[0];
    if (stream) { sourceW = stream.width; sourceH = stream.height; }
  } catch { /* zoom hints will be skipped if probe fails */ }

  // Only remove the rendered output. The source video is kept so post-export
  // actions (thumbnail generation, re-export with different settings) still work.
  const cleanup = () => {
    try { unlinkSync(outputPath); } catch {}
  };

  try {
    const { filterComplex, vMap, aMap, spanCount } = buildFilters(
      keptSegments, segments, videoDuration, sourceW, sourceH
    );

    if (spanCount === 0) {
      return NextResponse.json({ error: "No renderable spans after processing" }, { status: 400 });
    }

    await execFileAsync(FFMPEG, [
      "-i", filePath,
      "-filter_complex", filterComplex,
      "-map", vMap,
      "-map", aMap,
      "-c:v", "libx264",
      "-preset", "medium",
      "-crf", "17",
      "-c:a", "aac",
      "-b:a", "192k",
      "-movflags", "+faststart",
      outputPath,
    ], { maxBuffer: 100 * 1024 * 1024, timeout: 600_000 });

    const stat = statSync(outputPath);
    const nodeStream = createReadStream(outputPath);
    const webStream = Readable.toWeb(nodeStream) as ReadableStream;

    const response = new NextResponse(webStream, {
      headers: {
        "Content-Type": "video/mp4",
        "Content-Length": stat.size.toString(),
        "Content-Disposition": `attachment; filename="edited_${Date.now()}.mp4"`,
      },
    });

    setTimeout(cleanup, 10_000);
    return response;
  } catch (err: unknown) {
    const e = err as { message?: string; stderr?: string };
    const stderr = e.stderr ?? "";
    const msg    = e.message ?? String(err);
    // Log full details server-side
    console.error("Export ffmpeg stderr:\n", stderr);
    console.error("Export error:", msg);
    try { unlinkSync(outputPath); } catch {}
    // ffmpeg dumps version info first; the actual error is at the end of stderr
    const detail = stderr ? stderr.slice(-1000) : msg.slice(-1000);
    return NextResponse.json({ error: "Export failed: " + detail }, { status: 500 });
  }
}
