import type {
  MemoirBookAudioResponse,
  MemoirBookChapterDraft,
  MemoirBookOutput,
  MemoirBookPlan,
  MemoirBookPlanStructureCommandRequest,
  MemoirImageAssetListResponse,
} from "../../domain/memoir-book";
import type { MemoirAutomationEvent } from "../../domain/memoir-voice-session";
import type { MemoirBookSnapshot } from "../../domain/memoir-snapshots";
import { definePage, pageRuntime } from "../../services/page-definition";
import { accessibilityPageClass } from "../../services/accessibility-settings";
import { trackAuditAction } from "../../services/audit-tracker";
import type { BookChapterSources, BookChapterView, BookHomeData } from "./book-view-model";
import { promptRequiredText } from "../../services/text-prompt";
import { takeBookWorkspaceNavigationTarget } from "../../services/book-workspace-navigation";

import {
  acknowledgeMemoirAutomationEvent,
  acknowledgeDismissibleMemoirAutomationEvents,
} from "../../services/memoir-voice-session-api";
import {
  applyMemoirBookPlanStructureCommand,
  confirmMemoirBookPlan,
  createMemoirBookChapterDraftTTS,
  createMemoirBookShare,
  createMemoirBookPlanOutlineTTS,
  generateMemoirBookChapterTitle,
  generateMemoirBookPlan,
  getMemoirBookChapterDraft,
  getMemoirBookOutput,
  getMemoirBookShareStatus,
  revokeMemoirBookShare,
  updateMemoirBookChapterTitle,
} from "../../services/memoir-book-api";
import { getMemoirBookSnapshot } from "../../services/memoir-snapshot-api";
import { ApiError, optionalApiResource } from "../../services/request";
import {
  isAuthError,
  navigateToTranscripts,
  navigateToLogin,
  routeAuthError,
  returnToHome,
  returnToStory,
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
import { hideMiniProgramShareMenu } from "../../services/share";
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
} from "./book-view-model";
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
  buildChapterViews,
} from "./book-view-model";

export function bookCompletionPageUrl(projectId: string, projectTitle: string): string {
  return pageUrl("/pages/book-completion/index", {
    projectId,
    projectTitle,
  });
}

