import { downloadAuthenticatedFile } from "../../services/authenticated-media";
import type {
  MemoirBookAudioPlan,
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
  memoirAudioPlanLoader,
  getMemoirBookOutput,
  getMemoirBookOutputVersion,
  readMemoirBookShare, readMemoirSharedAudioPlan, downloadMemoirSharedAudio,
  previewMemoirBookShare,
  readMemoirDemoBook,
} from "../../services/memoir-book-api";
import { getMemoirChapterWorkspace } from "../../services/memoir-chapter-workspace-api";
import { navigateToLoginThenNarrate, returnToHomeThenNarrate, routeAuthError } from "../../services/navigation";
import { ApiError, cleanBaseUrl, readBackendConfig, readSessionCookie } from "../../services/request";
import { sharedReadingKey, readSharedReadingProgress, saveSharedReadingProgress, type SharedReadingProgress } from "../../stores/reading-progress";
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
import { enableMiniProgramShareMenu, hideMiniProgramShareMenu, readingShareMessage, readingShareImageUrl } from "../../services/share";
import { INITIAL_SHARE_LIKE_DATA, shareLikeMethods, type ShareLikeData } from "../../services/share-likes";
import type { MemoirShareLikeTarget } from "../../domain/memoir-share-likes";
import {
  bookReadingData,
  readingParagraphs,
  trimText,
  type BookReadingData,
  type BookOutputChapterPreviewView,
} from "../book/book-view-model";
import {
  playBookAudioPlan,
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

type BookReaderData = ReaderData & BookTTSState & ShareLikeData & {
  responseToken: string;
  pageClass: string;
  loading: boolean;
  playbackError: string;
  projectId: string;
  chapterId: string;
  sectionId: string;
  versionId: string;
  readerMode: "book" | "chapter";
  readerShared: boolean;
  readerDemo: boolean;
  sharePreview: boolean;
  shareScope: "book" | "chapter" | "story";
  shareImages: { id: string; chapterId: string; sectionId: string; url: string; caption: string; disclosure: string }[];
  shareCoverUrl: string;
  shareByline: string;
  sharePreface: string;
  shareAfterword: string;
  shareRetryable: boolean;
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
        ? `${chapter.sections.length}小节`
        : "整章正文",
    })),
    readerTitle,
    readerSubtitle: !subtitle || subtitle === "根据口述访谈整理"
      ? "根据口述访谈整理而成"
      : subtitle,
    readerMetaText: `${chapterCount}章 · ${data.bookCharCount || 0}字`,
    readerUpdateNotice: data.bookViewingHistoricalVersion
      ? "正在阅读历史版本。"
      : output?.is_stale
      ? (staleTitles.length
        ? `正在阅读已保存书稿，尚未包含《${staleTitles[0]}》${staleTitles.length > 1 ? "等章节" : ""}的最新变化。`
        : "正在阅读已保存书稿，尚未包含最新变化。")
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
    readerUpdateNotice: workspace.draft_needs_rewrite ? "正在阅读已保存正文，尚未包含最新变化。" : "",
  };
}

