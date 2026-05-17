export type FillerSensitivity = "conservative" | "balanced" | "aggressive";

export type WordTimestamp = {
  word: string;
  start: number;
  end: number;
};

export type WordCutSource = "filler" | "silence" | "manual";

export type WordCut = {
  id?: string;
  startSec: number;
  endSec: number;
  word: string;
  source: WordCutSource;
  renderStartSec?: number;
  renderEndSec?: number;
};

export type Transition = {
  type: "cut" | "j-cut" | "l-cut";
  offsetSec: number;
};

export type ZoomHint = {
  startScale: number;
  endScale: number;
  x: number;
  y: number;
};

export type LinterIssue = {
  severity: "error" | "warning";
  type: string;
  segIdx: number;
  detail: string;
};

export type DecisionSource = "pipeline" | "user" | "repair";

export type Segment = {
  start: string;
  end: string;
  startSec: number;
  endSec: number;
  text: string;
  keep: boolean;
  decisionSource?: DecisionSource;
  dropReason?: string;
  words?: WordTimestamp[];
  wordCuts?: WordCut[];
  jobRisk?: string;
  motionScore?: number;
  audioRms?: number;
  energyScore?: number;
  visualTag?: string | null;
  transition?: Transition;
  lCutTailSec?: number;
  duckLevel?: number;
  zoomHint?: ZoomHint | null;
  continuityRepair?: {
    reason: string;
    originalDropReason?: string;
  };
};

export type NarrativeTangent = { label: string; segIndices: number[] };

export type NarrativeRepGroup = {
  topic: string;
  bestIndex: number;
  duplicateIndices: number[];
};

export type NarrativeCircular = { label: string; segIndices: number[] };

export type NarrativeAnalysis = {
  coreStory?: string;
  narrativeArc?: string;
  tangents?: NarrativeTangent[];
  repetitionGroups?: NarrativeRepGroup[];
  circularSections?: NarrativeCircular[];
};

export type EditPlan = {
  version: 1;
  settings: {
    fillerSensitivity: FillerSensitivity;
  };
  segments: Segment[];
  issues: LinterIssue[];
  summary: string;
  totalDuration: string;
  editedDuration: string;
  language?: string;
  linterPassed?: boolean;
  directorConfig?: Record<string, unknown>;
  narrativeAnalysis?: NarrativeAnalysis;
};

export type JudgeItem = {
  segment_index: number;
  text: string;
  reason: string;
};

export type JudgeResult = {
  coherence: number;
  preservation: number;
  conciseness: number;
  coherence_reason: string;
  preservation_reason: string;
  conciseness_reason: string;
  false_positives: JudgeItem[];
  false_negatives: JudgeItem[];
  overall_notes: string;
};

export function buildCleanTranscript(segments: Segment[]): string {
  return segments
    .filter((segment) => segment.keep)
    .map((segment) => {
      if (!segment.words?.length) return segment.text.trim();
      const cuts = segment.wordCuts ?? [];
      return segment.words
        .filter((word, index) => {
          const id = wordCutId(segment, index);
          return !cuts.some(
            (cut) =>
              cut.id === id ||
              (word.start >= cut.startSec && word.end <= cut.endSec),
          );
        })
        .map((word) => word.word)
        .join(" ")
        .trim();
    })
    .join(" ");
}

export function wordCutId(segment: Segment, wordIndex: number): string {
  return `${segment.startSec.toFixed(3)}:${wordIndex}`;
}
