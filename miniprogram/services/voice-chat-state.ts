import type {
  MemoirGuidanceMode,
  TTSAlignmentSegment,
  VoiceChatErrorInfo,
  VoiceChatTranscriptLine,
  VoiceGatewayState,
} from "../domain/voice-chat";
import type { VoiceChatConnectionStatus } from "./memoir-streaming-asr";

export function modeLabel(mode: MemoirGuidanceMode): string {
  return mode === "guided_interview" ? "主题讲述" : "自由讲述";
}

export function statusText(status: VoiceChatConnectionStatus): string {
  const labels: Record<VoiceChatConnectionStatus, string> = {
    idle: "未连接",
    connecting: "连接中",
    ready: "已连接",
    listening: "正在听",
    stopping: "收尾中",
    closed: "已结束",
    error: "异常",
  };
  return labels[status];
}

export function gatewayStateText(state?: VoiceGatewayState): string {
  const labels: Record<VoiceGatewayState, string> = {
    ready: "识别就绪",
    listening: "正在收音",
    finishing: "识别收尾中",
    stopped: "识别已结束",
    failed: "连接异常",
  };
  return state ? labels[state] : "识别未同步";
}

export function errorKindText(error?: VoiceChatErrorInfo): string {
  if (!error) {
    return "";
  }
  const labels: Record<VoiceChatErrorInfo["kind"], string> = {
    connection: "连接错误",
    recorder: "录音错误",
    gateway: "网关错误",
    asr: "识别错误",
    persistence: "保存错误",
    timeout: "超时",
    unknown: "未知错误",
  };
  return labels[error.kind];
}

export function latestTranscriptText(lines: VoiceChatTranscriptLine[]): string {
  const userLines = lines.filter((line) => (line.role || "user") === "user");
  return userLines.length ? userLines[userLines.length - 1].text : "";
}

export function latestAssistantLine(lines: VoiceChatTranscriptLine[]): VoiceChatTranscriptLine | undefined {
  const latest = lines[lines.length - 1];
  return latest?.role === "assistant" ? latest : undefined;
}

export function latestUserMessageId(lines: VoiceChatTranscriptLine[]): string {
  const userLines = lines.filter((line) => (line.role || "user") === "user");
  const latest = userLines[userLines.length - 1];
  return latest?.message_id || latest?.id || "";
}

export function followUpSourceMessageId(lines: VoiceChatTranscriptLine[]): string {
  return latestUserMessageId(lines) || "opening";
}

export function coveredFollowUpSourceMessageId(lines: VoiceChatTranscriptLine[]): string {
  const latest = lines[lines.length - 1];
  return latest?.role === "assistant" ? followUpSourceMessageId(lines) : "";
}

export function ttsAlignmentFromMetadata(metadata?: Record<string, unknown>): TTSAlignmentSegment[] {
  const rawAlignment = metadata?.tts_alignment;
  if (!Array.isArray(rawAlignment)) {
    return [];
  }
  return rawAlignment.flatMap((item) => {
    if (!item || typeof item !== "object") {
      return [];
    }
    const segment = item as Record<string, unknown>;
    const text = String(segment.text || "");
    const startMs = Number(segment.start_ms);
    const endMs = Number(segment.end_ms);
    const textStart = Number(segment.text_start);
    const textEnd = Number(segment.text_end);
    if (!text || !Number.isFinite(startMs) || !Number.isFinite(endMs)
      || !Number.isFinite(textStart) || !Number.isFinite(textEnd)
      || startMs < 0 || endMs <= startMs || textStart < 0 || textEnd <= textStart) {
      return [];
    }
    return [{
      text,
      start_ms: startMs,
      end_ms: endMs,
      text_start: textStart,
      text_end: textEnd,
      confidence: typeof segment.confidence === "number" ? segment.confidence : null,
    }];
  });
}

export function ttsAlignmentFromAssistantLine(line?: VoiceChatTranscriptLine): TTSAlignmentSegment[] {
  return ttsAlignmentFromMetadata(line?.metadata_json);
}

export function latestAssistantMessageId(lines: VoiceChatTranscriptLine[]): string {
  const latest = latestAssistantLine(lines);
  return latest ? latest.message_id || latest.id : "";
}

export function visibleAssistantLine(
  lines: VoiceChatTranscriptLine[],
  dismissedMessageId: string,
): VoiceChatTranscriptLine | undefined {
  const latest = latestAssistantLine(lines);
  if (!latest) {
    return undefined;
  }
  const latestMessageId = latest.message_id || latest.id;
  return latestMessageId === dismissedMessageId ? undefined : latest;
}

export function canRequestFollowUp(
  lines: VoiceChatTranscriptLine[],
  mode: MemoirGuidanceMode,
  lastFollowUpSourceMessageId: string,
  followUpLoading: boolean,
): boolean {
  if (mode !== "guided_interview" || followUpLoading) {
    return false;
  }
  return followUpSourceMessageId(lines) !== lastFollowUpSourceMessageId;
}

export function assistantPanelCopy(lines: VoiceChatTranscriptLine[]): {
  assistantPromptLabel: string;
  assistantLoadingText: string;
  assistantEmptyText: string;
} {
  const hasUserMessage = Boolean(latestUserMessageId(lines));
  return hasUserMessage
    ? {
        assistantPromptLabel: "AI 追问",
        assistantLoadingText: "正在根据刚才的内容生成下一句追问",
        assistantEmptyText: "可以让 AI 根据刚才的内容继续追问。",
      }
    : {
        assistantPromptLabel: "AI 开场",
        assistantLoadingText: "正在准备第一句话",
        assistantEmptyText: "AI 会先说一句，帮你开始讲。",
      };
}

export function countTextChars(text: string): number {
  return Array.from(text.replace(/\s/g, "")).length;
}

export function totalTranscriptChars(lines: VoiceChatTranscriptLine[]): number {
  return lines
    .filter((line) => (line.role || "user") === "user")
    .reduce((total, line) => total + countTextChars(line.text), 0);
}

export function formatDuration(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`;
}

export function upsertTranscriptLine(
  lines: VoiceChatTranscriptLine[],
  nextLine: VoiceChatTranscriptLine,
): VoiceChatTranscriptLine[] {
  const matchIndex = lines.findIndex((line) => Boolean(
    (nextLine.message_id && line.message_id === nextLine.message_id)
    || (nextLine.utterance_id && line.utterance_id === nextLine.utterance_id),
  ));
  if (matchIndex < 0) {
    return [...lines, nextLine];
  }
  return lines.map((line, index) => (index === matchIndex ? nextLine : line));
}
