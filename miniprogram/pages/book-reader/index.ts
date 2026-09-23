import type {
  MemoirBookAudioResponse,
  MemoirBookOutput,
  MemoirBookOutputVersion,
} from "../../domain/memoir-book";
import type { MemoirChapterWorkspace } from "../../domain/chapter-workspace";
import {
  accessibilityPageClass,
  accessibilityPagePatch,
} from "../../services/accessibility-settings";
import { definePage, pageRuntime } from "../../services/page-definition";
import {
  createMemoirBookChapterDraftTTS,
  createMemoirBookOutputTTS,
  getMemoirBookOutput,
  getMemoirBookOutputVersion,
  readMemoirBookShare,
} from "../../services/memoir-book-api";
import { getMemoirChapterWorkspace } from "../../services/memoir-chapter-workspace-api";
import { routeAuthError } from "../../services/navigation";
import { AudioPlaybackController } from "../../services/audio-playback";
import { setPageNavigationTitle } from "../../services/page-navigation";
import { readBookTTSRequest } from "../../services/tts-settings";
import {
  VoiceAssistantTTSProgressRuntime,
  type VoiceAssistantTTSProgressPatch,
} from "../../services/voice-assistant-tts-progress";
import {
  datasetString,
  queryParam,
  type PageDatasetEvent,
  type PageQuery,
} from "../../services/page-input";
import { hideMiniProgramShareMenu } from "../../services/share";
import {
  bookReadingData,
  readingParagraphs,
  trimText,
  type BookReadingData,
  type BookOutputChapterPreviewView,
} from "../book/book-view-model";
import {
  playBookAudioFiles,
  stopBookPlayback,
  type BookTTSState,
} from "../book/book-tts-player";

type BookReaderTocItem = BookReadingData["bookPreviewTocItems"][number] & {
  expanded: boolean;
  storyCountText: string;
};

type ReaderData = Omit<BookReadingData, "bookPreviewTocItems"> & {
  bookPreviewTocItems: BookReaderTocItem[];
};

type BookReaderData = ReaderData & BookTTSState & {
  pageClass: string;
  loading: boolean;
  playbackError: string;
  projectId: string;
  chapterId: string;
  versionId: string;
  readerMode: "book" | "chapter";
  readerShared: boolean;
  readerChapterIndex: number;
  readerChapterTotal: number;
  readerTitle: string;
  readerSubtitle: string;
  readerMetaText: string;
  readerUpdateNotice: string;
  readerHighlightSectionId: string;
  readerHighlightParagraphIndex: number;
  readerHighlightBefore: string;
  readerHighlightCurrent: string;
  readerHighlightAfter: string;
};

type ReaderDatasetEvent = PageDatasetEvent<{
  chapterIndex?: string;
  chapterId?: string;
  sectionId?: string;
}>;

interface ChapterParagraphRange {
  sectionId: string;
  paragraphIndex: number;
  text: string;
  start: number;
  end: number;
}

const SENTENCE_ENDINGS = "。！？!?";
const SENTENCE_CLOSERS = "”’\"'」』】）)";
const PAGE_CLASS = "book-reader-page ui-page-shell ui-accessible-page";
const FLAT_READER_PAGE_CHARS = 6000;

type WxWithPageScrollTo = typeof wx & {
  pageScrollTo?: (options: { selector?: string; scrollTop?: number; duration?: number }) => void;
};

function scrollReader(options: { selector?: string; scrollTop?: number }) {
  const pageScrollTo = (wx as WxWithPageScrollTo).pageScrollTo;
  if (typeof pageScrollTo === "function") {
    pageScrollTo({ ...options, duration: 280 });
  }
}

function compactReaderChapter(chapter: BookOutputChapterPreviewView): BookOutputChapterPreviewView {
  return {
    ...chapter,
    content: chapter.sections.length ? "" : chapter.content,
    sections: chapter.sections.map((section) => ({ ...section, content: "" })),
  };
}

function flatReaderPages(content: string): string[] {
  const characters = Array.from(content);
  const pages: string[] = [];
  for (let index = 0; index < characters.length; index += FLAT_READER_PAGE_CHARS) {
    pages.push(characters.slice(index, index + FLAT_READER_PAGE_CHARS).join(""));
  }
  return pages;
}

