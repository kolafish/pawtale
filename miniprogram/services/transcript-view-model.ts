import type { MemoirVoiceTranscriptItem } from "../domain/memoir-transcript";
import { formatBeijingDateTimeShort } from "./time";

export interface TranscriptTextSegment {
  text: string;
  highlight: boolean;
}

export function formatTranscriptDurationSeconds(value: number): string {
  const seconds = Math.max(0, Math.round(value));
  if (seconds < 60) {
    return `${seconds} 秒`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return remainingSeconds ? `${minutes} 分 ${remainingSeconds} 秒` : `${minutes} 分`;
}

export function transcriptDurationText(item: MemoirVoiceTranscriptItem): string {
  if (item.audio_duration_seconds !== null && item.audio_duration_seconds !== undefined) {
    return formatTranscriptDurationSeconds(item.audio_duration_seconds);
  }
  if (item.start_ms !== null && item.start_ms !== undefined && item.end_ms !== null && item.end_ms !== undefined) {
    return formatTranscriptDurationSeconds(Math.max(0, Math.round((item.end_ms - item.start_ms) / 1000)));
  }
  return "";
}

export function transcriptAudioText(
  item: MemoirVoiceTranscriptItem,
  options: { includeDurationLabel?: boolean } = {},
): string {
  const duration = transcriptDurationText(item);
  if (!duration) {
    return "时长未知";
  }
  return options.includeDurationLabel ? `时长 ${duration}` : duration;
}

export function transcriptCreatedAtText(item: Pick<MemoirVoiceTranscriptItem, "created_at">): string {
  return formatBeijingDateTimeShort(item.created_at) || "讲述记录";
}

export function transcriptTitleText(item: MemoirVoiceTranscriptItem): string {
  const summaryTitle = (item.summary_title || "").trim();
  if (summaryTitle) {
    return summaryTitle;
  }
  const displayText = (item.display_text || item.transcript_edited || item.transcript_raw || "").trim();
  if (!displayText) return "讲述记录";
  return displayText.length > 32 ? `${displayText.slice(0, 32)}…` : displayText;
}

export function plainTranscriptSegments(text: string): TranscriptTextSegment[] {
  return text ? [{ text, highlight: false }] : [];
}

export function transcriptRecoveryText(status: MemoirVoiceTranscriptItem["transcription_status"]) {
  if (status === "empty") {
    return {
      title: "未检测到清晰说话声",
      summary: "原录音已保留，可以试听或重新识别。",
      notice: "未检测到清晰说话声。原录音已保留，可以试听或重新识别。",
      statusText: "未检测到清晰说话声",
      buttonText: "重新识别录音",
    };
  }
  if (status === "unrecognized") {
    return {
      title: "录音未识别出文字",
      summary: "原录音已保留，可以听一听或重新识别。",
      notice: "没有识别出文字。原录音已保留，可以听一听或重新识别。",
      statusText: "未识别出文字",
      buttonText: "重新识别录音",
    };
  }
  return {
    title: "录音已保存，等待生成文字",
    summary: "点击下方按钮，把已保存的录音转成文字。",
    notice: "录音已保存。把它转成文字后，就可以继续整理。",
    statusText: "等待生成文字",
    buttonText: "把录音转成文字",
  };
}
