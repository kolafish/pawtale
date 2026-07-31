import type { MemoirChapterWorkspace } from "../../domain/chapter-workspace";
import type { MemoirCompletionIssue } from "../../domain/memoir-completion-issue";
import { type MemoirChapterPreparationJob } from "../../services/memoir-book-completion-api";
import {
  type MemoirChapterSectionRegenerationJob,
} from "../../services/memoir-chapter-workspace-api";
import {
  aiTaskProgressPercent,
  type AITaskProgressMode,
  type AITaskProgressView,
} from "../../services/ai-task-progress";
import type { ChapterNextAction } from "./chapter-next-action";
import type {
  ChapterSectionView,
} from "./chapter-workspace-view";

export const PREPARATION_POLL_INTERVAL_MS = 1500;
export const SECTION_REGENERATION_POLL_INTERVAL_MS = 1500;

export function readableReviewText(
  text: string,
  workspace: MemoirChapterWorkspace,
): string {
  return workspace.sections
    .filter((section) => (
      section.status === "active"
      && section.include_in_book !== false
    ))
    .sort((left, right) => (
      left.sort_order - right.sort_order
      || left.id.localeCompare(right.id)
    ))
    .reduce(
      (value, section, index) => value.replace(
        new RegExp(`S${index + 1}(?!\\d)`, "g"),
        () => `第${index + 1}小节“${section.title}”`,
      ),
      text,
    );
}

export function readableIssue(
  issue: MemoirCompletionIssue,
  workspace: MemoirChapterWorkspace,
): MemoirCompletionIssue {
  return {
    ...issue,
    question: readableReviewText(issue.question, workspace),
    reason: readableReviewText(issue.reason, workspace),
  };
}

export type ChapterWorkspaceOperationKind =
  | ""
  | "prepare"
  | "regenerate_sections"
  | "merge_sections"
  | "move_material"
  | "confirm_issue"
  | "save_draft_section"
  | "confirm_chapter"
  | "revise_draft_section_with_ai"
  | "rewrite_chapter";

export type ChapterWorkspaceAudioPhase = "idle" | "preparing" | "playing";
export type DraftSectionEditingMode = "" | "manual" | "ai";

export function isActiveSectionRegeneration(
  job: MemoirChapterSectionRegenerationJob | null,
): boolean {
  return job?.status === "pending" || job?.status === "running";
}

export function isActiveChapterPreparation(
  job: MemoirChapterPreparationJob | null,
): boolean {
  return job?.status === "pending" || job?.status === "running";
}

export function chapterPreparationView(
  job: MemoirChapterPreparationJob,
): AITaskProgressView {
  const model = job.resolved_ai_model || job.requested_ai_model;
  const modelDetail = model ? ` · 模型 ${model}` : "";
  if (job.status === "failed") {
    const savedDetail = job.completed_section_count
      ? ` · 已保留 ${job.completed_section_count}/${job.total_section_count} 个小节`
      : "";
    return {
      title: job.operation === "rewrite" ? "正文生成失败" : "本章检查失败",
      detail: `${job.error_message || "可以重新开始"}${savedDetail}${modelDetail}`,
      progress: 0,
      progressMode: "hidden",
    };
  }
  if (job.status === "cancelled") {
    return {
      title: "已取消处理",
      detail: `原有章节和正文保持不变${modelDetail}`,
      progress: 0,
      progressMode: "hidden",
    };
  }
  if (job.stage === "checking_sections") {
    const progress = aiTaskProgressPercent(
      job.checked_section_count,
      job.total_section_count,
    );
    return {
      title: "正在逐节检查素材",
      detail: `已检查 ${job.checked_section_count}/${job.total_section_count} 个小节${job.current_section_title ? ` · ${job.current_section_title}` : ""}${modelDetail}`,
      progress,
      progressMode: "determinate",
    };
  }
  if (job.stage === "checking_chapter") {
    return {
      title: "正在检查整章结构",
      detail: `小节检查已完成，正在判断是否需要补充${modelDetail}`,
      progress: 40,
      progressMode: "indeterminate",
    };
  }
  if (job.stage === "generating_sections") {
    const progress = aiTaskProgressPercent(
      job.completed_section_count,
      job.total_section_count,
    );
    const resumed = job.resumed_section_count
      ? ` · 已恢复 ${job.resumed_section_count} 节`
      : "";
    return {
      title: job.operation === "rewrite"
        ? "正在更新有变化的小节"
        : "正在逐节生成正文",
      detail: `${job.operation === "rewrite" ? "已更新" : "已完成"} ${job.completed_section_count}/${job.total_section_count} 个小节${job.current_section_title ? ` · ${job.current_section_title}` : ""}${resumed}${modelDetail}`,
      progress,
      progressMode: "determinate",
    };
  }
  if (job.stage === "saving") {
    return {
      title: "正在保存本章正文",
      detail: `小节正文已经处理完成，正在更新章节版本${modelDetail}`,
      progress: 0,
      progressMode: "indeterminate",
    };
  }
  return {
    title: job.operation === "rewrite" ? "正文更新任务已排队" : "本章检查任务已排队",
    detail: `${job.operation === "rewrite" ? "即将更新" : "即将处理"} ${job.total_section_count} 个小节${modelDetail}`,
    progress: 0,
    progressMode: "indeterminate",
  };
}

