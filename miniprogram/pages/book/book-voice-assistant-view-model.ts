import type {
  MemoirBookSourceCoverage,
  MemoirBookVoiceCommandParseRequest,
  MemoirBookVoiceCommandParseResponse,
  MemoirBookVoiceCommandResponseKind,
  VoiceChatTranscriptLine,
} from "../../domain/voice-chat";
import { normalizeMemoirAsrText } from "../../services/asr-text-normalization";
import type { BookData } from "./book-view-model";
import {
  bookVoiceCommandActionText,
  canExecuteBookVoiceCommand,
  trimText,
} from "./book-view-model";

export type BookVoiceCommandPreviewPatch = Pick<
  BookData,
  | "bookVoiceCommandText"
  | "bookVoiceCommandIntentLabel"
  | "bookVoiceCommandResponseKind"
  | "bookVoiceCommandPanelTitle"
  | "bookVoiceCommandStateText"
  | "bookVoiceCommandDetailText"
  | "bookVoiceCommandSummary"
  | "bookVoiceCommandRequiresConfirmation"
  | "bookVoiceCommandPreview"
  | "bookVoiceCommandCanExecute"
  | "bookVoiceCommandActionText"
  | "bookVoiceCommandUndoText"
  | "bookVoiceCommandUndoSnapshot"
  | "bookVoiceAssistantStatusText"
  | "error"
>;

export type BookVoiceCommandResetPatch = Omit<
  BookVoiceCommandPreviewPatch,
  "bookVoiceAssistantStatusText" | "error"
>;

export function bookVoiceCommandResetPatch(): BookVoiceCommandResetPatch {
  return {
    bookVoiceCommandText: "",
    bookVoiceCommandIntentLabel: "",
    bookVoiceCommandResponseKind: "",
    bookVoiceCommandPanelTitle: "",
    bookVoiceCommandStateText: "",
    bookVoiceCommandDetailText: "",
    bookVoiceCommandSummary: "",
    bookVoiceCommandRequiresConfirmation: false,
    bookVoiceCommandPreview: null,
    bookVoiceCommandCanExecute: false,
    bookVoiceCommandActionText: "确认执行",
    bookVoiceCommandUndoText: "",
    bookVoiceCommandUndoSnapshot: null,
  };
}

export function bookVoiceListeningStartPatch(
  autoTriggered: boolean,
  keepCurrentPreview: boolean,
): Partial<BookData> {
  return {
    bookVoiceAssistantListening: true,
    bookVoiceAssistantBusy: false,
    bookVoiceAssistantExpanded: true,
    bookVoiceAssistantConversationActive: true,
    bookVoiceAssistantReplyText: "",
    bookVoiceAssistantSpeakingLineId: "",
    bookVoiceLiveText: "",
    bookVoiceAudioLevelPercent: 0,
    bookVoiceCaptureStateText: "正在打开麦克风",
    bookVoiceAssistantStatusText: autoTriggered ? "我继续听，你可以接着说" : "我在听，请说想怎么改",
    ...(keepCurrentPreview ? {} : bookVoiceCommandResetPatch()),
    error: "",
  };
}

export function clearBookVoiceCommandPreviewPatch(stage: BookData["stage"]): Partial<BookData> {
  return {
    ...bookVoiceCommandResetPatch(),
    bookVoiceAssistantStatusText: bookVoiceAssistantStatusText(stage),
    bookVoiceAssistantSpeakingLineId: "",
    bookVoiceLiveText: "",
    error: "",
  };
}

export function bookVoiceAssistantHint(stage: BookData["stage"], chapterCount: number): string {
  if (stage === "empty") {
    return "生成章节后，可以听目录或说修改";
  }
  if (stage === "plan_draft") {
    return chapterCount
      ? "可以说：读一下目录，或把某一章改名"
      : "生成章节后，可以用语音调整目录";
  }
  return "可以说：读本章、润色这一章，或确认本章";
}

export function bookVoiceAssistantStatusText(stage: BookData["stage"]): string {
  if (stage === "empty") {
    return "等待章节";
  }
  return "我可以帮你听和改";
}

interface BookVoiceIdlePatchOptions {
  collapse?: boolean;
  stopListening?: boolean;
  clearBusy?: boolean;
  clearReply?: boolean;
  clearTranscript?: boolean;
}

