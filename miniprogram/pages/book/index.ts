import type {
  MemoirBookAudioResponse,
  MemoirBookChapterDraft,
  MemoirBookOutput,
  MemoirBookPlan,
  MemoirBookPlanStructureCommandRequest,
  MemoirBookPlanChapterUpdateInput,
  MemoirBookSourceCoverage,
  MemoirBookVoiceCommandOperation,
  MemoirBookVoiceCommandParseRequest,
  MemoirBookVoiceCommandParseResponse,
  MemoirBookVoiceCommandResponseKind,
  VoiceChatErrorInfo,
  VoiceChatTranscriptLine,
  VoiceGatewayServerEvent,
} from "../../domain/voice-chat";
import type { BookChapterView, BookData, BookVoiceCommandUndoSnapshot } from "./book-view-model";
import { getMemoirIdentity } from "../../services/auth";

import {
  applyMemoirBookPlanStructureCommand,
  assignMemoirBookSources,
  confirmMemoirBookChapterDraft,
  confirmMemoirBookPlan,
  createMemoirBookChapterDraftTTS,
  createMemoirBookOutputTTS,
  createMemoirBookPlanOutlineTTS,
  createMemoirBookVoiceAssistantTTS,
  bookVoiceCommandGatewayUrl,
  generateMemoirBookChapterTitle,
  generateMemoirBookChapterDraft,
  generateMemoirBookOutput,
  generateMemoirBookPlan,
  getMemoirBookChapterDraft,
  getMemoirBookOutput,
  getMemoirBookPlan,
  getMemoirBookSourceCoverage,
  parseMemoirBookVoiceCommand,
  updateMemoirBookChapterTitle,
  updateMemoirBookPlan,
  updateMemoirBookChapterDraft,
} from "../../services/memoir-voice-api";
import { ApiError } from "../../services/request";
import { sourceAssignmentRequest } from "../../services/memoir-book-source-actions";
import {
  classifyVoiceClientConnectionError,
  MemoirStreamingAsrClient,
  type MemoirCaptureStats,
  type VoiceChatConnectionStatus,
} from "../../services/memoir-streaming-asr";
import { readBookTTSSettings } from "../../services/tts-settings";
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
  clearSourceChangeMetadata,
  draftActionText,
  draftStatusText,
  draftVersionText,
  guestBookData,
  latestDraftContent,
  planStage,
  removedSourceCount,
  sourceCoverageData,
  sourceChangeNotice,
  sourceSupplementCount,
  stageText,
  toPlanInput,
  toPlanUpdateInput,
  trimText,
  bookChapterSourceSummary,
  bookOutlineNarrationText,
  bookVoiceCommandActionText,
  canExecuteBookVoiceCommand,
} from "./book-view-model";
export {
  bookChapterSourceSummary,
  bookOutputStaleNotice,
  bookOutputVersionText,
  bookOutlineNarrationText,
  bookVoiceCommandActionText,
  bookProgressText,
  bookSourceCoverageNotice,
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
      direction?: string;
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

function isLoginError(error: unknown): boolean {
  return error instanceof ApiError && [401, 403].includes(error.statusCode);
}

function bookVoiceAssistantHint(stage: BookData["stage"], chapterCount: number): string {
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

function bookVoiceAssistantStatusText(stage: BookData["stage"]): string {
  if (stage === "empty") {
    return "等待章节";
  }
  return "我可以帮你听和改";
}

const BOOK_VOICE_AUTO_STOP_SILENCE_MS = 900;
const BOOK_VOICE_MIN_RECORDING_MS = 700;
const BOOK_VOICE_NO_VOICE_MS = 3500;
const BOOK_VOICE_AUTO_LISTEN_NO_VOICE_MS = 15000;

let bookVoiceStreamingClient: MemoirStreamingAsrClient | null = null;

function isBookVoiceReadIntent(preview: MemoirBookVoiceCommandParseResponse): boolean {
  return preview.intent === "read_outline" || preview.intent === "read_chapter";
}

function isBookVoiceConsultationIntent(preview: MemoirBookVoiceCommandParseResponse): boolean {
  return preview.intent === "review_chapter_title"
    || preview.intent === "review_chapter_length"
    || preview.intent === "review_chapter_structure";
}

function isBookVoiceAnswerIntent(preview: MemoirBookVoiceCommandParseResponse): boolean {
  return preview.intent === "answer_book_question" || preview.intent === "capability_help";
}

function isBookVoiceClarificationIntent(preview: MemoirBookVoiceCommandParseResponse): boolean {
  return preview.intent === "clarify_previous" || preview.intent === "unknown";
}

function isBookVoiceCommandConfirmationText(text: string): boolean {
  const normalized = trimText(text).replace(/[\s，。,.!！?？、；;：“”"‘’'（）()]/g, "");
  return /^(确认|确认执行|执行|可以|可以执行|就这样|就这样改|好的|好|没问题|按这个改)$/.test(normalized);
}

function normalizeBookVoiceAsrText(text: string): string {
  const trimmed = trimText(text);
  return trimmed
    .replace(/第\s*([0-9一二两三四五六七八九十]+)\s*张/g, "第$1章")
    .replace(/([0-9一二两三四五六七八九十]+)\s*张(?=(?:和|跟|与|的|换|调|移|顺序|章节|章|[，。,.!！?？、\s]|$))/g, "$1章")
    .replace(/几张/g, "几章")
    .replace(/多少张/g, "多少章");
}

function bookVoiceAssistantReplyText(preview: MemoirBookVoiceCommandParseResponse): string {
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

function bookVoiceConversationContext(
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

function bookVoiceCommandBookContext(
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

function bookVoiceCommandResponseKind(
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

function bookVoiceCommandPanelTitle(kind: MemoirBookVoiceCommandResponseKind): string {
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

function bookVoiceCommandStateText(
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

function bookVoiceCommandDetailText(
  preview: MemoirBookVoiceCommandParseResponse,
  kind: MemoirBookVoiceCommandResponseKind,
): string {
  if (kind === "answer" || kind === "consultation" || kind === "clarification") {
    return trimText(preview.reply_text || preview.summary);
  }
  return trimText(preview.summary);
}

Page<BookData, {
  onShow(): Promise<void>;
  loadBookHome(): Promise<void>;
  loadPlanForProject(projectId: string): Promise<void>;
  loadDraftsForPlan(plan: MemoirBookPlan): Promise<Record<string, MemoirBookChapterDraft | null>>;
  loadBookOutput(projectId: string): Promise<MemoirBookOutput | null>;
  loadSourceCoverage(projectId: string): Promise<MemoirBookSourceCoverage | null>;
  applyPlan(plan: MemoirBookPlan | null, draftsByChapterId?: Record<string, MemoirBookChapterDraft | null>): void;
  applyBookOutput(output: MemoirBookOutput | null): void;
  applySourceCoverage(coverage: MemoirBookSourceCoverage | null): void;
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
  generateChapterDraftById(chapterId: string, toastTitle?: string): Promise<boolean>;
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
  generateBook(): Promise<void>;
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
  executeBookVoiceCommand(options?: { confirmedByVoice?: boolean; autoListenAfterExecution?: boolean }): Promise<void>;
  undoBookVoiceCommand(): Promise<void>;
  clearBookVoiceCommandPreview(): void;
  stopBookVoiceConversation(): void;
  clearBookVoiceAssistantAutoListenTimer(): void;
  canStartBookVoiceAssistantAutoListen(): boolean;
  scheduleBookVoiceAssistantAutoListen(statusText?: string): void;
  playBookVoiceAssistantReply(text: string, options?: { autoListenAfterPlayback?: boolean }): Promise<void>;
  requestBookVoiceAssistantAudio(text: string): Promise<MemoirBookAudioResponse>;
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
  patchDraft(chapterId: string, draft: MemoirBookChapterDraft, options?: { clearPendingSources?: boolean }): void;
  clearBookOutput(): void;
  audio: WechatMiniprogram.InnerAudioContext | null;
  playToken: number;
  ttsProgressTimer: number | null;
  ttsProgressStartedAtMs: number;
  bookVoiceAutoListenTimer: number | null;
}>({
  audio: null,
  playToken: 0,
  ttsProgressTimer: null,
  ttsProgressStartedAtMs: 0,
  bookVoiceAutoListenTimer: null,

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
    bookVersionText: "尚未生成全书",
    bookStaleNotice: "",
    bookActionText: "生成全书",
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
  },

  async onShow() {
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
      this.setData({ needsLogin: false, projectId, projectTitle });
      if (!projectId) {
        this.applyPlan(null);
        this.applySourceCoverage(null);
        this.applyBookOutput(null);
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
      const coverage = await this.loadSourceCoverage(projectId);
      this.applySourceCoverage(coverage);
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
    } catch (error) {
      if (error instanceof ApiError && error.statusCode === 404) {
        this.applyPlan(null);
        this.applyBookOutput(null);
        return;
      }
      throw error;
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
    this.setData(bookOutputData(output));
  },

  applySourceCoverage(coverage) {
    this.setData(sourceCoverageData(this.data.plan, coverage));
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
    await this.generateChapterDraftById(chapterId, "章节稿已生成");
  },

  async generateChapterDraftById(chapterId, toastTitle = "章节稿已生成") {
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
      const draft = await generateMemoirBookChapterDraft(this.data.projectId, chapterId, {
        force_regenerate: forceRegenerate,
      });
      this.patchDraft(chapterId, draft, { clearPendingSources: true });
      wx.showToast({ title: toastTitle, icon: "success" });
      return true;
    } catch (error) {
      this.patchChapter(chapterId, {
        draftError: error instanceof Error ? error.message : "章节稿生成失败",
      });
      return false;
    } finally {
      this.patchChapter(chapterId, { draftBusy: false });
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
      this.patchDraft(chapterId, draft, { clearPendingSources: true });
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
    const drafted = await this.generateChapterDraftById(chapterId, "合并正文已生成");
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
    try {
      const output = await generateMemoirBookOutput(this.data.projectId, {
        force_regenerate: Boolean(this.data.bookOutput?.latest_version),
      });
      this.applyBookOutput(output);
      wx.showToast({ title: "全书已生成", icon: "success" });
    } catch (error) {
      if (isLoginError(error)) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "全书生成失败" });
    } finally {
      this.setData({ generatingBook: false });
    }
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
    this.playToken += 1;
    const token = this.playToken;
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
    this.stopPlayback();
    this.playToken += 1;
    const token = this.playToken;
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
    this.playToken += 1;
    const token = this.playToken;
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
      bookVoiceStreamingClient?.stopRecording();
      this.setData({ bookVoiceAssistantStatusText: "正在保存录音" });
      return;
    }
    if (this.data.bookVoiceAssistantBusy) {
      return;
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
    const connected = bookVoiceStreamingClient ? true : await this.connectBookVoiceCommandGateway();
    if (!connected || !bookVoiceStreamingClient) {
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
    const started = bookVoiceStreamingClient.startRecording({
      noVoiceActivityTimeoutMs: autoTriggered ? BOOK_VOICE_AUTO_LISTEN_NO_VOICE_MS : BOOK_VOICE_NO_VOICE_MS,
    });
    if (!started) {
      this.setData({
        bookVoiceAssistantListening: false,
        bookVoiceAssistantBusy: false,
        bookVoiceAssistantStatusText: "实时识别未就绪",
        error: "语音未就绪，请重试",
      });
      bookVoiceStreamingClient?.close({ force: true });
      bookVoiceStreamingClient = null;
    }
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
    let client: MemoirStreamingAsrClient | null = null;
    const isCurrentClient = () => Boolean(client && bookVoiceStreamingClient === client);
    try {
      bookVoiceStreamingClient?.close({ force: true });
      client = new MemoirStreamingAsrClient({
        voiceSessionId: `book-voice-command-${this.data.projectId}`,
        gatewayUrl: bookVoiceCommandGatewayUrl(this.data.projectId),
        guidanceMode: "free_narration",
        autoStopSilenceMs: BOOK_VOICE_AUTO_STOP_SILENCE_MS,
        minAutoStopRecordingMs: BOOK_VOICE_MIN_RECORDING_MS,
        callbacks: {
          onStatus: (status) => {
            if (isCurrentClient()) {
              this.handleBookVoiceStatus(status);
            }
          },
          onCaptureStats: (stats) => {
            if (isCurrentClient()) {
              this.handleBookVoiceCaptureStats(stats);
            }
          },
          onEvent: (event) => {
            if (isCurrentClient()) {
              this.handleBookVoiceGatewayEvent(event);
            }
          },
          onPartial: (text) => {
            if (isCurrentClient()) {
              this.handleBookVoicePartial(text);
            }
          },
          onFinal: (event) => {
            if (isCurrentClient()) {
              return this.handleBookVoiceFinal(event);
            }
            return undefined;
          },
          onError: (message, error) => {
            if (isCurrentClient()) {
              this.handleBookVoiceError(message, error);
            }
          },
        },
      });
      bookVoiceStreamingClient = client;
      await client.connect();
      this.setData({
        bookVoiceAssistantStatusText: "实时识别已连接",
        error: "",
      });
      return true;
    } catch (error) {
      if (client && bookVoiceStreamingClient === client) {
        bookVoiceStreamingClient = null;
      }
      client?.close({ force: true });
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
    bookVoiceStreamingClient?.close({ force: true });
    bookVoiceStreamingClient = null;
  },

  applyBookVoiceCommandPreview(preview) {
    const canExecute = canExecuteBookVoiceCommand(preview);
    const responseKind = bookVoiceCommandResponseKind(preview);
    const statusText = isBookVoiceClarificationIntent(preview)
      ? "需要再说明"
      : (isBookVoiceReadIntent(preview)
        ? "已理解，马上朗读"
        : (isBookVoiceConsultationIntent(preview)
          ? "已给建议"
          : (isBookVoiceAnswerIntent(preview) ? "已回答" : "已理解，等待确认")));
    this.setData({
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
      bookVoiceAssistantStatusText: statusText,
      error: "",
    });
  },

  async handleBookVoiceCommandTranscript(text) {
    const pendingPreview = this.data.bookVoiceCommandPreview;
    if (
      pendingPreview
      && this.data.bookVoiceCommandCanExecute
      && pendingPreview.requires_confirmation
      && isBookVoiceCommandConfirmationText(text)
    ) {
      this.setData({
        bookVoiceAssistantStatusText: "收到确认，正在执行",
      });
      await this.playBookVoiceAssistantReply("好的，我按刚才理解的修改。", { autoListenAfterPlayback: false });
      await this.executeBookVoiceCommand({ confirmedByVoice: true, autoListenAfterExecution: true });
      return;
    }

    this.setData({ bookVoiceAssistantStatusText: "正在理解修改需求" });
    const transcriptLines = this.data.bookVoiceTranscriptLines;
    const lastLine = transcriptLines[transcriptLines.length - 1];
    const contextLines = lastLine?.role === "user" && trimText(lastLine.text) === trimText(text)
      ? transcriptLines.slice(0, -1)
      : transcriptLines;
    const conversationContext = bookVoiceConversationContext(contextLines);
    const bookContext = bookVoiceCommandBookContext(this.data.sourceCoverage);
    const request: MemoirBookVoiceCommandParseRequest = {
      text,
      ...(conversationContext.length ? { conversation_context: conversationContext } : {}),
      ...(bookContext ? { book_context: bookContext } : {}),
    };
    const preview = await parseMemoirBookVoiceCommand(this.data.projectId, request);
    this.applyBookVoiceCommandPreview(preview);
    wx.showToast({ title: "已识别", icon: "success" });

    const replyText = bookVoiceAssistantReplyText(preview);
    await this.playBookVoiceAssistantReply(replyText, {
      autoListenAfterPlayback: !isBookVoiceReadIntent(preview),
    });
    if (isBookVoiceReadIntent(preview) && canExecuteBookVoiceCommand(preview)) {
      await this.executeBookVoiceCommand({ confirmedByVoice: true, autoListenAfterExecution: true });
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
      if (preview.intent === "rename_chapter" && operation.chapter_id && operation.new_title) {
        const plan = await this.applyPlanStructureCommand({
          type: "rename_chapter",
          chapter_id: operation.chapter_id,
          title: operation.new_title,
        }, "已按语音改名");
        if (plan && undoSnapshot) {
          this.setData({
            bookVoiceCommandCanExecute: false,
            bookVoiceCommandRequiresConfirmation: false,
            bookVoiceCommandStateText: "已执行",
            bookVoiceCommandActionText: "已执行",
            bookVoiceCommandUndoText: "撤回语音改名",
            bookVoiceCommandUndoSnapshot: undoSnapshot,
            bookVoiceAssistantStatusText: "已执行，可撤回",
          });
          if (options.autoListenAfterExecution) {
            await this.playBookVoiceAssistantReply("已按你说的改好了。还可以继续说新的修改。", {
              autoListenAfterPlayback: true,
            });
          }
        }
        return;
      }
      if (preview.intent === "move_chapter" && operation.chapter_id && operation.direction) {
        const plan = await this.applyPlanStructureCommand({
          type: "move_chapter",
          chapter_id: operation.chapter_id,
          direction: operation.direction === "up" ? "up" : "down",
        }, "已按语音调整顺序");
        if (plan && undoSnapshot) {
          this.setData({
            bookVoiceCommandCanExecute: false,
            bookVoiceCommandRequiresConfirmation: false,
            bookVoiceCommandStateText: "已执行",
            bookVoiceCommandActionText: "已执行",
            bookVoiceCommandUndoText: "撤回语音排序",
            bookVoiceCommandUndoSnapshot: undoSnapshot,
            bookVoiceAssistantStatusText: "已执行，可撤回",
          });
          if (options.autoListenAfterExecution) {
            await this.playBookVoiceAssistantReply("已按你说的调整好了。还可以继续说新的修改。", {
              autoListenAfterPlayback: true,
            });
          }
        }
        return;
      }
      if (preview.intent === "assign_source" && operation.chapter_id && operation.utterance_id) {
        this.setData({ assigningSourceId: operation.utterance_id });
        const plan = await assignMemoirBookSources(
          this.data.projectId,
          sourceAssignmentRequest(operation.chapter_id, operation.utterance_id),
        );
        const [draftsByChapterId, coverage] = await Promise.all([
          this.loadDraftsForPlan(plan),
          this.loadSourceCoverage(this.data.projectId),
        ]);
        this.applyPlan(plan, draftsByChapterId);
        this.applySourceCoverage(coverage);
        this.applyBookOutput(null);
        this.setData({
          bookVoiceCommandCanExecute: false,
          bookVoiceCommandRequiresConfirmation: false,
          bookVoiceCommandStateText: "已执行",
          bookVoiceCommandActionText: "已执行",
          bookVoiceCommandUndoText: "",
          bookVoiceCommandUndoSnapshot: null,
          bookVoiceAssistantStatusText: "已归入章节",
        });
        if (options.autoListenAfterExecution) {
          await this.playBookVoiceAssistantReply("已归入章节。还可以继续说下一条怎么处理。", {
            autoListenAfterPlayback: true,
          });
        }
        return;
      }
      this.setData({ error: "这个语音操作暂时只能预览" });
    } finally {
      if (preview.intent === "assign_source") {
        this.setData({ assigningSourceId: "" });
      }
      this.setData({ bookVoiceCommandExecuting: false });
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
    if (this.data.bookVoiceAssistantListening) {
      bookVoiceStreamingClient?.stopRecording();
      this.setData({
        bookVoiceAssistantConversationActive: false,
        bookVoiceAssistantStatusText: "正在保存录音",
      });
      return;
    }
    bookVoiceStreamingClient?.close({ force: true });
    bookVoiceStreamingClient = null;
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

  async playBookVoiceAssistantReply(text, options: { autoListenAfterPlayback?: boolean } = {}) {
    const replyText = trimText(text);
    if (!replyText || !this.data.projectId) {
      return;
    }
    const target = "book-voice-assistant";
    this.stopPlayback();
    this.playToken += 1;
    const token = this.playToken;
    const lineId = `book-ai-${Date.now()}-${this.data.bookVoiceTranscriptLines.length}`;
    this.appendBookVoiceTranscriptLine({
      id: lineId,
      role: "assistant",
      text: replyText,
    });
    this.setData({
      bookVoiceAssistantExpanded: true,
      ttsBusyTarget: target,
      bookVoiceAssistantBusy: true,
      bookVoiceAssistantReplyText: replyText,
      bookVoiceAssistantSpeakingLineId: lineId,
      bookVoiceAssistantStatusText: "AI 正在回复",
      error: "",
    });
    try {
      const response = await this.requestBookVoiceAssistantAudio(replyText);
      if (token !== this.playToken) {
        return;
      }
      this.setData({
        ttsBusyTarget: "",
        bookVoiceAssistantStatusText: "AI 正在说",
      });
      await this.playAudioFiles(response, target, token, replyText);
      if (token !== this.playToken) {
        return;
      }
      this.setData({ bookVoiceAssistantSpeakingLineId: "" });
      this.setData({ bookVoiceAssistantBusy: false });
      if (options.autoListenAfterPlayback) {
        this.scheduleBookVoiceAssistantAutoListen("我继续听");
      } else if (this.data.bookVoiceCommandPreview && !isBookVoiceReadIntent(this.data.bookVoiceCommandPreview)) {
        this.setData({ bookVoiceAssistantStatusText: "等你确认，或继续说" });
      }
    } catch (error) {
      if (isLoginError(error)) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      if (token === this.playToken) {
        this.setData({
          error: error instanceof Error ? error.message : "AI 语音回复失败",
          bookVoiceAssistantStatusText: "AI 回复失败",
        });
      }
    } finally {
      if (token === this.playToken && this.data.ttsBusyTarget === target) {
        this.setData({ ttsBusyTarget: "" });
      }
      if (token === this.playToken && this.data.bookVoiceAssistantBusy) {
        this.setData({ bookVoiceAssistantBusy: false });
      }
      if (token === this.playToken && this.data.bookVoiceAssistantSpeakingLineId === lineId) {
        this.setData({ bookVoiceAssistantSpeakingLineId: "" });
      }
    }
  },

  async requestBookVoiceAssistantAudio(text) {
    const settings = readBookTTSSettings();
    return createMemoirBookVoiceAssistantTTS(this.data.projectId, {
      text,
      ...(settings.voice ? { voice: settings.voice } : {}),
      ...(settings.speedRatio === 1 ? {} : { speed_ratio: settings.speedRatio }),
    });
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
    stopBookPlayback(this);
    if (this.data.bookVoiceAssistantSpeakingLineId) {
      this.setData({ bookVoiceAssistantSpeakingLineId: "" });
    }
  },

  onHide() {
    this.clearBookVoiceAssistantAutoListenTimer();
    bookVoiceStreamingClient?.close({ force: true });
    bookVoiceStreamingClient = null;
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

  patchDraft(chapterId, draft, options) {
    const chapter = this.data.chapters.find((candidate) => candidate.id === chapterId);
    const metadata = options?.clearPendingSources
      ? clearSourceChangeMetadata(chapter?.metadata_json)
      : (chapter?.metadata_json || {});
    const assignedPendingCount = sourceSupplementCount(metadata, draft);
    const assignedRemovedCount = removedSourceCount(metadata, draft);
    const content = latestDraftContent(draft);
    this.patchChapter(chapterId, {
      metadata_json: metadata,
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