export function sectionRegenerationView(
  job: MemoirChapterSectionRegenerationJob,
): AITaskProgressView {
  const model = job.resolved_ai_model || job.requested_ai_model;
  const modelDetail = model ? ` · 模型 ${model}` : "";
  if (job.status === "failed") {
    return {
      title: "小节整理失败",
      detail: `${job.error_message || "请稍后重新整理"}${modelDetail}`,
      progress: 0,
      progressMode: "hidden",
    };
  }
  if (job.status === "cancelled") {
    return {
      title: "已取消整理",
      detail: "原小节结构保持不变",
      progress: 0,
      progressMode: "hidden",
    };
  }
  if (job.stage === "planning_sections") {
    return {
      title: "正在规划小节结构",
      detail: `正在理解全章 ${job.material_count} 条讲述${modelDetail}`,
      progress: 0,
      progressMode: "indeterminate",
    };
  }
  if (job.stage === "assigning_materials") {
    const progress = aiTaskProgressPercent(
      job.processed_char_count,
      job.total_char_count,
    );
    return {
      title: "正在归类讲述",
      detail: `已处理 ${job.processed_material_count}/${job.material_count} 条 · ${job.completed_batch_count}/${job.batch_count} 批${modelDetail}`,
      progress,
      progressMode: "determinate",
    };
  }
  if (job.stage === "saving") {
    return {
      title: "正在保存新的小节结构",
      detail: `全部讲述已归类，正在进行一致性检查${modelDetail}`,
      progress: 0,
      progressMode: "indeterminate",
    };
  }
  return {
    title: "小节整理任务已排队",
    detail: `即将开始处理 ${job.material_count} 条讲述${modelDetail}`,
    progress: 0,
    progressMode: "indeterminate",
  };
}

export const CLOSED_DRAFT_SECTION_EDITOR = {
  editingMode: "" as DraftSectionEditingMode,
  editingSectionId: "",
  editingSectionContent: "",
  draftSectionAIInstruction: "",
};

type WxWithPageScrollTo = typeof wx & {
  pageScrollTo?: (options: { selector: string; duration?: number }) => void;
};

export function scrollToSection(selector: string) {
  const pageScrollTo = (wx as WxWithPageScrollTo).pageScrollTo;
  if (typeof pageScrollTo === "function") {
    pageScrollTo({ selector, duration: 250 });
  }
}

export interface ChapterWorkspacePageData {
  pageClass: string;
  loading: boolean;
  error: string;
  projectId: string;
  chapterId: string;
  workspace: MemoirChapterWorkspace | null;
  nextAction: ChapterNextAction;
  readinessTitle: string;
  readinessDetail: string;
  rewriteNoticeTitle: string;
  rewriteNoticeDetail: string;
  draftRecoveryTitle: string;
  draftRecoveryDetail: string;
  draftRecoveryActionText: string;
  draftRecoveryActionKind: "none" | "next" | "rewrite" | "status";
  draftRecoveryActionDisabled: boolean;
  narrativeSectionCount: number;
  sectionStructureSummary: string;
  rewriteDraftActionText: string;
  rewriteDraftBusyText: string;
  rewriteDraftScopeDetail: string;
  suggestionsExpanded: boolean;
  requiredIssues: MemoirCompletionIssue[];
  optionalIssues: MemoirCompletionIssue[];
  sectionViews: ChapterSectionView[];
  retainedDraftSectionCount: number;
  expandedSectionIds: Record<string, boolean>;
  canConfirmChapter: boolean;
  operationKind: ChapterWorkspaceOperationKind;
  operationTargetId: string;
  chapterPreparationJob: MemoirChapterPreparationJob | null;
  chapterPreparationTitle: string;
  chapterPreparationDetail: string;
  chapterPreparationProgress: number;
  chapterPreparationProgressMode: AITaskProgressMode;
  sectionRegenerationJob: MemoirChapterSectionRegenerationJob | null;
  sectionRegenerationTitle: string;
  sectionRegenerationDetail: string;
  sectionRegenerationProgress: number;
  sectionRegenerationProgressMode: AITaskProgressMode;
  audioPhase: ChapterWorkspaceAudioPhase;
  audioTarget: string;
  editingMode: DraftSectionEditingMode;
  editingSectionId: string;
  editingSectionContent: string;
  draftSectionDictationUrl: string;
  draftSectionAIInstruction: string;
}
