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
const PYTHON = path.join(process.cwd(), ".venv", "bin", "python3");
const DETECT_FACE_SCRIPT = path.join(process.cwd(), "scripts", "detect_face.py");

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
  source?: "filler" | "silence" | "manual" | "trim";
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

interface Overlay {
  id: string;
  sourceAttachSec: number;
  sourceEndSec?: number | null;
  durationSec: number;
  imagePath: string;
  imageUrl?: string;
  layout?: "overlay" | "split-left" | "split-right";
  label?: string;
}

// Maps rendered spans: source [srcStart,srcEnd) → output start time
interface SpanMap { srcStart: number; srcEnd: number; outStart: number; }

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
  audioInputIdx = 0,
): { filterComplex: string; vMap: string; aMap: string; spanCount: number; spanMap: SpanMap[] } {
  const vParts: string[] = [];
  const aParts: string[] = [];
  const vOutputs: string[] = [];
  const aOutputs: string[] = [];
  const spanMap: SpanMap[] = [];

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
        // Include any cut that OVERLAPS the group range, not just fully-contained
        // ones. Word-cut timestamps routinely extend past seg.endSec (Whisper word
        // ends, segment trims), and the old "fully contained" test silently dropped
        // those cuts — making the cut content reappear in the export. computeKeepSpans
        // clamps each zone to [group.vStart, group.vEnd], so an overlapping cut is
        // safely trimmed to the group rather than lost. (Matches the preview's
        // VideoTimeline.computeKeepSpans, which clamps instead of dropping.)
        if (wc.endSec > group.vStart && wc.startSec < group.vEnd) {
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

      aParts.push(`[${audioInputIdx}:a]${aChain.join(",")}[a${spanIdx}]`);
      aOutputs.push(`[a${spanIdx}]`);

      // Record source→output mapping for overlay positioning (built in the same
      // loop so it can't drift from what ffmpeg actually renders)
      spanMap.push({ srcStart: span.startSec, srcEnd: span.endSec, outStart: videoOutputSec });

      videoOutputSec += spanVideoDur;
      spanIdx++;
    }
  }

  if (spanIdx === 0) {
    return { filterComplex: "", vMap: "", aMap: "", spanCount: 0, spanMap: [] };
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
    spanMap,
  };
}

