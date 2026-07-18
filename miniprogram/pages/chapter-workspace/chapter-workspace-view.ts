import type { MemoirChapterWorkspace } from "../../domain/chapter-workspace";
import type {
  MemoirChapterSectionMaterialSummary,
  MemoirChapterSectionSummary,
  MemoirSectionType,
} from "../../domain/voice-chat";
import { chapterNextAction, type ChapterNextAction } from "./chapter-next-action";

export interface ChapterSectionMaterialView extends MemoirChapterSectionMaterialSummary {
  indexText: string;
}

export interface ChapterSectionView extends MemoirChapterSectionSummary {
  indexText: string;
  typeText: string;
  metaText: string;
  expanded: boolean;
  visibleMaterials: ChapterSectionMaterialView[];
}

export interface ChapterWorkspaceViewPatch {
  nextAction: ChapterNextAction;
  readinessTitle: string;
  readinessDetail: string;
  rewriteNoticeTitle: string;
  rewriteNoticeDetail: string;
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
  return [...(workspace.sections || [])]
    .filter((section) => (
      section.status === "active"
      && section.include_in_book !== false
    ))
    .sort((left, right) => left.sort_order - right.sort_order)
    .map((section, sectionIndex) => {
      const visibleMaterials = [...(section.materials || [])]
        .filter((material) => material.include_in_book !== false)
        .sort((left, right) => left.sort_order - right.sort_order)
        .map((material, materialIndex) => ({
          ...material,
          indexText: `讲述 ${materialIndex + 1}`,
        }));
      return {
        ...section,
        indexText: `${sectionIndex + 1}`,
        typeText: SECTION_TYPE_TEXT[section.section_type] || "内容小节",
        metaText: `${visibleMaterials.length} 条讲述`,
        expanded: Boolean(expandedSectionIds[section.id]),
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

export function chapterWorkspaceViewPatch(
  workspace: MemoirChapterWorkspace,
  expandedSectionIds: Record<string, boolean> = {},
): ChapterWorkspaceViewPatch {
  const summary = workspace.completion_summary;
  const sectionViews = visibleSectionViews(workspace, expandedSectionIds);
  const materialCount = sectionViews.reduce(
    (total, section) => total + section.visibleMaterials.length,
    0,
  );
  return {
    nextAction: chapterNextAction(summary),
    readinessTitle: summary.headline,
    readinessDetail: summary.detail,
    rewriteNoticeTitle: rewriteNoticeTitle(workspace),
    rewriteNoticeDetail: workspace.can_rewrite_draft
      ? "可以继续补充，也可以先用本章全部现有素材重新生成完整正文；旧版本仍会保留。"
      : "请先完成上方必须确认的事项，再重新生成正文。",
    sectionStructureSummary: `${sectionViews.length} 个小节 · ${materialCount} 条讲述`,
    sectionViews,
    canConfirmChapter:
      summary.state === "draft_ready"
      && !workspace.draft_needs_rewrite
      && Boolean(workspace.draft_version_id)
      && workspace.draft_sections.length > 0
      && workspace.draft_sections.every(
        (section) => Boolean(section.content.trim()),
      ),
  };
}
