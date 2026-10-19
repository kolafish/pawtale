import type { MemoirChapterWorkspace } from "../../domain/chapter-workspace";
import type { MemoirChapterPreparationJob } from "../../services/memoir-book-completion-api";
import type {
  MemoirChapterSectionMaterialSummary,
  MemoirChapterSectionSummary,
  MemoirSectionType,
} from "../../domain/memoir-book";
import { chapterNextAction, type ChapterNextAction } from "./chapter-next-action";

export interface ChapterSectionMaterialView extends MemoirChapterSectionMaterialSummary {
  indexText: string;
  assignmentOptions: Array<{
    id: string;
    title: string;
    action?: "create_section";
  }>;
}

export interface ChapterSectionView extends MemoirChapterSectionSummary {
  indexText: string;
  typeText: string;
  metaText: string;
  assignmentHint: string;
  mergeTargetId: string;
  mergeTargetTitle: string;
  hasDraft: boolean;
  draftContent: string;
  draftCanExpand: boolean;
  draftNeedsUpdate: boolean;
  expanded: boolean;
  visibleMaterials: ChapterSectionMaterialView[];
}

export interface ChapterWorkspaceViewPatch {
  nextAction: ChapterNextAction;
  chapterHeroKicker: string;
  chapterStoryCountText: string;
  chapterStoryDetail: string;
  chapterHasReadableDraft: boolean;
  chapterEditingHint: string;
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
  sectionViews: ChapterSectionView[];
  retainedDraftSectionCount: number;
}

export type ChapterCandidateMode = "changes" | "whole";
export type ChapterCandidatePreviewVersion = "before" | "after";

export type ChapterCandidateDiffKind = "equal" | "deleted" | "modified" | "added";

export interface ChapterCandidateDiffSegmentView {
  key: string;
  kind: ChapterCandidateDiffKind;
  text: string;
}

export interface ChapterCandidateViewPatch {
  candidateMode: ChapterCandidateMode;
  candidatePreviewVersion: ChapterCandidatePreviewVersion;
  candidateComparisonAvailable: boolean;
  candidateSummaryText: string;
  candidateDiffSegments: ChapterCandidateDiffSegmentView[];
  candidateWholeContent: string;
}

function visibleCharCount(value: string): number {
  return value.replace(/\s/g, "").length;
}

export function chapterCandidateViewPatch(
  workspace: MemoirChapterWorkspace | null,
  job: MemoirChapterPreparationJob | null,
  mode: ChapterCandidateMode = "changes",
  previewVersion: ChapterCandidatePreviewVersion = "after",
): ChapterCandidateViewPatch {
  const candidateContent = job?.candidate_content || "";
  const candidateSections = job?.candidate_sections || [];
  const candidateDiffSegments = (job?.candidate_diff_segments || []).map(
    (segment, index) => ({ ...segment, key: `${index}-${segment.kind}` }),
  );
  const hasTextChanges = candidateDiffSegments.some((segment) => segment.kind !== "equal");
  const comparisonAvailable = Boolean(
    workspace
    && candidateContent
    && candidateDiffSegments.length
    && job?.candidate_base_version_id === (workspace.draft_version_id || null),
  );
  if (!workspace || !candidateContent) {
    return {
      candidateMode: mode,
      candidatePreviewVersion: previewVersion,
      candidateComparisonAvailable: false,
      candidateSummaryText: "",
      candidateDiffSegments: [],
      candidateWholeContent: "",
    };
  }
  if (!comparisonAvailable) {
    return {
      candidateMode: "whole",
      candidatePreviewVersion: "after",
      candidateComparisonAvailable: false,
      candidateSummaryText: "当前稿仍在使用。请核对这次更新后再决定是否采用。",
      candidateDiffSegments: [],
      candidateWholeContent: candidateContent,
    };
  }

  const beforeById = new Map(
    workspace.draft_sections.map((section) => [section.section_id, section]),
  );
  const afterIds = new Set(candidateSections.map((section) => section.section_id));
  const changed = [
    ...candidateSections.map((section) => ({
      section_id: section.section_id,
      title: section.title || beforeById.get(section.section_id)?.title || "故事正文",
      before_content: beforeById.get(section.section_id)?.content || "",
      after_content: section.content,
    })),
    ...workspace.draft_sections
      .filter((section) => !afterIds.has(section.section_id))
      .map((section) => ({
        section_id: section.section_id,
        title: section.title,
        before_content: section.content,
        after_content: "",
      })),
  ].filter((section) => section.before_content !== section.after_content);
  const delta = visibleCharCount(candidateContent) - visibleCharCount(workspace.draft_content);
  const deltaText = delta > 0
    ? `增加 ${delta} 字`
    : delta < 0
      ? `减少 ${Math.abs(delta)} 字`
      : "字数不变";
  const effectiveMode = hasTextChanges ? mode : "whole";
  return {
    candidateMode: effectiveMode,
    candidatePreviewVersion: previewVersion,
    candidateComparisonAvailable: true,
    candidateSummaryText: hasTextChanges
      ? `与当前稿相比：${changed.length ? `${changed.length} 个故事有变化` : "正文有变化"} · ${deltaText}`
      : "新稿正文与当前稿相同。",
    candidateDiffSegments,
    candidateWholeContent: previewVersion === "before"
      ? workspace.draft_content
      : candidateContent,
  };
}

