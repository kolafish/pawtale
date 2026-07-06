import { readSelectedAIModel } from "./ai-model-settings";
import { memoirRequest } from "./memoir-api-client";

export interface DebugSpeakerProofreadContextLine {
  speakerId?: string | null;
  text: string;
  startMs?: number | null;
  endMs?: number | null;
}

export interface DebugSpeakerProofreadResponse {
  text: string;
  proofread_text: string;
  changed: boolean;
  provider?: string | null;
  context_line_count: number;
}

export function proofreadDebugSpeakerTranscriptLine(
  text: string,
  contextLines: DebugSpeakerProofreadContextLine[] = [],
  aiModel?: string,
): Promise<DebugSpeakerProofreadResponse> {
  const selectedModel = aiModel?.trim() || readSelectedAIModel();
  return memoirRequest<DebugSpeakerProofreadResponse>("/api/mobile/memoir/debug/speaker-transcribe/proofread", {
    method: "POST",
    data: {
      text,
      context_lines: contextLines
        .map((line) => ({
          speaker_id: line.speakerId || null,
          text: line.text.trim(),
          start_ms: typeof line.startMs === "number" ? line.startMs : null,
          end_ms: typeof line.endMs === "number" ? line.endMs : null,
        }))
        .filter((line) => line.text),
      ...(selectedModel ? { ai_model: selectedModel } : {}),
    },
  });
}
