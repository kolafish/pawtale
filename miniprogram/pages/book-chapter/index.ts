import type {
  MemoirBookChapterDraft,
  MemoirBookPlan,
  MemoirBookSourceCoverage,
} from "../../domain/voice-chat";
import {
  getMemoirBookChapterDraft,
  getMemoirBookPlan,
  getMemoirBookSourceCoverage,
} from "../../services/memoir-voice-api";
import { ApiError } from "../../services/request";
import type { BookChapterView } from "../book/book-view-model";
import {
  buildChapterViews,
  trimText,
} from "../book/book-view-model";

type ChapterDetailTab = "content" | "sources" | "more";

interface PageQuery {
  projectId?: string;
  chapterId?: string;
  tab?: string;
}

interface DatasetEvent {
  currentTarget: {
    dataset: {
      tab?: string;
    };
  };
}

interface ChapterMoreActionView {
  id: string;
  title: string;
  detailText: string;
}

interface BookChapterPageData {
  loading: boolean;
  error: string;
  projectId: string;
  chapterId: string;
  activeTab: ChapterDetailTab;
  planTitle: string;
  chapter: BookChapterView | null;
  chapterIndexText: string;
  contentText: string;
  sourceCountText: string;
  sourceEmptyText: string;
  moreActions: ChapterMoreActionView[];
}

const TAB_VALUES: ChapterDetailTab[] = ["content", "sources", "more"];

function decodeQueryValue(value = ""): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function normalizeTab(value = ""): ChapterDetailTab {
  const tab = decodeQueryValue(value) as ChapterDetailTab;
  return TAB_VALUES.includes(tab) ? tab : "content";
}

function chapterIndexText(plan: MemoirBookPlan | null, chapterId: string): string {
  const index = plan?.chapters
    .slice()
    .sort((left, right) => left.sort_order - right.sort_order)
    .findIndex((chapter) => chapter.id === chapterId) ?? -1;
  return index >= 0 ? `第 ${index + 1} 章` : "章节";
}

function moreActionsForChapter(chapter: BookChapterView): ChapterMoreActionView[] {
  return [
    {
      id: "history",
      title: "历史版本",
      detailText: chapter.draftVersionText || "查看本章历史版本",
    },
    {
      id: "move",
      title: "移动章节顺序",
      detailText: "调整这一章在全书里的位置",
    },
    {
      id: "merge",
      title: "合并到其他章节",
      detailText: "把这一章并入相邻或指定章节",
    },
    {
      id: "split",
      title: "拆分原始讲述",
      detailText: chapter.sourceCount ? `从 ${chapter.sourceCount} 条原始讲述里拆出新章` : "本章暂无原始讲述",
    },
  ];
}

async function loadChapterDraft(projectId: string, chapterId: string): Promise<MemoirBookChapterDraft | null> {
  try {
    return await getMemoirBookChapterDraft(projectId, chapterId);
  } catch (error) {
    if (error instanceof ApiError && error.statusCode === 404) {
      return null;
    }
    throw error;
  }
}

async function loadSourceCoverage(projectId: string): Promise<MemoirBookSourceCoverage | null> {
  try {
    return await getMemoirBookSourceCoverage(projectId);
  } catch (error) {
    if (error instanceof ApiError && error.statusCode === 404) {
      return null;
    }
    throw error;
  }
}

Page<BookChapterPageData, {
  onLoad(query?: PageQuery): Promise<void>;
  loadChapter(projectId: string, chapterId: string): Promise<void>;
  changeTab(event: DatasetEvent): void;
  goBack(): void;
}>({
  data: {
    loading: false,
    error: "",
    projectId: "",
    chapterId: "",
    activeTab: "content",
    planTitle: "",
    chapter: null,
    chapterIndexText: "章节",
    contentText: "",
    sourceCountText: "0 条原始讲述",
    sourceEmptyText: "本章还没有关联原始讲述",
    moreActions: [],
  },

  async onLoad(query = {}) {
    const projectId = decodeQueryValue(query.projectId || "");
    const chapterId = decodeQueryValue(query.chapterId || "");
    const activeTab = normalizeTab(query.tab || "");
    this.setData({ projectId, chapterId, activeTab, error: "" });
    if (!projectId || !chapterId) {
      this.setData({ error: "缺少章节信息" });
      return;
    }
    await this.loadChapter(projectId, chapterId);
  },

  async loadChapter(projectId, chapterId) {
    if (this.data.loading) {
      return;
    }
    this.setData({ loading: true, error: "" });
    try {
      const [plan, coverage, draft] = await Promise.all([
        getMemoirBookPlan(projectId),
        loadSourceCoverage(projectId),
        loadChapterDraft(projectId, chapterId),
      ]);
      const chapters = buildChapterViews(plan, { [chapterId]: draft }, coverage);
      const chapter = chapters.find((item) => item.id === chapterId) || null;
      if (!chapter) {
        this.setData({
          chapter: null,
          planTitle: plan.title,
          chapterIndexText: "章节",
          contentText: "",
          sourceCountText: "0 条原始讲述",
          moreActions: [],
          error: "没有找到这一章",
        });
        return;
      }
      const contentText = trimText(chapter.draftContent);
      this.setData({
        planTitle: plan.title,
        chapter,
        chapterIndexText: chapterIndexText(plan, chapterId),
        contentText: contentText || "本章正文还没有生成",
        sourceCountText: `${chapter.sourceCount} 条原始讲述`,
        sourceEmptyText: "本章还没有关联原始讲述",
        moreActions: moreActionsForChapter(chapter),
      });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "章节详情加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  changeTab(event) {
    const tab = String(event.currentTarget.dataset.tab || "");
    this.setData({ activeTab: normalizeTab(tab) });
  },

  goBack() {
    wx.navigateBack();
  },
});
