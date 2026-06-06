import { Segment, wordCutId } from "./editPlan";

export interface DiffWord {
  segIdx: number;
  wordIdx: number;
  word: string;
  norm: string;
}

export interface DiffResult {
  toDelete: DiffWord[];
  origWords: DiffWord[];
  /** 0–1 fraction of original words being deleted */
  deletionRatio: number;
}

export function normalizeToken(w: string): string {
  return w.toLowerCase().replace(/[^a-z0-9']/g, "");
}

/**
 * All currently-visible words (kept segments, not already word-cut).
 * Uses the exact same predicate as buildCleanTranscript so the baseline
 * matches what the user copied.
 */
export function buildVisibleWords(segments: Segment[]): DiffWord[] {
  const result: DiffWord[] = [];
  for (let si = 0; si < segments.length; si++) {
    const seg = segments[si];
    if (!seg.keep || !seg.words?.length) continue;
    const cuts = seg.wordCuts ?? [];
    for (let wi = 0; wi < seg.words.length; wi++) {
      const w = seg.words[wi];
      const id = wordCutId(seg, wi);
      const isCut = cuts.some(
        (c) => c.id === id || (w.start >= c.startSec && w.end <= c.endSec),
      );
      if (!isCut) {
        result.push({
          segIdx: si,
          wordIdx: wi,
          word: w.word,
          norm: normalizeToken(w.word),
        });
      }
    }
  }
  return result;
}

/**
 * LCS diff: returns which origWords should be cut to produce pastedText.
 * Empty paste is treated as a no-op (not "cut everything").
 */
export function diffTranscript(origWords: DiffWord[], pastedText: string): DiffResult {
  const pastedTokens = pastedText
    .replace(/\n+/g, " ")
    .split(/\s+/)
    .map(normalizeToken)
    .filter((t) => t.length > 0);

  const n = origWords.length;
  const m = pastedTokens.length;

  if (n === 0 || m === 0) {
    return { toDelete: [], origWords, deletionRatio: 0 };
  }

  // DP table as flat Int32Array (avoids nested array overhead)
  const dp = new Int32Array((n + 1) * (m + 1));
  const at = (i: number, j: number) => i * (m + 1) + j;

  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      if (origWords[i - 1].norm === pastedTokens[j - 1]) {
        dp[at(i, j)] = dp[at(i - 1, j - 1)] + 1;
      } else {
        dp[at(i, j)] = Math.max(dp[at(i - 1, j)], dp[at(i, j - 1)]);
      }
    }
  }

  // Backtrack to find matched original-word indices
  const matched = new Set<number>();
  let i = n, j = m;
  while (i > 0 && j > 0) {
    if (origWords[i - 1].norm === pastedTokens[j - 1]) {
      matched.add(i - 1);
      i--; j--;
    } else if (dp[at(i - 1, j)] >= dp[at(i, j - 1)]) {
      i--;
    } else {
      j--;
    }
  }

  const toDelete = origWords.filter((_, idx) => !matched.has(idx));
  return { toDelete, origWords, deletionRatio: toDelete.length / n };
}

/**
 * Groups toDelete words into contiguous per-segment runs for onCutRange calls.
 */
export function groupDeleteRanges(
  toDelete: DiffWord[],
): Array<{ segIdx: number; start: number; end: number }> {
  const bySegment = new Map<number, number[]>();
  for (const w of toDelete) {
    if (!bySegment.has(w.segIdx)) bySegment.set(w.segIdx, []);
    bySegment.get(w.segIdx)!.push(w.wordIdx);
  }

  const ranges: Array<{ segIdx: number; start: number; end: number }> = [];
  for (const [segIdx, indices] of bySegment) {
    const sorted = [...indices].sort((a, b) => a - b);
    let runStart = sorted[0], runEnd = sorted[0];
    for (let k = 1; k < sorted.length; k++) {
      if (sorted[k] === runEnd + 1) {
        runEnd = sorted[k];
      } else {
        ranges.push({ segIdx, start: runStart, end: runEnd });
        runStart = sorted[k];
        runEnd = sorted[k];
      }
    }
    ranges.push({ segIdx, start: runStart, end: runEnd });
  }
  return ranges;
}
