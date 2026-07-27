import type {
  MemoirBookAudioResponse,
  MemoirBookChapterDraft,
  MemoirBookOutput,
  MemoirBookPlan,
  MemoirBookPlanStructureCommandRequest,
  MemoirAutomationEvent,
  MemoirImageAssetListResponse,
} from "../../domain/voice-chat";
import type { MemoirBookSnapshot } from "../../domain/memoir-snapshots";
import { definePage } from "../../services/page-definition";
import { accessibilityPageClass } from "../../services/accessibility-settings";
import { trackAuditAction } from "../../services/audit-tracker";
import type { BookChapterView, BookHomeData } from "./book-view-model";
import { memoirProjectProfile } from "../../services/memoir-project-profile";
import { promptRequiredText } from "../../services/text-prompt";

import {
  acknowledgeMemoirAutomationEvent,
  acknowledgeDismissibleMemoirAutomationEvents,
  applyMemoirBookPlanStructureCommand,
  confirmMemoirBookPlan,
  createMemoirBookChapterDraftTTS,
  createMemoirBookPlanOutlineTTS,
  assembleMemoirBookOutput,
  generateMemoirBookChapterTitle,
  generateMemoirBookPlan,
  getMemoirBookChapterDraft,
  getMemoirBookSnapshot,
  getMemoirBookOutput,
  listMemoirAutomationEvents,
  updateMemoirBookChapterTitle,
  updateMemoirBookOutput,
} from "../../services/memoir-voice-api";
import { ApiError } from "../../services/request";
import {
  isAuthError,
  navigateToLogin,
  routeAuthError,
  switchToHome,
} from "../../services/navigation";
import {
  datasetChoice,
  datasetString,
  pageUrl,
  pickerIndex,
  type PageDatasetEvent,
  type PagePickerEvent,
  type PageTextInputEvent,
} from "../../services/page-input";
import { readBookTTSRequest } from "../../services/tts-settings";
import { AudioPlaybackController } from "../../services/audio-playback";
import {
  VoiceAssistantTTSProgressRuntime,
  type VoiceAssistantTTSProgressPatch,
} from "../../services/voice-assistant-tts-progress";
import { writeTranscriptNavigationTarget } from "../../services/transcript-navigation";
import {
  bookShareAppMessage,
  enableMiniProgramShareMenu,
} from "../../services/share";
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
  bookHomeOutputData,
  buildChapterViews,
  chapterCollectionData,
  guestBookData,
  latestDraftContent,
  planStage,
  stageText,
  toPlanInput,
  trimText,
  bookOutlineNarrationText,
  buildAutomationEventViews,
  pendingBookGenerationEvents,
  pendingChapterDraftGenerationIds,
} from "./book-view-model";
import {
  bookGenerationSettings,
} from "./book-design-model";
import {
  readCachedMemoirBook,
  writeCachedMemoirBook,
} from "../../stores/memoir-read-cache";
export {
  bookChapterDetailUrl,
  bookExportPageUrl,
} from "./book-page-plan-helpers";
export {
  bookChapterSourceSummary,
  bookOutlineNarrationText,
  bookProgressText,
  buildAutomationEventViews,
  buildChapterPickerOptions,
  buildChapterViews,
} from "./book-view-model";

export function bookCompletionPageUrl(projectId: string, projectTitle: string): string {
  return pageUrl("/pages/book-completion/index", {
    projectId,
    projectTitle,
  });
}

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

type BookWorkspaceView = "chapters" | "tools";

type BookPageData = BookHomeData & {
  pageClass: string;
  bookWorkspaceView: BookWorkspaceView;
};