export function bookVoiceIdlePatch(
  stage: BookData["stage"],
  options: BookVoiceIdlePatchOptions = {},
): Partial<BookData> {
  return {
    ...(options.collapse ? { bookVoiceAssistantExpanded: false } : {}),
    ...(options.stopListening ? { bookVoiceAssistantListening: false } : {}),
    ...(options.clearBusy ? { bookVoiceAssistantBusy: false } : {}),
    bookVoiceAssistantConversationActive: false,
    ...(options.clearReply ? { bookVoiceAssistantReplyText: "" } : {}),
    bookVoiceAssistantSpeakingLineId: "",
    bookVoiceLiveText: "",
    ...(options.clearTranscript ? { bookVoiceTranscriptLines: [] } : {}),
    bookVoiceAudioLevelPercent: 0,
    bookVoiceCaptureStateText: "麦克风未开始",
    bookVoiceAssistantStatusText: bookVoiceAssistantStatusText(stage),
  };
}

export function isBookVoiceReadIntent(preview: MemoirBookVoiceCommandParseResponse): boolean {
  return preview.intent === "read_outline" || preview.intent === "read_chapter";
}

export function isBookVoiceConsultationIntent(preview: MemoirBookVoiceCommandParseResponse): boolean {
  return preview.intent === "review_chapter_title"
    || preview.intent === "review_chapter_length"
    || preview.intent === "review_chapter_structure";
}

export function isBookVoiceAnswerIntent(preview: MemoirBookVoiceCommandParseResponse): boolean {
  return preview.intent === "read_uncovered_source"
    || preview.intent === "answer_book_question"
    || preview.intent === "capability_help";
}

export function isBookVoiceClarificationIntent(preview: MemoirBookVoiceCommandParseResponse): boolean {
  return preview.intent === "clarify_previous" || preview.intent === "unknown";
}

