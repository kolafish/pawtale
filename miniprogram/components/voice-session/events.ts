import type { MemoirGuidanceMode, MemoirInterviewType, MemoirSessionGoal } from "../../domain/memoir-voice-session";

export interface VoiceSessionEntry {
  voiceSessionId: string;
  projectId?: string;
  projectTitle?: string;
  mode?: MemoirGuidanceMode;
  interviewType?: MemoirInterviewType;
  sessionGoal?: MemoirSessionGoal;
  module?: string;
}

export type VoiceSessionExit =
  | { destination: "completion_issue"; projectId: string; projectTitle: string; issueId: string }
  | { destination: "story"; projectId: string; narrationSaved: boolean; finished?: boolean }
  | { destination: "completion" }
  | { destination: "transcripts" };

export interface VoiceSessionExitEvent { detail: VoiceSessionExit & { voiceSessionId: string } }
