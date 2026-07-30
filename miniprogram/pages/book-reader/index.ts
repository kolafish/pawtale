import type {
  MemoirBookAudioResponse,
  MemoirBookOutput,
  MemoirBookOutputVersion,
} from "../../domain/memoir-book";
import type { MemoirChapterWorkspace } from "../../domain/chapter-workspace";
import { definePage } from "../../services/page-definition";
import {
  createMemoirBookChapterDraftTTS,
  createMemoirBookOutputTTS,
  getMemoirChapterWorkspace,
  getMemoirBookOutput,
  getMemoirBookOutputVersion,
} from "../../services/memoir-voice-api";
import { routeAuthError } from "../../services/navigation";
import { AudioPlaybackController } from "../../services/audio-playback";
import { setPageNavigationTitle } from "../../services/page-navigation";
import { readBookTTSRequest } from "../../services/tts-settings";
import { VoiceAssistantTTSProgressRuntime } from "../../services/voice-assistant-tts-progress";
import {
  datasetString,
  queryParam,
  type PageDatasetEvent,
  type PageQuery,
} from "../../services/page-input";
import {
  bookShareAppMessage,
  bookShareTimeline,
  enableMiniProgramShareMenu,
} from "../../services/share";
import {
  bookOutputData,
  readingParagraphs,
  trimText,
  type BookData,
  type BookOutputChapterPreviewView,
} from "../book/book-view-model";
import {
  playBookAudioFiles,
  stopBookPlayback,
  type BookTTSState,
} from "../book/book-tts-player";

type BookReaderData = Pick<
  BookData,
  | "bookContent"
  | "bookCharCount"
  | "bookPreviewTitle"
  | "bookPreviewTocItems"
  | "bookPreviewChapters"
  | "bookManuscriptTitle"
  | "bookManuscriptSubtitle"
  | "bookVersionText"
  | "bookViewingHistoricalVersion"
  | "bookVersionNotice"
  | "bookStaleNotice"
> & BookTTSState & {
  loading: boolean;
  playbackError: string;
  projectId: string;
  chapterId: string;
  versionId: string;
  readerMode: "book" | "chapter";
  readerTitle: string;
  readerSubtitle: string;
};

type ReaderDatasetEvent = PageDatasetEvent<{ index?: string }>;

type WxWithPageScrollTo = typeof wx & {
  pageScrollTo?: (options: { selector?: string; scrollTop?: number; duration?: number }) => void;
};

function scrollReader(options: { selector?: string; scrollTop?: number }) {
  const pageScrollTo = (wx as WxWithPageScrollTo).pageScrollTo;
  if (typeof pageScrollTo === "function") {
    pageScrollTo({ ...options, duration: 280 });
  }
}

function readerData(
  output: MemoirBookOutput | null,
  version: MemoirBookOutputVersion | null = null,
): Pick<
  BookReaderData,
  | "bookContent"
  | "bookCharCount"
  | "bookPreviewTitle"
  | "bookPreviewTocItems"
  | "bookPreviewChapters"
  | "bookManuscriptTitle"
  | "bookManuscriptSubtitle"
  | "bookVersionText"
  | "bookViewingHistoricalVersion"
  | "bookVersionNotice"
  | "bookStaleNotice"
  | "readerTitle"
  | "readerSubtitle"
> {
  const data = bookOutputData(output, version);
  const readerTitle = (
    trimText(data.bookManuscriptTitle)
    || trimText(data.bookPreviewTitle)
    || "家庭回忆录"
  );
  return {
    bookContent: data.bookContent,
    bookCharCount: data.bookCharCount,
    bookPreviewTitle: data.bookPreviewTitle,
    bookPreviewTocItems: data.bookPreviewTocItems,
    bookPreviewChapters: data.bookPreviewChapters,
    bookManuscriptTitle: data.bookManuscriptTitle,
    bookManuscriptSubtitle: data.bookManuscriptSubtitle,
    bookVersionText: data.bookVersionText,
    bookViewingHistoricalVersion: data.bookViewingHistoricalVersion,
    bookVersionNotice: data.bookVersionNotice,
    bookStaleNotice: data.bookStaleNotice,
    readerTitle,
    readerSubtitle: trimText(data.bookManuscriptSubtitle || "根据口述访谈整理"),
  };
}

function chapterReaderData(
  workspace: MemoirChapterWorkspace,
): ReturnType<typeof readerData> {
  const sections = workspace.draft_sections
    .filter((section) => trimText(section.title) && trimText(section.content))
    .map((section, index) => {
      const content = trimText(section.content);
      return {
        id: section.section_id,
        index: index + 1,
        indexText: `${index + 1}`,
        title: trimText(section.title),
        content,
        paragraphs: readingParagraphs(content),
        excerpt: content.slice(0, 72),
        metaText: `${content.length} 字`,
      };
    });
  const content = sections.length
    ? sections.map((section) => `${section.title}\n\n${section.content}`).join("\n\n")
    : trimText(workspace.draft_content);
  const chapter: BookOutputChapterPreviewView = {
    id: workspace.chapter_id,
    chapterId: workspace.chapter_id,
    index: 1,
    indexText: "本章",
    title: trimText(workspace.chapter_title) || "本章正文",
    content,
    excerpt: content.slice(0, 96),
    metaText: `${sections.length} 个小节 · ${content.replace(/\s/g, "").length} 字`,
    sections,
  };
  return {
    bookContent: content,
    bookCharCount: content.replace(/\s/g, "").length,
    bookPreviewTitle: chapter.title,
    bookPreviewTocItems: [],
    bookPreviewChapters: content ? [chapter] : [],
    bookManuscriptTitle: "",
    bookManuscriptSubtitle: "",
    bookVersionText: workspace.draft_version
      ? `章节第 ${workspace.draft_version} 版`
      : "当前章节稿",
    bookViewingHistoricalVersion: false,
    bookVersionNotice: "",
    bookStaleNotice: workspace.draft_needs_rewrite
      ? "本章素材已经变化，这里仍是更新正文前的版本。"
      : "",
    readerTitle: chapter.title,
    readerSubtitle: chapter.metaText,
  };
}

