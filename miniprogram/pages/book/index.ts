import type {
  MemoirBookAudioResponse,
  MemoirBookChapterDraft,
  MemoirBookEditorialCopyTarget,
  MemoirBookOutputExportFormat,
  MemoirBookOutputExportLayout,
  MemoirBookOutput,
  MemoirBookCoverStyle,
  MemoirBookPlan,
  MemoirBookPlanStructureCommandRequest,
  MemoirBookPlanChapterUpdateInput,
  MemoirBookSourceCoverage,
  MemoirBookVoiceCommandOperation,
  MemoirBookVoiceCommandExecuteResponse,
  MemoirBookVoiceCommandParseResponse,
  MemoirAutomationEvent,
  VoiceChatErrorInfo,
  VoiceChatTranscriptLine,
  VoiceGatewayServerEvent,
} from "../../domain/voice-chat";
import { trackAuditAction } from "../../services/audit-tracker";
import type { BookChapterView, BookData, BookVoiceCommandUndoSnapshot } from "./book-view-model";
import { getMemoirIdentity } from "../../services/auth";
import { memoirProjectProfile } from "../../services/memoir-project-profile";

import {
  acknowledgeMemoirAutomationEvent,
  applyMemoirBookPlanStructureCommand,
  assignMemoirBookSources,
  confirmMemoirBookChapterDraft,
  confirmMemoirBookPlan,
  createMemoirBookChapterDraftTTS,
  downloadMemoirBookOutputExport,
  createMemoirBookOutputTTS,
  createMemoirBookPlanOutlineTTS,
  bookVoiceCommandGatewayUrl,
  bookVoiceAssistantTurnStreamUrl,
  enqueueMemoirBookChapterDraftGeneration,
  enqueueMemoirBookOutputEditorialCopyGeneration,
  enqueueMemoirBookOutputGeneration,
  executeMemoirBookVoiceCommand,
  generateMemoirBookChapterTitle,
  generateMemoirBookPlan,
  getMemoirBookChapterDraft,
  getMemoirBookOutput,
  getMemoirBookPlan,
  getMemoirBookSourceCoverage,
  listMemoirAutomationEvents,
  suggestMemoirBookOutputTitles,
  updateMemoirBookOutput,
  updateMemoirBookOutputChapterEditorialCopy,
  updateMemoirBookChapterTitle,
  updateMemoirBookPlan,
  updateMemoirBookChapterDraft,
} from "../../services/memoir-voice-api";
import { ApiError } from "../../services/request";
import { sourceAssignmentRequest } from "../../services/memoir-book-source-actions";
import {
  classifyVoiceClientConnectionError,
  type MemoirCaptureStats,
  type VoiceChatConnectionStatus,
} from "../../services/memoir-streaming-asr";
import { readBookTTSSettings } from "../../services/tts-settings";
import { AudioPlaybackController } from "../../services/audio-playback";
import {
  VoiceAssistantTurnRuntime,
} from "../../services/voice-assistant-turn-runtime";
import {
  VoiceAssistantTurnStreamClient,
  type VoiceAssistantTurnCommandPreviewEvent,
  type VoiceAssistantTurnCompletedEvent,
} from "../../services/voice-assistant-turn-stream";
import {
  type StreamingSpeechPlaybackFrameEvent,
} from "../../services/streaming-speech-playback";
import { StreamingVoiceController } from "../../services/streaming-voice-controller";
import { writeTranscriptNavigationTarget } from "../../services/transcript-navigation";
import {
  readVoiceAuthorizationConsent,
  syncVoiceAuthorizationConsent,
  VOICE_AUTHORIZATION_PAGE_URL,
} from "../../services/voice-authorization";
import { ensureRecorderPrivacyAuthorization } from "../../services/wechat-privacy";
import {
  playBookAudioFile,
  playBookAudioFiles,
  playbackTimeForProgress as playbackTimeForBookTTSProgress,
  resetBookTTSProgress as resetBookTTSPlaybackProgress,
  startBookTTSProgressTimer as startBookTTSPlaybackProgressTimer,
  stopBookPlayback,
  stopBookTTSProgressTimer as stopBookTTSPlaybackProgressTimer,
  updateBookTTSProgress as updateBookTTSPlaybackProgress,
} from "./book-tts-player";
import {
  NEW_CHAPTER_PICKER_VALUE,
  bookOutputData,
  buildChapterViews,
  chapterCollectionData,
  draftActionText,
  draftStatusText,
  draftVersionText,
  guestBookData,
  latestDraftContent,
  planStage,
  responseCount,
  sourceCoverageData,
  sourceChangeNotice,
  stageText,
  toPlanInput,
  toPlanUpdateInput,
  trimText,
  bookChapterSourceSummary,
  bookOutlineNarrationText,
  buildAutomationEventViews,
  BOOK_COVER_STYLE_OPTIONS,
  BOOK_EXPORT_LAYOUT_OPTIONS,
  canExecuteBookVoiceCommand,
} from "./book-view-model";
import {
  bookVoiceAssistantHint,
  bookVoiceAssistantStatusText,
  bookVoiceCommandParseRequest,
  bookVoiceCommandPreviewPatch,
  executedVoiceCommandStatusText,
  executedVoiceCommandUndoText,
  isBookVoiceCommandConfirmationText,
  isBookVoiceReadIntent,
  normalizeBookVoiceAsrText,
} from "./book-voice-assistant-view-model";
export {
  bookChapterSourceSummary,
  bookOutputStaleNotice,
  bookOutputVersionText,
  bookOutlineNarrationText,
  bookVoiceCommandActionText,
  bookProgressText,
  bookSourceCoverageNotice,
  buildAutomationEventViews,
  buildChapterPickerOptions,
  buildChapterViews,
  buildSourceActionPickerOptions,
  buildUncoveredSourceViews,
  canExecuteBookVoiceCommand,
  latestBookContent,
} from "./book-view-model";

interface DatasetEvent {
  currentTarget: {
    dataset: {
      id?: string;
      eventIds?: string;
      key?: string;
      action?: string;
      direction?: string;
      format?: MemoirBookOutputExportFormat;
      layout?: MemoirBookOutputExportLayout;
      target?: MemoirBookEditorialCopyTarget;
      title?: string;
      utteranceId?: string;
    };
  };
}

interface TextInputEvent extends DatasetEvent {
  detail: {
    value: string;
  };
}

interface PickerEvent extends DatasetEvent {
  detail: {
    value: string | number;
  };
}

interface SwitchEvent extends DatasetEvent {
  detail: {
    value: boolean;
  };
}

type PageScrollToOptions = {
  selector: string;
  duration?: number;
  offsetTop?: number;
};
type WxWithPageScroll = WechatMiniprogram.Wx & {
  pageScrollTo?: (options: PageScrollToOptions) => void;
};
type OpenDocumentOptions = {
  filePath: string;
  fileType?: "pdf";
  showMenu?: boolean;
  success?: () => void;
  fail?: (error: WechatMiniprogram.GeneralCallbackResult) => void;
};
type WxWithDocument = WechatMiniprogram.Wx & {
  openDocument?: (options: OpenDocumentOptions) => void;
};

function isLoginError(error: unknown): boolean {
  return error instanceof ApiError && [401, 403].includes(error.statusCode);
}

function latestBookTitle(output: MemoirBookOutput | null): string {
  return trimText(output?.latest_version?.manuscript?.profile?.title || output?.title || "");
}

function latestBookCoverStyle(output: MemoirBookOutput | null): MemoirBookCoverStyle {
  const style = output?.latest_version?.manuscript?.render_settings?.cover_style;
  const option = BOOK_COVER_STYLE_OPTIONS.find((candidate) => candidate.value === style);
  return option ? option.value : "classic_plain";
}

function latestBookMetadata(output: MemoirBookOutput | null): Record<string, string> {
  const manuscript = output?.latest_version?.manuscript || null;
  const frontMatter = manuscript?.front_matter || [];
  const preface = frontMatter.find((section) => section.kind === "preface")?.content || "";
  return {
    subtitle: trimText(manuscript?.profile?.subtitle || ""),
    narrator_name: trimText(manuscript?.profile?.narrator_name || ""),
    prepared_by: trimText(manuscript?.profile?.prepared_by || "家人整理"),
    preface: trimText(preface),
    afterword: trimText(manuscript?.back_matter?.afterword?.content || ""),
    back_cover_quote: trimText(manuscript?.render_settings?.back_cover_quote || ""),
  };
}

function bookMetadataChanged(data: BookData): boolean {
  const stored = latestBookMetadata(data.bookOutput);
  return (
    trimText(data.bookSubtitleInput) !== stored.subtitle
    || trimText(data.bookNarratorNameInput) !== stored.narrator_name
    || trimText(data.bookPreparedByInput || "家人整理") !== stored.prepared_by
    || trimText(data.bookPrefaceInput) !== stored.preface
    || trimText(data.bookAfterwordInput) !== stored.afterword
    || trimText(data.bookBackCoverQuoteInput) !== stored.back_cover_quote
  );
}

function currentBookMetadataPayload(data: BookData, includeEmpty: boolean): Record<string, string> {
  const values: Record<string, string> = {
    subtitle: trimText(data.bookSubtitleInput),
    narrator_name: trimText(data.bookNarratorNameInput),
    prepared_by: trimText(data.bookPreparedByInput) || "家人整理",
    preface: trimText(data.bookPrefaceInput),
    afterword: trimText(data.bookAfterwordInput),
    back_cover_quote: trimText(data.bookBackCoverQuoteInput),
  };
  if (includeEmpty) {
    return values;
  }
  return Object.keys(values).reduce<Record<string, string>>((memo, key) => {
    if (values[key]) {
      memo[key] = values[key];
    }
    return memo;
  }, {});
}

const BOOK_DESIGN_AUTOSAVE_DELAY_MS = 900;
const BOOK_GENERATION_EVENT_POLL_INTERVAL_MS = 3000;
const BOOK_GENERATION_EVENT_POLL_MAX_ATTEMPTS = 120;

function isBookGenerationEvent(event: MemoirAutomationEvent): boolean {
  return (
    event.event_type === "chapter_draft_generated"
    || event.event_type === "book_output_generated"
    || event.event_type === "book_editorial_copy_generated"
  );
}

function pendingBookGenerationEvents(events: MemoirAutomationEvent[]): MemoirAutomationEvent[] {
  return events.filter((event) => isBookGenerationEvent(event) && event.status === "pending");
}

function pendingChapterDraftGenerationIds(events: MemoirAutomationEvent[]): Set<string> {
  return new Set(
    pendingBookGenerationEvents(events)
      .filter((event) => event.event_type === "chapter_draft_generated")
      .map((event) => trimText(event.chapter_id || ""))
      .filter(Boolean),
  );
}

function hasPendingBookOutputGeneration(events: MemoirAutomationEvent[]): boolean {
  return pendingBookGenerationEvents(events).some((event) => event.event_type === "book_output_generated");
}

function mergeAutomationEvent(
  events: MemoirAutomationEvent[],
  event: MemoirAutomationEvent,
): MemoirAutomationEvent[] {
  return [event, ...events.filter((candidate) => candidate.id !== event.id)];
}

function bookDesignHasChanges(data: BookData): boolean {
  return data.bookTitleChanged || data.bookMetadataChanged || data.bookCoverStyleChanged;
}

function bookEditorialCopyBlockedMessage(data: BookData): string {
  if (!data.bookOutput?.latest_version || !data.bookContent) {
    return "请先生成全书";
  }
  if (data.bookViewingHistoricalVersion) {
    return "请切回最新版后再编辑成书文案";
  }
  if (data.hasDraftChanges) {
    return "请先保存当前章节修改，再编辑成书文案";
  }
  return "";
}

function isBookLevelEditorialCopyTarget(target: string): target is MemoirBookEditorialCopyTarget {
  return target === "preface" || target === "afterword" || target === "back_cover_quote";
}

function isChapterEditorialCopyTarget(target: string): target is MemoirBookEditorialCopyTarget {
  return target === "chapter_intros" || target === "pull_quotes";
}

function bookDesignSaveStatusText(data: BookData): string {
  if (!data.bookOutput?.latest_version) {
    return "生成时应用";
  }
  if (data.bookViewingHistoricalVersion) {
    return "切回最新版后自动保存";
  }
  if (data.bookTitleSaving) {
    return "保存中";
  }
  return bookDesignHasChanges(data) ? "待自动保存" : "已保存";
}

function defaultBookTitleInput(planTitle: string, projectTitle: string): string {
  return (
    trimText(planTitle.replace("章节规划", "").replace("主题回忆录", "回忆录").replace("主题", ""))
    || trimText(projectTitle)
    || "家庭回忆录"
  );
}

const BOOK_VOICE_AUTO_STOP_SILENCE_MS = 900;
const BOOK_VOICE_MIN_RECORDING_MS = 700;
const BOOK_VOICE_NO_VOICE_MS = 3500;
const BOOK_VOICE_AUTO_LISTEN_NO_VOICE_MS = 15000;
const BOOK_VOICE_STREAMING_TTS_PROGRESS_HOLD_SECONDS = 0.75;

