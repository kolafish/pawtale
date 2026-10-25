import type {
  MemoirTranscriptPolishLevel,
  Project,
} from "../domain/memoir";
import { readMemoirIdentitySnapshot } from "../stores/memoir-cache";

export interface TranscriptPolishOption {
  value: MemoirTranscriptPolishLevel;
  label: string;
  description: string;
}

export const TRANSCRIPT_POLISH_OPTIONS: TranscriptPolishOption[] = [
  {
    value: "low",
    label: "低 · 轻度整理",
    description: "理顺口语和句子，保留个人语气与事实。",
  },
  {
    value: "medium",
    label: "中 · 适度润色",
    description: "整理句群和段落，表达自然连贯，保留细节。",
  },
  {
    value: "high",
    label: "高 · 深度提炼",
    description: "重组叙述与节奏，保留细节，不补写事实。",
  },
];

export const TRANSCRIPT_POLISH_LEVELS = TRANSCRIPT_POLISH_OPTIONS.map(
  (option) => option.value,
);

export function transcriptPolishLevel(
  project?: Pick<Project, "metadata_json"> | null,
): MemoirTranscriptPolishLevel {
  const value = project?.metadata_json?.transcript_polish_level;
  return value === "low" || value === "medium" ? value : "high";
}

export function readSelectedTranscriptPolishLevel(): MemoirTranscriptPolishLevel | undefined {
  try {
    const snapshot = readMemoirIdentitySnapshot();
    return snapshot.isFresh
      ? transcriptPolishLevel(snapshot.identity?.active_project)
      : undefined;
  } catch {
    return undefined;
  }
}