definePage<BookReaderData>()({
  audioPlayback: new AudioPlaybackController(),
  ttsProgress: new VoiceAssistantTTSProgressRuntime(),
  playToken: 0,

  data: {
    loading: false,
    error: "",
    playbackError: "",
    projectId: "",
    chapterId: "",
    versionId: "",
    readerMode: "book",
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
    ...readerData(null),
  },

  async onLoad(query: PageQuery = {}) {
    const projectId = queryParam(query, "projectId");
    const chapterId = queryParam(query, "chapterId");
    const versionId = queryParam(query, "versionId");
    const readerMode = chapterId ? "chapter" : "book";
    this.setData({ projectId, chapterId, versionId, readerMode });
    if (!projectId) {
      this.setData({ error: "缺少成书信息，暂时无法打开。" });
      return;
    }
    if (chapterId) {
      setPageNavigationTitle("整章预览");
      await this.loadChapterReader(projectId, chapterId);
      return;
    }
    await this.loadBookReader(projectId, versionId);
  },

  onShow() {
    if (this.data.readerMode === "book") {
      enableMiniProgramShareMenu();
    }
  },

  onHide() {
    this.stopPlayback();
  },

  onUnload() {
    this.stopPlayback();
  },

  onShareAppMessage() {
    return bookShareAppMessage({
      projectId: this.data.projectId,
      bookTitle: this.data.readerTitle,
      hasBookContent: Boolean(this.data.bookContent),
    });
  },

  onShareTimeline() {
    return bookShareTimeline({
      projectId: this.data.projectId,
      bookTitle: this.data.readerTitle,
      hasBookContent: Boolean(this.data.bookContent),
    });
  },

  openReaderChapter(event: ReaderDatasetEvent) {
    const chapterIndex = datasetString(event, "index");
    if (!/^\d+$/.test(chapterIndex)) {
      return;
    }
    scrollReader({ selector: `#reader-chapter-${chapterIndex}` });
  },

  scrollReaderToTop() {
    scrollReader({ scrollTop: 0 });
  },

  async playReaderAudio() {
    const target = this.data.readerMode;
    if (this.data.playingTarget === target || this.data.ttsBusyTarget === target) {
      this.stopPlayback();
      return;
    }
    if (!this.data.projectId || !trimText(this.data.bookContent)) {
      return;
    }
    if (this.data.bookViewingHistoricalVersion) {
      this.setData({ playbackError: "历史版本没有对应朗读，请切回最新版后再播放。" });
      return;
    }
    this.stopPlayback();
    const token = this.audioPlayback.nextToken();
    this.playToken = token;
    this.setData({
      ttsBusyTarget: target,
      playbackError: "",
    });
    try {
      const response = this.data.readerMode === "chapter"
        ? await createMemoirBookChapterDraftTTS(
            this.data.projectId,
            this.data.chapterId,
            readBookTTSRequest(),
          )
        : await createMemoirBookOutputTTS(
            this.data.projectId,
            readBookTTSRequest(),
          );
      if (token !== this.playToken) {
        return;
      }
      this.setData({ ttsBusyTarget: "" });
      await this.playAudioFiles(response, target, token, trimText(this.data.bookContent));
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      if (token === this.playToken) {
        this.setData({
          playbackError: error instanceof Error
            ? error.message
            : `${this.data.readerMode === "chapter" ? "整章" : "全书"}朗读失败`,
        });
      }
    } finally {
      if (token === this.playToken && this.data.ttsBusyTarget === target) {
        this.setData({ ttsBusyTarget: "" });
      }
    }
  },

  async playAudioFiles(
    response: MemoirBookAudioResponse,
    target: string,
    token: number,
    activeText: string,
  ) {
    return playBookAudioFiles(this, response, target, token, activeText);
  },

  stopPlayback() {
    stopBookPlayback(this);
  },

  async loadBookReader(projectId: string, versionId = "") {
    this.stopPlayback();
    this.setData({ loading: true, error: "", playbackError: "" });
    try {
      const output = await getMemoirBookOutput(projectId);
      const latestVersion = output.latest_version || null;
      const version = versionId && versionId !== latestVersion?.id
        ? await getMemoirBookOutputVersion(projectId, versionId)
        : latestVersion;
      this.setData(readerData(output, version));
      if (!trimText(this.data.bookContent)) {
        this.setData({ error: "这本书还没有生成成书内容。" });
      }
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "成书打开失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  async loadChapterReader(projectId: string, chapterId: string) {
    this.stopPlayback();
    this.setData({ loading: true, error: "", playbackError: "" });
    try {
      const workspace = await getMemoirChapterWorkspace(projectId, chapterId);
      this.setData(chapterReaderData(workspace));
      if (!trimText(this.data.bookContent)) {
        this.setData({ error: "本章正文还没有生成。" });
      }
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "整章预览打开失败",
      });
    } finally {
      this.setData({ loading: false });
    }
  },
});
