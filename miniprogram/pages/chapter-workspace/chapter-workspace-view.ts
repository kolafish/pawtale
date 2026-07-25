import type { MemoirChapterWorkspace } from "../../domain/chapter-workspace";
import type {
  MemoirChapterSectionMaterialSummary,
  MemoirChapterSectionSummary,
  MemoirSectionType,
} from "../../domain/voice-chat";
import { chapterNextAction, type ChapterNextAction } from "./chapter-next-action";

export interface ChapterSectionMaterialView extends MemoirChapterSectionMaterialSummary {
  indexText: string;
  assignmentOptions: Array<{ id: string; title: string }>;
}

export interface ChapterSectionView extends MemoirChapterSectionSummary {
  indexText: string;
  typeText: string;
  metaText: string;
  assignmentHint: string;
  expanded: boolean;
  visibleMaterials: ChapterSectionMaterialView[];
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
  sectionStructureSummary: string;
  sectionViews: ChapterSectionView[];
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
  const assignmentOptions = [...(workspace.sections || [])]
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

function rewriteNoticeTitle(workspace: MemoirChapterWorkspace): string {
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
  return "本章素材已经变化";
}

function draftRecoveryPatch(
  workspace: MemoirChapterWorkspace,
  nextAction: ChapterNextAction,
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
      draftRecoveryTitle: "现在可以重新生成小节正文",
      draftRecoveryDetail: "系统会按当前小节结构重新整理正文，旧版本仍会保留。上方“重新整理小节分组”只在分组不合适时使用。",
      draftRecoveryActionText: "重新生成小节正文",
      draftRecoveryActionKind: "rewrite",
      draftRecoveryActionDisabled: false,
    };
  }
  if (nextAction.actionText) {
    return {
      draftRecoveryTitle: "先完成本章当前一步",
      draftRecoveryDetail: `先点击“${nextAction.actionText}”完成当前步骤；如果还有必需事项，这里会继续显示下一步，全部完成后会出现“重新生成小节正文”。上方“重新整理小节分组”不是当前必做步骤。`,
      draftRecoveryActionText: nextAction.actionText,
      draftRecoveryActionKind: "next",
      draftRecoveryActionDisabled: nextAction.disabled,
    };
  }
  return {
    draftRecoveryTitle: "先查看本章当前步骤",
    draftRecoveryDetail: "完成页面顶部要求处理的事项后，即可在这里重新生成小节正文。上方“重新整理小节分组”不是当前必做步骤。",
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
  const materialCount = sectionViews.reduce(
    (total, section) => total + section.visibleMaterials.length,
    0,
  );
  return {
    nextAction,
    readinessTitle: summary.headline,
    readinessDetail: summary.detail,
    rewriteNoticeTitle: rewriteNoticeTitle(workspace),
    rewriteNoticeDetail: workspace.can_rewrite_draft
      ? "可以继续补充，也可以先用本章全部现有素材重新生成完整正文；旧版本仍会保留。"
      : "请先完成本章当前步骤，再重新生成正文。",
    ...draftRecoveryPatch(workspace, nextAction),
    sectionStructureSummary: [
      `${narrativeSectionCount} 个小节`,
      `${materialCount} 条讲述`,
      ...(pendingAssignmentCount
        ? [`${pendingAssignmentCount} 条待归类`]
        : []),
    ].join(" · "),
    sectionViews,
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