const SECTION_TYPE_TEXT: Record<MemoirSectionType, string> = {
  event: "一段经历",
  life_fragment: "生活片段",
  person_portrait: "人物小记",
  life_stage_summary: "阶段回顾",
  viewpoint_reflection: "感想与思考",
};

function visibleSectionViews(
  workspace: MemoirChapterWorkspace,
  expandedSectionIds: Record<string, boolean>,
): ChapterSectionView[] {
  const draftsBySectionId = new Map(
    workspace.draft_sections.map((section) => [section.section_id, section]),
  );
  const assignmentOptions: ChapterSectionMaterialView["assignmentOptions"] = [
    ...(workspace.sections || []),
  ]
    .filter((section) => (
      section.status === "active"
      && section.include_in_book !== false
      && !section.requires_user_assignment
    ))
    .sort((left, right) => left.sort_order - right.sort_order)
    .map((section, index) => ({
      id: section.id,
      title: `${index + 1}. ${section.title}`,
    }));
  assignmentOptions.push({
    id: "",
    title: "＋ 新建故事",
    action: "create_section",
  });
  const visibleSections = [...(workspace.sections || [])]
    .filter((section) => (
      section.status === "active"
      && (
        section.include_in_book !== false
        || (
          section.requires_user_assignment
          && section.material_count > 0
        )
      )
    ))
    .sort((left, right) => (
      Number(left.requires_user_assignment)
      - Number(right.requires_user_assignment)
      || left.sort_order - right.sort_order
    ));
  let previousNarrativeSection: MemoirChapterSectionSummary | undefined;
  return visibleSections
    .map((section, sectionIndex) => {
      const draft = section.requires_user_assignment
        ? undefined
        : draftsBySectionId.get(section.id);
      const draftNeedsUpdate = draft
        ? (
          draft.title !== section.title
          || draft.section_type !== section.section_type
          || draft.section_version !== section.version
          || draft.sort_order !== section.sort_order
        )
        : false;
      const mergeTarget = section.requires_user_assignment
        ? undefined
        : previousNarrativeSection;
      const visibleMaterials = [...(section.materials || [])]
        .filter((material) => (
          section.requires_user_assignment
          || material.include_in_book !== false
        ))
        .sort((left, right) => left.sort_order - right.sort_order)
        .map((material, materialIndex) => ({
          ...material,
          indexText: `第 ${materialIndex + 1} 段`,
          assignmentOptions: section.requires_user_assignment
            ? assignmentOptions
            : [],
        }));
      if (!section.requires_user_assignment) {
        previousNarrativeSection = section;
      }
      return {
        ...section,
        indexText: section.requires_user_assignment
          ? "待"
          : String(sectionIndex + 1).padStart(2, "0"),
        typeText: section.requires_user_assignment
          ? "暂不纳入正文"
          : SECTION_TYPE_TEXT[section.section_type] || "故事",
        metaText: section.requires_user_assignment
          ? `${visibleMaterials.length} 段待整理的回忆`
          : `${visibleMaterials.length} 段回忆${draftNeedsUpdate ? " · 故事待更新" : ""}`,
        assignmentHint: section.requires_user_assignment
          ? "这些回忆暂未归入故事，因此不会纳入本章正文。您可以稍后归类，也可以点击回忆进入编辑或删除。"
          : "",
        mergeTargetId: mergeTarget?.id || "",
        mergeTargetTitle: mergeTarget?.title || "",
        hasDraft: Boolean(draft),
        draftContent: draft?.content || "",
        draftCanExpand: Boolean(
          draft
          && (
            visibleCharCount(draft.content) > 140
            || draft.content.split("\n").length > 7
          )
        ),
        draftNeedsUpdate,
        expanded: section.requires_user_assignment
          ? expandedSectionIds[section.id] !== false
          : Boolean(expandedSectionIds[section.id]),
        visibleMaterials,
      };
    });
}

