import type { MemoirChapterWorkspace } from "../../domain/chapter-workspace";
import type {
  MemoirChapterDraftSection,
  MemoirChapterSectionMaterialSummary,
  MemoirChapterSectionSummary,
  MemoirSectionType,
} from "../../domain/voice-chat";
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
  expanded: boolean;
  visibleMaterials: ChapterSectionMaterialView[];
}

export interface ChapterDraftSectionView extends MemoirChapterDraftSection {
  indexText: string;
  metaText: string;
  expanded: boolean;
}

export interface ChapterWorkspaceViewPatch {
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
  sectionViews: ChapterSectionView[];
  draftSectionViews: ChapterDraftSectionView[];
  canConfirmChapter: boolean;
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
    title: "＋ 新建小节",
    action: "create_section",
  });
  return [...(workspace.sections || [])]
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
    ))
    .map((section, sectionIndex) => {
      const visibleMaterials = [...(section.materials || [])]
        .filter((material) => (
          section.requires_user_assignment
          || material.include_in_book !== false
        ))
        .sort((left, right) => left.sort_order - right.sort_order)
        .map((material, materialIndex) => ({
          ...material,
          indexText: `讲述 ${materialIndex + 1}`,
          assignmentOptions: section.requires_user_assignment
            ? assignmentOptions
            : [],
        }));
      return {
        ...section,
        indexText: section.requires_user_assignment
          ? "待"
          : `${sectionIndex + 1}`,
        typeText: section.requires_user_assignment
          ? "需要选择小节"
          : SECTION_TYPE_TEXT[section.section_type] || "内容小节",
        metaText: `${visibleMaterials.length} 条讲述`,
        assignmentHint: section.requires_user_assignment
          ? "这些讲述都还保留在本章中。请选择最合适的小节，完成后才能生成正文。"
          : "",
        expanded: section.requires_user_assignment
          ? expandedSectionIds[section.id] !== false
          : Boolean(expandedSectionIds[section.id]),
        visibleMaterials,
      };
    });
}

function visibleDraftSectionViews(
  workspace: MemoirChapterWorkspace,
  expandedDraftSectionIds: Record<string, boolean>,
): ChapterDraftSectionView[] {
  return [...(workspace.draft_sections || [])]
    .sort((left, right) => left.sort_order - right.sort_order)
    .map((section, index) => ({
      ...section,
      indexText: `${index + 1}`,
      metaText: [
        `${section.used_material_ids.length} 条讲述整理`,
        `${section.content.replace(/\s/g, "").length} 字`,
      ].join(" · "),
      expanded: Boolean(expandedDraftSectionIds[section.section_id]),
    }));
}

function draftStructureChanged(workspace: MemoirChapterWorkspace): boolean {
  if (!workspace.draft_sections.length) {
    return false;
  }
  const currentSectionIds = workspace.sections
    .filter((section) => (
      section.status === "active"
      && section.include_in_book !== false
      && !section.requires_user_assignment
    ))
    .map((section) => section.id)
    .sort();
  const draftSectionIds = workspace.draft_sections
    .map((section) => section.section_id)
    .sort();
  return currentSectionIds.length !== draftSectionIds.length
    || currentSectionIds.some(
      (sectionId, index) => sectionId !== draftSectionIds[index],
    );
}

function rewriteNoticeTitle(
  workspace: MemoirChapterWorkspace,
  structureChanged: boolean,
): string {
  const completionCount = workspace.pending_completion_source_count || 0;
  const newCount = workspace.pending_new_source_count || 0;
  if (completionCount && newCount) {
    return `${completionCount} 条补充回答和 ${newCount} 条新增讲述尚未写入正文`;
  }
  if (completionCount) {
    return `${completionCount} 条补充回答尚未写入正文`;
  }
  if (newCount) {
    return `${newCount} 条新增讲述尚未写入正文`;
  }
  if (structureChanged) {
    return "现有正文仍按原来的小节保留";
  }
  return "本章素材已经变化";
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
      draftRecoveryDetail: `先点击“${nextAction.actionText}”完成当前步骤；如果还有必需事项，这里会继续显示下一步，全部完成后会出现“${rewriteDraftActionText}”。上方“重新整理小节分组”不是当前必做步骤。`,
      draftRecoveryActionText: nextAction.actionText,
      draftRecoveryActionKind: "next",
      draftRecoveryActionDisabled: nextAction.disabled,
    };
  }
  return {
    draftRecoveryTitle: "先查看本章当前步骤",
    draftRecoveryDetail: `完成页面顶部要求处理的事项后，即可在这里${rewriteDraftActionText}。上方“重新整理小节分组”不是当前必做步骤。`,
    draftRecoveryActionText: "查看当前步骤",
    draftRecoveryActionKind: "status",
    draftRecoveryActionDisabled: false,
  };
}

