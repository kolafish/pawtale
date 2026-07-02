import type {
  MemoirGuidanceMode,
  MemoirVoiceAssistantIntent,
  TTSAlignmentSegment,
  VoiceChatErrorInfo,
  VoiceChatTranscriptLine,
  VoiceGatewayState,
} from "../domain/voice-chat";
import type { VoiceChatConnectionStatus } from "./memoir-streaming-asr";

export function modeLabel(mode: MemoirGuidanceMode): string {
  return mode === "guided_interview" ? "小余陪着问" : "慢慢讲";
}

export function statusText(status: VoiceChatConnectionStatus): string {
  const labels: Record<VoiceChatConnectionStatus, string> = {
    idle: "未连接",
    connecting: "连接中",
    ready: "已连接",
    listening: "正在听",
    stopping: "保存中",
    closed: "已保存",
    error: "异常",
  };
  return labels[status];
}

export function gatewayStateText(state?: VoiceGatewayState): string {
  const labels: Record<VoiceGatewayState, string> = {
    ready: "识别就绪",
    listening: "正在收音",
    finishing: "识别保存中",
    stopped: "识别已保存",
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
  return latest?.role === "assistant" && assistantExpectsAnswer(latest) ? followUpSourceMessageId(lines) : "";
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

function stringFromMetadata(metadata: Record<string, unknown> | undefined, key: string): string {
  const value = metadata?.[key];
  return typeof value === "string" ? value : "";
}

export function assistantPrimaryIntent(line?: VoiceChatTranscriptLine): MemoirVoiceAssistantIntent | "" {
  const metadata = line?.metadata_json;
  const primaryIntent = stringFromMetadata(metadata, "primary_intent");
  if (primaryIntent) {
    return primaryIntent as MemoirVoiceAssistantIntent;
  }
  const source = stringFromMetadata(metadata, "source");
  const sourceIntent = source.startsWith("ai.") ? source.slice(3) : "";
  return sourceIntent === "no_answer_prompt"
    ? "repair"
    : sourceIntent as MemoirVoiceAssistantIntent | "";
}

export function assistantIntentLabel(
  intent: MemoirVoiceAssistantIntent | string,
  hasUserMessage: boolean,
): string {
  const labels: Record<string, string> = {
    opening: "小余想听您讲讲",
    follow_up: "小余想听您讲讲",
    acknowledge: "小余帮您接着整理",
    encourage_continue: "小余陪您继续讲",
    reflect_summary: "阶段小结",
    transition: "换个方向",
    repair: "小余再问一句",
  };
  return labels[intent] || (hasUserMessage ? "小余帮您接着整理" : "小余想听您讲讲");
}

export function assistantExpectsAnswer(line?: VoiceChatTranscriptLine): boolean {
  const value = line?.metadata_json?.expects_answer;
  return typeof value === "boolean" ? value : true;
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

export function assistantPanelCopy(
  lines: VoiceChatTranscriptLine[],
  assistantLine = latestAssistantLine(lines),
): {
  assistantPromptLabel: string;
  assistantLoadingText: string;
  assistantEmptyText: string;
} {
  const hasUserMessage = Boolean(latestUserMessageId(lines));
  const intent = assistantPrimaryIntent(assistantLine);
  return {
    assistantPromptLabel: assistantIntentLabel(intent, hasUserMessage),
    assistantLoadingText: hasUserMessage ? "小余正在想一想" : "小余正在准备第一句话",
    assistantEmptyText: hasUserMessage
      ? "可以让小余根据刚才的内容接着问。"
      : "小余会先问一句，帮您开始讲。",
  };
}

export interface AssistantTranscriptStateOptions {
  dismissedFollowUpMessageId?: string;
  guidanceMode: MemoirGuidanceMode;
  lastFollowUpSourceMessageId: string;
  followUpLoading: boolean;
  currentFollowUpTTSStateText?: string;
  assistantPromptText?: string;
  followUpMessageId?: string;
  followUpAudioFileId?: string;
  followUpTTSStateText?: string;
  ttsAlignment?: TTSAlignmentSegment[];
  canRequestFollowUp?: boolean;
  assistantExpectsAnswer?: boolean;
}

export interface AssistantTranscriptStatePatch {
  transcriptLines: VoiceChatTranscriptLine[];
  latestTranscriptText: string;
  assistantPromptText: string;
  assistantPromptLabel: string;
  assistantLoadingText: string;
  assistantEmptyText: string;
  assistantExpectsAnswer: boolean;
  followUpMessageId: string;
  followUpAudioFileId: string;
  followUpTTSStateText: string;
  ttsAlignment: TTSAlignmentSegment[];
  totalTranscriptCharCount: number;
  canRequestFollowUp: boolean;
}

export function assistantTranscriptState(
  lines: VoiceChatTranscriptLine[],
  options: AssistantTranscriptStateOptions,
): AssistantTranscriptStatePatch {
  const assistantLine = visibleAssistantLine(lines, options.dismissedFollowUpMessageId || "");
  const assistantMessageId = assistantLine?.message_id || assistantLine?.id || "";
  const assistantAudioFileId = assistantLine?.audio_file_id || "";
  return {
    transcriptLines: lines,
    latestTranscriptText: latestTranscriptText(lines),
    assistantPromptText: options.assistantPromptText ?? (assistantLine?.text || ""),
    ...assistantPanelCopy(lines, assistantLine),
    assistantExpectsAnswer: options.assistantExpectsAnswer ?? assistantExpectsAnswer(assistantLine),
    followUpMessageId: options.followUpMessageId ?? assistantMessageId,
    followUpAudioFileId: options.followUpAudioFileId ?? assistantAudioFileId,
    followUpTTSStateText: options.followUpTTSStateText
      ?? (assistantAudioFileId ? "语音已生成" : (options.currentFollowUpTTSStateText || "")),
    ttsAlignment: options.ttsAlignment ?? ttsAlignmentFromAssistantLine(assistantLine),
    totalTranscriptCharCount: totalTranscriptChars(lines),
    canRequestFollowUp: options.canRequestFollowUp ?? canRequestFollowUp(
      lines,
      options.guidanceMode,
      options.lastFollowUpSourceMessageId,
      options.followUpLoading,
    ),
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
    || (nextLine.utterance_id && line.utterance_id === nextLine.utterance_id)
    || (nextLine.id && line.id === nextLine.id),
  ));
  if (matchIndex < 0) {
    return [...lines, nextLine];
  }
  return lines.map((line, index) => (index === matchIndex ? nextLine : line));
}