definePage<BookPageData>()({
  audioPlayback: new AudioPlaybackController(),
  ttsProgress: new VoiceAssistantTTSProgressRuntime(),
  playToken: 0,
  bookGenerationPollTimer: null as number | null,
  bookGenerationPollAttempts: 0,
  automationEvents: [] as MemoirAutomationEvent[],
  bookLoadSequence: 0,

  data: {
    pageClass: "book-page",
    bookWorkspaceView: "chapters",
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
    const loadSequence = ++this.bookLoadSequence;
    const cached = readCachedMemoirBook();
    const localInvalidationRevision = cached.localInvalidationRevision || 0;
    const showedCachedData = Boolean(cached.payload);
    if (cached.payload) {
      this.applyBookSnapshot(cached.payload);
    } else {
      this.setData({ loading: true, error: "" });
    }
    try {
      const snapshot = await getMemoirBookSnapshot();
      if (loadSequence !== this.bookLoadSequence) {
        return;
      }
      const currentCache = readCachedMemoirBook();
      if (
        currentCache.status !== "miss"
        && (currentCache.localInvalidationRevision || 0) !== localInvalidationRevision
      ) {
        return;
      }
      writeCachedMemoirBook(snapshot);
      this.applyBookSnapshot(snapshot);
    } catch (error) {
      if (loadSequence !== this.bookLoadSequence) {
        return;
      }
      if (isAuthError(error)) {
        this.setData(guestBookData());
        return;
      }
      if (showedCachedData) {
        console.warn("refresh memoir book snapshot failed", error);
      } else {
        this.setData({ error: error instanceof Error ? error.message : "书稿页面加载失败" });
      }
    } finally {
      if (loadSequence === this.bookLoadSequence) {
        this.setData({ loading: false });
      }
    }
  },

  async loadPlanForProject(projectId: string) {
    const snapshot = await getMemoirBookSnapshot();
    if (snapshot.identity.active_project_id !== projectId) {
      return;
    }
    writeCachedMemoirBook(snapshot);
    this.applyBookSnapshot(snapshot);
  },

  applyBookSnapshot(snapshot: MemoirBookSnapshot) {
    const identity = snapshot.identity;
    const projectId = identity.active_project_id;
    const projectTitle = identity.active_project?.title || "尚未创建回忆录";
    const projectNarratorName = memoirProjectProfile(identity.active_project).narratorName;
    this.setData({
      loading: false,
      error: "",
      needsLogin: false,
      projectId,
      projectTitle,
      projectNarratorName,
    });
    this.applyPlan(snapshot.plan || null, snapshot.drafts_by_chapter_id || {});
    this.applyBookOutput(snapshot.output || null);
    this.applyAutomationEvents(snapshot.automation_events.items || []);
    this.applyImageAssetSummary(snapshot.image_assets);
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

  applyImageAssetSummary(response: MemoirImageAssetListResponse | null) {
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
      ? buildChapterViews(plan, draftsByChapterId, this.data.splitSourceSelections)
      : [];
    const stage = planStage(plan);
    this.setData({
      plan,
      planTitle: plan?.title || `${this.data.projectTitle || "我的回忆录"}章节规划`,
      stage,
      stageText: stageText(stage),
      chapterStructureEditing: stage === "drafting" ? this.data.chapterStructureEditing : false,
      ...chapterCollectionData(chapters, this.data.splitSourceSelections),
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
    const [draftsByChapterId, output] = plan.status === "confirmed"
      ? await Promise.all([
        this.loadDraftsForPlan(plan),
        this.loadBookOutput(plan.project_id),
      ])
      : [
        {},
        null,
      ] as const;
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
      if (routeAuthError(error, "login")) {
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
      if (routeAuthError(error, "login")) {
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
    this.openChapterEditor(event);
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
    if (this.data.titleBusyTarget) {
      return;
    }
    const actions: Array<{ label: string; run: () => unknown }> = [];
    actions.push({
      label: "整理本章",
      run: () => wx.navigateTo({ url: bookChapterDetailUrl(this.data.projectId, chapterId) }),
    });
    actions.push({
      label: "修改章名",
      run: () => this.editChapterTitleById(chapterId),
    });
    actions.push({
      label: "AI 重新起名",
      run: () => this.generateChapterTitleById(chapterId),
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

  openBookCompletion() {
    if (!this.data.projectId || this.data.stage !== "drafting") {
      return;
    }
    this.stopPlayback();
    wx.navigateTo({
      url: bookCompletionPageUrl(
        this.data.projectId,
        this.data.projectTitle,
      ),
    });
  },

  changeBookWorkspaceView(event: DatasetEvent) {
    const view = datasetString(event, "view");
    if (view !== "chapters" && view !== "tools") {
      return;
    }
    this.setData({ bookWorkspaceView: view });
  },

  changeChapterTitle(event: TextInputEvent) {
    const id = datasetString(event, "id");
    const chapter = findBookChapter(this.data.chapters, id);
    this.patchChapter(id, {
      editingTitle: event.detail.value,
      titleChanged: chapter ? trimText(event.detail.value) !== trimText(chapter.title) : false,
    });
  },

  async editChapterTitleById(chapterId: string) {
    const chapter = findBookChapter(this.data.chapters, chapterId);
    if (!chapter || !this.data.projectId || this.data.titleBusyTarget || this.data.updatingPlanStructure) {
      return false;
    }
    this.stopPlayback();
    const title = await promptRequiredText(
      "修改章名",
      `当前：${chapter.title}`,
    );
    if (title === null) {
      return false;
    }
    if (title.length > 80) {
      wx.showToast({ title: "章名不能超过 80 字", icon: "none" });
      return false;
    }
    if (title === trimText(chapter.title)) {
      return false;
    }
    this.setData({ titleBusyTarget: chapterId, error: "" });
    this.patchChapter(chapterId, { draftError: "" });
    try {
      const plan = await updateMemoirBookChapterTitle(
        this.data.projectId,
        chapterId,
        { title },
      );
      await this.applyUpdatedPlan(plan, "章名已修改");
      trackAuditAction("book.chapter_title_update", {}, { projectId: this.data.projectId });
      return true;
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return false;
      }
      this.patchChapter(chapterId, {
        draftError: error instanceof Error ? error.message : "章名修改失败",
      });
      return false;
    } finally {
      this.setData({ titleBusyTarget: "" });
    }
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
      if (routeAuthError(error, "login")) {
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
        confirmColor: "#a84f43",
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
      if (routeAuthError(error, "login")) {
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

  async playChapterAudioById(chapterId: string, options: { showPreparingToast?: boolean } = {}) {
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

  async playBookOutlineAudio() {
    const target = "outline";
    if (this.data.playingTarget === target) {
      this.stopPlayback();
      return;
    }
    if (!this.data.plan || !this.data.chapters.length) {
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
      error: "",
    });
    try {
      const response = await this.requestBookAudio(target);
      if (token !== this.playToken) {
        return;
      }
      this.setData({ ttsBusyTarget: "" });
      await this.playAudioFiles(response, target, token, activeText);
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      if (token === this.playToken) {
        this.setData({
          error: error instanceof Error ? error.message : "目录朗读失败",
        });
      }
    } finally {
      if (token === this.playToken && this.data.ttsBusyTarget === target) {
        this.setData({ ttsBusyTarget: "" });
      }
    }
  },

  async requestBookAudio(target: string) {
    const request = readBookTTSRequest();
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
    stopBookPlayback(this);
  },

  onHide() {
    this.clearBookGenerationEventPoll();
    this.stopPlayback();
  },

  onUnload() {
    this.clearBookGenerationEventPoll();
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
    navigateToLogin();
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
        this.data.splitSourceSelections,
      ),
    });
  },

});
