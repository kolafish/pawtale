export type AITaskProgressMode = "hidden" | "indeterminate" | "determinate";

export interface AITaskProgressView {
  title: string;
  detail: string;
  progress: number;
  progressMode: AITaskProgressMode;
}

export function aiTaskProgressPercent(
  completed: number,
  total: number,
): number {
  if (!Number.isFinite(completed) || !Number.isFinite(total) || total <= 0) {
    return 0;
  }
  const ratio = Math.max(0, Math.min(1, completed / total));
  return Math.round(ratio * 100);
}