export function chapterWorkspaceViewPatch(
  workspace: MemoirChapterWorkspace,
  expandedSectionIds: Record<string, boolean> = {},
  expandedDraftSectionIds: Record<string, boolean> = {},
): ChapterWorkspaceViewPatch {
  const summary = workspace.completion_summary;
  const nextAction = chapterNextAction(summary);
  const sectionViews = visibleSectionViews(workspace, expandedSectionIds);
  const pendingAssignmentCount =
    workspace.pending_assignment_material_count || 0;
  const narrativeSectionCount = sectionViews.filter(
    (section) => !section.requires_user_assignment,
  ).length;
  const rewriteDraftActionText = narrativeSectionCount
    ? `重新生成全部 ${narrativeSectionCount} 个小节正文`
    : "重新生成全部小节正文";
  const rewriteDraftBusyText = narrativeSectionCount
    ? `正在生成 ${narrativeSectionCount} 个小节正文…`
    : "正在生成全部小节正文…";
  const rewriteDraftScopeDetail = narrativeSectionCount
    ? `会按照上方当前 ${narrativeSectionCount} 个小节逐个生成正文，全部成功后更新整章；不会重新划分小节，旧正文会保留在历史版本中。`
    : "会按照上方当前小节逐个生成正文，全部成功后更新整章；不会重新划分小节，旧正文会保留在历史版本中。";
  const materialCount = sectionViews.reduce(
    (total, section) => total + section.visibleMaterials.length,
    0,
  );
  const structureChanged = draftStructureChanged(workspace);
  return {
    nextAction,
    readinessTitle: summary.headline,
    readinessDetail: summary.detail,
    rewriteNoticeTitle: rewriteNoticeTitle(workspace, structureChanged),
    rewriteNoticeDetail: structureChanged
      ? (
        workspace.can_rewrite_draft
          ? `旧正文没有丢失。${rewriteDraftScopeDetail}`
          : `旧正文没有丢失。请先完成待归类等当前步骤，再${rewriteDraftActionText}。`
      )
      : (
        workspace.can_rewrite_draft
          ? `可以继续补充，也可以现在生成。${rewriteDraftScopeDetail}`
          : `请先完成本章当前步骤，再${rewriteDraftActionText}；这不会重新划分小节。`
      ),
    ...draftRecoveryPatch(
      workspace,
      nextAction,
      rewriteDraftActionText,
      rewriteDraftScopeDetail,
    ),
    narrativeSectionCount,
    sectionStructureSummary: [
      `${narrativeSectionCount} 个小节`,
      `${materialCount} 条讲述`,
      ...(pendingAssignmentCount
        ? [`${pendingAssignmentCount} 条待归类`]
        : []),
    ].join(" · "),
    rewriteDraftActionText,
    rewriteDraftBusyText,
    rewriteDraftScopeDetail,
    sectionViews,
    draftSectionViews: visibleDraftSectionViews(
      workspace,
      expandedDraftSectionIds,
    ),
    canConfirmChapter:
      summary.state === "draft_ready"
      && !workspace.draft_needs_rewrite
      && Boolean(workspace.draft_version_id)
      && workspace.draft_sections.length > 0
      && workspace.draft_sections.every(
        (section) => Boolean(section.content.trim()),
      )
      && !pendingAssignmentCount,
  };
}
