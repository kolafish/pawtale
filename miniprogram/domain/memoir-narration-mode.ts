import type { MemoirVoiceSessionSummary } from "./memoir-voice-session";

const AI_INTERVIEW = {
  label: "AI 访谈",
  description: "自由讲述，小余适时追问。",
  action: "开始讲述",
};

export const NARRATION_MODES = {
  guided_interview: AI_INTERVIEW,
  // Free narration and guided interview are one AI interview now; the key stays for older sessions and projects.
  free_narration: AI_INTERVIEW,
  book_completion: { label: "书稿补充", description: "选择待补问题，再补讲。", action: "选择待补问题" },
  human_interview: { label: "真人访谈", description: "家人提问，分别记录双方。", action: "开始真人访谈" },
};

export type NarrationMode = keyof typeof NARRATION_MODES;
export type DefaultNarrationMode = NarrationMode;
export const NARRATION_MODE_OPTIONS = Object.entries(NARRATION_MODES)
  .filter(([value]) => value !== "free_narration")
  .map(([value, option]) => ({ value, ...option }));
export const DEFAULT_NARRATION_MODE_OPTIONS = NARRATION_MODE_OPTIONS as Array<
  (typeof NARRATION_MODE_OPTIONS)[number] & { value: DefaultNarrationMode }
>;

export function narrationModeForSession(session: Pick<MemoirVoiceSessionSummary, "guidance_mode" | "interview_type" | "session_goal">): NarrationMode {
  if (session.session_goal === "book_completion") return "book_completion";
  if (session.interview_type === "human_interview") return "human_interview";
  return "guided_interview";
}