definePage<BookReaderData>()({
  audioPlayback: pageRuntime(() => new AudioPlaybackController()),
  ttsProgress: pageRuntime(() => new VoiceAssistantTTSProgressRuntime()),
  playToken: 0,
  chapterParagraphRanges: [] as ChapterParagraphRange[],
  readerHighlightKey: "",
  readerText: "",
  readerAudioVersionId: "",
  bookChapters: [] as BookOutputChapterPreviewView[],
  flatPages: [] as string[],
  shareLikeTarget: null as MemoirShareLikeTarget | null,
  readingToken: "",
  readingProgressKey: "",
  sharedReadingObjectId: "",
  sharedReadingShown: false,
  sharedReadGeneration: 0,
  readingScrollTop: 0,
  readingProgress: null as SharedReadingProgress | null,


  data: {
    responseToken: "",
    pageClass: accessibilityPageClass(PAGE_CLASS),
    loading: false,
    error: "",
    playbackError: "",
    projectId: "",
    chapterId: "",
    sectionId: "",
    versionId: "",
    readerMode: "book",
    readerShared: false,
    readerDemo: false,
    sharePreview: false, shareScope: "book", shareImages: [], shareCoverUrl: "", shareByline: "",
    sharePreface: "", shareAfterword: "", shareRetryable: false,
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
    ...INITIAL_SHARE_LIKE_DATA,
  },

  ...shareLikeMethods,

  startMyStory() {
    if (!this.data.readerDemo) return;
    if (readSessionCookie()) returnToHomeThenNarrate();
    else navigateToLoginThenNarrate();
  },

  async onLoad(query: PageQuery = {}) {
    const projectId = queryParam(query, "projectId");
    const chapterId = queryParam(query, "chapterId");
    const sectionId = queryParam(query, "sectionId");
    const versionId = queryParam(query, "versionId");
    const shareToken = queryParam(query, "shareToken");
    const sharePreview = queryParam(query, "sharePreview") === "1";
    this.readingToken = shareToken;
    this.sharedReadingShown = false;
    this.setData({ responseToken: shareToken });
    // Visitors open the sample memoir's open chapters before logging in.
    const demo = queryParam(query, "demo") === "1";
    const readerMode = chapterId ? "chapter" : "book";
    this.setData({ projectId, chapterId, sectionId, versionId, readerMode, readerDemo: demo, sharePreview, readerShared: Boolean(shareToken) || demo || sharePreview });
    if (sharePreview) {
      await this.loadSharedBookReader(() => previewMemoirBookShare(projectId, chapterId || undefined, sectionId || undefined));
      return;
    }
    if (shareToken || demo) {
      await this.loadSharedBookReader(() => (demo ? readMemoirDemoBook() : readMemoirBookShare(shareToken)));
      // Family who open a forwarded link can like what they read.
      if (shareToken && !this.data.error) await this.loadShareLikes({ reading_token: shareToken });
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
  },

  onShow() {
    const returningToShare = Boolean(this.readingToken && this.sharedReadingShown);
    if (this.readingToken) this.sharedReadingShown = true;
    this.setData(accessibilityPagePatch(PAGE_CLASS));
    if (this.readingToken && !this.data.error) enableMiniProgramShareMenu(); else hideMiniProgramShareMenu();
    if (this.sharedReadingObjectId && this.readingProgressKey !== sharedReadingKey(this.sharedReadingObjectId, this.data.shareScope)) {
      this.stopPlayback();
      this.restoreSharedReadingPosition();
    }
    // Recheck the same link before showing a snapshot that may have been removed.
    if (returningToShare) void this.retrySharedReading();
    else this.refreshShareLikes();
  },

  onShareAppMessage() {
    const token = this.data.error ? "" : this.readingToken;
    return readingShareMessage(this.data.readerTitle, token, token ? readingShareImageUrl(token, undefined, this.readerAudioVersionId) : "");
  },

  openShareHome() { wx.reLaunch({ url: "/pages/home/index" }); },

  async retrySharedReading() {
    if (this.data.sharePreview) await this.loadSharedBookReader(() => previewMemoirBookShare(this.data.projectId, this.data.chapterId || undefined, this.data.sectionId || undefined));
    else if (this.data.readerDemo) await this.loadSharedBookReader(() => readMemoirDemoBook());
    else if (this.readingToken) {
      await this.loadSharedBookReader(() => readMemoirBookShare(this.readingToken));
      if (!this.data.error) await this.loadShareLikes({ reading_token: this.readingToken });
    }
  },

  onResponseUnavailable() {
    void this.retrySharedReading();
  },

  onShareUnavailable(error: Error) {
    this.sharedReadGeneration += 1;
    this.stopPlayback();
    this.readerText = "";
    this.readerAudioVersionId = "";
    this.bookChapters = [];
    this.flatPages = [];
    this.chapterParagraphRanges = [];
    hideMiniProgramShareMenu();
    this.setData({ ...readerData(null), readerChapterIndex: 0, readerChapterTotal: 0,
      shareImages: [], shareCoverUrl: "", shareByline: "", sharePreface: "", shareAfterword: "",
      loading: false, shareRetryable: !(error instanceof ApiError && [404, 410].includes(error.statusCode)), error: error.message });
  },

  onHide() {
    this.saveReadingProgress();
    this.stopPlayback();
  },

  onUnload() {
    this.saveReadingProgress();
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
    if (index < 0 || index >= this.data.readerChapterTotal) return;
    this.stopPlayback();
    this.readingScrollTop = 0;
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
    if (this.data.readerShared && !this.readingToken) return;
    const target = this.data.readerMode;
    if (this.data.playingTarget === target || this.data.ttsBusyTarget === target) {
      this.stopPlayback();
      return;
    }
    if ((!this.data.projectId && !this.readingToken) || !this.readerText) {
      return;
    }
    if (this.data.bookViewingHistoricalVersion) {
      this.setData({ playbackError: "历史版本没有对应朗读，请切回最新版后再播放。" });
      return;
    }
    this.stopPlayback();
    if (this.readingToken) {
      const chapterId = this.data.shareScope === "book" ? this.bookChapters[this.data.readerChapterIndex]?.chapterId : undefined;
      const readingToken = this.readingToken, versionId = this.readerAudioVersionId;
      this.audioPlayback.setDownloadAudioFile(id => downloadMemoirSharedAudio(readingToken, versionId, id, chapterId));
    }
    const token = this.audioPlayback.nextToken();
    this.playToken = token;
    this.setData({
      ttsBusyTarget: target,
      playbackError: "",
    });
    try {
      if (this.readingToken) {
        const chapterId = this.data.shareScope === "book" ? this.bookChapters[this.data.readerChapterIndex]?.chapterId : undefined;
        const versionId = this.readerAudioVersionId;
        const readingToken = this.readingToken;
        const plan = await readMemoirSharedAudioPlan(readingToken, versionId, chapterId);
        const prepare = (index: number, current: MemoirBookAudioPlan) => readMemoirSharedAudioPlan(
          readingToken, versionId, chapterId, { generate: true, start_index: index,
            source_text_hash: current.source_text_hash, profile_key: current.profile_key },
        );
        if (token !== this.playToken) return;
        const playbackToken = token;
        const saved = this.readingProgress;
        const resume = saved?.versionId === versionId && saved.audioHash === plan.source_text_hash
          && saved.audioChapterId === (chapterId || "") && saved.audioIndex < plan.segments.length
          ? { index: saved.audioIndex, seconds: Math.min(saved.audioSeconds, Math.max(0, (plan.segments[saved.audioIndex].duration_seconds || 0) - 0.1)) }
          : { index: 0, seconds: 0 };
        this.readingProgress = { versionId, chapterId: this.bookChapters[this.data.readerChapterIndex]?.chapterId || "",
          chapterIndex: this.data.readerChapterIndex, scrollTop: this.readingScrollTop,
          audioChapterId: chapterId || "", audioHash: plan.source_text_hash, audioIndex: resume.index, audioSeconds: resume.seconds };
        try {
          await playBookAudioPlan(this, plan, playbackToken, plan.content, prepare, target, resume);
          if (playbackToken === this.playToken) this.onBookAudioPosition(0, 0);
        } catch (error) {
          if (playbackToken === this.playToken) await this.handleSharedAudioError(error);
        }
        return;
      }
      const source = this.data.readerMode === "book"
        ? { kind: "book" as const, versionId: this.readerAudioVersionId }
        : { kind: "chapter" as const, chapterId: this.data.chapterId, versionId: this.readerAudioVersionId || undefined };
      const prepare = memoirAudioPlanLoader(this.data.projectId, source, readBookTTSRequest());
      const plan = await prepare();
      if (token === this.playToken) await playBookAudioPlan(this, plan, token, this.readerText, prepare, target);
    } catch (error) {
      if (this.readingToken) {
        if (token === this.playToken) await this.handleSharedAudioError(error);
        return;
      }
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

  async handleSharedAudioError(error: unknown) {
    if (error instanceof ApiError && [404, 409, 410].includes(error.statusCode)) {
      await this.retrySharedReading();
      if (this.data.error) return;
    }
    this.setData({ playbackError: error instanceof Error ? error.message : "朗读暂时不可用，可以先阅读正文" });
  },

  continueSharedReading() {
    if (this.readingScrollTop > 0) scrollReader({ scrollTop: this.readingScrollTop });
    else scrollReader({ selector: "#reader-body" });
  },

  onPageScroll(event: { scrollTop: number }) { this.readingScrollTop = event.scrollTop; },

  saveReadingProgress() {
    if (!this.readingProgressKey || this.data.error) return;
    const prior = this.readingProgress;
    const value: SharedReadingProgress = {
      versionId: this.readerAudioVersionId, chapterId: this.bookChapters[this.data.readerChapterIndex]?.chapterId || "",
      chapterIndex: this.data.readerChapterIndex, scrollTop: this.readingScrollTop,
      audioChapterId: prior?.audioChapterId || "", audioHash: prior?.audioHash || "",
      audioIndex: prior?.audioIndex || 0, audioSeconds: prior?.audioSeconds || 0,
    };
    this.readingProgress = value;
    saveSharedReadingProgress(this.readingProgressKey, value);
  },

  restoreReadingProgress(output: MemoirBookOutput) {
    this.sharedReadingObjectId = output.id;
    this.restoreSharedReadingPosition();
  },

  restoreSharedReadingPosition() {
    this.readingProgressKey = sharedReadingKey(this.sharedReadingObjectId, this.data.shareScope);
    const saved = readSharedReadingProgress(this.readingProgressKey);
    this.readingProgress = saved;
    this.readingScrollTop = 0;
    if (!saved) {
      if (this.data.readerChapterIndex !== 0) this.showReaderChapter(0);
      scrollReader({ scrollTop: 0 });
      return;
    }
    const sameVersion = saved.versionId === this.readerAudioVersionId;
    const index = saved.chapterId ? this.bookChapters.findIndex(chapter => chapter.chapterId === saved.chapterId)
      : (sameVersion ? Math.min(saved.chapterIndex, this.data.readerChapterTotal - 1) : 0);
    if (index >= 0) this.showReaderChapter(index);
    const scrollTop = sameVersion && index >= 0 ? saved.scrollTop : 0;
    this.readingScrollTop = scrollTop;
    if (!sameVersion) this.readingProgress = { ...saved, audioHash: "", audioIndex: 0, audioSeconds: 0 };
    this.setData({}, () => scrollReader({ scrollTop }));
  },

  onBookAudioPosition(index: number, seconds: number) {
    if (!this.readingProgress) return;
    this.readingProgress.audioIndex = index;
    this.readingProgress.audioSeconds = seconds;
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
    if (this.readingProgress && this.data.playingTarget) this.saveReadingProgress();
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
    this.readerAudioVersionId = (version || output.latest_version)?.id || "";
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

  async loadSharedBookReader(read: () => Promise<MemoirBookOutput>) {
    const generation = ++this.sharedReadGeneration;
    this.stopPlayback();
    this.setData({ loading: true, error: "", playbackError: "" });
    try {
      const output = await read();
      if (generation !== this.sharedReadGeneration) return;
      this.applyBookReaderOutput(output);
      const presentation = this.applySharePresentation(output, generation);
      if (this.readingToken) this.restoreReadingProgress(output);
      if (this.readingToken) enableMiniProgramShareMenu();
      this.setData({ loading: false });
      await presentation;
    } catch (error) {
      if (generation === this.sharedReadGeneration) this.onShareUnavailable(error instanceof Error ? error : new Error("分享链接已失效"));
    } finally {
      if (generation === this.sharedReadGeneration) this.setData({ loading: false });
    }
  },

  async applySharePresentation(output: MemoirBookOutput, generation: number) {
    const manuscript = output.latest_version?.manuscript;
    const scope = output.share_scope || "book";
    const imageUrl = async (url = ""): Promise<string> => {
      if (!url.startsWith("/api/mobile/memoir/")) return "";
      const base = cleanBaseUrl(readBackendConfig().httpBaseUrl);
      if (!this.data.sharePreview) return `${base}${url}`;
      try {
        return await downloadAuthenticatedFile(`${base}${url}`, { failureMessage: "配图暂时打不开", domainLabel: "分享配图" });
      } catch {
        return ""; // A missing illustration must not hide the saved text.
      }
    };
    this.setData({
      shareScope: scope, readerMode: scope === "book" ? "book" : "chapter", shareImages: [], shareCoverUrl: "",
      shareByline: [manuscript?.profile.narrator_name ? `${manuscript.profile.narrator_name} 口述` : "", manuscript?.profile.prepared_by || ""].filter(Boolean).join(" · "),
      sharePreface: (manuscript?.front_matter || []).filter(item => item.kind === "preface" && item.enabled).map(item => item.content).join("\n\n"),
      shareAfterword: manuscript?.back_matter.afterword.enabled ? manuscript.back_matter.afterword.content : "",
    });
    const images = await Promise.all((manuscript?.chapters || []).flatMap(chapter => chapter.illustrations.map(async item => ({
      id: item.image_asset_id, chapterId: chapter.chapter_id, sectionId: item.section_id || "",
      url: await imageUrl(item.image_url), caption: item.caption, disclosure: item.disclosure,
    }))));
    const cover = manuscript?.render_settings.cover_image;
    const coverUrl = cover ? await imageUrl(cover.image_url) : "";
    if (generation !== this.sharedReadGeneration) return;
    this.setData({
      shareImages: images.filter(item => Boolean(item.url)), shareCoverUrl: coverUrl,
    });
  },

  async loadChapterReader(projectId: string, chapterId: string) {
    this.stopPlayback();
    this.setData({ loading: true, error: "", playbackError: "" });
    try {
      const workspace = await getMemoirChapterWorkspace(projectId, chapterId);
      const data = chapterReaderData(workspace);
      this.readerAudioVersionId = workspace.draft_version_id || "";
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