export function bookReaderPageUrl(projectId: string): string {
  return pageUrl("/pages/book-reader/index", { projectId });
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

type BookWorkspaceView = "chapters" | "tools";

type BookPageData = BookHomeData & {
  pageClass: string;
  showingCachedBook: boolean;
  bookSharePanelOpen: boolean;
  bookShareActive: boolean;
  bookShareReady: boolean;
  bookShareBusy: boolean;
  bookWorkspaceView: BookWorkspaceView;
  planStructureBusyChapterId: string;
  planStructureBusyDirection: string;
};

definePage<BookPageData>()({
  audioPlayback: pageRuntime(() => new AudioPlaybackController()),
  ttsProgress: pageRuntime(() => new VoiceAssistantTTSProgressRuntime()),
  playToken: 0,
  automationEvents: [] as MemoirAutomationEvent[],
  bookLoadSequence: 0,
  bookPlan: null as MemoirBookPlan | null,
  bookChapterSources: {} as BookChapterSources,
  bookDraftsByChapterId: {} as Record<string, MemoirBookChapterDraft | null>,
  bookOutput: null as MemoirBookOutput | null,
  bookShareToken: "",

  data: {
    pageClass: "book-page",
    showingCachedBook: false,
    bookSharePanelOpen: false,
    bookShareActive: false,
    bookShareReady: false,
    bookShareBusy: false,
    bookWorkspaceView: "chapters",
    loading: false,
    needsLogin: false,
    generatingPlan: false,
    confirmingPlan: false,
    updatingPlanStructure: false,
    planStructureBusyChapterId: "",
    planStructureBusyDirection: "",
    error: "",
    projectId: "",
    projectTitle: "我的回忆录",
    planTitle: "",
    stage: "empty",
    stageText: "等待生成章节",
    chapters: [],
    chapterStructureEditing: false,
    splitSourceSelections: {},
    confirmedDraftCount: 0,
    totalChapterCount: 0,
    progressText: "还没有章节",
    titleBusyTarget: "",
    bookContentAvailable: false,
    bookCharCount: 0,
    bookPreviewTitle: "",
    bookStaleNotice: "",
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
    imageAssetSummaryText: "还没有图片素材",
  },

  async onShow() {
    this.applyBookWorkspaceNavigationTarget();
    this.setData({ pageClass: accessibilityPageClass("book-page") });
    hideMiniProgramShareMenu();
    await this.loadBookHome();
    if (this.data.bookSharePanelOpen) await this.refreshBookShareStatus();
  },

  onShareAppMessage() {
    return {
      title: this.bookShareToken ? `阅读《${this.data.bookPreviewTitle || "家庭回忆录"}》` : "老余口述",
      path: this.bookShareToken
        ? pageUrl("/pages/book-reader/index", { shareToken: this.bookShareToken })
        : "/pages/home/index",
    };
  },

  async openBookSharePanel() {
    if (!this.data.projectId || !this.data.bookContentAvailable) return;
    if (this.data.bookSharePanelOpen) {
      this.setData({ bookSharePanelOpen: false });
      return;
    }
    this.setData({ bookSharePanelOpen: true });
    await this.refreshBookShareStatus();
  },

  async refreshBookShareStatus() {
    if (!this.data.projectId) return;
    this.setData({ bookShareBusy: true });
    try {
      const status = await getMemoirBookShareStatus(this.data.projectId);
      if (!status.active) this.bookShareToken = "";
      this.setData({
        bookShareActive: status.active,
        bookShareReady: status.active && Boolean(this.bookShareToken),
      });
    } catch (error) {
      if (!routeAuthError(error, "login")) {
        this.setData({ error: error instanceof Error ? error.message : "分享状态读取失败" });
      }
    } finally {
      this.setData({ bookShareBusy: false });
    }
  },

  async createBookShare() {
    if (!this.data.projectId || this.data.bookShareBusy) return;
    this.setData({ bookShareBusy: true, error: "" });
    try {
      const share = await createMemoirBookShare(this.data.projectId);
      this.bookShareToken = share.token;
      this.setData({ bookShareActive: true, bookShareReady: true });
    } catch (error) {
      if (!routeAuthError(error, "login")) {
        this.setData({ error: error instanceof Error ? error.message : "生成分享链接失败" });
      }
    } finally {
      this.setData({ bookShareBusy: false });
    }
  },

  async revokeBookShare() {
    if (!this.data.projectId || this.data.bookShareBusy) return;
    this.setData({ bookShareBusy: true, error: "" });
    try {
      await revokeMemoirBookShare(this.data.projectId);
      this.bookShareToken = "";
      this.setData({ bookShareActive: false, bookShareReady: false });
    } catch (error) {
      if (!routeAuthError(error, "login")) {
        this.setData({ error: error instanceof Error ? error.message : "撤销分享链接失败" });
      }
    } finally {
      this.setData({ bookShareBusy: false });
    }
  },

  applyBookWorkspaceNavigationTarget() {
    const target = takeBookWorkspaceNavigationTarget();
    if (target) {
      this.setData({ bookWorkspaceView: target });
    }
  },

  async loadBookHome() {
    const loadSequence = ++this.bookLoadSequence;
    const cached = readCachedMemoirBook();
    const localInvalidationRevision = cached.localInvalidationRevision || 0;
    const showedCachedData = Boolean(cached.payload);
    if (cached.payload) {
      this.applyBookSnapshot(cached.payload);
    } else {
      this.setData({ loading: true, error: "", showingCachedBook: false });
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
      this.setData({ showingCachedBook: false });
    } catch (error) {
      if (loadSequence !== this.bookLoadSequence) {
        return;
      }
      if (isAuthError(error)) {
        this.bookPlan = null;
        this.bookChapterSources = {};
        this.bookDraftsByChapterId = {};
        this.bookOutput = null;
        this.bookShareToken = "";
        this.setData(guestBookData());
        this.setData({ showingCachedBook: false, bookSharePanelOpen: false, bookShareActive: false, bookShareReady: false });
        return;
      }
      if (showedCachedData) {
        this.setData({ showingCachedBook: true });
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

  applyBookSnapshot(snapshot: MemoirBookSnapshot) {
    const identity = snapshot.identity;
    const projectId = identity.active_project_id;
    const projectTitle = identity.active_project?.title || "尚未创建回忆录";
    if (projectId !== this.data.projectId) {
      this.bookShareToken = "";
      this.setData({ bookSharePanelOpen: false, bookShareActive: false, bookShareReady: false });
    }
    this.setData({
      loading: false,
      error: "",
      needsLogin: false,
      projectId,
      projectTitle,
    });
    this.applyPlan(snapshot.plan || null, snapshot.drafts_by_chapter_id || {});
    this.applyBookOutput(snapshot.output || null);
    this.applyAutomationEvents(snapshot.automation_events.items || []);
    this.applyImageAssetSummary(snapshot.image_assets);
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

  applyImageAssetSummary(response: MemoirImageAssetListResponse | null) {
    const total = response?.total || 0;
    const included = response?.included_count || 0;
    const unused = response?.unused_count || 0;
    const summary = total
      ? `${total} 张 · ${included} 张已入书 · ${unused} 张待选择`
      : "生成分享视频背景后，会自动收集到这里";
    this.setData({
      imageAssetTotal: total,
      imageAssetSummaryText: summary,
    });
  },

  applyPlan(plan: MemoirBookPlan | null, draftsByChapterId: Record<string, MemoirBookChapterDraft | null> = {}) {
    this.bookPlan = plan;
    this.bookChapterSources = Object.fromEntries(
      (plan?.chapters || []).map((chapter) => [chapter.id, chapter.source_refs]),
    );
    this.bookDraftsByChapterId = draftsByChapterId;
    const chapters = plan
      ? buildChapterViews(plan, draftsByChapterId, this.data.splitSourceSelections)
      : [];
    const stage = planStage(plan);
    if (stage !== "drafting") {
      this.bookOutput = null;
    }
    this.setData({
      planTitle: plan?.title || `${this.data.projectTitle || "我的回忆录"}章节规划`,
      stage,
      stageText: stageText(stage),
      chapterStructureEditing: stage === "drafting" ? this.data.chapterStructureEditing : false,
      ...chapterCollectionData(chapters, this.bookChapterSources, this.data.splitSourceSelections),
      ...(stage === "drafting" ? {} : bookHomeOutputData(null)),
    });
  },

  applyBookOutput(output: MemoirBookOutput | null) {
    this.bookOutput = output;
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
    this.setData({
      automationEventItems: buildAutomationEventViews(
        events,
        this.data.automationEventBusyTarget,
        this.data.automationEventExpandedTargets,
      ),
      automationDismissibleCount: events.filter((event) => (
        event.status === "completed" || event.status === "failed"
      )).length,
    });
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
      navigateToTranscripts();
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
        optionalApiResource(getMemoirBookOutput(plan.project_id)),
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
    if (!this.data.projectId || this.data.confirmingPlan || !this.bookPlan) {
      return;
    }
    const chapters = toPlanInput(this.data.chapters, this.bookChapterSources);
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
    const chapter = findBookChapter(this.data.chapters, chapterId);
    if (!chapterId || !this.data.projectId) {
      this.setData({ error: "缺少章节信息" });
      return;
    }
    if (this.data.updatingPlanStructure || chapter?.draftBusy) {
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
    if (this.data.stage !== "drafting" || !this.bookPlan || this.data.updatingPlanStructure) {
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
    const canShowAudioAction = chapter.draftAvailable
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
    if (!this.data.bookContentAvailable) {
      this.setData({ error: "请先生成书稿" });
      return;
    }
    const action = datasetString(event, "action") || "preview";
    wx.navigateTo({ url: bookExportPageUrl(this.data.projectId, action) });
  },

  openBookReader() {
    if (!this.data.projectId || !this.data.bookContentAvailable || this.data.bookStaleNotice) {
      return;
    }
    this.stopPlayback();
    wx.navigateTo({ url: bookReaderPageUrl(this.data.projectId) });
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
    if (this.data.projectId && this.bookPlan) {
      await this.applyPlanStructureCommand({ type: "delete_chapter", chapter_id: id }, "章节已删除");
      return;
    }
    const patch = removeLocalChapterPatch(this.data, this.bookChapterSources, id);
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
    const previousChapters = this.data.chapters;
    const fromIndex = previousChapters.findIndex((chapter) => chapter.id === chapterId);
    const toIndex = direction === "up" ? fromIndex - 1 : fromIndex + 1;
    const chapters = [...previousChapters];
    [chapters[fromIndex], chapters[toIndex]] = [chapters[toIndex], chapters[fromIndex]];
    this.setData(chapterCollectionData(chapters, this.bookChapterSources, this.data.splitSourceSelections));
    const plan = await this.applyPlanStructureCommand(command, "章节顺序已调整");
    if (!plan) {
      this.setData(chapterCollectionData(previousChapters, this.bookChapterSources, this.data.splitSourceSelections));
    }
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
        confirmColor: "#b65d49",
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
    const patch = toggleBookSplitSourcePatch(
      this.data,
      this.bookChapterSources,
      chapterId,
      utteranceId,
    );
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
    this.setData(toggleBookChapterSourcesPatch(this.data, this.bookChapterSources, chapterId));
  },

  async applyPlanStructureCommand(command: MemoirBookPlanStructureCommandRequest, toastTitle: string) {
    if (!this.data.projectId || !this.bookPlan || this.data.updatingPlanStructure) {
      return null;
    }
    this.stopPlayback();
    const movingChapter = command.type === "move_chapter";
    this.setData({
      updatingPlanStructure: true,
      planStructureBusyChapterId: movingChapter ? command.chapter_id || "" : "",
      planStructureBusyDirection: movingChapter ? command.direction || "" : "",
      error: "",
    });
    try {
      const plan = await applyMemoirBookPlanStructureCommand(this.data.projectId, command);
      if (movingChapter) {
        const previousSortOrderByChapterId = new Map(
          this.data.chapters.map((chapter) => [chapter.id, chapter.sort_order]),
        );
        const movedChapterTitles = plan.chapters
          .filter((chapter) => previousSortOrderByChapterId.get(chapter.id) !== chapter.sort_order)
          .map((chapter) => chapter.title);
        this.applyPlan(plan, this.bookDraftsByChapterId);
        if (this.bookOutput?.latest_version && movedChapterTitles.length) {
          this.applyBookOutput({
            ...this.bookOutput,
            is_stale: true,
            stale_chapter_titles: Array.from(new Set([
              ...(this.bookOutput.stale_chapter_titles || []),
              ...movedChapterTitles,
            ])),
          });
        }
        wx.showToast({ title: toastTitle, icon: "success" });
      } else {
        await this.applyUpdatedPlan(plan, toastTitle);
      }
      return plan;
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return null;
      }
      this.setData({ error: error instanceof Error ? error.message : "目录调整失败" });
      return null;
    } finally {
      this.setData({
        updatingPlanStructure: false,
        planStructureBusyChapterId: "",
        planStructureBusyDirection: "",
      });
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
    const draft = this.bookDraftsByChapterId[chapterId] || null;
    if (!draft?.latest_version) {
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
      await this.playAudioFiles(response, target, token, trimText(latestDraftContent(draft)));
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
    if (this.data.playingTarget === target || this.data.ttsBusyTarget === target) {
      this.stopPlayback();
      return;
    }
    if (!this.bookPlan || !this.data.chapters.length) {
      wx.showToast({ title: "先生成章节规划", icon: "none" });
      return;
    }
    if (
      !this.data.projectId
      || this.data.ttsBusyTarget
      || this.data.updatingPlanStructure
    ) {
      return;
    }
    this.stopPlayback();
    const token = this.audioPlayback.nextToken();
    this.playToken = token;
    const activeText = bookOutlineNarrationText(this.bookPlan.title, this.data.chapters);
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
    this.stopPlayback();
  },

  onUnload() {
    this.stopPlayback();
  },

  openTranscripts(event?: DatasetEvent) {
    const utteranceId = event?.currentTarget?.dataset?.id || "";
    if (utteranceId) {
      writeTranscriptNavigationTarget(utteranceId);
    }
    navigateToTranscripts();
  },

  openStory() {
    returnToStory(this.data.projectId);
  },

  openHome() {
    this.stopPlayback();
    returnToHome();
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
        this.bookChapterSources,
        this.data.splitSourceSelections,
      ),
    });
  },

});
