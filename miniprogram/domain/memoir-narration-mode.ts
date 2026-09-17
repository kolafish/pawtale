import type { MemoirVoiceSessionSummary } from "./memoir-voice-session";

export const NARRATION_MODES = {
  free_narration: { label: "自由讲述", description: "按自己的节奏讲，AI 倾听并轻声提示。", action: "开始自由讲述" },
  guided_interview: { label: "引导访谈", description: "AI 按人物、事件和话题提问，帮您展开回忆。", action: "开始引导访谈" },
  book_completion: { label: "书稿补充", description: "先选书稿中的待补问题，再围绕问题补讲。", action: "选择待补问题" },
  human_interview: { label: "真人访谈", description: "家人朋友来提问，分别记录采访者与讲述者。", action: "开始真人访谈" },
};

export type NarrationMode = keyof typeof NARRATION_MODES;
export type DefaultNarrationMode = NarrationMode;
export const NARRATION_MODE_OPTIONS = Object.entries(NARRATION_MODES).map(([value, option]) => ({ value, ...option }));
export const DEFAULT_NARRATION_MODE_OPTIONS = NARRATION_MODE_OPTIONS as Array<
  (typeof NARRATION_MODE_OPTIONS)[number] & { value: DefaultNarrationMode }
>;

export function narrationModeForSession(session: Pick<MemoirVoiceSessionSummary, "guidance_mode" | "interview_type" | "session_goal">): NarrationMode {
  if (session.session_goal === "book_completion") return "book_completion";
  if (session.interview_type === "human_interview") return "human_interview";
  return session.guidance_mode;
}
