import type {
  MemoirBookAudioResponse,
  MemoirBookChapterDraft,
  MemoirBookChapterSourceRef,
  MemoirBookOutput,
  MemoirBookPlan,
  MemoirBookPlanChapter,
  MemoirBookPlanChapterInput,
  MemoirBookPlanChapterUpdateInput,
  MemoirBookSourceCoverage,
  TTSAlignmentSegment,
} from "../../domain/voice-chat";
import { getMemoirIdentity } from "../../services/auth";
import {
  assignMemoirBookSources,
  confirmMemoirBookChapterDraft,
  confirmMemoirBookPlan,
  createMemoirBookChapterDraftTTS,
  createMemoirBookOutputTTS,
  downloadMemoirVoiceAudio,
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
import { configureAudiblePlayback } from "../../services/audio-playback";
import { ApiError } from "../../services/request";
import { readBookTTSSettings } from "../../services/tts-settings";
import { splitTextByAudioPlaybackProgress } from "../../services/tts-text-progress";
import { formatBeijingDateTime } from "../../services/time";

interface DatasetEvent {
  currentTarget: {
    dataset: {
      id?: string;
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

type BookStage = "empty" | "plan_draft" | "drafting";

const TIMEZONE_OFFSET_RE = /(?:[zZ]|[+-]\d{2}:?\d{2})$/;
const NEW_CHAPTER_PICKER_VALUE = "__new_chapter__";

interface ChapterPickerOption {
  label: string;
  value: string;
}

export interface BookUncoveredSourceView {
  utterance_id: string;
  previewText: string;
  created_at: string;
  createdAtText: string;
  sourceRef: MemoirBookChapterSourceRef;
}

export interface BookChapterView extends MemoirBookPlanChapter {
  editingTitle: string;
  editingSummary: string;
  titleChanged: boolean;
  ttsTarget: string;
  sourceCount: number;
  sourceSummary: string;
  pendingSourceCount: number;
  removedSourceCount: number;
  sourceChangeNotice: string;
  draft: MemoirBookChapterDraft | null;
  draftContent: string;
  draftCharCount: number;
  draftChanged: boolean;
  draftStatusText: string;
  draftVersionText: string;
  draftActionText: string;
  draftBusy: boolean;
  draftError: string;
}

interface BookData {
  loading: boolean;
  generatingPlan: boolean;
  confirmingPlan: boolean;
  updatingPlanStructure: boolean;
  error: string;
  projectId: string;
  projectTitle: string;
  plan: MemoirBookPlan | null;
  planTitle: string;
  stage: BookStage;
  stageText: string;
  chapters: BookChapterView[];
  confirmedDraftCount: number;
  totalChapterCount: number;
  allChaptersConfirmed: boolean;
  hasDraftChanges: boolean;
  canGenerateBook: boolean;
  progressText: string;
  sourceCoverage: MemoirBookSourceCoverage | null;
  sourceCoverageNotice: string;
  uncoveredSourceCount: number;
  uncoveredSourceDisplayText: string;
  uncoveredSources: BookUncoveredSourceView[];
  chapterPickerOptions: ChapterPickerOption[];
  assigningSourceId: string;
  titleBusyTarget: string;
  bookOutput: MemoirBookOutput | null;
  bookContent: string;
  bookCharCount: number;
  bookVersionText: string;
  bookStaleNotice: string;
  bookActionText: string;
  generatingBook: boolean;
  ttsBusyTarget: string;
  playingTarget: string;
  bookTTSActiveTarget: string;
  bookTTSActiveText: string;
  bookTTSSpokenText: string;
  bookTTSCurrentText: string;
  bookTTSRemainingText: string;
  bookTTSProgressPercent: number;
  bookTTSDurationSeconds: number;
  bookTTSAlignment: TTSAlignmentSegment[];
}

function trimText(value: string): string {
  return value.trim();
}

function positiveFiniteSeconds(value: number | null | undefined): number {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : 0;
}

function latestDraftContent(draft: MemoirBookChapterDraft | null): string {
  return draft?.latest_version?.content || "";
}

function draftStatusText(draft: MemoirBookChapterDraft | null): string {
  if (!draft?.latest_version) {
    return "未生成";
  }
  return draft.status === "confirmed" ? "已确认" : "待确认";
}

function sourceIdCount(metadata: Record<string, unknown> | null | undefined, key: string): number {
  const value = metadata?.[key];
  if (!Array.isArray(value)) {
    return 0;
  }
  return value.filter((item) => typeof item === "string" && item.trim()).length;
}

function pendingSourceCount(metadata: Record<string, unknown> | null | undefined): number {
  return sourceIdCount(metadata, "pending_source_utterance_ids");
}

function sourceSupplementCount(
  metadata: Record<string, unknown> | null | undefined,
  draft: MemoirBookChapterDraft | null,
): number {
  const count = pendingSourceCount(metadata);
  if (count) {
    return count;
  }
  return draft?.metadata_json?.status_reason === "new_sources_assigned" ? 1 : 0;
}

function removedSourceCount(
  metadata: Record<string, unknown> | null | undefined,
  draft: MemoirBookChapterDraft | null,
): number {
  const count = sourceIdCount(metadata, "removed_source_utterance_ids");
  if (count) {
    return count;
  }
  return draft?.metadata_json?.status_reason === "sources_deleted" ? 1 : 0;
}

function sourceChangeNotice(pendingCount: number, removedCount: number): string {
  if (removedCount) {
    return `${removedCount} 条来源已删除，请检查本章正文`;
  }
  if (pendingCount) {
    return `${pendingCount} 条新增来源待补写`;
  }
  return "";
}

function draftActionText(draft: MemoirBookChapterDraft | null, pendingCount: number, removedCount = 0): string {
  if (pendingCount) {
    return "补写新增";
  }
  if (removedCount) {
    return "整理本章";
  }
  return draft?.latest_version ? "重新生成" : "生成本章";
}

function draftVersionText(draft: MemoirBookChapterDraft | null): string {
  const version = draft?.latest_version?.version;
  return version ? `第 ${version} 版` : "暂无正文";
}

function clearSourceChangeMetadata(metadata: Record<string, unknown> | null | undefined): Record<string, unknown> {
  const next = { ...(metadata || {}) };
  delete next.pending_source_utterance_ids;
  delete next.source_assignment_updated_at;
  delete next.removed_source_utterance_ids;
  delete next.source_refs_removed_at;
  return next;
}

export function latestBookContent(output: MemoirBookOutput | null): string {
  return output?.latest_version?.content || "";
}

export function bookOutputVersionText(output: MemoirBookOutput | null): string {
  const version = output?.latest_version?.version;
  return version ? `全书第 ${version} 版` : "尚未生成全书";
}

export function bookOutputStaleNotice(output: MemoirBookOutput | null): string {
  if (!output?.is_stale) {
    return "";
  }
  const titles = (output.stale_chapter_titles || []).filter(Boolean).slice(0, 2);
  if (!titles.length) {
    return "章节内容已更新，请重新生成全书。";
  }
  const suffix = (output.stale_chapter_titles || []).length > 2 ? "等章节" : "";
  return `章节「${titles.join("、")}」${suffix}已更新，请重新生成全书。`;
}

function bookActionText(output: MemoirBookOutput | null): string {
  if (output?.is_stale) {
    return "更新全书";
  }
  return output?.latest_version ? "重新生成全书" : "生成全书";
}

function bookOutputData(output: MemoirBookOutput | null): Pick<
  BookData,
  "bookOutput" | "bookContent" | "bookCharCount" | "bookVersionText" | "bookStaleNotice" | "bookActionText"
> {
  const content = latestBookContent(output);
  return {
    bookOutput: output,
    bookContent: content,
    bookCharCount: trimText(content).length,
    bookVersionText: bookOutputVersionText(output),
    bookStaleNotice: bookOutputStaleNotice(output),
    bookActionText: bookActionText(output),
  };
}

export function bookSourceCoverageNotice(
  plan: MemoirBookPlan | null,
  coverage: MemoirBookSourceCoverage | null,
): string {
  const count = coverage?.uncovered_source_count || 0;
  if (!count) {
    return "";
  }
  if (!plan) {
    return `有 ${count} 条转写可用于生成章节`;
  }
  return `有 ${count} 条新转写尚未收录到书中`;
}

function sourceCoverageData(
  plan: MemoirBookPlan | null,
  coverage: MemoirBookSourceCoverage | null,
): Pick<
  BookData,
  "sourceCoverage" | "sourceCoverageNotice" | "uncoveredSourceCount" | "uncoveredSourceDisplayText" | "uncoveredSources"
> {
  const uncoveredSources = buildUncoveredSourceViews(coverage);
  const uncoveredSourceCount = coverage?.uncovered_source_count || 0;
  return {
    sourceCoverage: coverage,
    sourceCoverageNotice: bookSourceCoverageNotice(plan, coverage),
    uncoveredSourceCount,
    uncoveredSourceDisplayText: uncoveredSourceCount > uncoveredSources.length
      ? `最近 ${uncoveredSources.length}/${uncoveredSourceCount} 条`
      : `${uncoveredSourceCount} 条待收录`,
    uncoveredSources,
  };
}

function createdAtTime(value: string): number {
  const text = value.trim();
  if (!text) {
    return 0;
  }
  const normalized = TIMEZONE_OFFSET_RE.test(text) ? text : `${text}Z`;
  const time = new Date(normalized).getTime();
  return Number.isFinite(time) ? time : 0;
}

export function buildUncoveredSourceViews(
  coverage: MemoirBookSourceCoverage | null,
  limit = 12,
): BookUncoveredSourceView[] {
  return [...(coverage?.uncovered_sources || [])]
    .sort((left, right) => createdAtTime(right.created_at) - createdAtTime(left.created_at))
    .slice(0, limit)
    .map((source) => {
      const text = trimText(source.text);
      return {
        utterance_id: source.utterance_id,
        previewText: text.length > 96 ? `${text.slice(0, 96)}...` : text,
        created_at: source.created_at,
        createdAtText: formatBeijingDateTime(source.created_at) || "转写记录",
        sourceRef: {
          voice_session_id: source.voice_session_id,
          message_id: source.message_id,
          utterance_id: source.utterance_id,
          transcript_version_id: source.transcript_version_id,
          audio_file_id: source.audio_file_id,
          start_ms: source.start_ms,
          end_ms: source.end_ms,
          quote: text.slice(0, 180),
        },
      };
    });
}

export function buildChapterPickerOptions(chapters: BookChapterView[]): ChapterPickerOption[] {
  return chapters.map((chapter, index) => ({
    label: `第 ${index + 1} 章 ${chapter.title}`,
    value: chapter.id,
  }));
}

export function buildSourceActionPickerOptions(
  chapters: BookChapterView[],
  includeNewChapter = false,
): ChapterPickerOption[] {
  const options = buildChapterPickerOptions(chapters);
  if (includeNewChapter) {
    options.push({
      label: "作为新章",
      value: NEW_CHAPTER_PICKER_VALUE,
    });
  }
  return options;
}

function chapterStateData(chapters: BookChapterView[]): Pick<
  BookData,
  "confirmedDraftCount" | "totalChapterCount" | "allChaptersConfirmed" | "hasDraftChanges" | "canGenerateBook" | "progressText"
> {
  const confirmedDraftCount = chapters.filter((chapter) => chapter.draft?.status === "confirmed").length;
  const totalChapterCount = chapters.length;
  const hasDraftChanges = chapters.some((chapter) => chapter.draftChanged);
  const allChaptersConfirmed = totalChapterCount > 0 && confirmedDraftCount === totalChapterCount;
  return {
    confirmedDraftCount,
    totalChapterCount,
    allChaptersConfirmed,
    hasDraftChanges,
    canGenerateBook: allChaptersConfirmed && !hasDraftChanges,
    progressText: bookProgressText(chapters),
  };
}

export function bookChapterSourceSummary(chapter: Pick<MemoirBookPlanChapter, "source_refs">): string {
  const quotes = chapter.source_refs
    .map((ref) => trimText(ref.quote || ""))
    .filter(Boolean)
    .slice(0, 2);
  if (!quotes.length) {
    return `${chapter.source_refs.length} 条来源转写`;
  }
  return quotes.join(" / ");
}

export function buildChapterViews(
  plan: MemoirBookPlan,
  draftsByChapterId: Record<string, MemoirBookChapterDraft | null> = {},
): BookChapterView[] {
  return [...plan.chapters]
    .sort((left, right) => left.sort_order - right.sort_order)
    .map((chapter) => {
      const draft = draftsByChapterId[chapter.id] || null;
      const content = latestDraftContent(draft);
      const assignedPendingCount = sourceSupplementCount(chapter.metadata_json, draft);
      const assignedRemovedCount = removedSourceCount(chapter.metadata_json, draft);
      return {
        ...chapter,
        editingTitle: chapter.title,
        editingSummary: chapter.summary || "",
        titleChanged: false,
        ttsTarget: `chapter:${chapter.id}`,
        sourceCount: chapter.source_refs.length,
        sourceSummary: bookChapterSourceSummary(chapter),
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
        draftBusy: false,
        draftError: "",
      };
    });
}

export function bookProgressText(chapters: BookChapterView[]): string {
  if (!chapters.length) {
    return "还没有章节";
  }
  const confirmed = chapters.filter((chapter) => chapter.draft?.status === "confirmed").length;
  const generated = chapters.filter((chapter) => Boolean(chapter.draft?.latest_version)).length;
  return `${confirmed}/${chapters.length} 章已确认，${generated} 章已有正文`;
}

function planStage(plan: MemoirBookPlan | null): BookStage {
  if (!plan) {
    return "empty";
  }
  return plan.status === "confirmed" ? "drafting" : "plan_draft";
}

function stageText(stage: BookStage): string {
  if (stage === "empty") {
    return "等待生成章节";
  }
  if (stage === "plan_draft") {
    return "确认章节结构";
  }
  return "逐章成稿";
}

function toPlanInput(chapters: BookChapterView[]): MemoirBookPlanChapterInput[] {
  return chapters.map((chapter) => ({
    title: trimText(chapter.editingTitle),
    summary: trimText(chapter.editingSummary),
    source_refs: chapter.source_refs,
  }));
}

function toPlanUpdateInput(chapters: BookChapterView[]): MemoirBookPlanChapterUpdateInput[] {
  return chapters.map((chapter) => ({
    id: chapter.id,
    title: trimText(chapter.editingTitle || chapter.title),
    summary: trimText(chapter.editingSummary || chapter.summary),
    source_refs: chapter.source_refs,
  }));
}

export function titleFromUncoveredSource(source: BookUncoveredSourceView): string {
  const text = trimText(source.previewText)
    .replace(/\s+/g, "")
    .replace(/^(新增|新收录|补充)?转写(记录|内容)?[:：，,。]*/, "");
  const firstClause = text.split(/[，。！？、,.!?;；:：]/).find(Boolean) || text;
  if (!firstClause || firstClause.length < 3 || ["新增转写", "新收录转写", "转写记录"].includes(firstClause)) {
    return "补记往事";
  }
  return firstClause.length > 16 ? firstClause.slice(0, 16) : firstClause;
}

function isLoginError(error: unknown): boolean {
  return error instanceof ApiError && [401, 403].includes(error.statusCode);
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
  changeChapterSummary(event: TextInputEvent): void;
  saveChapterTitle(event: DatasetEvent): Promise<void>;
  generateChapterTitle(event: DatasetEvent): Promise<void>;
  removeChapter(event: DatasetEvent): void;
  generateChapterDraft(event: DatasetEvent): Promise<void>;
  changeDraftContent(event: TextInputEvent): void;
  saveChapterDraft(event: DatasetEvent): Promise<void>;
  confirmChapterDraft(event: DatasetEvent): Promise<void>;
  assignUncoveredSource(event: PickerEvent): Promise<void>;
  addUncoveredSourceAsChapter(event: DatasetEvent): Promise<void>;
  addUncoveredSourceAsChapterById(utteranceId: string): Promise<void>;
  deleteConfirmedChapter(event: DatasetEvent): Promise<void>;
  updatePlanStructure(chapters: MemoirBookPlanChapterUpdateInput[], toastTitle: string): Promise<void>;
  generateBook(): Promise<void>;
  playChapterAudio(event: DatasetEvent): Promise<void>;
  playBookAudio(): Promise<void>;
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
  openTranscripts(): void;
  openHome(): void;
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
      if (!identity.password_login_enabled) {
        wx.switchTab({ url: "/pages/me/index" });
        return;
      }
      const projectId = identity.active_project_id;
      const projectTitle = identity.active_project?.title || "我的回忆录";
      this.setData({ projectId, projectTitle });
      if (!projectId) {
        this.applyPlan(null);
        this.applySourceCoverage(null);
        return;
      }
      await this.loadPlanForProject(projectId);
    } catch (error) {
      if (isLoginError(error)) {
        wx.switchTab({ url: "/pages/me/index" });
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
      this.applyPlan(plan, draftsByChapterId);
      this.applyBookOutput(output);
      this.applySourceCoverage(coverage);
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
    const chapters = plan ? buildChapterViews(plan, draftsByChapterId) : [];
    const stage = planStage(plan);
    this.setData({
      plan,
      planTitle: plan?.title || `${this.data.projectTitle || "我的回忆录"}章节规划`,
      stage,
      stageText: stageText(stage),
      chapters,
      ...chapterStateData(chapters),
      chapterPickerOptions: buildSourceActionPickerOptions(chapters, stage === "drafting"),
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
    this.applyPlan(plan, draftsByChapterId);
    this.applySourceCoverage(coverage);
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
      this.setData({ error: "每个章节都需要标题和来源转写" });
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

  changeChapterSummary(event) {
    const id = event.currentTarget.dataset.id || "";
    this.patchChapter(id, { editingSummary: event.detail.value });
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
    const chapter = this.data.chapters.find((candidate) => candidate.id === chapterId);
    if (!chapter || !this.data.projectId || this.data.titleBusyTarget || this.data.updatingPlanStructure) {
      return;
    }
    this.stopPlayback();
    this.setData({ titleBusyTarget: chapterId, error: "" });
    this.patchChapter(chapterId, { draftError: "" });
    try {
      const plan = await generateMemoirBookChapterTitle(this.data.projectId, chapterId, {});
      await this.applyUpdatedPlan(plan, "已重新起名");
    } catch (error) {
      if (isLoginError(error)) {
        wx.switchTab({ url: "/pages/me/index" });
        return;
      }
      const message = error instanceof Error ? error.message : "AI 起名失败";
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

  removeChapter(event) {
    const id = event.currentTarget.dataset.id || "";
    if (!id || this.data.confirmingPlan || this.data.chapters.length <= 1) {
      return;
    }
    const chapters = this.data.chapters.filter((chapter) => chapter.id !== id);
    this.setData({
      chapters,
      ...chapterStateData(chapters),
      error: "",
    });
  },

  async generateChapterDraft(event) {
    const chapterId = event.currentTarget.dataset.id || "";
    const chapter = this.data.chapters.find((candidate) => candidate.id === chapterId);
    if (!chapter || !this.data.projectId || chapter.draftBusy || this.data.updatingPlanStructure) {
      return;
    }
    if (chapter.draftChanged) {
      this.patchChapter(chapterId, { draftError: "请先保存当前修改，再重新生成" });
      return;
    }
    this.patchChapter(chapterId, { draftBusy: true, draftError: "" });
    try {
      const draft = await generateMemoirBookChapterDraft(this.data.projectId, chapterId, {
        force_regenerate: Boolean(chapter.draft?.latest_version),
      });
      this.patchDraft(chapterId, draft, { clearPendingSources: true });
      wx.showToast({ title: "章节稿已生成", icon: "success" });
    } catch (error) {
      this.patchChapter(chapterId, {
        draftError: error instanceof Error ? error.message : "章节稿生成失败",
      });
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
      const plan = await assignMemoirBookSources(this.data.projectId, {
        assignments: [
          {
            chapter_id: option.value,
            utterance_ids: [utteranceId],
          },
        ],
      });
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
            summary: "整理这条新收录转写。",
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
        content: `删除「${chapter.title}」会移除本章正文，来源转写会回到待收录列表。`,
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
      return;
    }
    this.stopPlayback();
    this.setData({ updatingPlanStructure: true, error: "" });
    try {
      const plan = await updateMemoirBookPlan(this.data.projectId, {
        title: trimText(this.data.planTitle) || this.data.plan.title,
        chapters,
      });
      await this.applyUpdatedPlan(plan, toastTitle);
    } catch (error) {
      if (isLoginError(error)) {
        wx.switchTab({ url: "/pages/me/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "目录调整失败" });
    } finally {
      this.setData({ updatingPlanStructure: false });
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
    const audioFileIds = response.audio_file_ids || [];
    if (!audioFileIds.length) {
      throw new Error("没有可播放的音频");
    }
    const durationSeconds = positiveFiniteSeconds(response.duration_seconds);
    const alignment = Array.isArray(response.alignment) ? response.alignment : [];
    this.setData({
      playingTarget: target,
      error: "",
      bookTTSActiveTarget: target,
      bookTTSActiveText: activeText,
      bookTTSDurationSeconds: durationSeconds,
      bookTTSAlignment: alignment,
    });
    this.updateBookTTSProgress(0, durationSeconds);
    let elapsedBeforeSeconds = 0;
    try {
      for (let index = 0; index < audioFileIds.length; index += 1) {
        if (token !== this.playToken) {
          return;
        }
        const fallbackDurationSeconds = positiveFiniteSeconds(response.audio_file_durations_seconds?.[index]);
        const playedSeconds = await this.playAudioFile(
          audioFileIds[index],
          token,
          elapsedBeforeSeconds,
          fallbackDurationSeconds,
        );
        elapsedBeforeSeconds += fallbackDurationSeconds || playedSeconds;
      }
      if (token === this.playToken) {
        this.updateBookTTSProgress(durationSeconds || elapsedBeforeSeconds, durationSeconds || elapsedBeforeSeconds);
      }
    } finally {
      if (token === this.playToken) {
        this.setData({ playingTarget: "" });
      }
      this.stopBookTTSProgressTimer();
    }
  },

  async playAudioFile(audioFileId, token, elapsedBeforeSeconds, fallbackDurationSeconds) {
    const tempFilePath = await downloadMemoirVoiceAudio(audioFileId);
    if (token !== this.playToken) {
      return 0;
    }
    return new Promise<number>((resolve, reject) => {
      const audio = wx.createInnerAudioContext();
      this.audio = audio;
      let settled = false;
      const cleanup = () => {
        this.stopBookTTSProgressTimer();
        audio.destroy();
        if (this.audio === audio) {
          this.audio = null;
        }
      };
      const elapsedSeconds = () => {
        const duration = fallbackDurationSeconds
          || positiveFiniteSeconds(audio.duration)
          || this.playbackTimeForProgress(audio);
        return positiveFiniteSeconds(duration);
      };
      const resolveOnce = (seconds: number) => {
        if (settled) {
          return;
        }
        settled = true;
        cleanup();
        resolve(seconds);
      };
      const rejectOnce = (error: Error) => {
        if (settled) {
          return;
        }
        settled = true;
        cleanup();
        reject(error);
      };
      configureAudiblePlayback(audio);
      audio.src = tempFilePath;
      audio.onPlay(() => {
        this.ttsProgressStartedAtMs = Date.now();
        this.startBookTTSProgressTimer(token, audio, elapsedBeforeSeconds, fallbackDurationSeconds);
      });
      audio.onTimeUpdate(() => {
        if (token === this.playToken && this.audio === audio) {
          this.updateBookTTSProgress(
            elapsedBeforeSeconds + this.playbackTimeForProgress(audio),
            this.data.bookTTSDurationSeconds,
          );
        }
      });
      audio.onEnded(() => {
        const seconds = elapsedSeconds();
        this.updateBookTTSProgress(elapsedBeforeSeconds + seconds, this.data.bookTTSDurationSeconds);
        resolveOnce(seconds);
      });
      audio.onStop(() => {
        resolveOnce(this.playbackTimeForProgress(audio));
      });
      audio.onError((error) => {
        rejectOnce(new Error(error.errMsg || "音频播放失败"));
      });
      audio.play();
    });
  },

  updateBookTTSProgress(currentTimeSeconds, durationSeconds) {
    const activeText = this.data.bookTTSActiveText;
    if (!activeText) {
      return;
    }
    const progress = splitTextByAudioPlaybackProgress(
      activeText,
      currentTimeSeconds,
      positiveFiniteSeconds(durationSeconds) || this.data.bookTTSDurationSeconds,
      { alignment: this.data.bookTTSAlignment },
    );
    this.setData({
      bookTTSSpokenText: progress.spokenText,
      bookTTSCurrentText: progress.currentText,
      bookTTSRemainingText: progress.remainingText,
      bookTTSProgressPercent: progress.progressPercent,
    });
  },

  startBookTTSProgressTimer(token, audio, elapsedBeforeSeconds, durationSeconds) {
    this.stopBookTTSProgressTimer();
    const tick = () => {
      if (token !== this.playToken || this.audio !== audio) {
        this.stopBookTTSProgressTimer();
        return;
      }
      this.updateBookTTSProgress(
        elapsedBeforeSeconds + this.playbackTimeForProgress(audio),
        this.data.bookTTSDurationSeconds || elapsedBeforeSeconds + durationSeconds,
      );
    };
    tick();
    this.ttsProgressTimer = setInterval(tick, 250);
  },

  stopBookTTSProgressTimer() {
    if (this.ttsProgressTimer !== null) {
      clearInterval(this.ttsProgressTimer);
      this.ttsProgressTimer = null;
    }
  },

  resetBookTTSProgress() {
    this.stopBookTTSProgressTimer();
    this.setData({
      bookTTSActiveTarget: "",
      bookTTSActiveText: "",
      bookTTSSpokenText: "",
      bookTTSCurrentText: "",
      bookTTSRemainingText: "",
      bookTTSProgressPercent: 0,
      bookTTSDurationSeconds: 0,
      bookTTSAlignment: [],
    });
  },

  playbackTimeForProgress(audio) {
    const currentTime = positiveFiniteSeconds(audio.currentTime);
    if (currentTime) {
      return currentTime;
    }
    if (!this.ttsProgressStartedAtMs) {
      return 0;
    }
    return Math.max(0, (Date.now() - this.ttsProgressStartedAtMs) / 1000);
  },

  stopPlayback() {
    this.playToken += 1;
    this.stopBookTTSProgressTimer();
    if (this.audio) {
      this.audio.stop();
      this.audio.destroy();
      this.audio = null;
    }
    if (this.data.playingTarget || this.data.ttsBusyTarget) {
      this.setData({ playingTarget: "", ttsBusyTarget: "" });
    }
    this.resetBookTTSProgress();
  },

  onUnload() {
    this.stopPlayback();
  },

  openTranscripts() {
    wx.switchTab({ url: "/pages/transcripts/index" });
  },

  openHome() {
    this.stopPlayback();
    wx.switchTab({ url: "/pages/home/index" });
  },

  patchChapter(chapterId, patch) {
    if (!chapterId) {
      return;
    }
    const chapters = this.data.chapters.map((chapter) => (
      chapter.id === chapterId ? { ...chapter, ...patch } : chapter
    ));
    this.setData({
      chapters,
      ...chapterStateData(chapters),
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