export async function POST(req: NextRequest) {
  const { filePath, plan, segments: legacySegments, customAudioPath } = (await req.json()) as {
    filePath: string;
    plan?: { version?: number; segments?: Segment[]; enhancedAudioPath?: string; overlays?: Overlay[] };
    customAudioPath?: string | null;
    segments?: Segment[];
  };
  const segments = plan?.segments ?? legacySegments;
  const overlays = (plan?.overlays ?? []).filter((o) => existsSync(o.imagePath));
  // customAudioPath (user re-uploaded audio e.g. from Adobe Podcast) takes priority
  const enhancedAudioPath = customAudioPath ?? plan?.enhancedAudioPath ?? null;

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

  const exportedAudioPath = path.join(tmpDir, `${outputId}_exported_audio.wav`);

  // Keep rendered files for up to 1 hour so the Adobe Podcast round-trip can
  // reference them. Source video is never deleted here.
  const cleanup = () => {
    try { unlinkSync(outputPath); } catch {}
    try { unlinkSync(exportedAudioPath); } catch {}
  };

  try {
    const useEnhanced = enhancedAudioPath && existsSync(enhancedAudioPath);
    const audioInputIdx = useEnhanced ? 1 : 0;
    const { filterComplex: baseFilter, vMap: baseVMap, aMap, spanCount, spanMap } = buildFilters(
      keptSegments, segments, videoDuration, sourceW, sourceH, audioInputIdx
    );

    if (spanCount === 0) {
      return NextResponse.json({ error: "No renderable spans after processing" }, { status: 400 });
    }

    // ── Face detection (for split overlays) ─────────────────────────────────
    // Detect where in the frame the speaker's face is so we can center them
    // in the non-graphic panel rather than assuming they're at 50%.
    let faceCenterX = 0.5; // normalized, fallback to center
    let faceCenterY = 0.4; // normalized, fallback to slightly above center
    const hasSplitOverlay = overlays.some(o => o.layout === "split-left" || o.layout === "split-right");
    if (hasSplitOverlay && sourceW > 0) {
      try {
        const { stdout } = await execFileAsync(PYTHON, [DETECT_FACE_SCRIPT, filePath], { timeout: 30_000 });
        const fd = JSON.parse(stdout.trim());
        if (typeof fd.face_x === "number") faceCenterX = fd.face_x;
        if (typeof fd.face_y === "number") faceCenterY = fd.face_y;
        console.log(`[export] face detection: x=${faceCenterX.toFixed(3)} y=${faceCenterY.toFixed(3)} detected=${fd.detected} samples=${fd.samples ?? 0}`);
      } catch (e) {
        console.warn("[export] face detection failed, using center:", e instanceof Error ? e.message : e);
      }
    }

    // ── Overlay compositing ──────────────────────────────────────────────────
    // Image inputs start after: [0]=video, [1]=enhanced audio (optional)
    const overlayInputBase = useEnhanced ? 2 : 1;

    // Map each overlay's source attach time to its output time.
    // Walk spans in order; if the attach time falls in a cut zone, snap to the
    // next span's output start. If no span follows, skip the overlay.
    function srcToOut(srcT: number): number | null {
      for (const span of spanMap) {
        if (srcT < span.srcStart) {
          // srcT is before this span (in a cut zone preceding it) — snap here
          return span.outStart;
        }
        if (srcT < span.srcEnd) {
          // srcT is inside this span
          return span.outStart + (srcT - span.srcStart);
        }
      }
      return null; // past all rendered content — skip
    }

    // Total output duration — used to cap overlay end so it can't overshoot the video.
    const totalOutputDuration = spanMap.length > 0
      ? spanMap[spanMap.length - 1].outStart + (spanMap[spanMap.length - 1].srcEnd - spanMap[spanMap.length - 1].srcStart)
      : 0;

    // Compute output positions for each overlay image.
    // inputIdx tracks the actual ffmpeg -i index; it only increments for overlays
    // that pass the srcToOut check, matching the overlayImageInputs array order.
    type OverlayOp = { overlay: Overlay; outStart: number; outEnd: number; inputIdx: number };
    const overlayOps: OverlayOp[] = [];
    let nextInputIdx = overlayInputBase;
    for (const ov of overlays) {
      const outStart = srcToOut(ov.sourceAttachSec);
      if (outStart === null) continue; // attachment point past end — skip input too
      // If sourceEndSec is set, recompute duration from the current span map so
      // edits inside the overlay window (filler cuts, silence removal) automatically
      // shrink/grow the overlay rather than drifting onto different content.
      const rawOutEnd = ov.sourceEndSec != null ? srcToOut(ov.sourceEndSec) : null;
      // If sourceEndSec is past the last span, srcToOut returns null — fall back to stored
      // durationSec but cap to total output so we never overshoot the video length.
      const outEnd = Math.min(
        rawOutEnd ?? outStart + ov.durationSec,
        totalOutputDuration,
      );
      overlayOps.push({
        overlay: ov,
        outStart,
        outEnd: Math.max(outStart + 0.05, outEnd), // tiny floor only to avoid zero-duration
        inputIdx: nextInputIdx++,
      });
    }

    // Build filter_complex: append overlay chain after concat if any overlays
    let filterComplex = baseFilter;
    let vMap = baseVMap;

    if (overlayOps.length > 0) {
      const ovParts: string[] = [];
      let prevLabel = "outv";
      for (let i = 0; i < overlayOps.length; i++) {
        const { overlay: ov, outStart, outEnd, inputIdx } = overlayOps[i];
        const nextLabel = i < overlayOps.length - 1 ? `ov${i}` : "outv_final";
        const enableExpr = `gte(t,${outStart.toFixed(4)})*lt(t,${outEnd.toFixed(4)})`;
        const layout = ov.layout ?? "overlay";

        if ((layout === "split-left" || layout === "split-right") && sourceW > 0 && sourceH > 0) {
          // Zoom the video 1.35× into the speaker's face, then overlay the graphic on
          // the side on top of the zoomed video — no black panel, room background fills
          // in behind the graphic. The split+cover pattern prevents seeing both the
          // normal and zoomed video simultaneously.
          const zoomW = Math.floor(sourceW * 1.35 / 2) * 2;
          const zoomH = Math.floor(sourceH * 1.35 / 2) * 2;
          const imgW  = Math.floor(sourceW * 0.42 / 2) * 2; // graphic ≈ 42% of width
          const margin = Math.round(sourceW * 0.02);          // 2% margin from edge
          const imgX   = layout === "split-left" ? margin : sourceW - imgW - margin;
          // Face-aware crop: place the speaker's detected face at the center of
          // the non-graphic panel. Falls back to center (0.5) if detection failed.
          const facePxZoomed = faceCenterX * sourceW * 1.35; // face x in the zoomed frame
          // Center of the speaker's panel in the output
          const speakerPanelCenter = layout === "split-left"
            ? (imgW + margin) + (sourceW - imgW - margin) / 2   // center of right portion
            : (sourceW - imgW - margin) / 2;                     // center of left portion
          const rawCropX = Math.round(facePxZoomed - speakerPanelCenter);
          const cropX = Math.max(0, Math.min(zoomW - sourceW, rawCropX));
          const rawCropY = Math.round(faceCenterY * zoomH - sourceH / 2);
          const cropY = Math.max(0, Math.min(zoomH - sourceH, rawCropY));

          ovParts.push(`[${prevLabel}]split[base_${i}][cp_${i}]`);
          // Zoom + directional crop: speaker appears opposite the graphic
          ovParts.push(`[cp_${i}]scale=${zoomW}:${zoomH},crop=${sourceW}:${sourceH}:${cropX}:${cropY}[zoomed_${i}]`);
          // Graphic: scale to fit the panel width, contained — room bg shows in margins
          ovParts.push(`[${inputIdx}:v]scale=${imgW}:${sourceH}:force_original_aspect_ratio=decrease[img_fit_${i}]`);
          // Overlay graphic on the zoomed video, vertically centered
          ovParts.push(`[zoomed_${i}][img_fit_${i}]overlay=x=${imgX}:y=(H-h)/2[withimg_${i}]`);
          // Cover base video with zoomed+graphic version only during enable window
          ovParts.push(`[base_${i}][withimg_${i}]overlay=x=0:y=0:enable='${enableExpr}'[${nextLabel}]`);
        } else {
          // Regular overlay: scale graphic and composite centered or side-positioned
          const scaledW = sourceW > 0 ? Math.round(sourceW * 0.6) : 720;
          ovParts.push(`[${inputIdx}:v]scale=${scaledW}:-1[ovs${i}]`);
          ovParts.push(`[${prevLabel}][ovs${i}]overlay=x=(W-w)/2:y=(H-h)/2:enable='${enableExpr}'[${nextLabel}]`);
        }
        prevLabel = nextLabel;
      }
      filterComplex = baseFilter + ";" + ovParts.join(";");
      vMap = `[${prevLabel}]`;
    }

    // -framerate 25 -loop 1: treat the JPEG as a 25fps looping stream so ffmpeg
    // has a stable PTS for every frame of the output — without this a single-frame
    // image can present at PTS=0 and bleed through at unexpected times.
    // -t outEnd+0.5: cap the loop at just past the overlay's enable window so the
    // infinite stream terminates; without this ffmpeg spins forever draining it.
    const overlayImageInputs = overlayOps.flatMap(({ overlay, outEnd }) => [
      "-framerate", "25", "-loop", "1", "-t", (outEnd + 0.5).toFixed(3), "-i", overlay.imagePath,
    ]);
    const ffmpegInputs = [
      "-i", filePath,
      ...(useEnhanced ? ["-i", enhancedAudioPath!] : []),
      ...overlayImageInputs,
    ];

    await execFileAsync(FFMPEG, [
      ...ffmpegInputs,
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

    // Extract audio from the rendered cut for the Adobe Podcast round-trip.
    try {
      await execFileAsync(FFMPEG, [
        "-y", "-i", outputPath, "-vn", "-ar", "48000", "-ac", "1", exportedAudioPath,
      ], { timeout: 120_000 });
    } catch { /* non-fatal — round-trip just won't be available */ }

    const stat = statSync(outputPath);
    const nodeStream = createReadStream(outputPath);
    const webStream = Readable.toWeb(nodeStream) as ReadableStream;

    const response = new NextResponse(webStream, {
      headers: {
        "Content-Type": "video/mp4",
        "Content-Length": stat.size.toString(),
        "Content-Disposition": `attachment; filename="edited_${Date.now()}.mp4"`,
        "X-Exported-Video-Path": outputPath,
        "X-Exported-Audio-Path": existsSync(exportedAudioPath) ? exportedAudioPath : "",
      },
    });

    // Keep files for 1 hour for the Adobe round-trip, then clean up.
    setTimeout(cleanup, 60 * 60 * 1000);
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