function textLength(value: string): number {
  return Array.from(value).length;
}

function chapterParagraphRanges(
  sections: BookOutputChapterPreviewView["sections"],
): ChapterParagraphRange[] {
  const ranges: ChapterParagraphRange[] = [];
  let chapterOffset = 0;
  sections.forEach((section, sectionIndex) => {
    chapterOffset += textLength(section.title) + 2;
    let searchOffset = 0;
    section.paragraphs.forEach((paragraph, paragraphIndex) => {
      const startInContent = section.content.indexOf(paragraph, searchOffset);
      if (startInContent < 0) {
        return;
      }
      const start = chapterOffset + textLength(section.content.slice(0, startInContent));
      ranges.push({
        sectionId: section.id,
        paragraphIndex,
        text: paragraph,
        start,
        end: start + textLength(paragraph),
      });
      searchOffset = startInContent + paragraph.length;
    });
    chapterOffset += textLength(section.content);
    if (sectionIndex < sections.length - 1) {
      chapterOffset += 2;
    }
  });
  return ranges;
}

function sentenceHighlight(
  range: ChapterParagraphRange,
  currentTextStart: number,
): { before: string; current: string; after: string } {
  const characters = Array.from(range.text);
  const currentIndex = currentTextStart - range.start;
  let sentenceStart = 0;
  for (let index = 0; index < characters.length; index += 1) {
    if (!SENTENCE_ENDINGS.includes(characters[index])) {
      continue;
    }
    let sentenceEnd = index + 1;
    while (
      sentenceEnd < characters.length
      && SENTENCE_CLOSERS.includes(characters[sentenceEnd])
    ) {
      sentenceEnd += 1;
    }
    if (currentIndex < sentenceEnd) {
      return {
        before: characters.slice(0, sentenceStart).join(""),
        current: characters.slice(sentenceStart, sentenceEnd).join(""),
        after: characters.slice(sentenceEnd).join(""),
      };
    }
    sentenceStart = sentenceEnd;
    index = sentenceEnd - 1;
  }
  return {
    before: characters.slice(0, sentenceStart).join(""),
    current: characters.slice(sentenceStart).join(""),
    after: "",
  };
}

function readerData(
  output: MemoirBookOutput | null,
  version: MemoirBookOutputVersion | null = null,
): ReaderData & Pick<
  BookReaderData,
  "readerTitle" | "readerSubtitle" | "readerMetaText" | "readerUpdateNotice"
> {
  const data = bookReadingData(output, version);
  const readerTitle = (
    trimText(data.bookManuscriptTitle)
    || trimText(data.bookPreviewTitle)
    || "家庭回忆录"
  );
  const subtitle = trimText(data.bookManuscriptSubtitle);
  const chapterCount = data.bookPreviewChapters.length || data.bookPreviewTocItems.length;
  const staleTitles = (output?.stale_chapter_titles || []).filter(Boolean);
  return {
    ...data,
    bookPreviewTocItems: data.bookPreviewTocItems.map((chapter) => ({
      ...chapter,
      expanded: false,
      storyCountText: chapter.sections.length
        ? `${chapter.sections.length}个故事`
        : "整章正文",
    })),
    readerTitle,
    readerSubtitle: !subtitle || subtitle === "根据口述访谈整理"
      ? "根据口述访谈整理而成"
      : subtitle,
    readerMetaText: `${chapterCount}章 · ${data.bookCharCount || 0}字`,
    readerUpdateNotice: output?.is_stale
      ? (staleTitles.length
        ? `《${staleTitles[0]}》${staleTitles.length > 1 ? "等章节" : ""}刚刚更新`
        : "这本书刚刚更新")
      : "",
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
    readerMetaText: "",
    readerUpdateNotice: workspace.draft_needs_rewrite ? "这篇故事刚刚更新" : "",
  };
}

