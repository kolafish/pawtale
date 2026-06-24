import type {
  MemoirBookAudioResponse,
  MemoirBookChapterDraft,
  MemoirBookChapterSourceRef,
  MemoirBookOutput,
  MemoirBookPlan,
  MemoirBookPlanChapterUpdateInput,
  MemoirBookSourceCoverage,
} from "../../domain/voice-chat";
import type { BookChapterView, BookData } from "./book-view-model";
import { getMemoirIdentity } from "../../services/auth";

import {
  assignMemoirBookSources,
  confirmMemoirBookChapterDraft,
  confirmMemoirBookPlan,
  createMemoirBookChapterDraftTTS,
  createMemoirBookOutputTTS,
  generateMemoirBookChapterTitle,
  generateMemoirBookChapterDraft,
  generateMemoirBookOutput,
  generateMemoirBookPlan,
  getMemoirBookChapterDraft,
  getMemoirBookOutput,
  getMemoirBookPlan,
  getMemoirBookSourceCoverage,
  updateMemoirBookChapterTitle,
  updateMemoirBookPlan,
  updateMemoirBookChapterDraft,
} from "../../services/memoir-voice-api";
import { ApiError } from "../../services/request";
import { sourceAssignmentRequest } from "../../services/memoir-book-source-actions";
import { readBookTTSSettings } from "../../services/tts-settings";
import { writeTranscriptNavigationTarget } from "../../services/transcript-navigation";
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
  titleFromUncoveredSource,
  toPlanInput,
  toPlanUpdateInput,
  trimText,
  uniqueSourceRefs,
  bookChapterSourceSummary,
} from "./book-view-model";
export {
  bookChapterSourceSummary,
  bookOutputStaleNotice,
  bookOutputVersionText,
  bookProgressText,
  bookSourceCoverageNotice,
  buildChapterPickerOptions,
  buildChapterViews,
  buildSourceActionPickerOptions,
  buildUncoveredSourceViews,
  latestBookContent,
  titleFromUncoveredSource,
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
  updatePlanStructure(chapters: MemoirBookPlanChapterUpdateInput[], toastTitle: string): Promise<MemoirBookPlan | null>;
  regenerateMergedChapter(chapterId: string): Promise<void>;
  generateBook(): Promise<void>;
  playChapterAudio(event: DatasetEvent): Promise<void>;
  playBookAudio(): Promise<void>;
  playBookOutlineAudio(): void;
  startBookVoiceCommand(): void;
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
}>({
  audio: null,
  playToken: 0,
  ttsProgressTimer: null,
  ttsProgressStartedAtMs: 0,

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
    bookVoiceAssistantHint: "生成章节后，可以听目录或说修改",
    bookVoiceAssistantStatusText: "等待章节",
    bookVoiceAssistantListening: false,
    bookVoiceAssistantBusy: false,
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
    this.setData({
      plan,
      planTitle: plan?.title || `${this.data.projectTitle || "我的回忆录"}章节规划`,
      stage,
      stageText: stageText(stage),
      bookVoiceAssistantHint: bookVoiceAssistantHint(stage, chapters.length),
      bookVoiceAssistantStatusText: bookVoiceAssistantStatusText(stage),
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
      this.setData({ error: "每个章节都需要标题和收录转写" });
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
    const chapters = this.data.chapters.filter((chapter) => chapter.id !== id);
    if (this.data.projectId && this.data.plan) {
      await this.updatePlanStructure(toPlanUpdateInput(chapters), "章节已删除");
      return;
    }
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
    const chapters = [...this.data.chapters];
    [chapters[fromIndex], chapters[toIndex]] = [chapters[toIndex], chapters[fromIndex]];
    const reordered = chapters.map((chapter, index) => ({ ...chapter, sort_order: index }));
    await this.updatePlanStructure(toPlanUpdateInput(reordered), "章节顺序已调整");
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
    const chapters = this.data.chapters
      .filter((chapter) => chapter.id !== sourceChapter.id)
      .map((chapter) => {
        if (chapter.id !== targetChapter.id) {
          return chapter;
        }
        const sourceRefs = uniqueSourceRefs([...targetChapter.source_refs, ...sourceChapter.source_refs]);
        return {
          ...chapter,
          source_refs: sourceRefs,
          sourceCount: sourceRefs.length,
          sourceSummary: bookChapterSourceSummary({ source_refs: sourceRefs }),
          draftChanged: false,
        };
      })
      .map((chapter, index) => ({ ...chapter, sort_order: index }));
    const plan = await this.updatePlanStructure(toPlanUpdateInput(chapters), "章节已合并");
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
    const keptRefs: MemoirBookChapterSourceRef[] = [];
    const splitRefs: MemoirBookChapterSourceRef[] = [];
    chapter.source_refs.forEach((ref) => {
      if (selectedIds.has(ref.utterance_id)) {
        splitRefs.push(ref);
        return;
      }
      keptRefs.push(ref);
    });
    if (!splitRefs.length || !keptRefs.length) {
      this.setData({ error: "拆分时至少保留 1 条素材在原章节" });
      return;
    }
    const title = trimText(chapter.editingTitle || chapter.title) || "未命名章节";
    const updates = this.data.chapters.flatMap((candidate) => {
      if (candidate.id !== chapterId) {
        return [candidate];
      }
      return [
        {
          ...candidate,
          source_refs: keptRefs,
          sourceCount: keptRefs.length,
          sourceSummary: bookChapterSourceSummary({ source_refs: keptRefs }),
        },
        {
          title: `${title}（拆出）`,
          source_refs: splitRefs,
        },
      ];
    });
    const splitSourceSelections = { ...this.data.splitSourceSelections };
    delete splitSourceSelections[chapterId];
    this.setData({ splitSourceSelections });
    await this.updatePlanStructure(toPlanUpdateInput(updates as BookChapterView[]), "章节已拆分");
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
      await this.updatePlanStructure(
        [
          ...toPlanUpdateInput(this.data.chapters),
          {
            title: titleFromUncoveredSource(source),
            source_refs: [source.sourceRef],
          },
        ],
        "已新增章节",
      );
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
        content: `删除「${chapter.title}」会移除本章正文，收录转写会回到待收录列表。`,
        confirmText: "删除",
        confirmColor: "#b4234a",
        success: (result) => resolve(Boolean(result.confirm)),
        fail: () => resolve(false),
      });
    });
    if (!confirmed) {
      return;
    }
    await this.updatePlanStructure(
      toPlanUpdateInput(this.data.chapters.filter((candidate) => candidate.id !== chapterId)),
      "章节已删除",
    );
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

  playBookOutlineAudio() {
    if (!this.data.plan || !this.data.chapters.length) {
      this.setData({
        bookVoiceAssistantStatusText: "先生成章节",
      });
      wx.showToast({ title: "先生成章节规划", icon: "none" });
      return;
    }
    this.setData({
      bookVoiceAssistantStatusText: "目录朗读将在下一步接入",
      error: "",
    });
    wx.showToast({ title: "下一步接入听目录", icon: "none" });
  },

  startBookVoiceCommand() {
    if (!this.data.plan || !this.data.chapters.length) {
      this.setData({
        bookVoiceAssistantStatusText: "先生成章节",
      });
      wx.showToast({ title: "先生成章节规划", icon: "none" });
      return;
    }
    this.setData({
      bookVoiceAssistantStatusText: "语音修改将在下一步接入",
      error: "",
    });
    wx.showToast({ title: "下一步接入语音修改", icon: "none" });
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
  },

  onUnload() {
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