Page<BookData, {
  onShow(): Promise<void>;
  loadBookHome(): Promise<void>;
  loadPlanForProject(projectId: string): Promise<void>;
  loadDraftsForPlan(plan: MemoirBookPlan): Promise<Record<string, MemoirBookChapterDraft | null>>;
  loadBookOutput(projectId: string): Promise<MemoirBookOutput | null>;
  loadSourceCoverage(projectId: string): Promise<MemoirBookSourceCoverage | null>;
  loadAutomationEvents(projectId: string): Promise<MemoirAutomationEvent[]>;
  refreshAutomationEventsAfterGeneration(): Promise<void>;
  scheduleBookGenerationEventPoll(): void;
  clearBookGenerationEventPoll(): void;
  syncBookGenerationBusyState(events: MemoirAutomationEvent[]): void;
  applyPlan(plan: MemoirBookPlan | null, draftsByChapterId?: Record<string, MemoirBookChapterDraft | null>): void;
  applyBookOutput(output: MemoirBookOutput | null): void;
  applySourceCoverage(coverage: MemoirBookSourceCoverage | null): void;
  applyAutomationEvents(events: MemoirAutomationEvent[]): void;
  toggleAutomationEventDetails(event: DatasetEvent): void;
  acknowledgeAutomationEvent(event: DatasetEvent): Promise<void>;
  applyUpdatedPlan(plan: MemoirBookPlan, toastTitle: string): Promise<void>;
  generatePlan(): Promise<void>;
  confirmPlan(): Promise<void>;
  changePlanTitle(event: TextInputEvent): void;
  changeChapterTitle(event: TextInputEvent): void;
  saveChapterTitle(event: DatasetEvent): Promise<void>;
  generateChapterTitle(event: DatasetEvent): Promise<void>;
  generateChapterTitleById(chapterId: string, toastTitle?: string): Promise<boolean>;
  removeChapter(event: DatasetEvent): Promise<void>;
  moveChapter(event: DatasetEvent): Promise<void>;
  mergeChapterInto(event: PickerEvent): Promise<void>;
  toggleSplitSource(event: DatasetEvent): void;
  splitSelectedChapterSources(event: DatasetEvent): Promise<void>;
  toggleChapterSources(event: DatasetEvent): void;
  generateChapterDraft(event: DatasetEvent): Promise<void>;
  generateChapterDraftById(chapterId: string): Promise<boolean>;
  changeDraftContent(event: TextInputEvent): void;
  saveChapterDraft(event: DatasetEvent): Promise<void>;
  confirmChapterDraft(event: DatasetEvent): Promise<void>;
  assignUncoveredSource(event: PickerEvent): Promise<void>;
  addUncoveredSourceAsChapter(event: DatasetEvent): Promise<void>;
  addUncoveredSourceAsChapterById(utteranceId: string): Promise<void>;
  deleteConfirmedChapter(event: DatasetEvent): Promise<void>;
  applyPlanStructureCommand(
    command: MemoirBookPlanStructureCommandRequest,
    toastTitle: string
  ): Promise<MemoirBookPlan | null>;
  updatePlanStructure(chapters: MemoirBookPlanChapterUpdateInput[], toastTitle: string): Promise<MemoirBookPlan | null>;
  regenerateMergedChapter(chapterId: string): Promise<void>;
  changeBookTitle(event: TextInputEvent): void;
  changeBookMetadataField(event: TextInputEvent): void;
  scheduleBookDesignAutosave(): void;
  clearBookDesignAutosaveTimer(): void;
  flushBookDesignAutosave(): void;
  autosaveBookDesignSettings(): Promise<void>;
  saveBookDesignSettings(): Promise<void>;
  suggestBookTitles(): Promise<void>;
  chooseBookTitleSuggestion(event: DatasetEvent): void;
  changeBookCoverStyle(event: PickerEvent): void;
  ensureBookDesignSavedForEditorialCopy(): Promise<boolean>;
  regenerateBookEditorialCopy(event: DatasetEvent): Promise<void>;
  regenerateBookChapterEditorialCopy(event: DatasetEvent): Promise<void>;
  openBookChapterEditorialEditor(event: DatasetEvent): void;
  closeBookChapterEditorialEditor(): void;
  changeBookChapterEditorialField(event: TextInputEvent): void;
  saveBookChapterEditorialCopy(): Promise<void>;
  generateBook(): Promise<void>;
  changeBookOutputVersion(event: PickerEvent): void;
  changeBookExportLayout(event: PickerEvent): void;
  toggleBookExportSourceAppendix(event: SwitchEvent): void;
  toggleBookPreviewChapters(): void;
  downloadBookOutputExport(event: DatasetEvent): Promise<void>;
  editBookOutputChapter(event: DatasetEvent): void;
  playChapterAudio(event: DatasetEvent): Promise<void>;
  playChapterAudioById(chapterId: string, options?: { autoListenAfterPlayback?: boolean }): Promise<void>;
  playBookAudio(): Promise<void>;
  playBookOutlineAudio(options?: { autoListenAfterPlayback?: boolean }): Promise<void>;
  startBookVoiceCommand(options?: { autoTriggered?: boolean }): Promise<void>;
  connectBookVoiceCommandGateway(): Promise<boolean>;
  ensureBookVoiceAuthorization(autoTriggered?: boolean): Promise<boolean>;
  appendBookVoiceTranscriptLine(line: VoiceChatTranscriptLine): void;
  handleBookVoiceStatus(status: VoiceChatConnectionStatus): void;
  handleBookVoiceCaptureStats(stats: MemoirCaptureStats): void;
  handleBookVoiceGatewayEvent(event: VoiceGatewayServerEvent): void;
  handleBookVoicePartial(text: string): void;
  handleBookVoiceFinal(event: VoiceGatewayServerEvent): Promise<void>;
  handleBookVoiceError(message: string, error?: VoiceChatErrorInfo): void;
  applyBookVoiceCommandPreview(preview: MemoirBookVoiceCommandParseResponse): void;
  handleBookVoiceCommandTranscript(text: string): Promise<void>;
  runBookVoiceAssistantTurn(text: string): Promise<boolean>;
  bookVoiceAssistantTurnPayload(text: string): Record<string, unknown>;
  handleBookVoiceCommandPreview(event: VoiceAssistantTurnCommandPreviewEvent): void;
  applyBookVoiceAssistantTurnText(text: string): void;
  applyCompletedBookVoiceAssistantTurn(
    event: VoiceAssistantTurnCompletedEvent,
    undoSnapshot: BookVoiceCommandUndoSnapshot | null,
    options?: { autoListenAfterTurn?: boolean },
  ): Promise<void>;
  executeBookVoiceCommand(options?: { confirmedByVoice?: boolean; autoListenAfterExecution?: boolean }): Promise<void>;
  applyBookVoiceCommandExecution(
    response: MemoirBookVoiceCommandExecuteResponse,
    undoSnapshot: BookVoiceCommandUndoSnapshot | null,
    options?: { autoListenAfterExecution?: boolean }
  ): Promise<void>;
  undoBookVoiceCommand(): Promise<void>;
  clearBookVoiceCommandPreview(): void;
  stopBookVoiceConversation(): void;
  clearBookVoiceAssistantAutoListenTimer(): void;
  canStartBookVoiceAssistantAutoListen(): boolean;
  scheduleBookVoiceAssistantAutoListen(statusText?: string): void;
  canInterruptBookVoiceAssistantReply(): boolean;
  interruptBookVoiceAssistantReply(): void;
  finishBookVoiceAssistantAudioFrames(token: number, activeText: string): Promise<void>;
  markBookVoiceStreamingAudioFrameScheduled(event: StreamingSpeechPlaybackFrameEvent): void;
  updateBookVoiceStreamingTTSProgress(): void;
  startBookVoiceStreamingTTSProgressTimer(token: number): void;
  bookVoiceCommandUndoSnapshot(): BookVoiceCommandUndoSnapshot | null;
  confirmBookVoiceCommandExecution(content: string, confirmText?: string): Promise<boolean>;
  requestBookAudio(target: string): Promise<MemoirBookAudioResponse>;
  playAudioFiles(
    response: MemoirBookAudioResponse,
    target: string,
    token: number,
    activeText: string,
  ): Promise<void>;
  playAudioFile(
    audioFileId: string,
    token: number,
    elapsedBeforeSeconds: number,
    fallbackDurationSeconds: number,
  ): Promise<number>;
  updateBookTTSProgress(currentTimeSeconds: number, durationSeconds?: number): void;
  startBookTTSProgressTimer(
    token: number,
    audio: WechatMiniprogram.InnerAudioContext,
    elapsedBeforeSeconds: number,
    durationSeconds: number,
  ): void;
  stopBookTTSProgressTimer(): void;
  resetBookTTSProgress(): void;
  playbackTimeForProgress(audio: WechatMiniprogram.InnerAudioContext): number;
  stopPlayback(): void;
  onHide(): void;
  onUnload(): void;
  openTranscripts(event?: DatasetEvent): void;
  openHome(): void;
  openLogin(): void;
  createProject(): void;
  patchChapter(chapterId: string, patch: Partial<BookChapterView>): void;
  patchDraft(chapterId: string, draft: MemoirBookChapterDraft): void;
  clearBookOutput(): void;
  audioPlayback: AudioPlaybackController;
  bookVoiceStreaming: StreamingVoiceController;
  bookVoiceAssistantTurnRuntime: VoiceAssistantTurnRuntime | null;
  bookVoiceStreamingAudioStartSeconds: number;
  bookVoiceStreamingAudioEndSeconds: number;
  playToken: number;
  ttsProgressTimer: number | null;
  ttsProgressStartedAtMs: number;
  bookVoiceAutoListenTimer: number | null;
  bookDesignAutosaveTimer: number | null;
  bookGenerationPollTimer: number | null;
  bookGenerationPollAttempts: number;
  automationEvents: MemoirAutomationEvent[];
  backgroundDraftBusyChapterIds: Record<string, boolean>;
  bookOutputGenerationSubmitting: boolean;
}>({
  audioPlayback: new AudioPlaybackController(),
  bookVoiceStreaming: new StreamingVoiceController(),
  bookVoiceAssistantTurnRuntime: null,
  bookVoiceStreamingAudioStartSeconds: -1,
  bookVoiceStreamingAudioEndSeconds: 0,
  playToken: 0,
  ttsProgressTimer: null,
  ttsProgressStartedAtMs: 0,
  bookVoiceAutoListenTimer: null,
  bookDesignAutosaveTimer: null,
  bookGenerationPollTimer: null,
  bookGenerationPollAttempts: 0,
  automationEvents: [],
  backgroundDraftBusyChapterIds: {},
  bookOutputGenerationSubmitting: false,

  data: {
    loading: false,
    needsLogin: false,
    generatingPlan: false,
    confirmingPlan: false,
    updatingPlanStructure: false,
    generatingBook: false,
    error: "",
    projectId: "",
    projectTitle: "我的回忆录",
    projectNarratorName: "",
    plan: null,
    planTitle: "",
    stage: "empty",
    stageText: "等待生成章节",
    chapters: [],
    splitSourceSelections: {},
    confirmedDraftCount: 0,
    totalChapterCount: 0,
    allChaptersConfirmed: false,
    hasDraftChanges: false,
    canGenerateBook: false,
    progressText: "还没有章节",
    sourceCoverage: null,
    sourceCoverageNotice: "",
    uncoveredSourceCount: 0,
    uncoveredSourceDisplayText: "",
    uncoveredSources: [],
    chapterPickerOptions: [],
    assigningSourceId: "",
    titleBusyTarget: "",
    bookOutput: null,
    bookContent: "",
    bookCharCount: 0,
    bookPreviewTitle: "",
    bookPreviewTocItems: [],
    bookPreviewChapters: [],
    bookPreviewChaptersExpanded: false,
    bookManuscriptAvailable: false,
    bookManuscriptTitle: "",
    bookManuscriptSubtitle: "",
    bookManuscriptTemplateLabel: "基础成书版",
    bookManuscriptSettingsText: "",
    bookManuscriptStructureItems: [],
    bookManuscriptFrontMatterItems: [],
    bookManuscriptChapterItems: [],
    bookManuscriptBackMatterItems: [],
    bookVersionText: "尚未生成全书",
    bookVersionOptions: [],
    bookSelectedVersionId: "",
    bookSelectedVersionIndex: 0,
    bookViewingHistoricalVersion: false,
    bookVersionNotice: "",
    bookStaleNotice: "",
    bookActionText: "生成全书",
    bookExportBusyTarget: "",
    bookExportLayout: "standard",
    bookExportLayoutIndex: 0,
    bookExportLayoutLabel: BOOK_EXPORT_LAYOUT_OPTIONS[0].label,
    bookExportLayoutDetailText: BOOK_EXPORT_LAYOUT_OPTIONS[0].detailText,
    bookExportLayoutOptions: BOOK_EXPORT_LAYOUT_OPTIONS,
    bookExportShowSourceAppendix: true,
    bookTitleInput: "",
    bookSubtitleInput: "",
    bookNarratorNameInput: "",
    bookPreparedByInput: "家人整理",
    bookPrefaceInput: "",
    bookAfterwordInput: "",
    bookBackCoverQuoteInput: "",
    bookTitleChanged: false,
    bookMetadataChanged: false,
    bookTitleSaving: false,
    bookDesignSaveStatusText: "生成时应用",
    bookTitleSuggesting: false,
    bookTitleSuggestions: [],
    bookCoverStyle: "classic_plain",
    bookCoverStyleIndex: 0,
    bookCoverStyleLabel: BOOK_COVER_STYLE_OPTIONS[0].label,
    bookCoverStyleDetailText: BOOK_COVER_STYLE_OPTIONS[0].detailText,
    bookCoverStyleOptions: BOOK_COVER_STYLE_OPTIONS,
    bookCoverStyleChanged: false,
    bookEditorialCopyBusyTarget: "",
    bookChapterEditorialEditorVisible: false,
    bookChapterEditorialChapterId: "",
    bookChapterEditorialChapterTitle: "",
    bookChapterEditorialIntroInput: "",
    bookChapterEditorialPullQuoteInput: "",
    bookChapterEditorialSourceRefId: "",
    bookChapterEditorialSourceText: "",
    ttsBusyTarget: "",
    playingTarget: "",
    bookTTSActiveTarget: "",
    bookTTSActiveText: "",
    bookTTSSpokenText: "",
    bookTTSCurrentText: "",
    bookTTSRemainingText: "",
    bookTTSProgressPercent: 0,
    bookTTSDurationSeconds: 0,
    bookTTSAlignment: [],
    bookVoiceAssistantExpanded: false,
    bookVoiceAssistantHint: "生成章节后，可以听目录或说修改",
    bookVoiceAssistantStatusText: "等待章节",
    bookVoiceAssistantListening: false,
    bookVoiceAssistantBusy: false,
    bookVoiceAssistantConversationActive: false,
    bookVoiceAssistantReplyText: "",
    bookVoiceAssistantSpeakingLineId: "",
    bookVoiceLiveText: "",
    bookVoiceTranscriptLines: [],
    bookVoiceAudioLevelPercent: 0,
    bookVoiceCaptureStateText: "麦克风未开始",
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
    bookVoiceCommandExecuting: false,
    bookVoiceCommandUndoText: "",
    bookVoiceCommandUndoSnapshot: null,
    automationEventItems: [],
    automationEventBusyTarget: "",
    automationEventExpandedTargets: {},
    automationEventLoading: false,
    automationEventError: "",
  },

  async onShow() {
    this.bookGenerationPollAttempts = 0;
    await this.loadBookHome();
  },

  async loadBookHome() {
    if (this.data.loading) {
      return;
    }
    this.setData({ loading: true, error: "" });
    try {
      const identity = await getMemoirIdentity();
      const projectId = identity.active_project_id;
      const projectTitle = identity.active_project?.title || "尚未创建回忆录";
      const projectNarratorName = memoirProjectProfile(identity.active_project).narratorName;
      this.setData({ needsLogin: false, projectId, projectTitle, projectNarratorName });
      if (!projectId) {
        this.applyPlan(null);
        this.applySourceCoverage(null);
        this.applyBookOutput(null);
        this.applyAutomationEvents([]);
        return;
      }
      await this.loadPlanForProject(projectId);
    } catch (error) {
      if (isLoginError(error)) {
        this.setData(guestBookData());
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "成书页面加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  async loadPlanForProject(projectId: string) {
    try {
      const [coverage, automationEvents] = await Promise.all([
        this.loadSourceCoverage(projectId),
        this.loadAutomationEvents(projectId),
      ]);
      this.applySourceCoverage(coverage);
      this.applyAutomationEvents(automationEvents);
      const plan = await getMemoirBookPlan(projectId);
      let draftsByChapterId: Record<string, MemoirBookChapterDraft | null> = {};
      let output: MemoirBookOutput | null = null;
      if (plan.status === "confirmed") {
        [draftsByChapterId, output] = await Promise.all([
          this.loadDraftsForPlan(plan),
          this.loadBookOutput(projectId),
        ]);
      }
      this.applySourceCoverage(coverage);
      this.applyPlan(plan, draftsByChapterId);
      this.applyBookOutput(output);
      this.applyAutomationEvents(automationEvents);
    } catch (error) {
      if (error instanceof ApiError && error.statusCode === 404) {
        this.applyPlan(null);
        this.applyBookOutput(null);
        return;
      }
      throw error;
    }
  },

  async loadAutomationEvents(projectId) {
    this.setData({ automationEventLoading: true, automationEventError: "" });
    try {
      const response = await listMemoirAutomationEvents(projectId, 8);
      return response.items || [];
    } catch (error) {
      this.setData({
        automationEventError: error instanceof Error ? error.message : "自动处理记录加载失败",
      });
      return [];
    } finally {
      this.setData({ automationEventLoading: false });
    }
  },

  async refreshAutomationEventsAfterGeneration() {
    if (!this.data.projectId) {
      return;
    }
    const hadPendingGeneration = pendingBookGenerationEvents(this.automationEvents).length > 0;
    const events = await this.loadAutomationEvents(this.data.projectId);
    if (this.data.automationEventError) {
      if (hadPendingGeneration) {
        this.scheduleBookGenerationEventPoll();
      }
      return;
    }
    const hasPendingGeneration = pendingBookGenerationEvents(events).length > 0;
    this.applyAutomationEvents(events);
    if (hasPendingGeneration) {
      this.scheduleBookGenerationEventPoll();
      return;
    }
    if (hadPendingGeneration) {
      this.bookGenerationPollAttempts = 0;
      await this.loadPlanForProject(this.data.projectId);
    }
  },

  scheduleBookGenerationEventPoll() {
    if (this.bookGenerationPollTimer !== null || !this.data.projectId) {
      return;
    }
    if (!pendingBookGenerationEvents(this.automationEvents).length) {
      this.bookGenerationPollAttempts = 0;
      return;
    }
    if (this.bookGenerationPollAttempts >= BOOK_GENERATION_EVENT_POLL_MAX_ATTEMPTS) {
      return;
    }
    this.bookGenerationPollAttempts += 1;
    const timer = setTimeout(() => {
      this.bookGenerationPollTimer = null;
      void this.refreshAutomationEventsAfterGeneration();
    }, BOOK_GENERATION_EVENT_POLL_INTERVAL_MS);
    (timer as unknown as { unref?: () => void }).unref?.();
    this.bookGenerationPollTimer = timer as unknown as number;
  },

  clearBookGenerationEventPoll() {
    if (this.bookGenerationPollTimer !== null) {
      clearTimeout(this.bookGenerationPollTimer);
      this.bookGenerationPollTimer = null;
    }
  },

  async loadDraftsForPlan(plan) {
    const entries = await Promise.all(
      plan.chapters.map(async (chapter) => {
        try {
          const draft = await getMemoirBookChapterDraft(plan.project_id, chapter.id);
          return [chapter.id, draft] as const;
        } catch (error) {
          if (error instanceof ApiError && error.statusCode === 404) {
            return [chapter.id, null] as const;
          }
          throw error;
        }
      }),
    );
    return entries.reduce<Record<string, MemoirBookChapterDraft | null>>((memo, [chapterId, draft]) => {
      memo[chapterId] = draft;
      return memo;
    }, {});
  },

  async loadBookOutput(projectId) {
    try {
      return await getMemoirBookOutput(projectId);
    } catch (error) {
      if (error instanceof ApiError && error.statusCode === 404) {
        return null;
      }
      throw error;
    }
  },

  async loadSourceCoverage(projectId) {
    try {
      return await getMemoirBookSourceCoverage(projectId);
    } catch (error) {
      if (error instanceof ApiError && error.statusCode === 404) {
        return null;
      }
      throw error;
    }
  },

  applyPlan(plan, draftsByChapterId = {}) {
    const chapters = plan
      ? buildChapterViews(plan, draftsByChapterId, this.data.sourceCoverage, this.data.splitSourceSelections)
      : [];
    const stage = planStage(plan);
    const shouldResetVoiceStatus = !this.data.bookVoiceAssistantConversationActive || !plan;
    this.setData({
      plan,
      planTitle: plan?.title || `${this.data.projectTitle || "我的回忆录"}章节规划`,
      stage,
      stageText: stageText(stage),
      bookVoiceAssistantHint: bookVoiceAssistantHint(stage, chapters.length),
      ...(shouldResetVoiceStatus ? {
        bookVoiceAssistantExpanded: false,
        bookVoiceAssistantStatusText: bookVoiceAssistantStatusText(stage),
        bookVoiceAssistantConversationActive: false,
        bookVoiceAssistantReplyText: "",
        bookVoiceAssistantSpeakingLineId: "",
        bookVoiceLiveText: "",
        bookVoiceTranscriptLines: [],
        bookVoiceAudioLevelPercent: 0,
        bookVoiceCaptureStateText: "麦克风未开始",
      } : {}),
      ...chapterCollectionData(chapters, stage, this.data.sourceCoverage, this.data.splitSourceSelections),
      ...sourceCoverageData(plan, this.data.sourceCoverage),
      ...(stage === "drafting" ? {} : bookOutputData(null)),
    });
  },

  applyBookOutput(output) {
    const outputData = bookOutputData(output);
    if (!output && !outputData.bookTitleInput) {
      outputData.bookTitleInput = defaultBookTitleInput(this.data.planTitle, this.data.projectTitle);
    }
    if (!outputData.bookNarratorNameInput && this.data.projectNarratorName) {
      outputData.bookNarratorNameInput = this.data.projectNarratorName;
      outputData.bookMetadataChanged = Boolean(output?.latest_version);
    }
    this.setData({
      ...outputData,
      bookPreviewChaptersExpanded: false,
    });
    if (outputData.bookMetadataChanged && output?.latest_version && this.data.projectId) {
      this.scheduleBookDesignAutosave();
    }
  },

  changeBookTitle(event) {
    const value = event.detail.value;
    const storedTitle = latestBookTitle(this.data.bookOutput)
      || this.data.bookManuscriptTitle
      || this.data.bookPreviewTitle;
    this.setData({
      bookTitleInput: value,
      bookTitleChanged: trimText(value) !== trimText(storedTitle),
    });
    this.scheduleBookDesignAutosave();
  },

  changeBookMetadataField(event) {
    const key = String(event.currentTarget.dataset.key || "");
    const fieldByKey: Record<string, keyof BookData> = {
      subtitle: "bookSubtitleInput",
      narrator_name: "bookNarratorNameInput",
      prepared_by: "bookPreparedByInput",
      preface: "bookPrefaceInput",
      afterword: "bookAfterwordInput",
      back_cover_quote: "bookBackCoverQuoteInput",
    };
    const field = fieldByKey[key];
    if (!field) {
      return;
    }
    const value = event.detail.value;
    const nextData = { ...this.data, [field]: value } as BookData;
    this.setData({
      [field]: value,
      bookMetadataChanged: bookMetadataChanged(nextData),
      error: "",
    });
    this.scheduleBookDesignAutosave();
  },

  async suggestBookTitles() {
    if (!this.data.projectId || this.data.bookTitleSuggesting || this.data.generatingBook) {
      return;
    }
    if (!this.data.allChaptersConfirmed) {
      this.setData({ error: "请先确认所有章节正文" });
      return;
    }
    if (this.data.hasDraftChanges) {
      this.setData({ error: "请先保存当前章节修改" });
      return;
    }
    this.setData({ bookTitleSuggesting: true, error: "" });
    try {
      const response = await suggestMemoirBookOutputTitles(this.data.projectId, { count: 6 });
      const suggestions = (response.suggestions || []).map((item) => trimText(item)).filter(Boolean);
      this.setData({ bookTitleSuggestions: suggestions });
      if (!suggestions.length) {
        this.setData({ error: "暂时没有生成合适的书名" });
      }
    } catch (error) {
      if (isLoginError(error)) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "AI 起名失败" });
    } finally {
      this.setData({ bookTitleSuggesting: false });
    }
  },

  chooseBookTitleSuggestion(event) {
    const title = trimText(event.currentTarget.dataset.title || "");
    if (!title) {
      return;
    }
    const storedTitle = latestBookTitle(this.data.bookOutput)
      || this.data.bookManuscriptTitle
      || this.data.bookPreviewTitle;
    this.setData({
      bookTitleInput: title,
      bookTitleChanged: title !== trimText(storedTitle),
      error: "",
    });
    this.scheduleBookDesignAutosave();
  },

  changeBookCoverStyle(event) {
    const index = Number(event.detail.value);
    const option = this.data.bookCoverStyleOptions[index];
    if (!option) {
      return;
    }
    this.setData({
      bookCoverStyle: option.value,
      bookCoverStyleIndex: index,
      bookCoverStyleLabel: option.label,
      bookCoverStyleDetailText: option.detailText,
      bookCoverStyleChanged: option.value !== latestBookCoverStyle(this.data.bookOutput),
      error: "",
    });
    this.scheduleBookDesignAutosave();
  },

  scheduleBookDesignAutosave() {
    this.clearBookDesignAutosaveTimer();
    if (!this.data.bookOutput?.latest_version || this.data.bookViewingHistoricalVersion) {
      this.setData({ bookDesignSaveStatusText: bookDesignSaveStatusText(this.data) });
      return;
    }
    if (!bookDesignHasChanges(this.data)) {
      this.setData({ bookDesignSaveStatusText: "已保存" });
      return;
    }
    this.setData({ bookDesignSaveStatusText: "待自动保存" });
    if (this.data.bookTitleSaving || this.data.generatingBook || this.data.updatingPlanStructure) {
      return;
    }
    this.bookDesignAutosaveTimer = setTimeout(() => {
      this.bookDesignAutosaveTimer = null;
      void this.autosaveBookDesignSettings();
    }, BOOK_DESIGN_AUTOSAVE_DELAY_MS);
  },

  clearBookDesignAutosaveTimer() {
    if (this.bookDesignAutosaveTimer !== null) {
      clearTimeout(this.bookDesignAutosaveTimer);
      this.bookDesignAutosaveTimer = null;
    }
  },

  flushBookDesignAutosave() {
    const hadPendingAutosave = this.bookDesignAutosaveTimer !== null;
    this.clearBookDesignAutosaveTimer();
    if (hadPendingAutosave && bookDesignHasChanges(this.data)) {
      void this.autosaveBookDesignSettings();
    }
  },

  async autosaveBookDesignSettings() {
    this.clearBookDesignAutosaveTimer();
    if (!this.data.projectId || this.data.bookTitleSaving || this.data.generatingBook) {
      return;
    }
    if (!this.data.bookOutput?.latest_version) {
      this.setData({ bookDesignSaveStatusText: "生成时应用" });
      return;
    }
    if (this.data.bookViewingHistoricalVersion) {
      this.setData({
        error: "请切回最新版后再自动保存成书信息",
        bookDesignSaveStatusText: "切回最新版后自动保存",
      });
      return;
    }
    if (!bookDesignHasChanges(this.data)) {
      this.setData({ bookDesignSaveStatusText: "已保存" });
      return;
    }
    const title = trimText(this.data.bookTitleInput);
    if (!title) {
      this.setData({ error: "书名不能为空", bookDesignSaveStatusText: "保存失败" });
      return;
    }
    const coverStyle = this.data.bookCoverStyle;
    const metadata = currentBookMetadataPayload(this.data, true);
    this.stopPlayback();
    this.setData({ bookTitleSaving: true, bookDesignSaveStatusText: "保存中", error: "" });
    try {
      const output = await updateMemoirBookOutput(this.data.projectId, {
        title,
        subtitle: metadata.subtitle,
        narrator_name: metadata.narrator_name,
        prepared_by: metadata.prepared_by,
        preface: metadata.preface,
        afterword: metadata.afterword,
        back_cover_quote: metadata.back_cover_quote,
        cover_style: coverStyle,
      });
      this.applyBookOutput(output);
      const option = this.data.bookCoverStyleOptions.find((candidate) => candidate.value === coverStyle)
        || this.data.bookCoverStyleOptions[0];
      this.setData({
        bookTitleInput: title,
        bookSubtitleInput: metadata.subtitle,
        bookNarratorNameInput: metadata.narrator_name,
        bookPreparedByInput: metadata.prepared_by,
        bookPrefaceInput: metadata.preface,
        bookAfterwordInput: metadata.afterword,
        bookBackCoverQuoteInput: metadata.back_cover_quote,
        bookTitleChanged: false,
        bookMetadataChanged: false,
        bookCoverStyle: option.value,
        bookCoverStyleIndex: Math.max(
          0,
          this.data.bookCoverStyleOptions.findIndex((candidate) => candidate.value === option.value),
        ),
        bookCoverStyleLabel: option.label,
        bookCoverStyleDetailText: option.detailText,
        bookCoverStyleChanged: false,
        bookDesignSaveStatusText: "已自动保存",
      });
    } catch (error) {
      if (isLoginError(error)) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "保存成书信息失败",
        bookDesignSaveStatusText: "保存失败",
      });
    } finally {
      this.setData({ bookTitleSaving: false });
    }
  },

  async saveBookDesignSettings() {
    await this.autosaveBookDesignSettings();
  },

  async ensureBookDesignSavedForEditorialCopy() {
    this.clearBookDesignAutosaveTimer();
    if (!bookDesignHasChanges(this.data)) {
      return true;
    }
    await this.autosaveBookDesignSettings();
    return !bookDesignHasChanges(this.data);
  },

  async regenerateBookEditorialCopy(event) {
    const target = String(event.currentTarget.dataset.target || "");
    if (!isBookLevelEditorialCopyTarget(target)) {
      return;
    }
    if (!this.data.projectId || this.data.bookEditorialCopyBusyTarget === target || this.data.bookTitleSaving) {
      return;
    }
    const blockedMessage = bookEditorialCopyBlockedMessage(this.data);
    if (blockedMessage) {
      this.setData({ error: blockedMessage });
      return;
    }
    if (!(await this.ensureBookDesignSavedForEditorialCopy())) {
      return;
    }
    this.stopPlayback();
    this.setData({ bookEditorialCopyBusyTarget: target, error: "" });
    try {
      const event = await enqueueMemoirBookOutputEditorialCopyGeneration(this.data.projectId, { targets: [target] });
      this.applyAutomationEvents(mergeAutomationEvent(this.automationEvents, event));
      wx.showToast({ title: "已后台生成", icon: "success" });
    } catch (error) {
      if (isLoginError(error)) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "成书文案提交失败" });
    } finally {
      if (this.data.bookEditorialCopyBusyTarget === target) {
        this.setData({ bookEditorialCopyBusyTarget: "" });
      }
    }
  },

  async regenerateBookChapterEditorialCopy(event) {
    const chapterId = event.currentTarget.dataset.id || "";
    const target = String(event.currentTarget.dataset.target || "");
    if (!chapterId || !isChapterEditorialCopyTarget(target)) {
      return;
    }
    const busyTarget = `${target}:${chapterId}`;
    if (!this.data.projectId || this.data.bookEditorialCopyBusyTarget === busyTarget || this.data.bookTitleSaving) {
      return;
    }
    const blockedMessage = bookEditorialCopyBlockedMessage(this.data);
    if (blockedMessage) {
      this.setData({ error: blockedMessage });
      return;
    }
    if (!(await this.ensureBookDesignSavedForEditorialCopy())) {
      return;
    }
    this.stopPlayback();
    this.setData({ bookEditorialCopyBusyTarget: busyTarget, error: "" });
    try {
      const event = await enqueueMemoirBookOutputEditorialCopyGeneration(this.data.projectId, {
        targets: [target],
        chapter_ids: [chapterId],
      });
      this.applyAutomationEvents(mergeAutomationEvent(this.automationEvents, event));
      wx.showToast({ title: "已后台生成", icon: "success" });
    } catch (error) {
      if (isLoginError(error)) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "章节文案提交失败" });
    } finally {
      if (this.data.bookEditorialCopyBusyTarget === busyTarget) {
        this.setData({ bookEditorialCopyBusyTarget: "" });
      }
    }
  },

  openBookChapterEditorialEditor(event) {
    const chapterId = event.currentTarget.dataset.id || "";
    if (!chapterId) {
      return;
    }
    const blockedMessage = bookEditorialCopyBlockedMessage(this.data);
    if (blockedMessage) {
      this.setData({ error: blockedMessage });
      return;
    }
    const chapter = this.data.bookManuscriptChapterItems.find((candidate) => candidate.chapterId === chapterId);
    if (!chapter) {
      this.setData({ error: "没有找到可编辑的章节文案" });
      return;
    }
    this.stopPlayback();
    this.setData({
      bookChapterEditorialEditorVisible: true,
      bookChapterEditorialChapterId: chapter.chapterId,
      bookChapterEditorialChapterTitle: chapter.title,
      bookChapterEditorialIntroInput: chapter.intro,
      bookChapterEditorialPullQuoteInput: chapter.quoteText,
      bookChapterEditorialSourceRefId: chapter.quoteSourceRefId,
      bookChapterEditorialSourceText: chapter.quoteSourceText,
      error: "",
    });
  },

  closeBookChapterEditorialEditor() {
    this.setData({
      bookChapterEditorialEditorVisible: false,
      bookChapterEditorialChapterId: "",
      bookChapterEditorialChapterTitle: "",
      bookChapterEditorialIntroInput: "",
      bookChapterEditorialPullQuoteInput: "",
      bookChapterEditorialSourceRefId: "",
      bookChapterEditorialSourceText: "",
    });
  },

  changeBookChapterEditorialField(event) {
    const key = String(event.currentTarget.dataset.key || "");
    if (key === "intro") {
      this.setData({ bookChapterEditorialIntroInput: event.detail.value, error: "" });
      return;
    }
    if (key === "pull_quote") {
      this.setData({ bookChapterEditorialPullQuoteInput: event.detail.value, error: "" });
    }
  },

  async saveBookChapterEditorialCopy() {
    const chapterId = this.data.bookChapterEditorialChapterId;
    if (!chapterId || !this.data.projectId || this.data.bookEditorialCopyBusyTarget || this.data.bookTitleSaving) {
      return;
    }
    const blockedMessage = bookEditorialCopyBlockedMessage(this.data);
    if (blockedMessage) {
      this.setData({ error: blockedMessage });
      return;
    }
    if (!(await this.ensureBookDesignSavedForEditorialCopy())) {
      return;
    }
    const busyTarget = `manual:${chapterId}`;
    this.stopPlayback();
    this.setData({ bookEditorialCopyBusyTarget: busyTarget, error: "" });
    try {
      const output = await updateMemoirBookOutputChapterEditorialCopy(this.data.projectId, chapterId, {
        intro: trimText(this.data.bookChapterEditorialIntroInput),
        pull_quote: trimText(this.data.bookChapterEditorialPullQuoteInput),
        pull_quote_source_ref_id: trimText(this.data.bookChapterEditorialSourceRefId) || null,
      });
      this.applyBookOutput(output);
      this.closeBookChapterEditorialEditor();
      wx.showToast({ title: "已保存", icon: "success" });
    } catch (error) {
      if (isLoginError(error)) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "章节文案保存失败" });
    } finally {
      this.setData({ bookEditorialCopyBusyTarget: "" });
    }
  },

  changeBookOutputVersion(event) {
    const index = Number(event.detail.value);
    const option = this.data.bookVersionOptions[index];
    if (!option || !this.data.bookOutput) {
      return;
    }
    if (this.data.playingTarget === "book") {
      this.stopPlayback();
    }
    this.setData({
      ...bookOutputData(this.data.bookOutput, option.id),
      bookPreviewChaptersExpanded: false,
      bookChapterEditorialEditorVisible: false,
    });
  },

  changeBookExportLayout(event) {
    const index = Number(event.detail.value);
    const option = this.data.bookExportLayoutOptions[index];
    if (!option) {
      return;
    }
    this.setData({
      bookExportLayout: option.value,
      bookExportLayoutIndex: index,
      bookExportLayoutLabel: option.label,
      bookExportLayoutDetailText: option.detailText,
    });
  },

  toggleBookExportSourceAppendix(event) {
    this.setData({ bookExportShowSourceAppendix: Boolean(event.detail.value) });
  },

  toggleBookPreviewChapters() {
    this.setData({ bookPreviewChaptersExpanded: !this.data.bookPreviewChaptersExpanded });
  },

  async downloadBookOutputExport(event) {
    const format: MemoirBookOutputExportFormat = event.currentTarget.dataset.format === "txt" ? "txt" : "pdf";
    const layout: MemoirBookOutputExportLayout = format === "pdf" && event.currentTarget.dataset.layout === "large_print"
      ? "large_print"
      : (format === "pdf" ? this.data.bookExportLayout : "standard");
    const target = `book-export:${format}:${layout}`;
    if (!this.data.projectId || this.data.bookExportBusyTarget || this.data.generatingBook) {
      return;
    }
    if (!this.data.bookContent) {
      this.setData({ error: "请先生成全书" });
      return;
    }
    if (this.data.hasDraftChanges) {
      this.setData({ error: "请先保存当前章节修改，再导出全书" });
      return;
    }
    if (this.data.bookOutput?.is_stale) {
      this.setData({ error: "章节内容已更新，请先更新全书再导出" });
      return;
    }
    this.setData({ bookExportBusyTarget: target, error: "" });
    try {
      const filePath = await downloadMemoirBookOutputExport(
        this.data.projectId,
        format,
        this.data.bookSelectedVersionId,
        layout,
        format === "pdf" ? this.data.bookExportShowSourceAppendix : true,
      );
      if (format === "pdf") {
        const wxWithDocument = wx as WxWithDocument;
        if (typeof wxWithDocument.openDocument === "function") {
          await new Promise<void>((resolve, reject) => {
            wxWithDocument.openDocument?.({
              filePath,
              fileType: "pdf",
              showMenu: true,
              success: () => resolve(),
              fail: (error) => reject(new Error(error.errMsg || "PDF 打开失败")),
            });
          });
        }
      }
      wx.showToast({ title: format === "pdf" ? "PDF 已打开" : "文本已下载", icon: "success" });
    } catch (error) {
      if (isLoginError(error)) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "全书导出失败" });
    } finally {
      this.setData({ bookExportBusyTarget: "" });
    }
  },

  applySourceCoverage(coverage) {
    this.setData(sourceCoverageData(this.data.plan, coverage));
  },

  syncBookGenerationBusyState(events) {
    const pendingChapterIds = pendingChapterDraftGenerationIds(events);
    const previousPendingIds = Object.keys(this.backgroundDraftBusyChapterIds);
    const changedChapterIds = new Set([...previousPendingIds, ...Array.from(pendingChapterIds)]);
    this.backgroundDraftBusyChapterIds = Array.from(pendingChapterIds).reduce<Record<string, boolean>>((memo, id) => {
      memo[id] = true;
      return memo;
    }, {});
    const hasPendingBookOutput = hasPendingBookOutputGeneration(events);
    const dataPatch: Partial<BookData> = {
      generatingBook: this.bookOutputGenerationSubmitting || hasPendingBookOutput,
    };
    if (changedChapterIds.size) {
      const chapters = this.data.chapters.map((chapter) => (
        changedChapterIds.has(chapter.id)
          ? { ...chapter, draftBusy: pendingChapterIds.has(chapter.id) }
          : chapter
      ));
      Object.assign(dataPatch, chapterCollectionData(
        chapters,
        this.data.stage,
        this.data.sourceCoverage,
        this.data.splitSourceSelections,
      ));
    }
    this.setData(dataPatch);
  },

  applyAutomationEvents(events) {
    this.automationEvents = events;
    this.setData({
      automationEventItems: buildAutomationEventViews(
        events,
        this.data.automationEventBusyTarget,
        this.data.automationEventExpandedTargets,
      ),
    });
    this.syncBookGenerationBusyState(events);
    this.scheduleBookGenerationEventPoll();
  },

  toggleAutomationEventDetails(event) {
    const eventId = event.currentTarget.dataset.id || "";
    if (!eventId) {
      return;
    }
    const expandedTargets = {
      ...this.data.automationEventExpandedTargets,
      [eventId]: !this.data.automationEventExpandedTargets[eventId],
    };
    this.setData({
      automationEventExpandedTargets: expandedTargets,
      automationEventItems: this.data.automationEventItems.map((item) => (
        item.id === eventId
          ? {
            ...item,
            detailExpanded: Boolean(expandedTargets[eventId]),
            detailToggleText: expandedTargets[eventId]
              ? "收起详情"
              : `查看 ${item.detailItems.length} 条详情`,
          }
          : item
      )),
    });
  },

  async acknowledgeAutomationEvent(event) {
    const eventId = event.currentTarget.dataset.id || "";
    const eventIdsText = event.currentTarget.dataset.eventIds || eventId;
    const action = event.currentTarget.dataset.action || "acknowledge";
    if (action === "review_source") {
      const utteranceId = event.currentTarget.dataset.utteranceId || "";
      if (!utteranceId) {
        this.setData({ automationEventError: "缺少要整理的讲述记录" });
        return;
      }
      writeTranscriptNavigationTarget(utteranceId);
      wx.switchTab({ url: "/pages/transcripts/index" });
      return;
    }
    if (action !== "acknowledge" && action !== "acknowledge_group") {
      return;
    }
    if (!this.data.projectId || !eventId || this.data.automationEventBusyTarget) {
      return;
    }
    const eventIds = eventIdsText.split(",").map((item) => item.trim()).filter(Boolean);
    if (!eventIds.length) {
      return;
    }
    this.setData({
      automationEventBusyTarget: eventId,
      automationEventItems: this.data.automationEventItems.map((item) => (
        item.id === eventId ? { ...item, busy: true } : item
      )),
      automationEventError: "",
    });
    try {
      await Promise.all(eventIds.map((id) => acknowledgeMemoirAutomationEvent(this.data.projectId, id)));
      this.setData({
        automationEventItems: this.data.automationEventItems.filter((item) => item.id !== eventId),
      });
    } catch (error) {
      this.setData({
        automationEventError: error instanceof Error ? error.message : "自动处理记录确认失败",
      });
    } finally {
      this.setData({ automationEventBusyTarget: "" });
    }
  },

  async applyUpdatedPlan(plan, toastTitle) {
    const [draftsByChapterId, coverage, output] = plan.status === "confirmed"
      ? await Promise.all([
        this.loadDraftsForPlan(plan),
        this.loadSourceCoverage(plan.project_id),
        this.loadBookOutput(plan.project_id),
      ])
      : [
        {},
        await this.loadSourceCoverage(plan.project_id),
        null,
      ] as const;
    this.applySourceCoverage(coverage);
    this.applyPlan(plan, draftsByChapterId);
    this.applyBookOutput(output);
    wx.showToast({ title: toastTitle, icon: "success" });
  },

  async generatePlan() {
    if (!this.data.projectId || this.data.generatingPlan || this.data.loading) {
      return;
    }
    this.setData({ generatingPlan: true, error: "" });
    try {
      const plan = await generateMemoirBookPlan(this.data.projectId, { max_chapters: 8 });
      this.applyPlan(plan);
      trackAuditAction("book.plan_generate", { chapter_count: plan.chapters.length }, { projectId: this.data.projectId });
      wx.showToast({ title: "已生成章节", icon: "success" });
    } catch (error) {
      if (isLoginError(error)) {
        wx.switchTab({ url: "/pages/me/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "章节生成失败" });
    } finally {
      this.setData({ generatingPlan: false });
    }
  },

  async confirmPlan() {
    if (!this.data.projectId || this.data.confirmingPlan || !this.data.plan) {
      return;
    }
    const chapters = toPlanInput(this.data.chapters);
    if (!chapters.length) {
      this.setData({ error: "至少保留一个章节" });
      return;
    }
    const invalid = chapters.find((chapter) => !chapter.title || !chapter.source_refs.length);
    if (invalid) {
      this.setData({ error: "每个章节都需要标题和收录讲述" });
      return;
    }
    this.setData({ confirmingPlan: true, error: "" });
    try {
      const plan = await confirmMemoirBookPlan(this.data.projectId, {
        title: trimText(this.data.planTitle) || `${this.data.projectTitle}章节规划`,
        chapters,
      });
      const draftsByChapterId = await this.loadDraftsForPlan(plan);
      this.applyPlan(plan, draftsByChapterId);
      trackAuditAction("book.plan_confirm", { chapter_count: plan.chapters.length }, { projectId: this.data.projectId });
      wx.showToast({ title: "章节已确认", icon: "success" });
    } catch (error) {
      if (isLoginError(error)) {
        wx.switchTab({ url: "/pages/me/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "章节确认失败" });
    } finally {
      this.setData({ confirmingPlan: false });
    }
  },

  changePlanTitle(event) {
    this.setData({ planTitle: event.detail.value });
  },

  changeChapterTitle(event) {
    const id = event.currentTarget.dataset.id || "";
    const chapter = this.data.chapters.find((candidate) => candidate.id === id);
    this.patchChapter(id, {
      editingTitle: event.detail.value,
      titleChanged: chapter ? trimText(event.detail.value) !== trimText(chapter.title) : false,
    });
  },

  async saveChapterTitle(event) {
    const chapterId = event.currentTarget.dataset.id || "";
    const chapter = this.data.chapters.find((candidate) => candidate.id === chapterId);
    if (!chapter || !this.data.projectId || this.data.titleBusyTarget || this.data.updatingPlanStructure) {
      return;
    }
    const title = trimText(chapter.editingTitle);
    if (!title) {
      this.patchChapter(chapterId, { draftError: "章节名不能为空" });
      return;
    }
    if (title === trimText(chapter.title)) {
      this.patchChapter(chapterId, { titleChanged: false, draftError: "" });
      wx.showToast({ title: "章名已保存", icon: "success" });
      return;
    }
    this.stopPlayback();
    this.setData({ titleBusyTarget: chapterId, error: "" });
    this.patchChapter(chapterId, { draftError: "" });
    try {
      const plan = await updateMemoirBookChapterTitle(this.data.projectId, chapterId, { title });
      await this.applyUpdatedPlan(plan, "章名已保存");
    } catch (error) {
      if (isLoginError(error)) {
        wx.switchTab({ url: "/pages/me/index" });
        return;
      }
      const message = error instanceof Error ? error.message : "章节名保存失败";
      this.patchChapter(chapterId, {
        draftError: message,
      });
      if (this.data.stage === "plan_draft") {
        this.setData({ error: message });
      }
    } finally {
      this.setData({ titleBusyTarget: "" });
    }
  },

  async generateChapterTitle(event) {
    const chapterId = event.currentTarget.dataset.id || "";
    await this.generateChapterTitleById(chapterId, "已重新起名");
  },

  async generateChapterTitleById(chapterId, toastTitle = "已重新起名") {
    const chapter = this.data.chapters.find((candidate) => candidate.id === chapterId);
    if (!chapter || !this.data.projectId || this.data.titleBusyTarget || this.data.updatingPlanStructure) {
      return false;
    }
    this.stopPlayback();
    this.setData({ titleBusyTarget: chapterId, error: "" });
    this.patchChapter(chapterId, { draftError: "" });
    try {
      const plan = await generateMemoirBookChapterTitle(this.data.projectId, chapterId, {});
      await this.applyUpdatedPlan(plan, toastTitle);
      trackAuditAction("book.chapter_title_generate", {}, { projectId: this.data.projectId });
      return true;
    } catch (error) {
      if (isLoginError(error)) {
        wx.switchTab({ url: "/pages/me/index" });
        return false;
      }
      const message = error instanceof Error ? error.message : "AI 起名失败";
      this.patchChapter(chapterId, {
        draftError: message,
      });
      if (this.data.stage === "plan_draft") {
        this.setData({ error: message });
      }
      return false;
    } finally {
      this.setData({ titleBusyTarget: "" });
    }
  },

  async removeChapter(event) {
    const id = event.currentTarget.dataset.id || "";
    if (!id || this.data.confirmingPlan || this.data.updatingPlanStructure || this.data.chapters.length <= 1) {
      return;
    }
    if (this.data.projectId && this.data.plan) {
      await this.applyPlanStructureCommand({ type: "delete_chapter", chapter_id: id }, "章节已删除");
      return;
    }
    const chapters = this.data.chapters.filter((chapter) => chapter.id !== id);
    this.setData({
      ...chapterCollectionData(chapters, this.data.stage, this.data.sourceCoverage, this.data.splitSourceSelections),
      error: "",
    });
  },

  async moveChapter(event) {
    const chapterId = event.currentTarget.dataset.id || "";
    const direction = event.currentTarget.dataset.direction;
    if (!chapterId || this.data.updatingPlanStructure) {
      return;
    }
    const fromIndex = this.data.chapters.findIndex((chapter) => chapter.id === chapterId);
    const toIndex = direction === "up" ? fromIndex - 1 : fromIndex + 1;
    if (fromIndex < 0 || toIndex < 0 || toIndex >= this.data.chapters.length) {
      return;
    }
    await this.applyPlanStructureCommand({
      type: "move_chapter",
      chapter_id: chapterId,
      direction: direction === "up" ? "up" : "down",
    }, "章节顺序已调整");
  },

  async mergeChapterInto(event) {
    const sourceChapterId = event.currentTarget.dataset.id || "";
    const optionIndex = Number(event.detail.value);
    const sourceChapter = this.data.chapters.find((chapter) => chapter.id === sourceChapterId);
    const option = sourceChapter?.mergeTargetOptions[optionIndex];
    const targetChapter = option
      ? this.data.chapters.find((chapter) => chapter.id === option.value)
      : undefined;
    if (!sourceChapter || !targetChapter || this.data.updatingPlanStructure) {
      return;
    }
    const confirmed = await new Promise<boolean>((resolve) => {
      wx.showModal({
        title: "合并章节",
        content: `把「${sourceChapter.title}」合并到「${targetChapter.title}」，本章素材会保留在合并后的章节中。`,
        confirmText: "合并",
        confirmColor: "#9f4a3c",
        success: (result) => resolve(Boolean(result.confirm)),
        fail: () => resolve(false),
      });
    });
    if (!confirmed) {
      return;
    }
    const plan = await this.applyPlanStructureCommand({
      type: "merge_chapters",
      chapter_id: sourceChapter.id,
      target_chapter_id: targetChapter.id,
    }, "章节已合并");
    if (plan?.status === "confirmed") {
      await this.regenerateMergedChapter(targetChapter.id);
    } else if (plan) {
      await this.generateChapterTitleById(targetChapter.id, "已重写章名");
    }
  },

  toggleSplitSource(event) {
    const chapterId = event.currentTarget.dataset.id || "";
    const utteranceId = event.currentTarget.dataset.utteranceId || "";
    const chapter = this.data.chapters.find((candidate) => candidate.id === chapterId);
    if (!chapter || !utteranceId || chapter.source_refs.length <= 1 || this.data.updatingPlanStructure || chapter.draftBusy) {
      return;
    }
    const selected = new Set(this.data.splitSourceSelections[chapterId] || []);
    if (selected.has(utteranceId)) {
      selected.delete(utteranceId);
    } else {
      selected.add(utteranceId);
    }
    const splitSourceSelections = { ...this.data.splitSourceSelections };
    if (selected.size) {
      splitSourceSelections[chapterId] = Array.from(selected);
    } else {
      delete splitSourceSelections[chapterId];
    }
    this.setData({
      splitSourceSelections,
      ...chapterCollectionData(this.data.chapters, this.data.stage, this.data.sourceCoverage, splitSourceSelections),
      error: "",
    });
  },

  async splitSelectedChapterSources(event) {
    const chapterId = event.currentTarget.dataset.id || "";
    const chapter = this.data.chapters.find((candidate) => candidate.id === chapterId);
    const selectedIds = new Set(this.data.splitSourceSelections[chapterId] || []);
    if (!chapter || !selectedIds.size || this.data.updatingPlanStructure || chapter.draftBusy) {
      return;
    }
    if (selectedIds.size >= chapter.source_refs.length) {
      this.setData({ error: "拆分时至少保留 1 条素材在原章节" });
      return;
    }
    const title = trimText(chapter.editingTitle || chapter.title) || "未命名章节";
    const splitSourceSelections = { ...this.data.splitSourceSelections };
    delete splitSourceSelections[chapterId];
    this.setData({ splitSourceSelections });
    await this.applyPlanStructureCommand({
      type: "split_chapter",
      chapter_id: chapterId,
      title: `${title}（拆出）`,
      utterance_ids: Array.from(selectedIds),
    }, "章节已拆分");
  },

  toggleChapterSources(event) {
    const chapterId = event.currentTarget.dataset.id || "";
    const chapters = this.data.chapters.map((chapter) => (
      chapter.id === chapterId
        ? { ...chapter, sourceListExpanded: !chapter.sourceListExpanded }
        : chapter
    ));
    this.setData(chapterCollectionData(
      chapters,
      this.data.stage,
      this.data.sourceCoverage,
      this.data.splitSourceSelections,
    ));
  },

  async generateChapterDraft(event) {
    const chapterId = event.currentTarget.dataset.id || "";
    await this.generateChapterDraftById(chapterId);
  },

  async generateChapterDraftById(chapterId) {
    const chapter = this.data.chapters.find((candidate) => candidate.id === chapterId);
    if (!chapter || !this.data.projectId || chapter.draftBusy || this.data.updatingPlanStructure) {
      return false;
    }
    if (chapter.draftChanged) {
      this.patchChapter(chapterId, { draftError: "请先保存当前修改，再重新生成" });
      return false;
    }
    this.patchChapter(chapterId, { draftBusy: true, draftError: "" });
    try {
      const shouldSupplementPendingSources = chapter.pendingSourceCount > 0 && !chapter.removedSourceCount;
      const forceRegenerate = Boolean(chapter.draft?.latest_version && !shouldSupplementPendingSources);
      const event = await enqueueMemoirBookChapterDraftGeneration(this.data.projectId, chapterId, {
        force_regenerate: forceRegenerate,
      });
      this.applyAutomationEvents(mergeAutomationEvent(this.automationEvents, event));
      trackAuditAction(
        "book.chapter_draft_generate",
        { force_regenerate: forceRegenerate },
        { projectId: this.data.projectId },
      );
      wx.showToast({ title: "已后台生成", icon: "success" });
      return true;
    } catch (error) {
      this.patchChapter(chapterId, {
        draftError: error instanceof Error ? error.message : "章节稿生成失败",
      });
      this.patchChapter(chapterId, { draftBusy: false });
      return false;
    }
  },

  changeDraftContent(event) {
    const chapterId = event.currentTarget.dataset.id || "";
    const value = event.detail.value;
    const chapter = this.data.chapters.find((candidate) => candidate.id === chapterId);
    if (!chapter) {
      return;
    }
    const original = latestDraftContent(chapter.draft);
    this.patchChapter(chapterId, {
      draftContent: value,
      draftCharCount: trimText(value).length,
      draftChanged: trimText(value) !== trimText(original),
      draftError: "",
    });
  },

  async saveChapterDraft(event) {
    const chapterId = event.currentTarget.dataset.id || "";
    const chapter = this.data.chapters.find((candidate) => candidate.id === chapterId);
    if (!chapter || !this.data.projectId || chapter.draftBusy || this.data.updatingPlanStructure) {
      return;
    }
    const content = trimText(chapter.draftContent);
    if (!content) {
      this.patchChapter(chapterId, { draftError: "章节稿内容不能为空" });
      return;
    }
    this.patchChapter(chapterId, { draftBusy: true, draftError: "" });
    try {
      const draft = await updateMemoirBookChapterDraft(this.data.projectId, chapterId, { content });
      this.patchDraft(chapterId, draft);
      wx.showToast({ title: "章节稿已保存", icon: "success" });
    } catch (error) {
      this.patchChapter(chapterId, {
        draftError: error instanceof Error ? error.message : "章节稿保存失败",
      });
    } finally {
      this.patchChapter(chapterId, { draftBusy: false });
    }
  },

  async confirmChapterDraft(event) {
    const chapterId = event.currentTarget.dataset.id || "";
    const chapter = this.data.chapters.find((candidate) => candidate.id === chapterId);
    if (
      !chapter
      || !this.data.projectId
      || chapter.draftBusy
      || this.data.updatingPlanStructure
      || !chapter.draft?.latest_version
    ) {
      return;
    }
    this.patchChapter(chapterId, { draftBusy: true, draftError: "" });
    try {
      const draft = await confirmMemoirBookChapterDraft(this.data.projectId, chapterId);
      this.patchDraft(chapterId, draft);
      trackAuditAction("book.chapter_draft_confirm", {}, { projectId: this.data.projectId });
      wx.showToast({ title: "本章已确认", icon: "success" });
    } catch (error) {
      this.patchChapter(chapterId, {
        draftError: error instanceof Error ? error.message : "章节稿确认失败",
      });
    } finally {
      this.patchChapter(chapterId, { draftBusy: false });
    }
  },

  async assignUncoveredSource(event) {
    const utteranceId = event.currentTarget.dataset.id || "";
    const optionIndex = Number(event.detail.value);
    const option = this.data.chapterPickerOptions[optionIndex];
    if (!utteranceId || !option || !this.data.projectId || this.data.assigningSourceId) {
      return;
    }
    if (option.value === NEW_CHAPTER_PICKER_VALUE) {
      await this.addUncoveredSourceAsChapterById(utteranceId);
      return;
    }
    this.stopPlayback();
    this.setData({ assigningSourceId: utteranceId, error: "" });
    try {
      const plan = await assignMemoirBookSources(
        this.data.projectId,
        sourceAssignmentRequest(option.value, utteranceId),
      );
      const [draftsByChapterId, coverage] = await Promise.all([
        this.loadDraftsForPlan(plan),
        this.loadSourceCoverage(this.data.projectId),
      ]);
      this.applyPlan(plan, draftsByChapterId);
      this.applySourceCoverage(coverage);
      this.applyBookOutput(null);
      wx.showToast({ title: "已归入章节", icon: "success" });
    } catch (error) {
      if (isLoginError(error)) {
        wx.switchTab({ url: "/pages/me/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "归入章节失败" });
    } finally {
      this.setData({ assigningSourceId: "" });
    }
  },

  async addUncoveredSourceAsChapter(event) {
    const utteranceId = event.currentTarget.dataset.id || "";
    await this.addUncoveredSourceAsChapterById(utteranceId);
  },

  async addUncoveredSourceAsChapterById(utteranceId) {
    const source = this.data.uncoveredSources.find((candidate) => candidate.utterance_id === utteranceId);
    if (!source || !this.data.plan || this.data.stage !== "drafting" || this.data.updatingPlanStructure) {
      return;
    }
    this.setData({ assigningSourceId: utteranceId });
    try {
      await this.applyPlanStructureCommand({
        type: "create_chapter_from_source",
        utterance_id: source.utterance_id,
      }, "已新增章节");
    } finally {
      this.setData({ assigningSourceId: "" });
    }
  },

  async deleteConfirmedChapter(event) {
    const chapterId = event.currentTarget.dataset.id || "";
    const chapter = this.data.chapters.find((candidate) => candidate.id === chapterId);
    if (
      !chapter
      || this.data.stage !== "drafting"
      || this.data.chapters.length <= 1
      || this.data.updatingPlanStructure
      || chapter.draftBusy
    ) {
      return;
    }
    const confirmed = await new Promise<boolean>((resolve) => {
      wx.showModal({
        title: "删除章节",
        content: `删除「${chapter.title}」会移除本章正文，收录讲述会回到待收录列表。`,
        confirmText: "删除",
        confirmColor: "#b4234a",
        success: (result) => resolve(Boolean(result.confirm)),
        fail: () => resolve(false),
      });
    });
    if (!confirmed) {
      return;
    }
    await this.applyPlanStructureCommand({
      type: "delete_chapter",
      chapter_id: chapterId,
    }, "章节已删除");
  },

  async applyPlanStructureCommand(command, toastTitle) {
    if (!this.data.projectId || !this.data.plan || this.data.updatingPlanStructure) {
      return null;
    }
    if (this.data.stage === "drafting" && this.data.hasDraftChanges) {
      this.setData({ error: "请先保存当前章节修改，再调整章节结构" });
      return null;
    }
    this.stopPlayback();
    this.setData({ updatingPlanStructure: true, error: "" });
    try {
      const plan = await applyMemoirBookPlanStructureCommand(this.data.projectId, command);
      await this.applyUpdatedPlan(plan, toastTitle);
      return plan;
    } catch (error) {
      if (isLoginError(error)) {
        wx.switchTab({ url: "/pages/me/index" });
        return null;
      }
      this.setData({ error: error instanceof Error ? error.message : "目录调整失败" });
      return null;
    } finally {
      this.setData({ updatingPlanStructure: false });
    }
  },

  async updatePlanStructure(chapters, toastTitle) {
    if (!this.data.projectId || !this.data.plan || this.data.updatingPlanStructure) {
      return null;
    }
    if (this.data.stage === "drafting" && this.data.hasDraftChanges) {
      this.setData({ error: "请先保存当前章节修改，再调整章节结构" });
      return null;
    }
    this.stopPlayback();
    this.setData({ updatingPlanStructure: true, error: "" });
    try {
      const plan = await updateMemoirBookPlan(this.data.projectId, {
        title: trimText(this.data.planTitle) || this.data.plan.title,
        chapters,
      });
      await this.applyUpdatedPlan(plan, toastTitle);
      return plan;
    } catch (error) {
      if (isLoginError(error)) {
        wx.switchTab({ url: "/pages/me/index" });
        return null;
      }
      this.setData({ error: error instanceof Error ? error.message : "目录调整失败" });
      return null;
    } finally {
      this.setData({ updatingPlanStructure: false });
    }
  },

  async regenerateMergedChapter(chapterId) {
    const renamed = await this.generateChapterTitleById(chapterId, "已重写章名");
    const drafted = await this.generateChapterDraftById(chapterId);
    if (!renamed || !drafted) {
      this.patchChapter(chapterId, {
        draftError: "章节已合并，请检查章名和正文后再确认",
      });
    }
  },

  async generateBook() {
    if (!this.data.projectId || this.data.generatingBook || this.data.updatingPlanStructure) {
      return;
    }
    if (!this.data.allChaptersConfirmed) {
      this.setData({ error: "请先确认所有章节正文" });
      return;
    }
    if (this.data.hasDraftChanges) {
      this.setData({ error: "请先保存当前章节修改" });
      return;
    }
    this.stopPlayback();
    this.setData({ generatingBook: true, error: "" });
    this.bookOutputGenerationSubmitting = true;
    try {
      const metadata = currentBookMetadataPayload(this.data, false);
      const event = await enqueueMemoirBookOutputGeneration(this.data.projectId, {
        force_regenerate: Boolean(this.data.bookOutput?.latest_version),
        title: trimText(this.data.bookTitleInput) || undefined,
        ...metadata,
        cover_style: this.data.bookCoverStyle,
      });
      this.applyAutomationEvents(mergeAutomationEvent(this.automationEvents, event));
      trackAuditAction(
        "book.output_generate",
        { force_regenerate: Boolean(this.data.bookOutput?.latest_version) },
        { projectId: this.data.projectId },
      );
      wx.showToast({ title: "已后台生成", icon: "success" });
    } catch (error) {
      if (isLoginError(error)) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "全书生成提交失败" });
    } finally {
      this.bookOutputGenerationSubmitting = false;
      this.setData({ generatingBook: hasPendingBookOutputGeneration(this.automationEvents) });
    }
  },

  editBookOutputChapter(event) {
    const chapterId = event.currentTarget.dataset.id || "";
    if (!chapterId) {
      return;
    }
    this.stopPlayback();
    this.patchChapter(chapterId, { draftError: "" });
    (wx as WxWithPageScroll).pageScrollTo?.({
      selector: `#chapter-editor-${chapterId}`,
      duration: 260,
      offsetTop: 12,
    });
  },

  async playChapterAudio(event) {
    const chapterId = event.currentTarget.dataset.id || "";
    await this.playChapterAudioById(chapterId);
  },

  async playChapterAudioById(chapterId, options: { autoListenAfterPlayback?: boolean } = {}) {
    const chapter = this.data.chapters.find((candidate) => candidate.id === chapterId);
    if (!chapter || !this.data.projectId || this.data.updatingPlanStructure) {
      return;
    }
    const target = chapter.ttsTarget;
    if (this.data.playingTarget === target) {
      this.stopPlayback();
      return;
    }
    if (this.data.ttsBusyTarget) {
      return;
    }
    if (!chapter.draft?.latest_version) {
      this.patchChapter(chapterId, { draftError: "请先生成章节稿" });
      return;
    }
    if (chapter.draftChanged) {
      this.patchChapter(chapterId, { draftError: "请先保存当前修改" });
      return;
    }
    this.stopPlayback();
    const token = this.audioPlayback.nextToken();
    this.playToken = token;
    this.setData({ ttsBusyTarget: target, error: "" });
    this.patchChapter(chapterId, { draftError: "" });
    try {
      const response = await this.requestBookAudio(target);
      if (token !== this.playToken) {
        return;
      }
      this.setData({ ttsBusyTarget: "" });
      await this.playAudioFiles(response, target, token, trimText(chapter.draftContent));
      if (token === this.playToken && options.autoListenAfterPlayback) {
        this.scheduleBookVoiceAssistantAutoListen("这一章读完了，我继续听");
      }
    } catch (error) {
      if (isLoginError(error)) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      if (token === this.playToken) {
        this.patchChapter(chapterId, {
          draftError: error instanceof Error ? error.message : "章节朗读失败",
        });
      }
    } finally {
      if (token === this.playToken && this.data.ttsBusyTarget === target) {
        this.setData({ ttsBusyTarget: "" });
      }
    }
  },

  async playBookAudio() {
    const target = "book";
    if (this.data.playingTarget === target) {
      this.stopPlayback();
      return;
    }
    if (!this.data.projectId || this.data.ttsBusyTarget || this.data.updatingPlanStructure) {
      return;
    }
    if (!this.data.bookContent) {
      this.setData({ error: "请先生成全书" });
      return;
    }
    if (this.data.hasDraftChanges) {
      this.setData({ error: "请先保存当前章节修改，再朗读全书" });
      return;
    }
    if (this.data.bookOutput?.is_stale) {
      this.setData({ error: "章节内容已更新，请先更新全书再朗读" });
      return;
    }
    if (this.data.bookViewingHistoricalVersion) {
      this.setData({ error: "正在查看历史版本，请先切回最新版再朗读" });
      return;
    }
    this.stopPlayback();
    const token = this.audioPlayback.nextToken();
    this.playToken = token;
    this.setData({ ttsBusyTarget: target, error: "" });
    try {
      const response = await this.requestBookAudio(target);
      if (token !== this.playToken) {
        return;
      }
      this.setData({ ttsBusyTarget: "" });
      await this.playAudioFiles(response, target, token, trimText(this.data.bookContent));
    } catch (error) {
      if (isLoginError(error)) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      if (token === this.playToken) {
        this.setData({ error: error instanceof Error ? error.message : "全书朗读失败" });
      }
    } finally {
      if (token === this.playToken && this.data.ttsBusyTarget === target) {
        this.setData({ ttsBusyTarget: "" });
      }
    }
  },

  async playBookOutlineAudio(options: { autoListenAfterPlayback?: boolean } = {}) {
    const target = "outline";
    if (this.data.playingTarget === target) {
      this.stopPlayback();
      this.stopBookVoiceConversation();
      this.setData({ bookVoiceAssistantStatusText: bookVoiceAssistantStatusText(this.data.stage) });
      return;
    }
    if (!this.data.plan || !this.data.chapters.length) {
      this.setData({
        bookVoiceAssistantStatusText: "先生成章节",
      });
      wx.showToast({ title: "先生成章节规划", icon: "none" });
      return;
    }
    if (!this.data.projectId || this.data.ttsBusyTarget || this.data.updatingPlanStructure) {
      return;
    }
    this.stopPlayback();
    const token = this.audioPlayback.nextToken();
    this.playToken = token;
    const activeText = bookOutlineNarrationText(this.data.plan.title, this.data.chapters);
    this.setData({
      ttsBusyTarget: target,
      bookVoiceAssistantStatusText: "正在准备目录",
      error: "",
    });
    try {
      const response = await this.requestBookAudio(target);
      if (token !== this.playToken) {
        return;
      }
      this.setData({
        ttsBusyTarget: "",
        bookVoiceAssistantStatusText: "正在朗读目录",
      });
      await this.playAudioFiles(response, target, token, activeText);
      if (token === this.playToken) {
        if (options.autoListenAfterPlayback) {
          this.scheduleBookVoiceAssistantAutoListen("目录读完了，我继续听");
        } else {
          this.setData({ bookVoiceAssistantStatusText: bookVoiceAssistantStatusText(this.data.stage) });
        }
      }
    } catch (error) {
      if (isLoginError(error)) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      if (token === this.playToken) {
        this.setData({
          error: error instanceof Error ? error.message : "目录朗读失败",
          bookVoiceAssistantStatusText: "目录朗读失败",
        });
      }
    } finally {
      if (token === this.playToken && this.data.ttsBusyTarget === target) {
        this.setData({ ttsBusyTarget: "" });
      }
    }
  },

  async startBookVoiceCommand(options: { autoTriggered?: boolean } = {}) {
    const autoTriggered = Boolean(options.autoTriggered);
    if (this.data.bookVoiceAssistantListening) {
      this.bookVoiceStreaming.stopRecording();
      this.setData({ bookVoiceAssistantStatusText: "正在保存录音" });
      return;
    }
    if (this.data.bookVoiceAssistantBusy) {
      if (!this.canInterruptBookVoiceAssistantReply()) {
        return;
      }
      this.interruptBookVoiceAssistantReply();
    }
    if (!this.data.plan || !this.data.chapters.length) {
      this.setData({
        bookVoiceAssistantStatusText: "先生成章节",
      });
      wx.showToast({ title: "先生成章节规划", icon: "none" });
      return;
    }
    if (!this.data.projectId || this.data.updatingPlanStructure || this.data.generatingBook) {
      return;
    }
    this.clearBookVoiceAssistantAutoListenTimer();
    this.stopPlayback();
    const authorized = await this.ensureBookVoiceAuthorization(autoTriggered);
    if (!authorized) {
      return;
    }
    const connected = this.bookVoiceStreaming.hasClient() ? true : await this.connectBookVoiceCommandGateway();
    if (!connected || !this.bookVoiceStreaming.hasClient()) {
      return;
    }
    const keepCurrentPreview = autoTriggered && Boolean(this.data.bookVoiceCommandPreview);
    this.setData({
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
      ...(keepCurrentPreview ? {} : {
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
      }),
      error: "",
    });
    const started = this.bookVoiceStreaming.startRecording({
      noVoiceActivityTimeoutMs: autoTriggered ? BOOK_VOICE_AUTO_LISTEN_NO_VOICE_MS : BOOK_VOICE_NO_VOICE_MS,
    });
    if (!started) {
      this.setData({
        bookVoiceAssistantListening: false,
        bookVoiceAssistantBusy: false,
        bookVoiceAssistantStatusText: "实时识别未就绪",
        error: "语音未就绪，请重试",
      });
      this.bookVoiceStreaming.close({ force: true });
      return;
    }
    trackAuditAction(
      "book.voice_command_start",
      { auto_triggered: autoTriggered },
      { projectId: this.data.projectId },
    );
  },

  async ensureBookVoiceAuthorization(autoTriggered = false) {
    if (!readVoiceAuthorizationConsent()) {
      this.setData({
        bookVoiceAssistantExpanded: true,
        bookVoiceAssistantConversationActive: false,
        bookVoiceAssistantStatusText: autoTriggered ? "自动收听已暂停，等待声纹授权" : "请先同意声纹授权",
        error: "请先阅读并同意《声纹授权协议》后再开始录音",
      });
      wx.navigateTo({ url: VOICE_AUTHORIZATION_PAGE_URL });
      return false;
    }
    try {
      await syncVoiceAuthorizationConsent();
      await ensureRecorderPrivacyAuthorization();
      return true;
    } catch (error) {
      const message = error instanceof Error ? error.message : "录音授权失败";
      this.setData({
        bookVoiceAssistantExpanded: true,
        bookVoiceAssistantConversationActive: false,
        bookVoiceAssistantStatusText: "录音授权失败",
        error: message,
      });
      return false;
    }
  },

  async connectBookVoiceCommandGateway() {
    if (!this.data.projectId) {
      return false;
    }
    this.setData({
      bookVoiceAssistantExpanded: true,
      bookVoiceAssistantBusy: true,
      bookVoiceAssistantStatusText: "正在连接实时识别",
      error: "",
    });
    try {
      await this.bookVoiceStreaming.connect({
        voiceSessionId: `book-voice-command-${this.data.projectId}`,
        gatewayUrl: bookVoiceCommandGatewayUrl(this.data.projectId),
        guidanceMode: "free_narration",
        autoStopSilenceMs: BOOK_VOICE_AUTO_STOP_SILENCE_MS,
        minAutoStopRecordingMs: BOOK_VOICE_MIN_RECORDING_MS,
        callbacks: {
          onStatus: (status) => {
            this.handleBookVoiceStatus(status);
          },
          onCaptureStats: (stats) => {
            this.handleBookVoiceCaptureStats(stats);
          },
          onEvent: (event) => {
            this.handleBookVoiceGatewayEvent(event);
          },
          onPartial: (text) => {
            this.handleBookVoicePartial(text);
          },
          onFinal: (event) => {
            void this.handleBookVoiceFinal(event);
          },
          onError: (message, error) => {
            this.handleBookVoiceError(message, error);
          },
        },
      });
      this.setData({
        bookVoiceAssistantStatusText: "实时识别已连接",
        error: "",
      });
      return true;
    } catch (error) {
      const message = error instanceof Error ? error.message : "实时识别连接失败";
      this.handleBookVoiceError(message, classifyVoiceClientConnectionError(message));
      return false;
    } finally {
      this.setData({ bookVoiceAssistantBusy: false });
    }
  },

  appendBookVoiceTranscriptLine(line) {
    const lines = this.data.bookVoiceTranscriptLines || [];
    const index = lines.findIndex((item) => item.id === line.id);
    const nextLines = index >= 0
      ? lines.map((item, itemIndex) => (itemIndex === index ? { ...item, ...line } : item))
      : [...lines, line];
    this.setData({ bookVoiceTranscriptLines: nextLines });
  },

  handleBookVoiceStatus(status) {
    const statusTextMap: Record<VoiceChatConnectionStatus, string> = {
      idle: "实时识别未连接",
      connecting: "正在连接实时识别",
      ready: "我可以听你说",
      listening: "我在听，请说想怎么改",
      stopping: "正在收尾识别",
      closed: "语音对话已暂停",
      error: "实时识别异常",
    };
    const patch: Partial<BookData> = {
      bookVoiceAssistantListening: status === "listening",
    };
    const shouldPreserveSemanticStatus = status === "ready" && (
      this.data.bookVoiceAssistantBusy
      || !this.data.bookVoiceAssistantConversationActive
      || Boolean(this.data.bookVoiceCommandText || this.data.bookVoiceCommandSummary)
    );
    if (!shouldPreserveSemanticStatus) {
      patch.bookVoiceAssistantStatusText = statusTextMap[status];
    }
    this.setData(patch);
  },

  handleBookVoiceCaptureStats(stats) {
    const labels: Record<MemoirCaptureStats["state"], string> = {
      idle: "麦克风未开始",
      opening: "正在打开麦克风",
      recording: "录音中",
      stopping: "正在收尾",
      error: "录音异常",
    };
    const patch: Partial<BookData> = {
      bookVoiceCaptureStateText: labels[stats.state],
      bookVoiceAudioLevelPercent: Math.min(100, Math.round(Math.sqrt(stats.audioLevel || 0) * 100)),
    };
    if (stats.autoStopped && stats.autoStopReason === "no_voice") {
      patch.bookVoiceAssistantListening = false;
      patch.bookVoiceAssistantBusy = false;
      patch.bookVoiceAssistantConversationActive = false;
      patch.bookVoiceAssistantStatusText = "没听到声音，先暂停";
      patch.bookVoiceLiveText = "";
    } else if (stats.autoStopped) {
      patch.bookVoiceAssistantStatusText = "检测到停顿，正在识别";
    }
    this.setData(patch);
  },

  handleBookVoiceGatewayEvent(event) {
    if (event.type === "audio.ended") {
      this.setData({
        bookVoiceAssistantListening: false,
        bookVoiceCaptureStateText: "麦克风未开始",
        bookVoiceAudioLevelPercent: 0,
      });
      const hasRecognizedCommand = Boolean(
        this.data.bookVoiceCommandText || this.data.bookVoiceCommandSummary,
      );
      if (!this.data.bookVoiceLiveText && !this.data.bookVoiceAssistantBusy && !hasRecognizedCommand) {
        this.setData({
          bookVoiceAssistantConversationActive: false,
          bookVoiceAssistantStatusText: this.data.bookVoiceAssistantStatusText === "没听到声音，先暂停"
            ? "语音对话已暂停"
            : "已结束收听",
        });
      }
    }
    if (event.type === "session.stopped") {
      this.setData({ bookVoiceAssistantListening: false });
    }
  },

  handleBookVoicePartial(text) {
    const displayText = normalizeBookVoiceAsrText(text || "");
    this.setData({
      bookVoiceAssistantExpanded: true,
      bookVoiceLiveText: displayText,
      bookVoiceAssistantStatusText: displayText ? "正在实时识别" : "我在听，请说想怎么改",
      error: "",
    });
  },

  async handleBookVoiceFinal(event) {
    const text = normalizeBookVoiceAsrText(event.text || "");
    if (!text) {
      return;
    }
    this.appendBookVoiceTranscriptLine({
      id: event.message_id || event.utterance_id || `book-user-${Date.now()}`,
      role: "user",
      text,
      message_id: event.message_id,
      utterance_id: event.utterance_id,
      audio_file_id: event.audio_file_id,
      transcript_version_id: event.transcript_version_id,
      start_ms: event.start_ms,
      end_ms: event.end_ms,
    });
    this.setData({
      bookVoiceLiveText: "",
      bookVoiceAssistantListening: false,
      bookVoiceAssistantBusy: true,
      bookVoiceAssistantStatusText: "正在理解修改需求",
    });
    try {
      await this.handleBookVoiceCommandTranscript(text);
    } catch (error) {
      if (isLoginError(error)) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "语音修改识别失败",
        bookVoiceAssistantConversationActive: false,
        bookVoiceAssistantStatusText: "没有听清",
      });
    } finally {
      this.setData({
        bookVoiceAssistantListening: false,
        bookVoiceAssistantBusy: false,
      });
    }
  },

  handleBookVoiceError(message, error) {
    const errorMessage = error?.message || message || "实时识别异常";
    this.setData({
      error: errorMessage,
      bookVoiceAssistantListening: false,
      bookVoiceAssistantBusy: false,
      bookVoiceAssistantConversationActive: false,
      bookVoiceAssistantStatusText: errorMessage,
      bookVoiceCaptureStateText: "录音异常",
    });
    this.bookVoiceStreaming.close({ force: true });
  },

  applyBookVoiceCommandPreview(preview) {
    this.setData(bookVoiceCommandPreviewPatch(preview));
  },

  async handleBookVoiceCommandTranscript(text) {
    await this.runBookVoiceAssistantTurn(text);
  },

  async runBookVoiceAssistantTurn(text) {
    if (!this.data.projectId) {
      return false;
    }
    const pendingPreview = this.data.bookVoiceCommandPreview;
    const undoSnapshot = (
      pendingPreview
      && this.data.bookVoiceCommandCanExecute
      && pendingPreview.requires_confirmation
      && isBookVoiceCommandConfirmationText(text)
    ) ? this.bookVoiceCommandUndoSnapshot() : null;
    const token = this.audioPlayback.nextToken();
    this.playToken = token;
    this.bookVoiceStreamingAudioStartSeconds = -1;
    this.bookVoiceStreamingAudioEndSeconds = 0;
    this.setData({
      bookVoiceAssistantBusy: true,
      ttsBusyTarget: "book-voice-assistant",
      bookVoiceAssistantStatusText: "正在理解修改需求",
      error: "",
    });
    const settings = readBookTTSSettings();
    const runtime = new VoiceAssistantTurnRuntime(`book-voice-command-${this.data.projectId}`, {
      onStarted: () => {
        if (token === this.playToken) {
          this.setData({ bookVoiceAssistantStatusText: "AI 正在回复" });
        }
      },
      onCommandPreview: (event) => {
        if (token === this.playToken) {
          this.handleBookVoiceCommandPreview(event);
        }
      },
      onTextDelta: (event) => {
        if (token === this.playToken) {
          this.applyBookVoiceAssistantTurnText(event.text);
        }
      },
      onAudioFrame: () => {
        if (token === this.playToken) {
          this.setData({
            ttsBusyTarget: "",
            bookTTSActiveTarget: "book-voice-assistant",
            bookTTSActiveText: this.data.bookVoiceAssistantReplyText,
            bookTTSRemainingText: this.data.bookVoiceAssistantReplyText,
            bookVoiceAssistantStatusText: "AI 正在说",
          });
        }
      },
      onAudioFrameScheduled: (event) => {
        if (token === this.playToken) {
          this.markBookVoiceStreamingAudioFrameScheduled(event);
          this.updateBookVoiceStreamingTTSProgress();
          this.startBookVoiceStreamingTTSProgressTimer(token);
        }
      },
      onAudioDrained: () => {
        if (token === this.playToken) {
          void this.finishBookVoiceAssistantAudioFrames(token, this.data.bookVoiceAssistantReplyText);
        }
      },
      onPlaybackError: (error) => {
        if (token === this.playToken) {
          this.setData({
            error: error.message,
            bookVoiceAssistantStatusText: "AI 语音播放失败",
          });
        }
      },
    }, {
      createStreamClient: (voiceSessionId, callbacks) => (
        new VoiceAssistantTurnStreamClient(voiceSessionId, callbacks, {
          streamUrl: bookVoiceAssistantTurnStreamUrl(this.data.projectId),
        })
      ),
    });
    this.bookVoiceAssistantTurnRuntime = runtime;
    try {
      const { completed, playbackError } = await runtime.start({
        voice: settings.voice || undefined,
        speedRatio: settings.speedRatio,
        extraPayload: this.bookVoiceAssistantTurnPayload(text),
      });
      if (token !== this.playToken) {
        return false;
      }
      this.setData({
        ttsBusyTarget: "",
        bookTTSActiveTarget: "",
        bookVoiceAssistantBusy: false,
        bookVoiceAssistantSpeakingLineId: "",
      });
      await this.applyCompletedBookVoiceAssistantTurn(completed, undoSnapshot, {
        autoListenAfterTurn: !playbackError,
      });
      if (playbackError && token === this.playToken) {
        this.setData({ error: playbackError.message });
      }
      return !playbackError;
    } catch (error) {
      if (isLoginError(error)) {
        wx.reLaunch({ url: "/pages/login/index" });
        return false;
      }
      if (token === this.playToken) {
        this.setData({
          error: error instanceof Error ? error.message : "语音修改识别失败",
          bookVoiceAssistantConversationActive: false,
          bookVoiceAssistantStatusText: "没有听清",
        });
      }
      return false;
    } finally {
      if (this.bookVoiceAssistantTurnRuntime === runtime) {
        this.bookVoiceAssistantTurnRuntime = null;
      }
      if (token === this.playToken) {
        this.stopBookTTSProgressTimer();
        this.setData({
          ttsBusyTarget: "",
          bookTTSActiveTarget: this.data.bookTTSActiveTarget === "book-voice-assistant"
            ? ""
            : this.data.bookTTSActiveTarget,
          bookVoiceAssistantBusy: false,
          bookVoiceAssistantSpeakingLineId: "",
        });
      }
    }
  },

  bookVoiceAssistantTurnPayload(text) {
    const request = bookVoiceCommandParseRequest(
      text,
      this.data.bookVoiceTranscriptLines,
      this.data.sourceCoverage,
    ) as unknown as Record<string, unknown>;
    if (this.data.bookVoiceCommandPreview) {
      request.pending_command = this.data.bookVoiceCommandPreview;
    }
    return request;
  },

  handleBookVoiceCommandPreview(event) {
    this.applyBookVoiceCommandPreview(event.command_preview);
    wx.showToast({ title: "已识别", icon: "success" });
  },

  applyBookVoiceAssistantTurnText(text) {
    const replyText = trimText(text);
    if (!replyText) {
      return;
    }
    const lineId = this.data.bookVoiceAssistantSpeakingLineId
      || `book-ai-${Date.now()}-${this.data.bookVoiceTranscriptLines.length}`;
    this.appendBookVoiceTranscriptLine({
      id: lineId,
      role: "assistant",
      text: replyText,
    });
    this.setData({
      bookVoiceAssistantExpanded: true,
      bookVoiceAssistantReplyText: replyText,
      bookVoiceAssistantSpeakingLineId: lineId,
      bookTTSActiveText: replyText,
      bookTTSRemainingText: replyText,
      bookVoiceAssistantStatusText: "AI 正在回复",
      error: "",
    });
  },

  async applyCompletedBookVoiceAssistantTurn(event, undoSnapshot, options = {}) {
    const autoListenAfterTurn = options.autoListenAfterTurn !== false;
    const preview = event.command_preview || this.data.bookVoiceCommandPreview;
    if (preview) {
      this.applyBookVoiceCommandPreview(preview);
    }
    if (event.execution_result) {
      await this.applyBookVoiceCommandExecution(event.execution_result, undoSnapshot, {
        autoListenAfterExecution: false,
      });
      if (autoListenAfterTurn) {
        this.scheduleBookVoiceAssistantAutoListen(executedVoiceCommandStatusText(event.execution_result.intent));
      }
      return;
    }
    if (preview && isBookVoiceReadIntent(preview) && canExecuteBookVoiceCommand(preview)) {
      await this.executeBookVoiceCommand({ confirmedByVoice: true, autoListenAfterExecution: autoListenAfterTurn });
      return;
    }
    if (autoListenAfterTurn && preview && !isBookVoiceReadIntent(preview)) {
      this.scheduleBookVoiceAssistantAutoListen(
        preview.requires_confirmation && canExecuteBookVoiceCommand(preview)
          ? "等你确认，或继续说"
          : "我继续听",
      );
    }
  },

  async executeBookVoiceCommand(options: { confirmedByVoice?: boolean; autoListenAfterExecution?: boolean } = {}) {
    const preview = this.data.bookVoiceCommandPreview;
    const operation = preview?.operations?.[0] as MemoirBookVoiceCommandOperation | undefined;
    if (
      !preview
      || !operation
      || !this.data.bookVoiceCommandCanExecute
      || this.data.bookVoiceCommandExecuting
      || this.data.updatingPlanStructure
    ) {
      return;
    }
    if (preview.intent === "read_outline") {
      await this.playBookOutlineAudio({ autoListenAfterPlayback: options.autoListenAfterExecution });
      return;
    }
    if (preview.intent === "read_chapter" && operation.chapter_id) {
      await this.playChapterAudioById(operation.chapter_id, { autoListenAfterPlayback: options.autoListenAfterExecution });
      return;
    }

    if (!options.confirmedByVoice) {
      const confirmed = await this.confirmBookVoiceCommandExecution(preview.summary);
      if (!confirmed) {
        return;
      }
    }
    const undoSnapshot = this.bookVoiceCommandUndoSnapshot();
    this.setData({ bookVoiceCommandExecuting: true, error: "" });
    try {
      if (preview.intent === "assign_source" && operation.utterance_id) {
        this.setData({ assigningSourceId: operation.utterance_id });
      }
      const response = await executeMemoirBookVoiceCommand(this.data.projectId, {
        intent: preview.intent,
        operation,
      });
      await this.applyBookVoiceCommandExecution(response, undoSnapshot, options);
      trackAuditAction(
        "book.voice_command_execute",
        { intent: response.intent, confirmed_by_voice: Boolean(options.confirmedByVoice) },
        { projectId: this.data.projectId },
      );
    } catch (error) {
      if (isLoginError(error)) {
        wx.switchTab({ url: "/pages/me/index" });
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "语音修改执行失败",
        bookVoiceAssistantStatusText: "执行失败",
      });
    } finally {
      if (preview.intent === "assign_source") {
        this.setData({ assigningSourceId: "" });
      }
      this.setData({ bookVoiceCommandExecuting: false });
    }
  },

  async applyBookVoiceCommandExecution(response, undoSnapshot, options = {}) {
    const [draftsByChapterId, output] = await Promise.all([
      this.loadDraftsForPlan(response.plan),
      response.intent === "assign_source" ? Promise.resolve(null) : this.loadBookOutput(this.data.projectId),
    ]);
    const coverage = response.source_coverage || await this.loadSourceCoverage(this.data.projectId);
    this.applySourceCoverage(coverage);
    this.applyPlan(response.plan, draftsByChapterId);
    this.applyBookOutput(output);

    const undoText = executedVoiceCommandUndoText(response.intent);
    this.setData({
      bookVoiceCommandCanExecute: false,
      bookVoiceCommandRequiresConfirmation: false,
      bookVoiceCommandStateText: "已执行",
      bookVoiceCommandActionText: "已执行",
      bookVoiceCommandUndoText: undoText,
      bookVoiceCommandUndoSnapshot: undoText ? undoSnapshot : null,
      bookVoiceAssistantStatusText: executedVoiceCommandStatusText(response.intent),
    });
    if (options.autoListenAfterExecution) {
      this.scheduleBookVoiceAssistantAutoListen(executedVoiceCommandStatusText(response.intent));
    }
  },

  async undoBookVoiceCommand() {
    const snapshot = this.data.bookVoiceCommandUndoSnapshot;
    if (!snapshot || this.data.bookVoiceCommandExecuting || this.data.updatingPlanStructure) {
      return;
    }
    const confirmed = await this.confirmBookVoiceCommandExecution("撤回上一次语音修改？", "撤回");
    if (!confirmed) {
      return;
    }
    this.setData({
      bookVoiceCommandExecuting: true,
      planTitle: snapshot.planTitle,
      error: "",
    });
    try {
      const plan = await this.updatePlanStructure(snapshot.chapters, "已撤回语音修改");
      if (plan) {
        this.setData({
          bookVoiceCommandUndoText: "",
          bookVoiceCommandUndoSnapshot: null,
          bookVoiceAssistantStatusText: "已撤回",
        });
      }
    } finally {
      this.setData({ bookVoiceCommandExecuting: false });
    }
  },

  clearBookVoiceCommandPreview() {
    this.setData({
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
      bookVoiceAssistantStatusText: bookVoiceAssistantStatusText(this.data.stage),
      bookVoiceAssistantSpeakingLineId: "",
      bookVoiceLiveText: "",
      error: "",
    });
  },

  stopBookVoiceConversation() {
    this.clearBookVoiceAssistantAutoListenTimer();
    this.bookVoiceAssistantTurnRuntime?.close();
    this.bookVoiceAssistantTurnRuntime = null;
    if (this.data.bookVoiceAssistantListening) {
      this.bookVoiceStreaming.stopRecording();
      this.setData({
        bookVoiceAssistantConversationActive: false,
        bookVoiceAssistantStatusText: "正在保存录音",
      });
      return;
    }
    this.bookVoiceStreaming.close({ force: true });
    this.setData({
      bookVoiceAssistantConversationActive: false,
      bookVoiceAssistantBusy: false,
      bookVoiceAssistantReplyText: "",
      bookVoiceAssistantSpeakingLineId: "",
      bookVoiceLiveText: "",
      bookVoiceAudioLevelPercent: 0,
      bookVoiceCaptureStateText: "麦克风未开始",
      bookVoiceAssistantStatusText: bookVoiceAssistantStatusText(this.data.stage),
    });
  },

  clearBookVoiceAssistantAutoListenTimer() {
    if (this.bookVoiceAutoListenTimer !== null) {
      clearTimeout(this.bookVoiceAutoListenTimer);
      this.bookVoiceAutoListenTimer = null;
    }
  },

  canStartBookVoiceAssistantAutoListen() {
    return Boolean(
      this.data.bookVoiceAssistantConversationActive
      && this.data.bookVoiceAssistantExpanded
      && this.data.plan
      && this.data.chapters.length
      && this.data.projectId
      && !this.data.loading
      && !this.data.bookVoiceAssistantListening
      && !this.data.bookVoiceAssistantBusy
      && !this.data.ttsBusyTarget
      && !this.data.playingTarget
      && !this.data.updatingPlanStructure
      && !this.data.generatingBook,
    );
  },

  scheduleBookVoiceAssistantAutoListen(statusText = "我继续听") {
    this.clearBookVoiceAssistantAutoListenTimer();
    if (!this.canStartBookVoiceAssistantAutoListen()) {
      return;
    }
    this.setData({
      bookVoiceAssistantExpanded: true,
      bookVoiceAssistantConversationActive: true,
      bookVoiceAssistantBusy: false,
      bookVoiceAssistantStatusText: statusText,
    });
    this.bookVoiceAutoListenTimer = setTimeout(() => {
      this.bookVoiceAutoListenTimer = null;
      if (this.canStartBookVoiceAssistantAutoListen()) {
        void this.startBookVoiceCommand({ autoTriggered: true });
      }
    }, 250) as unknown as number;
  },

  canInterruptBookVoiceAssistantReply() {
    return Boolean(
      this.data.bookVoiceAssistantConversationActive
      && (
        this.data.bookVoiceAssistantSpeakingLineId
        || this.data.bookTTSActiveTarget === "book-voice-assistant"
        || this.data.ttsBusyTarget === "book-voice-assistant"
        || this.bookVoiceAssistantTurnRuntime
      ),
    );
  },

  interruptBookVoiceAssistantReply() {
    const interruptedLineId = this.data.bookVoiceAssistantSpeakingLineId;
    this.clearBookVoiceAssistantAutoListenTimer();
    this.bookVoiceAssistantTurnRuntime?.interrupt();
    this.bookVoiceAssistantTurnRuntime = null;
    stopBookPlayback(this);
    const transcriptLines = interruptedLineId
      ? this.data.bookVoiceTranscriptLines.filter((line) => line.id !== interruptedLineId)
      : this.data.bookVoiceTranscriptLines;
    this.setData({
      bookVoiceTranscriptLines: transcriptLines,
      bookVoiceAssistantBusy: false,
      bookVoiceAssistantReplyText: "",
      bookVoiceAssistantSpeakingLineId: "",
      bookVoiceAssistantStatusText: "已打断，正在听你说",
      bookTTSActiveTarget: "",
      bookTTSActiveText: "",
      bookTTSSpokenText: "",
      bookTTSCurrentText: "",
      bookTTSRemainingText: "",
      bookTTSProgressPercent: 0,
    });
  },

  async finishBookVoiceAssistantAudioFrames(token, activeText) {
    if (token !== this.playToken) {
      return;
    }
    this.stopBookTTSProgressTimer();
    const durationSeconds = this.bookVoiceStreamingAudioStartSeconds >= 0
      ? Math.max(0, this.bookVoiceStreamingAudioEndSeconds - this.bookVoiceStreamingAudioStartSeconds)
      : this.data.bookTTSDurationSeconds;
    this.setData({
      bookTTSSpokenText: activeText,
      bookTTSCurrentText: "",
      bookTTSRemainingText: "",
      bookTTSProgressPercent: 100,
      bookTTSDurationSeconds: Math.max(this.data.bookTTSDurationSeconds, durationSeconds),
    });
  },

  markBookVoiceStreamingAudioFrameScheduled(event) {
    if (this.bookVoiceStreamingAudioStartSeconds < 0) {
      this.bookVoiceStreamingAudioStartSeconds = event.startAtSeconds;
    }
    const frameEndSeconds = event.startAtSeconds + event.durationSeconds;
    this.bookVoiceStreamingAudioEndSeconds = Math.max(
      this.bookVoiceStreamingAudioEndSeconds,
      frameEndSeconds,
    );
    this.setData({
      bookTTSDurationSeconds: Math.max(
        this.data.bookTTSDurationSeconds,
        this.bookVoiceStreamingAudioEndSeconds - this.bookVoiceStreamingAudioStartSeconds,
        0,
      ),
    });
  },

  updateBookVoiceStreamingTTSProgress() {
    if (this.bookVoiceStreamingAudioStartSeconds < 0) {
      this.updateBookTTSProgress(0, 0);
      return;
    }
    const currentTimeSeconds = this.bookVoiceAssistantTurnRuntime?.stats()?.currentTimeSeconds;
    const current = Number.isFinite(currentTimeSeconds) ? Number(currentTimeSeconds) : 0;
    const playbackTime = Math.max(0, current - this.bookVoiceStreamingAudioStartSeconds);
    const scheduledDuration = Math.max(
      0,
      this.bookVoiceStreamingAudioEndSeconds - this.bookVoiceStreamingAudioStartSeconds,
    );
    this.updateBookTTSProgress(
      playbackTime,
      Math.max(scheduledDuration, playbackTime + BOOK_VOICE_STREAMING_TTS_PROGRESS_HOLD_SECONDS),
    );
  },

  startBookVoiceStreamingTTSProgressTimer(token) {
    if (this.ttsProgressTimer !== null) {
      return;
    }
    const timer = setInterval(() => {
      if (
        token !== this.playToken
        || !this.bookVoiceAssistantTurnRuntime
        || this.data.bookTTSActiveTarget !== "book-voice-assistant"
      ) {
        if (this.ttsProgressTimer === timer) {
          this.stopBookTTSProgressTimer();
        }
        return;
      }
      this.updateBookVoiceStreamingTTSProgress();
    }, 250);
    this.ttsProgressTimer = timer;
  },

  bookVoiceCommandUndoSnapshot() {
    if (!this.data.plan || !this.data.chapters.length) {
      return null;
    }
    return {
      planTitle: trimText(this.data.planTitle) || this.data.plan.title,
      chapters: toPlanUpdateInput(this.data.chapters),
    };
  },

  async confirmBookVoiceCommandExecution(content, confirmText = "执行") {
    return new Promise<boolean>((resolve) => {
      wx.showModal({
        title: "语音修改",
        content,
        confirmText,
        confirmColor: "#9f4a3c",
        success: (result) => resolve(Boolean(result.confirm)),
        fail: () => resolve(false),
      });
    });
  },

  async requestBookAudio(target) {
    const settings = readBookTTSSettings();
    const request = {
      ...(settings.voice ? { voice: settings.voice } : {}),
      ...(settings.speedRatio === 1 ? {} : { speed_ratio: settings.speedRatio }),
    };
    if (target === "book") {
      return createMemoirBookOutputTTS(this.data.projectId, request);
    }
    if (target === "outline") {
      return createMemoirBookPlanOutlineTTS(this.data.projectId, request);
    }
    if (target.startsWith("chapter:")) {
      return createMemoirBookChapterDraftTTS(this.data.projectId, target.slice("chapter:".length), request);
    }
    throw new Error("朗读目标无效");
  },

  async playAudioFiles(response, target, token, activeText) {
    return playBookAudioFiles(this, response, target, token, activeText);
  },

  async playAudioFile(audioFileId, token, elapsedBeforeSeconds, fallbackDurationSeconds) {
    return playBookAudioFile(this, audioFileId, token, elapsedBeforeSeconds, fallbackDurationSeconds);
  },

  updateBookTTSProgress(currentTimeSeconds, durationSeconds) {
    updateBookTTSPlaybackProgress(this, currentTimeSeconds, durationSeconds);
  },

  startBookTTSProgressTimer(token, audio, elapsedBeforeSeconds, durationSeconds) {
    startBookTTSPlaybackProgressTimer(this, token, audio, elapsedBeforeSeconds, durationSeconds);
  },

  stopBookTTSProgressTimer() {
    stopBookTTSPlaybackProgressTimer(this);
  },

  resetBookTTSProgress() {
    resetBookTTSPlaybackProgress(this);
  },

  playbackTimeForProgress(audio) {
    return playbackTimeForBookTTSProgress(this, audio);
  },

  stopPlayback() {
    this.bookVoiceAssistantTurnRuntime?.close();
    this.bookVoiceAssistantTurnRuntime = null;
    this.bookVoiceStreamingAudioStartSeconds = -1;
    this.bookVoiceStreamingAudioEndSeconds = 0;
    stopBookPlayback(this);
    if (this.data.bookVoiceAssistantSpeakingLineId) {
      this.setData({ bookVoiceAssistantSpeakingLineId: "" });
    }
  },

  onHide() {
    this.flushBookDesignAutosave();
    this.clearBookGenerationEventPoll();
    this.clearBookVoiceAssistantAutoListenTimer();
    this.bookVoiceStreaming.close({ force: true });
    this.stopPlayback();
    if (
      this.data.bookVoiceAssistantConversationActive
      || this.data.bookVoiceAssistantListening
      || this.data.bookVoiceAssistantBusy
      || this.data.bookVoiceLiveText
    ) {
      this.setData({
        bookVoiceAssistantListening: false,
        bookVoiceAssistantBusy: false,
        bookVoiceAssistantConversationActive: false,
        bookVoiceAssistantSpeakingLineId: "",
        bookVoiceLiveText: "",
        bookVoiceAudioLevelPercent: 0,
        bookVoiceCaptureStateText: "麦克风未开始",
        bookVoiceAssistantStatusText: bookVoiceAssistantStatusText(this.data.stage),
      });
    }
  },

  onUnload() {
    this.flushBookDesignAutosave();
    this.clearBookGenerationEventPoll();
    this.stopBookVoiceConversation();
    this.stopPlayback();
  },

  openTranscripts(event) {
    const utteranceId = event?.currentTarget?.dataset?.id || "";
    if (utteranceId) {
      writeTranscriptNavigationTarget(utteranceId);
    }
    wx.switchTab({ url: "/pages/transcripts/index" });
  },

  openHome() {
    this.stopPlayback();
    wx.switchTab({ url: "/pages/home/index" });
  },

  openLogin() {
    this.stopPlayback();
    wx.switchTab({ url: "/pages/me/index" });
  },

  createProject() {
    this.stopPlayback();
    wx.navigateTo({ url: "/pages/project-edit/index" });
  },

  patchChapter(chapterId, patch) {
    if (!chapterId) {
      return;
    }
    const chapters = this.data.chapters.map((chapter) => (
      chapter.id === chapterId ? { ...chapter, ...patch } : chapter
    ));
    this.setData({
      ...chapterCollectionData(
        chapters,
        this.data.stage,
        this.data.sourceCoverage,
        this.data.splitSourceSelections,
      ),
    });
  },

  patchDraft(chapterId, draft) {
    const assignedPendingCount = responseCount(draft.pending_source_count);
    const assignedRemovedCount = responseCount(draft.removed_source_count);
    const content = latestDraftContent(draft);
    this.patchChapter(chapterId, {
      pending_source_count: assignedPendingCount,
      removed_source_count: assignedRemovedCount,
      pendingSourceCount: assignedPendingCount,
      removedSourceCount: assignedRemovedCount,
      sourceChangeNotice: sourceChangeNotice(assignedPendingCount, assignedRemovedCount),
      draft,
      draftContent: content,
      draftCharCount: trimText(content).length,
      draftChanged: false,
      draftStatusText: draftStatusText(draft),
      draftVersionText: draftVersionText(draft),
      draftActionText: draftActionText(draft, assignedPendingCount, assignedRemovedCount),
      draftError: "",
    });
    this.clearBookOutput();
  },

  clearBookOutput() {
    if (this.data.playingTarget === "book") {
      this.stopPlayback();
    }
    this.applyBookOutput(null);
  },
});
