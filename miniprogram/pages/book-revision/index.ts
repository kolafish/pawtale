import type {
  MemoirBookManuscriptChapter,
  MemoirBookOutputSection,
} from "../../domain/memoir-book";
import { definePage } from "../../services/page-definition";
import {
  applyMemoirBookRevisionProposal,
  dismissMemoirBookRevisionProposal,
  getMemoirBookRevisionProposal,
  type MemoirBookRevisionProposal,
  type MemoirBookRevisionSectionComparison,
} from "../../services/memoir-voice-api";
import { routeAuthError } from "../../services/navigation";
import {
  datasetString,
  pageUrl,
  queryParam,
  type PageDatasetEvent,
  type PageQuery,
} from "../../services/page-input";


type RevisionMode = "changes" | "whole";
type PreviewVersion = "before" | "after";

interface RevisionSectionView extends MemoirBookRevisionSectionComparison {
  display_content: string;
  display_label: string;
}

interface RevisionChapterView {
  chapter_id: string;
  title: string;
  sections: RevisionSectionView[];
}

interface WholeChapterView {
  chapter_id: string;
  index_text: string;
  title: string;
  sections: MemoirBookOutputSection[];
  content: string;
}

interface BookRevisionData {
  projectId: string;
  proposalId: string;
  loading: boolean;
  busy: boolean;
  error: string;
  proposal: MemoirBookRevisionProposal | null;
  mode: RevisionMode;
  wholeVersion: PreviewVersion;
  beforeSectionIds: string[];
  changedChapters: RevisionChapterView[];
  wholeChapters: WholeChapterView[];
}

type RevisionDatasetEvent = PageDatasetEvent<{
  mode?: string;
  version?: string;
  sectionId?: string;
}>;

function wholeBookChapters(
  proposal: MemoirBookRevisionProposal,
  version: PreviewVersion,
): WholeChapterView[] {
  const chapters = proposal[version].manuscript?.chapters || [];
  return chapters.map((chapter: MemoirBookManuscriptChapter, index) => ({
    chapter_id: chapter.chapter_id,
    index_text: `第 ${index + 1} 章`,
    title: chapter.title,
    sections: chapter.sections || [],
    content: chapter.content,
  }));
}

export function revisionViewPatch(
  proposal: MemoirBookRevisionProposal,
  beforeSectionIds: string[] = [],
  wholeVersion: PreviewVersion = "after",
): Pick<
  BookRevisionData,
  "proposal" | "beforeSectionIds" | "changedChapters" | "wholeChapters"
> {
  const beforeIds = new Set(beforeSectionIds);
  return {
    proposal,
    beforeSectionIds,
    changedChapters: proposal.comparison.chapters.map((chapter) => ({
      ...chapter,
      sections: chapter.sections.map((section) => {
        const showingBefore = beforeIds.has(section.section_id);
        return {
          ...section,
          display_content: showingBefore
            ? section.before_content
            : section.after_content,
          display_label: showingBefore ? "修改前" : "修改后",
        };
      }),
    })),
    wholeChapters: wholeBookChapters(proposal, wholeVersion),
  };
}

function confirmAction(title: string, content: string): Promise<boolean> {
  return new Promise((resolve) => {
    wx.showModal({
      title,
      content,
      confirmText: "确认",
      cancelText: "再看看",
      success: (result) => resolve(result.confirm),
      fail: () => resolve(false),
    });
  });
}

definePage<BookRevisionData>()({
  data: {
    projectId: "",
    proposalId: "",
    loading: true,
    busy: false,
    error: "",
    proposal: null,
    mode: "changes",
    wholeVersion: "after",
    beforeSectionIds: [],
    changedChapters: [],
    wholeChapters: [],
  },

  async onLoad(query: PageQuery = {}) {
    const projectId = queryParam(query, "projectId");
    const proposalId = queryParam(query, "proposalId");
    this.setData({ projectId, proposalId });
    if (!projectId || !proposalId) {
      this.setData({ loading: false, error: "缺少修改预览信息" });
      return;
    }
    await this.loadProposal();
  },

  async loadProposal() {
    this.setData({ loading: true, error: "" });
    try {
      const proposal = await getMemoirBookRevisionProposal(
        this.data.projectId,
        this.data.proposalId,
      );
      this.setData({
        loading: false,
        ...revisionViewPatch(
          proposal,
          this.data.beforeSectionIds,
          this.data.wholeVersion,
        ),
      });
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        loading: false,
        error: error instanceof Error ? error.message : "修改预览加载失败",
      });
    }
  },

  switchMode(event: RevisionDatasetEvent) {
    const mode = datasetString(event, "mode");
    if (mode === "changes" || mode === "whole") {
      this.setData({ mode });
    }
  },

  switchWholeVersion(event: RevisionDatasetEvent) {
    const version = datasetString(event, "version");
    const proposal = this.data.proposal;
    if (!proposal || (version !== "before" && version !== "after")) {
      return;
    }
    this.setData({
      wholeVersion: version,
      wholeChapters: wholeBookChapters(proposal, version),
    });
  },

  toggleSectionVersion(event: RevisionDatasetEvent) {
    const sectionId = datasetString(event, "sectionId");
    const proposal = this.data.proposal;
    if (!proposal || !sectionId) {
      return;
    }
    const beforeSectionIds = this.data.beforeSectionIds.includes(sectionId)
      ? this.data.beforeSectionIds.filter((value) => value !== sectionId)
      : [...this.data.beforeSectionIds, sectionId];
    this.setData(revisionViewPatch(
      proposal,
      beforeSectionIds,
      this.data.wholeVersion,
    ));
  },

  async applyRevision() {
    if (!this.data.proposal?.can_apply || this.data.busy) {
      return;
    }
    if (!await confirmAction(
      "确认使用这版修改？",
      "确认后会生成新的正式书稿版本，原版本仍会保留。",
    )) {
      return;
    }
    this.setData({ busy: true, error: "" });
    try {
      const proposal = await applyMemoirBookRevisionProposal(
        this.data.projectId,
        this.data.proposalId,
      );
      this.setData({
        busy: false,
        ...revisionViewPatch(
          proposal,
          this.data.beforeSectionIds,
          this.data.wholeVersion,
        ),
      });
      wx.showToast({ title: "修改已写入书稿", icon: "success" });
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        busy: false,
        error: error instanceof Error ? error.message : "确认修改失败",
      });
    }
  },

  async dismissRevision() {
    if (!this.data.proposal?.can_dismiss || this.data.busy) {
      return;
    }
    if (!await confirmAction(
      "放弃这版修改？",
      "当前书稿不会改变；之后仍可重新生成修改预览。",
    )) {
      return;
    }
    this.setData({ busy: true, error: "" });
    try {
      await dismissMemoirBookRevisionProposal(
        this.data.projectId,
        this.data.proposalId,
      );
      wx.navigateBack();
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        busy: false,
        error: error instanceof Error ? error.message : "放弃修改失败",
      });
    }
  },

  openAppliedBook() {
    if (!this.data.projectId) {
      return;
    }
    wx.navigateTo({
      url: pageUrl("/pages/book-export/index", {
        projectId: this.data.projectId,
        action: "preview",
      }),
    });
  },
});
