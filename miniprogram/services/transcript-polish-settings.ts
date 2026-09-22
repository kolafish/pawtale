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
    description: "去掉填充词、磕绊、赘词和无信息重复，其余尽量保留原话。",
  },
  {
    value: "medium",
    label: "中 · 适度润色",
    description: "适量调整口语表达，使文字自然、连贯、偏书面。",
  },
  {
    value: "high",
    label: "高 · 深度提炼",
    description: "允许较大幅度重组和美化表达，但不改动或补写事实。",
  },
];

export const TRANSCRIPT_POLISH_LEVELS = TRANSCRIPT_POLISH_OPTIONS.map(
  (option) => option.value,
);

export function transcriptPolishLevel(
  project?: Pick<Project, "metadata_json"> | null,
): MemoirTranscriptPolishLevel {
  const value = project?.metadata_json?.transcript_polish_level;
  return value === "low" || value === "high" ? value : "medium";
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