definePage<BookReaderData>()({
  audioPlayback: pageRuntime(() => new AudioPlaybackController()),
  ttsProgress: pageRuntime(() => new VoiceAssistantTTSProgressRuntime()),
  playToken: 0,
  readerVisible: true,
  chapterParagraphRanges: [] as ChapterParagraphRange[],
  readerHighlightKey: "",
  readerText: "",
  bookChapters: [] as BookOutputChapterPreviewView[],
  flatPages: [] as string[],

  data: {
    pageClass: accessibilityPageClass(PAGE_CLASS),
    loading: false,
    error: "",
    playbackError: "",
    projectId: "",
    chapterId: "",
    versionId: "",
    readerMode: "book",
    readerShared: false,
    readerChapterIndex: 0,
    readerChapterTotal: 0,
    readerHighlightSectionId: "",
    readerHighlightParagraphIndex: -1,
    readerHighlightBefore: "",
    readerHighlightCurrent: "",
    readerHighlightAfter: "",
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
    this.readerVisible = true;
    const projectId = queryParam(query, "projectId");
    const chapterId = queryParam(query, "chapterId");
    const versionId = queryParam(query, "versionId");
    const shareToken = queryParam(query, "shareToken");
    const readerMode = chapterId ? "chapter" : "book";
    this.setData({ projectId, chapterId, versionId, readerMode, readerShared: Boolean(shareToken) });
    if (shareToken) {
      await this.loadSharedBookReader(shareToken);
      return;
    }
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
    if (queryParam(query, "listen") === "1" && this.readerVisible && !this.data.error && !this.data.bookViewingHistoricalVersion) {
      void this.playReaderAudio();
    }
  },

  onShow() {
    this.readerVisible = true;
    this.setData(accessibilityPagePatch(PAGE_CLASS));
    hideMiniProgramShareMenu();
  },

  onHide() {
    this.readerVisible = false;
    this.stopPlayback();
  },

  onUnload() {
    this.readerVisible = false;
    this.stopPlayback();
  },

  toggleTocChapter(event: ReaderDatasetEvent) {
    const chapterIndexText = datasetString(event, "chapterIndex");
    if (!/^\d+$/.test(chapterIndexText)) {
      return;
    }
    const chapterIndex = Number(chapterIndexText);
    const chapter = this.data.bookPreviewTocItems[chapterIndex];
    if (!chapter) {
      return;
    }
    if (!chapter.sections.length) {
      this.showReaderChapter(this.bookChapters.findIndex((item) => item.chapterId === chapter.chapterId));
      return;
    }
    this.setData({
      bookPreviewTocItems: this.data.bookPreviewTocItems.map((item, index) => (
        index === chapterIndex ? { ...item, expanded: !item.expanded } : item
      )),
    });
  },

  openReaderChapter(event: ReaderDatasetEvent) {
    const chapterId = datasetString(event, "chapterId");
    this.showReaderChapter(this.bookChapters.findIndex((item) => item.chapterId === chapterId));
  },

  showReaderChapter(index: number, sectionIndex?: number) {
    if (this.data.readerMode !== "book" || index < 0 || index >= this.data.readerChapterTotal) return;
    const chapter = this.bookChapters[index];
    this.setData({
      readerChapterIndex: index,
      bookPreviewChapters: chapter ? [chapter] : [],
      bookContent: chapter ? "" : this.flatPages[index] || "",
    }, () => scrollReader({ selector: sectionIndex === undefined ? "#reader-body" : `#reader-section-${sectionIndex}` }));
  },

  previousReaderChapter() {
    this.showReaderChapter(this.data.readerChapterIndex - 1);
  },

  nextReaderChapter() {
    this.showReaderChapter(this.data.readerChapterIndex + 1);
  },

  openReaderSection(event: ReaderDatasetEvent) {
    const chapterId = datasetString(event, "chapterId");
    const sectionId = datasetString(event, "sectionId");
    const chapterIndex = this.bookChapters.findIndex((item) => item.chapterId === chapterId);
    const sectionIndex = this.bookChapters[chapterIndex]?.sections.findIndex((item) => item.id === sectionId) ?? -1;
    if (sectionIndex >= 0) this.showReaderChapter(chapterIndex, sectionIndex);
  },

  scrollReaderToTop() {
    scrollReader({ scrollTop: 0 });
  },

  async playReaderAudio() {
    if (this.data.readerShared) return;
    const target = this.data.readerMode;
    if (this.data.playingTarget === target || this.data.ttsBusyTarget === target) {
      this.stopPlayback();
      return;
    }
    if (!this.data.projectId || !this.readerText) {
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
      await this.playAudioFiles(response, target, token, this.readerText);
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

  onBookTTSProgressPatch(patch: VoiceAssistantTTSProgressPatch) {
    if (this.data.readerMode !== "chapter" || !patch.ttsCurrentText) {
      this.clearReaderHighlight();
      return;
    }
    const currentTextStart = textLength(patch.ttsSpokenText);
    const range = this.chapterParagraphRanges.find(
      (item) => currentTextStart >= item.start && currentTextStart < item.end,
    );
    if (!range) {
      this.clearReaderHighlight();
      return;
    }
    const highlight = sentenceHighlight(range, currentTextStart);
    const key = [
      range.sectionId,
      range.paragraphIndex,
      highlight.before.length,
      highlight.current.length,
    ].join(":");
    if (key === this.readerHighlightKey) {
      return;
    }
    this.readerHighlightKey = key;
    this.setData({
      readerHighlightSectionId: range.sectionId,
      readerHighlightParagraphIndex: range.paragraphIndex,
      readerHighlightBefore: highlight.before,
      readerHighlightCurrent: highlight.current,
      readerHighlightAfter: highlight.after,
    });
  },

  clearReaderHighlight() {
    if (!this.readerHighlightKey && !this.data.readerHighlightCurrent) {
      return;
    }
    this.readerHighlightKey = "";
    this.setData({
      readerHighlightSectionId: "",
      readerHighlightParagraphIndex: -1,
      readerHighlightBefore: "",
      readerHighlightCurrent: "",
      readerHighlightAfter: "",
    });
  },

  stopPlayback() {
    stopBookPlayback(this);
    this.clearReaderHighlight();
  },

  async loadBookReader(projectId: string, versionId = "") {
    this.stopPlayback();
    this.chapterParagraphRanges = [];
    this.setData({ loading: true, error: "", playbackError: "" });
    try {
      const output = await getMemoirBookOutput(projectId);
      const latestVersion = output.latest_version || null;
      const version = versionId && versionId !== latestVersion?.id
        ? await getMemoirBookOutputVersion(projectId, versionId)
        : latestVersion;
      this.applyBookReaderOutput(output, version);
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "成书打开失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  applyBookReaderOutput(output: MemoirBookOutput, version: MemoirBookOutputVersion | null = null) {
    const data = readerData(output, version);
    this.readerText = trimText(data.bookContent);
    this.bookChapters = data.bookPreviewChapters.map(compactReaderChapter);
    this.flatPages = this.bookChapters.length ? [] : flatReaderPages(data.bookContent);
    this.setData({
      ...data,
      bookContent: this.bookChapters.length ? "" : this.flatPages[0] || "",
      bookPreviewChapters: this.bookChapters.slice(0, 1),
      readerChapterIndex: 0,
      readerChapterTotal: this.bookChapters.length || this.flatPages.length,
    });
    if (!this.readerText) this.setData({ error: "这本书还没有生成成书内容。" });
  },

  async loadSharedBookReader(token: string) {
    this.stopPlayback();
    this.setData({ loading: true, error: "", playbackError: "" });
    try {
      this.applyBookReaderOutput(await readMemoirBookShare(token));
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "分享链接已失效" });
    } finally {
      this.setData({ loading: false });
    }
  },

  async loadChapterReader(projectId: string, chapterId: string) {
    this.stopPlayback();
    this.setData({ loading: true, error: "", playbackError: "" });
    try {
      const workspace = await getMemoirChapterWorkspace(projectId, chapterId);
      const data = chapterReaderData(workspace);
      this.readerText = trimText(data.bookContent);
      this.chapterParagraphRanges = chapterParagraphRanges(
        data.bookPreviewChapters[0]?.sections || [],
      );
      this.bookChapters = data.bookPreviewChapters.map(compactReaderChapter);
      this.flatPages = [];
      this.setData({
        ...data,
        bookContent: this.bookChapters.length ? "" : data.bookContent,
        bookPreviewChapters: this.bookChapters,
        readerChapterIndex: 0,
        readerChapterTotal: 1,
      });
      if (!this.readerText) {
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
