export type VoiceSessionExit =
  | { destination: "story"; projectId: string; narrationSaved: boolean }
  | { destination: "completion"; explainSaved?: boolean }
  | { destination: "transcripts" };

export interface VoiceSessionExitEvent { detail: VoiceSessionExit }
