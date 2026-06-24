import type {
  MemoirAssistantTTSResponse,
  MemoirVoiceFollowUpResponse,
  TTSAlignmentSegment,
  VoiceChatTranscriptLine,
} from "../domain/voice-chat";
import { ttsAlignmentFromMetadata } from "./voice-chat-state";

export interface AssistantTurnQueueItem {
  messageId: string;
  text: string;
  expectsAnswer: boolean;
  autoListenAfterPlayback: boolean;
  forceRegenerate: boolean;
  line: VoiceChatTranscriptLine;
  prepare?: Promise<PreparedAssistantTurnAudio>;
}

export interface PreparedAssistantTurnAudio {
  messageId: string;
  audioFileId: string;
  alignment: TTSAlignmentSegment[];
  metadata: Record<string, unknown>;
}

export function assistantResponseMetadata(response: MemoirVoiceFollowUpResponse): Record<string, unknown> {
  const metadata = { ...(response.message.metadata_json || {}) };
  if (response.primary_intent) {
    metadata.primary_intent = response.primary_intent;
  }
  if (Array.isArray(response.intent_tags)) {
    metadata.intent_tags = response.intent_tags;
  }
  if (typeof response.expects_answer === "boolean") {
    metadata.expects_answer = response.expects_answer;
  }
  return metadata;
}

export function assistantLineFromResponse(response: MemoirVoiceFollowUpResponse): VoiceChatTranscriptLine {
  return {
    id: response.message.id,
    role: response.message.role,
    text: response.message.content_text,
    message_id: response.message.id,
    audio_file_id: response.message.audio_file_id || "",
    transcript_version_id: response.message.transcript_version_id,
    metadata_json: assistantResponseMetadata(response),
  };
}

export function assistantTurnQueueItem(
  response: MemoirVoiceFollowUpResponse,
  options: {
    autoListenAfterPlayback?: boolean;
    forceRegenerate?: boolean;
  } = {},
): AssistantTurnQueueItem {
  const line = assistantLineFromResponse(response);
  return {
    messageId: response.message.id,
    text: response.follow_up_text,
    expectsAnswer: response.expects_answer !== false,
    autoListenAfterPlayback: options.autoListenAfterPlayback ?? response.expects_answer !== false,
    forceRegenerate: options.forceRegenerate ?? false,
    line,
  };
}

export function applyAssistantTTSResponse(
  lines: VoiceChatTranscriptLine[],
  response: MemoirAssistantTTSResponse,
): {
  lines: VoiceChatTranscriptLine[];
  prepared: PreparedAssistantTurnAudio;
} {
  const matchedLine = lines.find((line) => (line.message_id || line.id) === response.message.id);
  const metadata = response.message.metadata_json || matchedLine?.metadata_json || {};
  const alignment = response.alignment && response.alignment.length
    ? response.alignment
    : ttsAlignmentFromMetadata(metadata);
  return {
    lines: lines.map((line) => {
      if ((line.message_id || line.id) !== response.message.id) {
        return line;
      }
      return {
        ...line,
        audio_file_id: response.audio_file_id,
        metadata_json: metadata,
      };
    }),
    prepared: {
      messageId: response.message.id,
      audioFileId: response.audio_file_id,
      alignment,
      metadata,
    },
  };
}