function currentNarrativeSectionIds(
  workspace: MemoirChapterWorkspace,
): string[] {
  return workspace.sections
    .filter((section) => (
      section.status === "active"
      && section.include_in_book !== false
      && !section.requires_user_assignment
    ))
    .map((section) => section.id);
}

function retainedDraftSectionCount(workspace: MemoirChapterWorkspace): number {
  const currentSectionIds = new Set(currentNarrativeSectionIds(workspace));
  return workspace.draft_sections.filter(
    (section) => !currentSectionIds.has(section.section_id),
  ).length;
}

function draftStructureChanged(workspace: MemoirChapterWorkspace): boolean {
  if (!workspace.draft_sections.length) {
    return false;
  }
  const currentSectionIds = currentNarrativeSectionIds(workspace).sort();
  const draftSectionIds = workspace.draft_sections
    .map((section) => section.section_id)
    .sort();
  return currentSectionIds.length !== draftSectionIds.length
    || currentSectionIds.some(
      (sectionId, index) => sectionId !== draftSectionIds[index],
    );
}

function changedDraftSectionCount(
  workspace: MemoirChapterWorkspace,
): number {
  const draftsBySectionId = new Map(
    workspace.draft_sections.map((section) => [section.section_id, section]),
  );
  return workspace.sections.filter((section) => {
    if (
      section.status !== "active"
      || section.include_in_book === false
      || section.requires_user_assignment
    ) {
      return false;
    }
    const draft = draftsBySectionId.get(section.id);
    return !draft
      || draft.title !== section.title
      || draft.section_type !== section.section_type
      || draft.section_version !== section.version
      || draft.sort_order !== section.sort_order;
  }).length;
}

function rewriteNoticeTitle(
  workspace: MemoirChapterWorkspace,
  structureChanged: boolean,
): string {
  const completionCount = workspace.pending_completion_source_count || 0;
  const newCount = workspace.pending_new_source_count || 0;
  if (completionCount && newCount) {
    return `${completionCount} 条补充回答和 ${newCount} 段新增回忆尚未写入正文`;
  }
  if (completionCount) {
    return `${completionCount} 条补充回答尚未写入正文`;
  }
  if (newCount) {
    return `${newCount} 段新增回忆尚未写入正文`;
  }
  if (structureChanged) {
    return "现有正文仍按原来的故事保留";
  }
  return "本章回忆已经变化";
}

function draftRecoveryPatch(
  workspace: MemoirChapterWorkspace,
  nextAction: ChapterNextAction,
  rewriteDraftActionText: string,
  rewriteDraftScopeDetail: string,
): Pick<
  ChapterWorkspaceViewPatch,
  | "draftRecoveryTitle"
  | "draftRecoveryDetail"
  | "draftRecoveryActionText"
  | "draftRecoveryActionKind"
  | "draftRecoveryActionDisabled"
> {
  if (!workspace.draft_content || workspace.draft_sections.length > 0) {
    return {
      draftRecoveryTitle: "",
      draftRecoveryDetail: "",
      draftRecoveryActionText: "",
      draftRecoveryActionKind: "none",
      draftRecoveryActionDisabled: true,
    };
  }
  if (workspace.can_rewrite_draft) {
    return {
      draftRecoveryTitle: `现在可以${rewriteDraftActionText}`,
      draftRecoveryDetail: rewriteDraftScopeDetail,
      draftRecoveryActionText: rewriteDraftActionText,
      draftRecoveryActionKind: "rewrite",
      draftRecoveryActionDisabled: false,
    };
  }
  if (nextAction.actionText) {
    return {
      draftRecoveryTitle: "先完成本章当前一步",
      draftRecoveryDetail: `先点击“${nextAction.actionText}”完成当前步骤；如果还有必需事项，这里会继续显示下一步，全部完成后会出现“${rewriteDraftActionText}”。“重新整理故事分组”不是当前必做步骤。`,
      draftRecoveryActionText: nextAction.actionText,
      draftRecoveryActionKind: "next",
      draftRecoveryActionDisabled: nextAction.disabled,
    };
  }
  return {
    draftRecoveryTitle: "先查看本章当前步骤",
    draftRecoveryDetail: `完成页面顶部要求处理的事项后，即可在这里${rewriteDraftActionText}。“重新整理故事分组”不是当前必做步骤。`,
    draftRecoveryActionText: "查看当前步骤",
    draftRecoveryActionKind: "status",
    draftRecoveryActionDisabled: false,
  };
}

