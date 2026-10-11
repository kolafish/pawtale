import type { MemoirVoiceSessionSummary } from "./memoir-voice-session";

const AI_INTERVIEW = {
  label: "AI 访谈",
  description: "想讲什么就讲什么，小余在旁边听；一段讲完了，会陪您想想接下来聊什么。",
  action: "开始讲述",
};

export const NARRATION_MODES = {
  guided_interview: AI_INTERVIEW,
  // Free narration and guided interview are one AI interview now; the key stays for older sessions and projects.
  free_narration: AI_INTERVIEW,
  book_completion: { label: "书稿补充", description: "先选书稿中的待补问题，再围绕问题补讲。", action: "选择待补问题" },
  human_interview: { label: "真人访谈", description: "家人朋友来提问，分别记录采访者与讲述者。", action: "开始真人访谈" },
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
