import type {
  MemoirBookChapterDraft,
  MemoirBookChapterSourceRef,
  MemoirBookOutput,
  MemoirBookPlan,
  MemoirBookPlanChapter,
  MemoirBookPlanChapterInput,
  MemoirBookPlanChapterUpdateInput,
  MemoirBookSourceCoverage,
  MemoirBookVoiceCommandParseResponse,
  TTSAlignmentSegment,
} from "../../domain/voice-chat";
import {
  clearSourceChangeMetadata,
  removedSourceCount,
  sourceRefKey,
  sourceSupplementCount,
  uniqueSourceRefs,
} from "../../services/memoir-book-sources";
import { formatBeijingDateTimeShort } from "../../services/time";

export {
  clearSourceChangeMetadata,
  removedSourceCount,
  sourceSupplementCount,
  uniqueSourceRefs,
} from "../../services/memoir-book-sources";

export type BookStage = "empty" | "plan_draft" | "drafting";

const TIMEZONE_OFFSET_RE = /(?:[zZ]|[+-]\d{2}:?\d{2})$/;
export const NEW_CHAPTER_PICKER_VALUE = "__new_chapter__";

export interface ChapterPickerOption {
  label: string;
  value: string;
}

export interface BookUncoveredSourceView {
  utterance_id: string;
  recordNumber: number;
  previewText: string;
  created_at: string;
  createdAtText: string;
  sourceRef: MemoirBookChapterSourceRef;
}

export interface BookChapterSourceView extends MemoirBookChapterSourceRef {
  sourceIndex: number;
  recordNumber: number;
  labelText: string;
  createdAtText: string;
  charCount: number;
  previewText: string;
  splitSelected: boolean;
}

export interface BookChapterView extends MemoirBookPlanChapter {
  editingTitle: string;
  titleChanged: boolean;
  ttsTarget: string;
  sourceCount: number;
  sourceSummary: string;
  sourceViews: BookChapterSourceView[];
  sourceListExpanded: boolean;
  sourceToggleText: string;
  mergeTargetOptions: ChapterPickerOption[];
  splitSelectedCount: number;
  splitActionText: string;
  canSplitSelectedSources: boolean;
  canMoveUp: boolean;
  canMoveDown: boolean;
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

export interface BookVoiceCommandUndoSnapshot {
  planTitle: string;
  chapters: MemoirBookPlanChapterUpdateInput[];
}

export interface BookData {
  loading: boolean;
  needsLogin: boolean;
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
  splitSourceSelections: Record<string, string[]>;
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
  bookVoiceAssistantHint: string;
  bookVoiceAssistantStatusText: string;
  bookVoiceAssistantListening: boolean;
  bookVoiceAssistantBusy: boolean;
  bookVoiceCommandText: string;
  bookVoiceCommandIntentLabel: string;
  bookVoiceCommandSummary: string;
  bookVoiceCommandRequiresConfirmation: boolean;
  bookVoiceCommandPreview: MemoirBookVoiceCommandParseResponse | null;
  bookVoiceCommandCanExecute: boolean;
  bookVoiceCommandActionText: string;
  bookVoiceCommandExecuting: boolean;
  bookVoiceCommandUndoText: string;
  bookVoiceCommandUndoSnapshot: BookVoiceCommandUndoSnapshot | null;
}

export function trimText(value: string): string {
  return value.trim();
}

export function positiveFiniteSeconds(value: number | null | undefined): number {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : 0;
}

export function latestDraftContent(draft: MemoirBookChapterDraft | null): string {
  return draft?.latest_version?.content || "";
}

export function draftStatusText(draft: MemoirBookChapterDraft | null): string {
  if (!draft?.latest_version) {
    return "未生成";
  }
  return draft.status === "confirmed" ? "已确认" : "待确认";
}

export function sourceChangeNotice(pendingCount: number, removedCount: number): string {
  if (removedCount) {
    return `${removedCount} 条素材已删除，章节稿需要更新`;
  }
  if (pendingCount) {
    return `${pendingCount} 条新增转写待补写`;
  }
  return "";
}

export function draftActionText(draft: MemoirBookChapterDraft | null, pendingCount: number, removedCount = 0): string {
  if (removedCount) {
    return "整理本章";
  }
  if (pendingCount) {
    return "补写新增";
  }
  return draft?.latest_version ? "重新生成" : "生成本章";
}

export function draftVersionText(draft: MemoirBookChapterDraft | null): string {
  const version = draft?.latest_version?.version;
  return version ? `第 ${version} 版` : "暂无正文";
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

export function bookOutputData(output: MemoirBookOutput | null): Pick<
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

export function sourceCoverageData(
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
        recordNumber: source.source_index,
        previewText: text.length > 96 ? `${text.slice(0, 96)}...` : text,
        created_at: source.created_at,
        createdAtText: formatBeijingDateTimeShort(source.created_at) || "转写记录",
        sourceRef: {
          voice_session_id: source.voice_session_id,
          message_id: source.message_id,
          utterance_id: source.utterance_id,
          transcript_version_id: source.transcript_version_id,
          audio_file_id: source.audio_file_id,
          start_ms: source.start_ms,
          end_ms: source.end_ms,
          created_at: source.created_at,
          quote: text.slice(0, 180),
        },
      };
    });
}

