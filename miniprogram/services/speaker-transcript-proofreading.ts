import { memoirAIRequest } from "./memoir-api-client";

export interface SpeakerProofreadContextLine {
  speakerId?: string | null;
  text: string;
  startMs?: number | null;
  endMs?: number | null;
}

export interface SpeakerProofreadResponse {
  text: string;
  proofread_text: string;
  changed: boolean;
  provider?: string | null;
  context_line_count: number;
}

export function proofreadSpeakerTranscriptLine(
  text: string,
  contextLines: SpeakerProofreadContextLine[] = [],
  aiModel?: string,
): Promise<SpeakerProofreadResponse> {
  return memoirAIRequest<
    SpeakerProofreadResponse,
    {
      text: string;
      context_lines: Array<{
        speaker_id: string | null;
        text: string;
        start_ms: number | null;
        end_ms: number | null;
      }>;
      ai_model: string | null;
    }
  >(
    "/api/mobile/memoir/speaker-transcription/proofread",
    {
      text,
      context_lines: contextLines
        .map((line) => ({
          speaker_id: line.speakerId || null,
          text: line.text.trim(),
          start_ms: typeof line.startMs === "number" ? line.startMs : null,
          end_ms: typeof line.endMs === "number" ? line.endMs : null,
        }))
        .filter((line) => line.text),
      ai_model: aiModel?.trim() || null,
    },
  );
}
