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
    label: "低 · 保留原话",
    description: "去口头赘词和重复，尽量保留原话。",
  },
  {
    value: "medium",
    label: "中 · 适度润色",
    description: "适度调整口语，让文字自然连贯。",
  },
  {
    value: "high",
    label: "高 · 深度提炼",
    description: "重组和润色表达，不改动或补写事实。",
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
