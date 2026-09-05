import type { MemoirStorySnapshot } from "../../domain/memoir-snapshots";
import { formatBeijingDateTimeShort } from "../../services/time";
import type { StoryViewData } from "./story-view-data";

export function storyViewData(snapshot: MemoirStorySnapshot): StoryViewData {
  const project = snapshot.identity.active_project;
  const progress = snapshot.progress;
  const chapters = snapshot.chapters.map((chapter, index) => ({
    id: chapter.id,
    title: chapter.title || "未命名章节",
    timeRangeText: "",
    summaryText: `${chapter.sections.length} 小节 · ${chapter.material_count} 段讲述`,
    outcomeText: chapter.state_text,
    readable: chapter.readable,
    imageCount: chapter.image_count,
    videoCount: chapter.video_count,
    state: chapter.state,
    expanded: index === 0,
    highlighted: false,
    sections: chapter.sections.map((section) => ({
      id: section.id,
      title: section.title || "未命名小节",
      materialText: `${section.material_count} 段讲述`,
      imageCount: section.image_count,
      videoCount: section.video_count,
      highlighted: false,
    })),
  }));
  const count = snapshot.book_char_count || snapshot.chapters.reduce((total, chapter) => total + chapter.char_count, 0);
  const pending = progress?.pending_material_count || 0;
  const needsUser = progress?.needs_user_material_count || 0;
  const materialCount = progress?.material_count || 0;
  const included = snapshot.chapters.filter((chapter) => chapter.include_in_book).length;
  return {
    memoir: {
      title: project?.title || "我的故事",
      createdText: project?.created_at ? `创建于 ${formatBeijingDateTimeShort(project.created_at)}` : "",
      updatedText: snapshot.updated_at ? `更新于 ${formatBeijingDateTimeShort(snapshot.updated_at)}` : "",
      progressLabel: "书稿",
      progressValue: included ? `${included} 章` : "尚未生成",
      progressDetail: snapshot.book_readable ? "阅读草稿" : "查看整理进度",
    },
    overview: {
      title: "人生总览",
      summaryText: `${chapters.length} 章 · ${chapters.reduce((total, chapter) => total + chapter.sections.length, 0)} 小节 · ${materialCount} 段讲述`,
      outcomeText: count >= 10_000 ? `约 ${(count / 10_000).toFixed(1)} 万字` : count ? `${count} 字` : "还没有正文",
    },
    chapters,
    unassignedMaterialTitle: pending ? "待归入故事的讲述" : materialCount ? "全部讲述" : "从一段讲述开始",
    unassignedMaterialText: pending ? `还有 ${pending} 段讲述未归入故事` : materialCount ? "所有讲述都已归入故事" : "还没有讲述",
    unassignedMaterialDetail: needsUser ? `其中 ${needsUser} 段需要您补充或确认，查看讲述后可以修改` : pending ? "后台会继续整理；无法自动归入的内容会提示您处理" : "讲下新的回忆后，它会从这里长成故事",
    unassignedImageCount: snapshot.unassigned_image_count,
    unassignedVideoCount: snapshot.unassigned_video_count,
  };
}
