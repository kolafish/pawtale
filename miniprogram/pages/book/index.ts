import type {
  MemoirBookAudioResponse,
  MemoirBookChapterDraft,
  MemoirBookOutput,
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
import { definePage } from "../../services/page-definition";
import { accessibilityPageClass } from "../../services/accessibility-settings";
import { trackAuditAction } from "../../services/audit-tracker";
import type { BookChapterView, BookHomeData, BookVoiceCommandUndoSnapshot } from "./book-view-model";
import { getMemoirIdentity } from "../../services/auth";
import { memoirProjectProfile } from "../../services/memoir-project-profile";

import {
  acknowledgeMemoirAutomationEvent,
  acknowledgeDismissibleMemoirAutomationEvents,
  applyMemoirBookPlanStructureCommand,
  assignMemoirBookSources,
  confirmMemoirBookPlan,
  createMemoirBookChapterDraftTTS,
  createMemoirBookPlanOutlineTTS,
  bookVoiceCommandGatewayUrl,
  bookVoiceAssistantTurnStreamUrl,
  assembleMemoirBookOutput,
  executeMemoirBookVoiceCommand,
  enqueueMemoirBookChapterSourceRefresh,
  generateMemoirBookChapterTitle,
  generateMemoirBookPlan,
  getMemoirBookChapterDraft,
  getMemoirBookOutput,
  getMemoirBookPlan,
  getMemoirBookSourceCoverage,
  listMemoirImageAssets,
  listMemoirAutomationEvents,
  updateMemoirBookPlan,
  updateMemoirBookOutput,
} from "../../services/memoir-voice-api";
import { ApiError } from "../../services/request";
import {
  isAuthError,
  routeAuthError,
  switchToHome,
  switchToMe,
} from "../../services/navigation";
import {
  datasetChoice,
  datasetString,
  pickerIndex,
  type PageDatasetEvent,
  type PagePickerEvent,
  type PageTextInputEvent,
} from "../../services/page-input";
import { sourceAssignmentRequest } from "../../services/memoir-book-source-actions";
import {
  classifyVoiceClientConnectionError,
  type MemoirCaptureStats,
  type VoiceChatConnectionStatus,
} from "../../services/memoir-streaming-asr";
import { readBookTTSSettings } from "../../services/tts-settings";
import { AudioPlaybackController } from "../../services/audio-playback";
import {
  VoiceAssistantTTSProgressRuntime,
  type VoiceAssistantTTSProgressPatch,
} from "../../services/voice-assistant-tts-progress";
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
import {
  bookShareAppMessage,
  enableMiniProgramShareMenu,
} from "../../services/share";
import { ensureRecorderPrivacyAuthorization } from "../../services/wechat-privacy";
import {
  applyBookTTSProgressPatch as applyBookTTSPlaybackProgressPatch,
  playBookAudioFiles,
  stopBookPlayback,
  stopBookTTSProgressTimer as stopBookTTSPlaybackProgressTimer,
} from "./book-tts-player";
import {
  CHAPTER_MOVE_DIRECTIONS,
  bookChapterDetailUrl,
  bookExportPageUrl,
  chapterMoveCommand,
  findBookChapter,
  removeLocalChapterPatch,
  splitSelectedChapterSourcesPlan,
  toggleBookChapterSourcesPatch,
  toggleBookSplitSourcePatch,
} from "./book-page-plan-helpers";
import {
  NEW_CHAPTER_PICKER_VALUE,
  bookHomeOutputData,
  buildChapterViews,
  chapterCollectionData,
  guestBookData,
  latestDraftContent,
  planStage,
  sourceCoverageData,
  stageText,
  toPlanInput,
  toPlanUpdateInput,
  trimText,
  bookOutlineNarrationText,
  buildAutomationEventViews,
  mergeAutomationEvent,
  pendingBookGenerationEvents,
  pendingChapterDraftGenerationIds,
} from "./book-view-model";
import {
  bookGenerationSettings,
} from "./book-design-model";
import {
  bookVoiceAssistantHint,
  bookVoiceAssistantStatusText,
  bookVoiceCommandResetPatch,
  bookVoiceIdlePatch,
  bookVoiceListeningStartPatch,
  bookVoiceCommandParseRequest,
  bookVoiceCommandPreviewPatch,
  clearBookVoiceCommandPreviewPatch,
  bookVoiceCommandAssigningSourceId,
  bookVoiceCommandExecutionMode,
  canExecuteBookVoiceCommand,
  executedVoiceCommandStatusText,
  executedVoiceCommandUndoText,
  isBookVoiceCommandConfirmationText,
  normalizeBookVoiceAsrText,
  shouldReloadBookOutputAfterVoiceCommand,
} from "./book-voice-assistant-view-model";
export {
  bookChapterDetailUrl,
  bookExportPageUrl,
} from "./book-page-plan-helpers";
export {
  bookChapterSourceSummary,
  bookOutlineNarrationText,
  bookProgressText,
  bookSourceCoverageNotice,
  buildAutomationEventViews,
  buildChapterPickerOptions,
  buildChapterViews,
  buildSourceActionPickerOptions,
  buildUncoveredSourceViews,
} from "./book-view-model";
export {
  canExecuteBookVoiceCommand,
} from "./book-voice-assistant-view-model";

interface BookDataset {
  id?: string;
  eventIds?: string;
  action?: string;
  direction?: string;
  utteranceId?: string;
  view?: string;
}

type DatasetEvent = PageDatasetEvent<BookDataset>;
type TextInputEvent = PageTextInputEvent<BookDataset>;
type PickerEvent = PagePickerEvent<BookDataset>;

const BOOK_GENERATION_EVENT_POLL_INTERVAL_MS = 3000;
const BOOK_GENERATION_EVENT_POLL_MAX_ATTEMPTS = 120;

function defaultBookTitle(planTitle: string, projectTitle: string): string {
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

type BookWorkspaceView = "chapters" | "tools";

type BookPageData = BookHomeData & {
  pageClass: string;
  bookWorkspaceView: BookWorkspaceView;
  sourceCoverageExpanded: boolean;
};

definePage<BookPageData>()({
  audioPlayback: new AudioPlaybackController(),
  bookVoiceStreaming: new StreamingVoiceController(),
  bookVoiceAssistantTurnRuntime: null as VoiceAssistantTurnRuntime | null,
  ttsProgress: new VoiceAssistantTTSProgressRuntime(),
  playToken: 0,
  bookVoiceAutoListenTimer: null as number | null,
  bookGenerationPollTimer: null as number | null,
  bookGenerationPollAttempts: 0,
  automationEvents: [] as MemoirAutomationEvent[],

  data: {
    pageClass: "book-page",
    bookWorkspaceView: "chapters",
    sourceCoverageExpanded: false,
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
    chapterStructureEditing: false,
    splitSourceSelections: {},
    confirmedDraftCount: 0,
    totalChapterCount: 0,
    allChaptersConfirmed: false,
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
    ...bookVoiceCommandResetPatch(),
    bookVoiceCommandExecuting: false,
    automationEventItems: [],
    automationEventBusyTarget: "",
    automationDismissibleCount: 0,
    automationEventClearing: false,
    automationEventExpandedTargets: {},
    automationEventLoading: false,
    automationEventError: "",
    imageAssetLoading: false,
    imageAssetTotal: 0,
    imageAssetReadyCount: 0,
    imageAssetIncludedCount: 0,
    imageAssetUnusedCount: 0,
    imageAssetSummaryText: "还没有图片素材",
  },

  async onShow() {
    this.setData({ pageClass: accessibilityPageClass("book-page") });
    enableMiniProgramShareMenu({ includeTimeline: false });
    this.bookGenerationPollAttempts = 0;
    await this.loadBookHome();
  },

  onShareAppMessage() {
    return bookShareAppMessage({
      projectId: this.data.projectId,
      projectTitle: this.data.projectTitle,
      bookTitle: this.data.bookPreviewTitle || this.data.planTitle,
      hasBookContent: Boolean(this.data.bookContent),
    });
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
        this.applyImageAssetSummary(null);
        return;
      }
      await this.loadPlanForProject(projectId);
    } catch (error) {
      if (isAuthError(error)) {
        this.setData(guestBookData());
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "书稿页面加载失败" });
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
      const imageAssets = await this.loadImageAssetSummary(projectId);
      this.applySourceCoverage(coverage);
      this.applyAutomationEvents(automationEvents);
      this.applyImageAssetSummary(imageAssets);
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
      this.applyImageAssetSummary(imageAssets);
    } catch (error) {
      if (error instanceof ApiError && error.statusCode === 404) {
        this.applyPlan(null);
        this.applyBookOutput(null);
        this.applyImageAssetSummary(null);
        return;
      }
      throw error;
    }
  },

  async loadAutomationEvents(projectId: string) {
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

  async loadDraftsForPlan(plan: MemoirBookPlan) {
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

  async loadBookOutput(projectId: string) {
    try {
      return await getMemoirBookOutput(projectId);
    } catch (error) {
      if (error instanceof ApiError && error.statusCode === 404) {
        return null;
      }
      throw error;
    }
  },

  async loadSourceCoverage(projectId: string) {
    try {
      return await getMemoirBookSourceCoverage(projectId);
    } catch (error) {
      if (error instanceof ApiError && error.statusCode === 404) {
        return null;
      }
      throw error;
    }
  },

  async loadImageAssetSummary(projectId: string) {
    this.setData({ imageAssetLoading: true });
    try {
      return await listMemoirImageAssets(projectId);
    } catch (_error) {
      return null;
    } finally {
      this.setData({ imageAssetLoading: false });
    }
  },

  applyImageAssetSummary(response: Awaited<ReturnType<typeof listMemoirImageAssets>> | null) {
    const total = response?.total || 0;
    const included = response?.included_count || 0;
    const unused = response?.unused_count || 0;
    const summary = total
      ? `${total} 张 · ${included} 张已入书 · ${unused} 张待选择`
      : "生成分享视频背景后，会自动收集到这里";
    this.setData({
      imageAssetTotal: total,
      imageAssetReadyCount: response?.ready_count || 0,
      imageAssetIncludedCount: included,
      imageAssetUnusedCount: unused,
      imageAssetSummaryText: summary,
    });
  },

  applyPlan(plan: MemoirBookPlan | null, draftsByChapterId: Record<string, MemoirBookChapterDraft | null> = {}) {
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
      chapterStructureEditing: stage === "drafting" ? this.data.chapterStructureEditing : false,
      bookVoiceAssistantHint: bookVoiceAssistantHint(stage, chapters.length),
      ...(shouldResetVoiceStatus ? bookVoiceIdlePatch(stage, {
        collapse: true,
        clearReply: true,
        clearTranscript: true,
      }) : {}),
      ...chapterCollectionData(chapters, stage, this.data.sourceCoverage, this.data.splitSourceSelections),
      ...sourceCoverageData(plan, this.data.sourceCoverage),
      ...(stage === "drafting" ? {} : bookHomeOutputData(null)),
    });
  },

  applyBookOutput(output: MemoirBookOutput | null) {
    this.setData(bookHomeOutputData(output));
  },

  openImageAssetsPage() {
    if (!this.data.projectId) {
      this.setData({ error: "请先创建回忆录" });
      return;
    }
    wx.navigateTo({
      url: `/pages/image-assets/index?projectId=${encodeURIComponent(this.data.projectId)}`,
    });
  },

  applySourceCoverage(coverage: MemoirBookSourceCoverage | null) {
    this.setData(sourceCoverageData(this.data.plan, coverage));
  },

  applyAutomationEvents(events: MemoirAutomationEvent[]) {
    this.automationEvents = events;
    const pendingChapterIds = pendingChapterDraftGenerationIds(events);
    const chapters = this.data.chapters.map((chapter) => ({
      ...chapter,
      draftBusy: pendingChapterIds.has(chapter.id),
    }));
    this.setData({
      ...chapterCollectionData(
        chapters,
        this.data.stage,
        this.data.sourceCoverage,
        this.data.splitSourceSelections,
      ),
      automationEventItems: buildAutomationEventViews(
        events,
        this.data.automationEventBusyTarget,
        this.data.automationEventExpandedTargets,
      ),
      automationDismissibleCount: events.filter((event) => (
        event.status === "completed" || event.status === "failed"
      )).length,
    });
    this.scheduleBookGenerationEventPoll();
  },

  toggleAutomationEventDetails(event: DatasetEvent) {
    const eventId = datasetString(event, "id");
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

  async acknowledgeAutomationEvent(event: DatasetEvent) {
    const eventId = datasetString(event, "id");
    const eventIdsText = datasetString(event, "eventIds") || eventId;
    const action = datasetString(event, "action") || "acknowledge";
    if (action === "review_source") {
      const utteranceId = datasetString(event, "utteranceId");
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
      const acknowledgedIds = new Set(eventIds);
      this.applyAutomationEvents(
        this.automationEvents.filter((item) => !acknowledgedIds.has(item.id)),
      );
    } catch (error) {
      this.setData({
        automationEventError: error instanceof Error ? error.message : "自动处理记录确认失败",
      });
    } finally {
      this.setData({ automationEventBusyTarget: "" });
    }
  },

  async acknowledgeDismissibleAutomationEvents() {
    if (
      !this.data.projectId
      || !this.data.automationDismissibleCount
      || this.data.automationEventClearing
    ) {
      return;
    }
    this.setData({ automationEventClearing: true, automationEventError: "" });
    try {
      await acknowledgeDismissibleMemoirAutomationEvents(this.data.projectId);
      this.applyAutomationEvents(
        this.automationEvents.filter((event) => (
          event.status === "pending" || event.status === "needs_review"
        )),
      );
      wx.showToast({ title: "已收起完成记录", icon: "none" });
    } catch (error) {
      this.setData({
        automationEventError: error instanceof Error ? error.message : "完成记录收起失败",
      });
    } finally {
      this.setData({ automationEventClearing: false });
    }
  },

  async applyUpdatedPlan(plan: MemoirBookPlan, toastTitle: string) {
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
      if (routeAuthError(error, "me")) {
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
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "章节确认失败" });
    } finally {
      this.setData({ confirmingPlan: false });
    }
  },

  changePlanTitle(event: TextInputEvent) {
    this.setData({ planTitle: event.detail.value });
  },

  openChapterEditor(event: DatasetEvent) {
    const chapterId = datasetString(event, "id");
    if (!chapterId || !this.data.projectId) {
      this.setData({ error: "缺少章节信息" });
      return;
    }
    this.stopPlayback();
    wx.navigateTo({ url: bookChapterDetailUrl(this.data.projectId, chapterId) });
  },

  async runChapterPrimaryAction(event: DatasetEvent) {
    const chapterId = datasetString(event, "id");
    const chapter = findBookChapter(this.data.chapters, chapterId);
    if (!chapter || !this.data.projectId) {
      this.setData({ error: "缺少章节信息" });
      return;
    }
    if (!chapter.pendingSourceCount || chapter.removedSourceCount) {
      this.openChapterEditor(event);
      return;
    }
    if (chapter.draftBusy) {
      return;
    }
    this.patchChapter(chapterId, { draftBusy: true, draftError: "" });
    try {
      const generationEvent = await enqueueMemoirBookChapterSourceRefresh(
        this.data.projectId,
        chapterId,
      );
      this.applyAutomationEvents(
        mergeAutomationEvent(this.automationEvents, generationEvent),
      );
      wx.showToast({ title: "正在补写新增讲述", icon: "none" });
    } catch (error) {
      this.patchChapter(chapterId, {
        draftBusy: false,
        draftError: error instanceof Error ? error.message : "新增讲述补写失败",
      });
    }
  },

  toggleChapterStructureEditing() {
    if (this.data.stage !== "drafting" || !this.data.plan || this.data.updatingPlanStructure) {
      return;
    }
    this.stopPlayback();
    this.setData({
      chapterStructureEditing: !this.data.chapterStructureEditing,
      error: "",
    });
  },

  openChapterMoreActions(event: DatasetEvent) {
    const chapterId = datasetString(event, "id");
    const chapter = findBookChapter(this.data.chapters, chapterId);
    if (!chapter || !this.data.projectId) {
      this.setData({ error: "缺少章节信息" });
      return;
    }
    const actions: Array<{ label: string; run: () => void | Promise<void> }> = [];
    actions.push({
      label: "整理本章",
      run: () => wx.navigateTo({ url: bookChapterDetailUrl(this.data.projectId, chapterId) }),
    });
    const canShowAudioAction = Boolean(chapter.draft?.latest_version)
      && (!this.data.ttsBusyTarget || this.data.playingTarget === chapter.ttsTarget);
    if (canShowAudioAction) {
      actions.push({
        label: this.data.playingTarget === chapter.ttsTarget ? "停止朗读" : "朗读",
        run: () => this.playChapterAudioById(chapterId, { showPreparingToast: true }),
      });
    }
    const nextIncludeInBook = chapter.include_in_book === false;
    actions.push({
      label: nextIncludeInBook ? "恢复收入本书" : "不收入本书",
      run: () => this.setChapterBookInclusion(chapterId, nextIncludeInBook),
    });
    wx.showActionSheet({
      itemList: actions.map((item) => item.label),
      success: (result) => {
        const action = actions[result.tapIndex];
        if (action) {
          void action.run();
        }
      },
    });
  },

  openBookExportPage(event: DatasetEvent) {
    if (!this.data.projectId) {
      return;
    }
    if (!this.data.bookContent) {
      this.setData({ error: "请先生成书稿" });
      return;
    }
    const action = datasetString(event, "action") || "preview";
    wx.navigateTo({ url: bookExportPageUrl(this.data.projectId, action) });
  },

  changeBookWorkspaceView(event: DatasetEvent) {
    const view = datasetString(event, "view");
    if (view !== "chapters" && view !== "tools") {
      return;
    }
    this.setData({ bookWorkspaceView: view });
  },

  toggleSourceCoverageExpanded() {
    this.setData({ sourceCoverageExpanded: !this.data.sourceCoverageExpanded });
  },

  changeChapterTitle(event: TextInputEvent) {
    const id = datasetString(event, "id");
    const chapter = findBookChapter(this.data.chapters, id);
    this.patchChapter(id, {
      editingTitle: event.detail.value,
      titleChanged: chapter ? trimText(event.detail.value) !== trimText(chapter.title) : false,
    });
  },

  async generateChapterTitle(event: DatasetEvent) {
    const chapterId = datasetString(event, "id");
    await this.generateChapterTitleById(chapterId, "已重新起名");
  },

  async generateChapterTitleById(chapterId: string, toastTitle: string = "已重新起名") {
    const chapter = findBookChapter(this.data.chapters, chapterId);
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
      if (routeAuthError(error, "me")) {
        return false;
      }
      const message = error instanceof Error ? error.message : "小余起名失败";
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

  async removeChapter(event: DatasetEvent) {
    const id = datasetString(event, "id");
    if (!id || this.data.confirmingPlan || this.data.updatingPlanStructure || this.data.chapters.length <= 1) {
      return;
    }
    if (this.data.projectId && this.data.plan) {
      await this.applyPlanStructureCommand({ type: "delete_chapter", chapter_id: id }, "章节已删除");
      return;
    }
    const patch = removeLocalChapterPatch(this.data, id);
    if (patch) {
      this.setData(patch);
    }
  },

  async setChapterBookInclusion(chapterId: string, includeInBook: boolean) {
    if (!chapterId || this.data.updatingPlanStructure) {
      return;
    }
    await this.applyPlanStructureCommand(
      {
        type: "set_chapter_book_inclusion",
        chapter_id: chapterId,
        include_in_book: includeInBook,
      },
      includeInBook ? "已恢复收入本书" : "已设为不收入本书，请重新生成全书",
    );
  },

  async moveChapter(event: DatasetEvent) {
    const chapterId = datasetString(event, "id");
    const direction = datasetChoice(event, "direction", CHAPTER_MOVE_DIRECTIONS, "down");
    if (!chapterId || this.data.updatingPlanStructure) {
      return;
    }
    const command = chapterMoveCommand(this.data.chapters, chapterId, direction);
    if (!command) {
      return;
    }
    await this.applyPlanStructureCommand(command, "章节顺序已调整");
  },

  async mergeChapterInto(event: PickerEvent) {
    const sourceChapterId = datasetString(event, "id");
    const optionIndex = pickerIndex(event);
    const sourceChapter = findBookChapter(this.data.chapters, sourceChapterId);
    const option = sourceChapter?.mergeTargetOptions[optionIndex];
    const targetChapter = option
      ? findBookChapter(this.data.chapters, option.value)
      : null;
    if (!sourceChapter || !targetChapter || this.data.updatingPlanStructure) {
      return;
    }
    const confirmed = await new Promise<boolean>((resolve) => {
      wx.showModal({
        title: "合并章节",
        content: `把「${sourceChapter.title}」合并到「${targetChapter.title}」，本章素材会保留在合并后的章节中。`,
        confirmText: "合并",
        confirmColor: "#a64b3e",
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
      await this.generateChapterTitleById(targetChapter.id, "已重写章名");
      this.patchChapter(targetChapter.id, {
        draftError: "章节已合并，请进入章节工作台重新整理正文",
      });
    } else if (plan) {
      await this.generateChapterTitleById(targetChapter.id, "已重写章名");
    }
  },

  toggleSplitSource(event: DatasetEvent) {
    const chapterId = datasetString(event, "id");
    const utteranceId = datasetString(event, "utteranceId");
    if (this.data.updatingPlanStructure) {
      return;
    }
    const patch = toggleBookSplitSourcePatch(this.data, chapterId, utteranceId);
    if (patch) {
      this.setData(patch);
    }
  },

  async splitSelectedChapterSources(event: DatasetEvent) {
    const chapterId = datasetString(event, "id");
    if (this.data.updatingPlanStructure) {
      return;
    }
    const plan = splitSelectedChapterSourcesPlan(this.data, chapterId);
    if (!plan) {
      return;
    }
    if (plan.error) {
      this.setData({ error: plan.error });
      return;
    }
    if (plan.patch) {
      this.setData(plan.patch);
    }
    if (plan.command) {
      await this.applyPlanStructureCommand(plan.command, "章节已拆分");
    }
  },

  toggleChapterSources(event: DatasetEvent) {
    const chapterId = datasetString(event, "id");
    this.setData(toggleBookChapterSourcesPatch(this.data, chapterId));
  },

  async assignUncoveredSource(event: PickerEvent) {
    const utteranceId = datasetString(event, "id");
    const optionIndex = pickerIndex(event);
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
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "归入章节失败" });
    } finally {
      this.setData({ assigningSourceId: "" });
    }
  },

  async addUncoveredSourceAsChapterById(utteranceId: string) {
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

  async applyPlanStructureCommand(command: MemoirBookPlanStructureCommandRequest, toastTitle: string) {
    if (!this.data.projectId || !this.data.plan || this.data.updatingPlanStructure) {
      return null;
    }
    this.stopPlayback();
    this.setData({ updatingPlanStructure: true, error: "" });
    try {
      const plan = await applyMemoirBookPlanStructureCommand(this.data.projectId, command);
      await this.applyUpdatedPlan(plan, toastTitle);
      return plan;
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return null;
      }
      this.setData({ error: error instanceof Error ? error.message : "目录调整失败" });
      return null;
    } finally {
      this.setData({ updatingPlanStructure: false });
    }
  },

  async updatePlanStructure(chapters: MemoirBookPlanChapterUpdateInput[], toastTitle: string) {
    if (!this.data.projectId || !this.data.plan || this.data.updatingPlanStructure) {
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
      if (routeAuthError(error, "me")) {
        return null;
      }
      this.setData({ error: error instanceof Error ? error.message : "目录调整失败" });
      return null;
    } finally {
      this.setData({ updatingPlanStructure: false });
    }
  },

  async generateBook() {
    if (!this.data.projectId || this.data.generatingBook || this.data.updatingPlanStructure) {
      return;
    }
    if (!this.data.allChaptersConfirmed) {
      this.setData({ error: "请先确认所有收入本书的章节正文" });
      return;
    }
    this.stopPlayback();
    this.setData({ generatingBook: true, error: "" });
    try {
      const settings = bookGenerationSettings(this.data.bookOutput, {
        title: defaultBookTitle(this.data.planTitle, this.data.projectTitle),
        narratorName: this.data.projectNarratorName,
      });
      await assembleMemoirBookOutput(this.data.projectId);
      const output = await updateMemoirBookOutput(this.data.projectId, settings);
      this.applyBookOutput(output);
      trackAuditAction(
        "book.output_generate",
        { source: "confirmed_chapter_drafts" },
        { projectId: this.data.projectId },
      );
      wx.showToast({ title: "全书稿已更新", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "书稿整理提交失败" });
    } finally {
      this.setData({ generatingBook: false });
    }
  },

  async playChapterAudioById(chapterId: string, options: { autoListenAfterPlayback?: boolean; showPreparingToast?: boolean } = {}) {
    const chapter = findBookChapter(this.data.chapters, chapterId);
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
    if (options.showPreparingToast) {
      wx.showToast({ title: "正在准备朗读", icon: "none" });
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
      await this.playAudioFiles(response, target, token, trimText(latestDraftContent(chapter.draft)));
      if (token === this.playToken && options.autoListenAfterPlayback) {
        this.scheduleBookVoiceAssistantAutoListen("这一章读完了，我继续听");
      }
    } catch (error) {
      if (routeAuthError(error, "login")) {
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
      if (routeAuthError(error, "login")) {
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
    this.setData(bookVoiceListeningStartPatch(autoTriggered, keepCurrentPreview));
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

  async ensureBookVoiceAuthorization(autoTriggered: boolean = false) {
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

  appendBookVoiceTranscriptLine(line: VoiceChatTranscriptLine) {
    const lines = this.data.bookVoiceTranscriptLines || [];
    const index = lines.findIndex((item) => item.id === line.id);
    const nextLines = index >= 0
      ? lines.map((item, itemIndex) => (itemIndex === index ? { ...item, ...line } : item))
      : [...lines, line];
    this.setData({ bookVoiceTranscriptLines: nextLines });
  },

  handleBookVoiceStatus(status: VoiceChatConnectionStatus) {
    const statusTextMap: Record<VoiceChatConnectionStatus, string> = {
      idle: "实时识别未连接",
      connecting: "正在连接实时识别",
      ready: "我可以听你说",
      listening: "我在听，请说想怎么改",
      stopping: "正在收尾识别",
      closed: "语音对话已暂停",
      error: "实时识别异常",
    };
    const patch: Partial<BookHomeData> = {
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

  handleBookVoiceCaptureStats(stats: MemoirCaptureStats) {
    const labels: Record<MemoirCaptureStats["state"], string> = {
      idle: "麦克风未开始",
      opening: "正在打开麦克风",
      recording: "录音中",
      stopping: "正在收尾",
      error: "录音异常",
    };
    const patch: Partial<BookHomeData> = {
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

  handleBookVoiceGatewayEvent(event: VoiceGatewayServerEvent) {
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

  handleBookVoicePartial(text: string) {
    const displayText = normalizeBookVoiceAsrText(text || "");
    this.setData({
      bookVoiceAssistantExpanded: true,
      bookVoiceLiveText: displayText,
      bookVoiceAssistantStatusText: displayText ? "正在实时识别" : "我在听，请说想怎么改",
      error: "",
    });
  },

  async handleBookVoiceFinal(event: VoiceGatewayServerEvent) {
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
      if (routeAuthError(error, "login")) {
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

  handleBookVoiceError(message: string, error?: VoiceChatErrorInfo) {
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

  applyBookVoiceCommandPreview(preview: MemoirBookVoiceCommandParseResponse) {
    this.setData(bookVoiceCommandPreviewPatch(preview));
  },

  async handleBookVoiceCommandTranscript(text: string) {
    await this.runBookVoiceAssistantTurn(text);
  },

  async runBookVoiceAssistantTurn(text: string) {
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
    this.ttsProgress.resetStreamingAudio();
    this.setData({
      bookVoiceAssistantBusy: true,
      ttsBusyTarget: "book-voice-assistant",
      bookVoiceAssistantStatusText: "正在理解修改需求",
      error: "",
    });
    const settings = readBookTTSSettings();
    const runtime = new VoiceAssistantTurnRuntime(`book-voice-command-${this.data.projectId}`, {
      onStarted: () => {
        this.setData({ bookVoiceAssistantStatusText: "小余正在回复" });
      },
      onCommandPreview: (event) => {
        this.handleBookVoiceCommandPreview(event);
      },
      onTextDelta: (event) => {
        this.applyBookVoiceAssistantTurnText(event.text);
      },
      onAudioFrame: () => {
        this.setData({
          ttsBusyTarget: "",
          bookTTSActiveTarget: "book-voice-assistant",
          bookTTSActiveText: this.data.bookVoiceAssistantReplyText,
          bookTTSRemainingText: this.data.bookVoiceAssistantReplyText,
          bookVoiceAssistantStatusText: "小余正在说",
        });
      },
      onAudioFrameScheduled: (event) => {
        this.markBookVoiceStreamingAudioFrameScheduled(event);
        this.updateBookVoiceStreamingTTSProgress();
        this.startBookVoiceStreamingTTSProgressTimer(token);
      },
      onAudioDrained: () => {
        void this.finishBookVoiceAssistantAudioFrames(token, this.data.bookVoiceAssistantReplyText);
      },
      onPlaybackError: (error) => {
        this.setData({
          error: error.message,
          bookVoiceAssistantStatusText: "小余语音播放失败",
        });
      },
    }, {
      isActive: () => token === this.playToken,
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
      if (routeAuthError(error, "login")) {
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

  bookVoiceAssistantTurnPayload(text: string) {
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

  handleBookVoiceCommandPreview(event: VoiceAssistantTurnCommandPreviewEvent) {
    this.applyBookVoiceCommandPreview(event.command_preview);
    wx.showToast({ title: "已识别", icon: "success" });
  },

  applyBookVoiceAssistantTurnText(text: string) {
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
      bookVoiceAssistantStatusText: "小余正在回复",
      error: "",
    });
  },

  async applyCompletedBookVoiceAssistantTurn(
    event: VoiceAssistantTurnCompletedEvent,
    undoSnapshot: BookVoiceCommandUndoSnapshot | null,
    options: { autoListenAfterTurn?: boolean } = {},
  ) {
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
    const previewExecutionMode = preview ? bookVoiceCommandExecutionMode(preview) : null;
    const isReadCommand = previewExecutionMode === "read_outline" || previewExecutionMode === "read_chapter";
    if (isReadCommand && canExecuteBookVoiceCommand(preview)) {
      await this.executeBookVoiceCommand({ confirmedByVoice: true, autoListenAfterExecution: autoListenAfterTurn });
      return;
    }
    if (autoListenAfterTurn && preview && !isReadCommand) {
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
    const executionMode = bookVoiceCommandExecutionMode(preview);
    if (executionMode === "read_outline") {
      await this.playBookOutlineAudio({ autoListenAfterPlayback: options.autoListenAfterExecution });
      return;
    }
    if (executionMode === "read_chapter" && operation.chapter_id) {
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
    const assigningSourceId = bookVoiceCommandAssigningSourceId(preview);
    this.setData({ bookVoiceCommandExecuting: true, error: "" });
    try {
      if (assigningSourceId) {
        this.setData({ assigningSourceId });
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
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "语音修改执行失败",
        bookVoiceAssistantStatusText: "执行失败",
      });
    } finally {
      if (assigningSourceId) {
        this.setData({ assigningSourceId: "" });
      }
      this.setData({ bookVoiceCommandExecuting: false });
    }
  },

  async applyBookVoiceCommandExecution(
    response: MemoirBookVoiceCommandExecuteResponse,
    undoSnapshot: BookVoiceCommandUndoSnapshot | null,
    options: { autoListenAfterExecution?: boolean } = {},
  ) {
    const [draftsByChapterId, output] = await Promise.all([
      this.loadDraftsForPlan(response.plan),
      shouldReloadBookOutputAfterVoiceCommand(response.intent)
        ? this.loadBookOutput(this.data.projectId)
        : Promise.resolve(null),
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
    this.setData(clearBookVoiceCommandPreviewPatch(this.data.stage));
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
    this.setData(bookVoiceIdlePatch(this.data.stage, {
      clearBusy: true,
      clearReply: true,
    }));
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

  scheduleBookVoiceAssistantAutoListen(statusText: string = "我继续听") {
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
      bookVoiceAssistantStatusText: "正在听您说",
      bookTTSActiveTarget: "",
      bookTTSActiveText: "",
      bookTTSSpokenText: "",
      bookTTSCurrentText: "",
      bookTTSRemainingText: "",
      bookTTSProgressPercent: 0,
    });
  },

  async finishBookVoiceAssistantAudioFrames(token: number, activeText: string) {
    if (token !== this.playToken) {
      return;
    }
    this.stopBookTTSProgressTimer();
    const durationSeconds = this.ttsProgress.streamingDurationSeconds() || this.data.bookTTSDurationSeconds;
    this.setData({
      bookTTSSpokenText: activeText,
      bookTTSCurrentText: "",
      bookTTSRemainingText: "",
      bookTTSProgressPercent: 100,
      bookTTSDurationSeconds: Math.max(this.data.bookTTSDurationSeconds, durationSeconds),
    });
  },

  markBookVoiceStreamingAudioFrameScheduled(event: StreamingSpeechPlaybackFrameEvent) {
    this.ttsProgress.markStreamingAudioFrameScheduled(event);
    this.setData({
      bookTTSDurationSeconds: Math.max(
        this.data.bookTTSDurationSeconds,
        this.ttsProgress.streamingDurationSeconds(),
      ),
    });
  },

  updateBookVoiceStreamingTTSProgress() {
    this.ttsProgress.updateStreamingProgress(
      this.data.bookTTSActiveText,
      this.bookVoiceAssistantTurnRuntime?.stats()?.currentTimeSeconds,
      (patch) => this.applyBookTTSProgressPatch(patch),
      BOOK_VOICE_STREAMING_TTS_PROGRESS_HOLD_SECONDS,
    );
  },

  startBookVoiceStreamingTTSProgressTimer(token: number) {
    this.ttsProgress.startStreamingTimer({
      isActive: () => (
        token === this.playToken
        && Boolean(this.bookVoiceAssistantTurnRuntime)
        && this.data.bookTTSActiveTarget === "book-voice-assistant"
      ),
      currentTimeSeconds: () => this.bookVoiceAssistantTurnRuntime?.stats()?.currentTimeSeconds,
      text: () => this.data.bookTTSActiveText,
      holdSeconds: BOOK_VOICE_STREAMING_TTS_PROGRESS_HOLD_SECONDS,
      applyPatch: (patch) => this.applyBookTTSProgressPatch(patch),
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

  async confirmBookVoiceCommandExecution(content: string, confirmText: string = "执行") {
    return new Promise<boolean>((resolve) => {
      wx.showModal({
        title: "语音修改",
        content,
        confirmText,
        confirmColor: "#a64b3e",
        success: (result) => resolve(Boolean(result.confirm)),
        fail: () => resolve(false),
      });
    });
  },

  async requestBookAudio(target: string) {
    const settings = readBookTTSSettings();
    const request = {
      ...(settings.voice ? { voice: settings.voice } : {}),
      ...(settings.speedRatio === 1 ? {} : { speed_ratio: settings.speedRatio }),
    };
    if (target === "outline") {
      return createMemoirBookPlanOutlineTTS(this.data.projectId, request);
    }
    if (target.startsWith("chapter:")) {
      return createMemoirBookChapterDraftTTS(this.data.projectId, target.slice("chapter:".length), request);
    }
    throw new Error("朗读目标无效");
  },

  async playAudioFiles(response: MemoirBookAudioResponse, target: string, token: number, activeText: string) {
    return playBookAudioFiles(this, response, target, token, activeText);
  },

  applyBookTTSProgressPatch(patch: VoiceAssistantTTSProgressPatch) {
    applyBookTTSPlaybackProgressPatch(this, patch);
  },

  stopBookTTSProgressTimer() {
    stopBookTTSPlaybackProgressTimer(this);
  },

  stopPlayback() {
    this.bookVoiceAssistantTurnRuntime?.close();
    this.bookVoiceAssistantTurnRuntime = null;
    this.ttsProgress.resetStreamingAudio();
    stopBookPlayback(this);
    if (this.data.bookVoiceAssistantSpeakingLineId) {
      this.setData({ bookVoiceAssistantSpeakingLineId: "" });
    }
  },

  onHide() {
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
      this.setData(bookVoiceIdlePatch(this.data.stage, {
        stopListening: true,
        clearBusy: true,
      }));
    }
  },

  onUnload() {
    this.clearBookGenerationEventPoll();
    this.stopBookVoiceConversation();
    this.stopPlayback();
  },

  openTranscripts(event?: DatasetEvent) {
    const utteranceId = event?.currentTarget?.dataset?.id || "";
    if (utteranceId) {
      writeTranscriptNavigationTarget(utteranceId);
    }
    wx.switchTab({ url: "/pages/transcripts/index" });
  },

  openHome() {
    this.stopPlayback();
    switchToHome();
  },

  openLogin() {
    this.stopPlayback();
    switchToMe();
  },

  createProject() {
    this.stopPlayback();
    wx.navigateTo({ url: "/pages/project-edit/index" });
  },

  patchChapter(chapterId: string, patch: Partial<BookChapterView>) {
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

});