export function buildChapterPickerOptions(chapters: BookChapterView[]): ChapterPickerOption[] {
  return chapters.map(chapterOption);
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

function chapterOption(chapter: Pick<BookChapterView, "id" | "title" | "editingTitle">, index: number): ChapterPickerOption {
  const title = trimText(chapter.editingTitle || chapter.title) || "未命名章节";
  return {
    label: `第 ${index + 1} 章 ${title}`,
    value: chapter.id,
  };
}

function sourcePreviewText(ref: MemoirBookChapterSourceRef): string {
  const text = trimText(ref.quote || "");
  if (!text) {
    return "未摘录转写内容";
  }
  return text.length > 72 ? `${text.slice(0, 72)}...` : text;
}

function sourceCreatedAtText(ref: MemoirBookChapterSourceRef): string {
  return ref.created_at ? formatBeijingDateTimeShort(ref.created_at) : "时间未知";
}

function sourceRefSortKey(ref: MemoirBookChapterSourceRef): string {
  return sourceRefKey(ref);
}

function planSourceRecordNumbers(chapters: Pick<MemoirBookPlanChapter, "source_refs">[]): Record<string, number> {
  const seen = new Set<string>();
  const refs: MemoirBookChapterSourceRef[] = [];
  chapters.forEach((chapter) => {
    chapter.source_refs.forEach((ref) => {
      const key = sourceRefKey(ref);
      if (seen.has(key)) {
        return;
      }
      seen.add(key);
      refs.push(ref);
    });
  });
  return refs
    .sort((left, right) => (
      createdAtTime(left.created_at || "") - createdAtTime(right.created_at || "")
      || sourceRefSortKey(left).localeCompare(sourceRefSortKey(right))
    ))
    .reduce<Record<string, number>>((memo, ref, index) => {
      memo[sourceRefKey(ref)] = index + 1;
      return memo;
    }, {});
}

function coverageSourceRecordNumbers(coverage: MemoirBookSourceCoverage | null): Record<string, number> {
  return [...(coverage?.sources || [])].reduce<Record<string, number>>((memo, source) => {
    memo[source.utterance_id] = source.source_index;
    return memo;
  }, {});
}

function chapterSourceView(
  ref: MemoirBookChapterSourceRef,
  index: number,
  recordNumbers: Record<string, number>,
  fallbackRecordNumbers: Record<string, number>,
  selectedSourceIds: Set<string>,
): BookChapterSourceView {
  const previewText = sourcePreviewText(ref);
  const createdAtText = sourceCreatedAtText(ref);
  const rawCharCount = trimText(ref.quote || "").length;
  const charCount = rawCharCount || previewText.length;
  const key = sourceRefKey(ref);
  const recordNumber = recordNumbers[key] || fallbackRecordNumbers[key] || index + 1;
  return {
    ...ref,
    sourceIndex: index + 1,
    recordNumber,
    labelText: `语音记录 ${recordNumber} · 素材 ${index + 1} · ${createdAtText} · ${charCount} 字`,
    createdAtText,
    charCount,
    previewText,
    splitSelected: selectedSourceIds.has(ref.utterance_id),
  };
}

function decorateChapterStructureOptions(
  chapters: BookChapterView[],
  coverage: MemoirBookSourceCoverage | null = null,
  splitSelections: Record<string, string[]> = {},
): BookChapterView[] {
  const options = chapters.map(chapterOption);
  const recordNumbers = coverageSourceRecordNumbers(coverage);
  const fallbackRecordNumbers = planSourceRecordNumbers(chapters);
  return chapters.map((chapter, index) => {
    const targetOptions = options.filter((option) => option.value !== chapter.id);
    const selectedSourceIds = new Set(splitSelections[chapter.id] || []);
    const selectedCount = chapter.source_refs.filter((ref) => selectedSourceIds.has(ref.utterance_id)).length;
    return {
      ...chapter,
      sourceViews: chapter.source_refs.map((ref, sourceIndex) => (
        chapterSourceView(ref, sourceIndex, recordNumbers, fallbackRecordNumbers, selectedSourceIds)
      )),
      sourceToggleText: chapter.sourceListExpanded ? "收起素材" : `查看 ${chapter.source_refs.length} 条`,
      mergeTargetOptions: targetOptions,
      splitSelectedCount: selectedCount,
      splitActionText: selectedCount ? `拆出所选 ${selectedCount} 条` : "拆出所选",
      canSplitSelectedSources: selectedCount > 0 && selectedCount < chapter.source_refs.length,
      canMoveUp: index > 0,
      canMoveDown: index < chapters.length - 1,
    };
  });
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

export function chapterCollectionData(
  chapters: BookChapterView[],
  stage: BookStage,
  coverage: MemoirBookSourceCoverage | null = null,
  splitSelections: Record<string, string[]> = {},
): Pick<
  BookData,
  | "chapters"
  | "confirmedDraftCount"
  | "totalChapterCount"
  | "allChaptersConfirmed"
  | "hasDraftChanges"
  | "canGenerateBook"
  | "progressText"
  | "chapterPickerOptions"
> {
  const decorated = decorateChapterStructureOptions(chapters, coverage, splitSelections);
  return {
    chapters: decorated,
    ...chapterStateData(decorated),
    chapterPickerOptions: buildSourceActionPickerOptions(decorated, stage === "drafting"),
  };
}

export function bookChapterSourceSummary(chapter: Pick<MemoirBookPlanChapter, "source_refs">): string {
  return `本章素材：${chapter.source_refs.length} 条收录转写`;
}

export function bookOutlineNarrationText(
  planTitle: string,
  chapters: Pick<BookChapterView, "title" | "sourceCount">[],
): string {
  const title = trimText(planTitle) || "回忆录章节规划";
  const lines = [`《${title}》现在有 ${chapters.length} 章。`];
  chapters.forEach((chapter, index) => {
    const chapterTitle = trimText(chapter.title) || "未命名章节";
    lines.push(`第 ${index + 1} 章，${chapterTitle}。本章收录 ${chapter.sourceCount} 条语音素材。`);
  });
  lines.push("你可以继续调整章节顺序、合并章节，或者说出想修改的地方。");
  return lines.join("\n");
}

export function canExecuteBookVoiceCommand(preview: MemoirBookVoiceCommandParseResponse | null): boolean {
  if (!preview || !preview.operations.length || preview.intent === "unknown") {
    return false;
  }
  const operation = preview.operations[0];
  if (preview.intent === "read_outline") {
    return true;
  }
  if (preview.intent === "read_chapter") {
    return Boolean(operation.chapter_id);
  }
  if (preview.intent === "rename_chapter") {
    return Boolean(operation.chapter_id && operation.new_title);
  }
  if (preview.intent === "move_chapter") {
    return Boolean(operation.chapter_id && (operation.direction === "up" || operation.direction === "down"));
  }
  return false;
}

export function bookVoiceCommandActionText(preview: MemoirBookVoiceCommandParseResponse | null): string {
  if (!preview) {
    return "确认执行";
  }
  if (preview.intent === "read_outline" || preview.intent === "read_chapter") {
    return "开始朗读";
  }
  return "确认执行";
}

export function buildChapterViews(
  plan: MemoirBookPlan,
  draftsByChapterId: Record<string, MemoirBookChapterDraft | null> = {},
  coverage: MemoirBookSourceCoverage | null = null,
  splitSelections: Record<string, string[]> = {},
): BookChapterView[] {
  const chapters = [...plan.chapters]
    .sort((left, right) => left.sort_order - right.sort_order)
    .map((chapter) => {
      const draft = draftsByChapterId[chapter.id] || null;
      const content = latestDraftContent(draft);
      const assignedPendingCount = sourceSupplementCount(chapter.metadata_json, draft);
      const assignedRemovedCount = removedSourceCount(chapter.metadata_json, draft);
      return {
        ...chapter,
        editingTitle: chapter.title,
        titleChanged: false,
        ttsTarget: `chapter:${chapter.id}`,
        sourceCount: chapter.source_refs.length,
        sourceSummary: bookChapterSourceSummary(chapter),
        sourceViews: [],
        sourceListExpanded: false,
        sourceToggleText: "",
        mergeTargetOptions: [],
        splitSelectedCount: 0,
        splitActionText: "拆出所选",
        canSplitSelectedSources: false,
        canMoveUp: false,
        canMoveDown: false,
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
  return decorateChapterStructureOptions(chapters, coverage, splitSelections);
}

export function bookProgressText(chapters: BookChapterView[]): string {
  if (!chapters.length) {
    return "还没有章节";
  }
  const confirmed = chapters.filter((chapter) => chapter.draft?.status === "confirmed").length;
  const generated = chapters.filter((chapter) => Boolean(chapter.draft?.latest_version)).length;
  return `${confirmed}/${chapters.length} 章已确认，${generated} 章已有正文`;
}

export function planStage(plan: MemoirBookPlan | null): BookStage {
  if (!plan) {
    return "empty";
  }
  return plan.status === "confirmed" ? "drafting" : "plan_draft";
}

export function stageText(stage: BookStage): string {
  if (stage === "empty") {
    return "等待生成章节";
  }
  if (stage === "plan_draft") {
    return "确认章节结构";
  }
  return "逐章成稿";
}

export function guestBookData(): Partial<BookData> {
  return {
    needsLogin: true,
    projectId: "",
    projectTitle: "我的回忆录",
    plan: null,
    planTitle: "",
    stage: "empty",
    stageText: stageText("empty"),
    chapters: [],
    confirmedDraftCount: 0,
    totalChapterCount: 0,
    allChaptersConfirmed: false,
    hasDraftChanges: false,
    canGenerateBook: false,
    progressText: "登录后可以开始整理",
    splitSourceSelections: {},
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
    bookVoiceCommandText: "",
    bookVoiceCommandIntentLabel: "",
    bookVoiceCommandSummary: "",
    bookVoiceCommandRequiresConfirmation: false,
    bookVoiceCommandPreview: null,
    bookVoiceCommandCanExecute: false,
    bookVoiceCommandActionText: "确认执行",
    bookVoiceCommandExecuting: false,
    bookVoiceCommandUndoText: "",
    bookVoiceCommandUndoSnapshot: null,
  };
}

export function toPlanInput(chapters: BookChapterView[]): MemoirBookPlanChapterInput[] {
  return chapters.map((chapter) => ({
    title: trimText(chapter.editingTitle),
    source_refs: chapter.source_refs,
  }));
}

export function toPlanUpdateInput(chapters: BookChapterView[]): MemoirBookPlanChapterUpdateInput[] {
  return chapters.map((chapter) => ({
    id: chapter.id,
    title: trimText(chapter.editingTitle || chapter.title),
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