export function isBookVoiceCommandConfirmationText(text: string): boolean {
  const normalized = trimText(text).replace(/[\s，。,.!！?？、；;：“”"‘’'（）()]/g, "");
  return /^(确认|确认执行|执行|可以|可以执行|就这样|就这样改|好的|好|没问题|按这个改)$/.test(normalized);
}

export function normalizeBookVoiceAsrText(text: string): string {
  return normalizeMemoirAsrText(trimText(text));
}

export function bookVoiceAssistantReplyText(preview: MemoirBookVoiceCommandParseResponse): string {
  const replyText = trimText(preview.reply_text || "");
  if (replyText) {
    return replyText;
  }
  if (preview.intent === "read_outline") {
    return "好，我现在读目录。读完后我会继续听你说。";
  }
  if (preview.intent === "read_chapter") {
    return "好，我来读这一章。读完后我会继续听你说。";
  }
  if (isBookVoiceAnswerIntent(preview)) {
    return preview.summary || "我可以回答当前目录状态，也可以帮你改章名、调顺序或合并章节。";
  }
  if (preview.intent === "clarify_previous") {
    return preview.summary || "我还没确定你指的是哪一处，请直接说要改哪一章。";
  }
  if (preview.intent === "unknown") {
    return "我还没理解清楚。你可以问现在有几章、第一章是不是太短，或者直接说把第一章改名为什么。";
  }
  if (isBookVoiceConsultationIntent(preview)) {
    return `我的建议是：${preview.summary}`;
  }
  if (preview.requires_confirmation && canExecuteBookVoiceCommand(preview)) {
    return `我理解了，${preview.summary} 如果没问题，说确认执行，我再改。`;
  }
  return `我理解到的是：${preview.summary} 这个操作现在先展示预览，不会直接改动。`;
}

export function bookVoiceConversationContext(
  lines: VoiceChatTranscriptLine[],
): NonNullable<MemoirBookVoiceCommandParseRequest["conversation_context"]> {
  return lines
    .filter((line) => (line.role === "user" || line.role === "assistant") && trimText(line.text))
    .slice(-6)
    .map((line) => ({
      role: line.role as "user" | "assistant",
      text: trimText(line.text).slice(0, 240),
    }));
}

export function bookVoiceCommandBookContext(
  coverage: MemoirBookSourceCoverage | null,
): MemoirBookVoiceCommandParseRequest["book_context"] {
  if (!coverage) {
    return undefined;
  }
  return {
    source_coverage: {
      total_source_count: coverage.total_source_count || 0,
      covered_source_count: coverage.covered_source_count || 0,
      uncovered_source_count: coverage.uncovered_source_count || 0,
    },
    uncovered_sources: (coverage.uncovered_sources || []).slice(0, 12).map((source) => ({
      source_index: source.source_index,
      utterance_id: source.utterance_id,
      text: trimText(source.text || "").slice(0, 180),
    })),
  };
}

export function bookVoiceCommandParseRequest(
  text: string,
  transcriptLines: VoiceChatTranscriptLine[],
  coverage: MemoirBookSourceCoverage | null,
): MemoirBookVoiceCommandParseRequest {
  const lastLine = transcriptLines[transcriptLines.length - 1];
  const contextLines = lastLine?.role === "user" && trimText(lastLine.text) === trimText(text)
    ? transcriptLines.slice(0, -1)
    : transcriptLines;
  const conversationContext = bookVoiceConversationContext(contextLines);
  const bookContext = bookVoiceCommandBookContext(coverage);
  return {
    text,
    ...(conversationContext.length ? { conversation_context: conversationContext } : {}),
    ...(bookContext ? { book_context: bookContext } : {}),
  };
}

export function bookVoiceCommandResponseKind(
  preview: MemoirBookVoiceCommandParseResponse,
): MemoirBookVoiceCommandResponseKind {
  if (preview.response_kind) {
    return preview.response_kind;
  }
  if (isBookVoiceConsultationIntent(preview)) {
    return "consultation";
  }
  if (isBookVoiceAnswerIntent(preview)) {
    return "answer";
  }
  if (isBookVoiceClarificationIntent(preview)) {
    return "clarification";
  }
  return "action_preview";
}

export function bookVoiceCommandPanelTitle(kind: MemoirBookVoiceCommandResponseKind): string {
  if (kind === "answer") {
    return "回答";
  }
  if (kind === "consultation") {
    return "建议";
  }
  if (kind === "clarification") {
    return "需要补充";
  }
  return "操作预览";
}

export function bookVoiceCommandStateText(
  preview: MemoirBookVoiceCommandParseResponse,
  kind: MemoirBookVoiceCommandResponseKind,
  canExecute: boolean,
): string {
  if (kind === "answer") {
    return "已回答";
  }
  if (kind === "consultation") {
    return "建议";
  }
  if (kind === "clarification") {
    return "需补充";
  }
  if (isBookVoiceReadIntent(preview)) {
    return "即将朗读";
  }
  if (preview.requires_confirmation && !canExecute) {
    return "仅预览";
  }
  return preview.requires_confirmation ? "待确认" : "已识别";
}

export function bookVoiceCommandDetailText(
  preview: MemoirBookVoiceCommandParseResponse,
  kind: MemoirBookVoiceCommandResponseKind,
): string {
  if (kind === "answer" || kind === "consultation" || kind === "clarification") {
    return trimText(preview.reply_text || preview.summary);
  }
  return trimText(preview.summary);
}

export function bookVoiceCommandPreviewStatusText(preview: MemoirBookVoiceCommandParseResponse): string {
  if (isBookVoiceClarificationIntent(preview)) {
    return "需要再说明";
  }
  if (isBookVoiceReadIntent(preview)) {
    return "已理解，马上朗读";
  }
  if (isBookVoiceConsultationIntent(preview)) {
    return "已给建议";
  }
  if (isBookVoiceAnswerIntent(preview)) {
    return "已回答";
  }
  return "已理解，等待确认";
}

export function bookVoiceCommandPreviewPatch(
  preview: MemoirBookVoiceCommandParseResponse,
): BookVoiceCommandPreviewPatch {
  const canExecute = canExecuteBookVoiceCommand(preview);
  const responseKind = bookVoiceCommandResponseKind(preview);
  return {
    bookVoiceCommandText: preview.original_text,
    bookVoiceCommandIntentLabel: preview.intent_label,
    bookVoiceCommandResponseKind: responseKind,
    bookVoiceCommandPanelTitle: bookVoiceCommandPanelTitle(responseKind),
    bookVoiceCommandStateText: bookVoiceCommandStateText(preview, responseKind, canExecute),
    bookVoiceCommandDetailText: bookVoiceCommandDetailText(preview, responseKind),
    bookVoiceCommandSummary: preview.summary,
    bookVoiceCommandRequiresConfirmation: preview.requires_confirmation,
    bookVoiceCommandPreview: preview,
    bookVoiceCommandCanExecute: canExecute,
    bookVoiceCommandActionText: canExecute ? bookVoiceCommandActionText(preview) : "暂不执行",
    bookVoiceCommandUndoText: "",
    bookVoiceCommandUndoSnapshot: null,
    bookVoiceAssistantStatusText: bookVoiceCommandPreviewStatusText(preview),
    error: "",
  };
}

export function executedVoiceCommandStatusText(intent: string): string {
  if (intent === "assign_source") {
    return "已归入章节";
  }
  return "已执行，可撤回";
}

export function executedVoiceCommandUndoText(intent: string): string {
  if (intent === "rename_chapter") {
    return "撤回语音改名";
  }
  if (intent === "move_chapter") {
    return "撤回语音排序";
  }
  return "";
}

export function executedVoiceCommandReplyText(intent: string): string {
  if (intent === "assign_source") {
    return "已归入章节。还可以继续说下一条怎么处理。";
  }
  if (intent === "move_chapter") {
    return "已按你说的调整好了。还可以继续说新的修改。";
  }
  return "已按你说的改好了。还可以继续说新的修改。";
}