export function chapterWorkspaceViewPatch(
  workspace: MemoirChapterWorkspace,
  expandedSectionIds: Record<string, boolean> = {},
): ChapterWorkspaceViewPatch {
  const summary = workspace.completion_summary;
  const nextAction = chapterNextAction(summary);
  const sectionViews = visibleSectionViews(workspace, expandedSectionIds);
  const pendingAssignmentCount =
    workspace.pending_assignment_material_count || 0;
  const narrativeSectionCount = sectionViews.filter(
    (section) => !section.requires_user_assignment,
  ).length;
  const affectedSectionCount = changedDraftSectionCount(workspace);
  const rewriteDraftActionText = "更新相关故事";
  const rewriteDraftBusyText = "正在更新相关故事…";
  const rewriteDraftScopeDetail = affectedSectionCount
    ? `本章有 ${affectedSectionCount} 个故事的素材或结构发生变化。会重点更新相关内容，并按需要调整有联系的故事衔接；旧正文保留在历史版本中。`
    : "会根据当前故事整理相关内容，并按需要调整衔接；旧正文保留在历史版本中。";
  const includedMaterialCount = sectionViews.reduce(
    (total, section) => (
      section.requires_user_assignment
        ? total
        : total + section.visibleMaterials.length
    ),
    0,
  );
  const structureChanged = draftStructureChanged(workspace);
  const chapterHasReadableDraft = Boolean(
    workspace.draft_version_id
    && workspace.draft_content.trim(),
  );
  const needsMaterial = !chapterHasReadableDraft && !includedMaterialCount && !pendingAssignmentCount;
  const emptyDraftTitle = summary.state === "failed" || summary.state === "preparing"
    ? summary.headline
    : needsMaterial ? "本章还需要讲述素材" : summary.headline;
  const emptyDraftDetail = summary.state === "failed" || summary.state === "preparing"
    ? summary.detail
    : needsMaterial ? "请回到首页继续讲述与本章有关的回忆，补充后再查看本章当前步骤。" : summary.detail;
  return {
    nextAction,
    chapterHeroKicker: chapterHasReadableDraft
      ? "已整理"
      : summary.state === "failed" ? "这一章尚未整理完成" : emptyDraftTitle,
    chapterStoryCountText: narrativeSectionCount
      ? `${narrativeSectionCount} 个故事`
      : "暂无故事",
    chapterStoryDetail: chapterHasReadableDraft && workspace.draft_needs_rewrite
      ? "有新回忆待整理，当前正文仍可阅读。"
      : summary.state === "draft_ready"
        ? ""
        : chapterHasReadableDraft
          ? ""
          : emptyDraftDetail,
    chapterHasReadableDraft,
    chapterEditingHint: pendingAssignmentCount
      ? `${pendingAssignmentCount} 段回忆待整理`
      : workspace.draft_needs_rewrite
        ? "有新回忆等待写进故事"
        : "",
    readinessTitle: chapterHasReadableDraft ? summary.headline : emptyDraftTitle,
    readinessDetail: chapterHasReadableDraft ? summary.detail : emptyDraftDetail,
    rewriteNoticeTitle: rewriteNoticeTitle(workspace, structureChanged),
    rewriteNoticeDetail: structureChanged
      ? (
        workspace.can_rewrite_draft
          ? `旧正文没有丢失。${rewriteDraftScopeDetail}`
          : `旧正文没有丢失。请先完成当前必需步骤，再${rewriteDraftActionText}。`
      )
      : (
        workspace.can_rewrite_draft
          ? `可以继续补充，也可以现在生成。${rewriteDraftScopeDetail}`
          : `请先完成本章当前步骤，再${rewriteDraftActionText}；这不会重新划分故事。`
      ),
    ...draftRecoveryPatch(
      workspace,
      nextAction,
      rewriteDraftActionText,
      rewriteDraftScopeDetail,
    ),
    narrativeSectionCount,
    sectionStructureSummary: [
      `${narrativeSectionCount} 个故事`,
      `${includedMaterialCount} 段回忆`,
      ...(pendingAssignmentCount
        ? [`${pendingAssignmentCount} 段待整理`]
        : []),
    ].join(" · "),
    rewriteDraftActionText,
    rewriteDraftBusyText,
    rewriteDraftScopeDetail,
    sectionViews,
    retainedDraftSectionCount: retainedDraftSectionCount(workspace),
  };
}
