import { downloadAuthenticatedFile } from "../../services/authenticated-media";
import { INITIAL_EXCERPT_DATA, readingExcerptMethods } from "../../services/reading-excerpt-share";
import { excerptShareMessage } from "../../services/share";
import type {
  MemoirExcerptShare,
  MemoirBookAudioPlan,
  MemoirBookManuscriptIllustration,
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
  memoirImageAssetFileUrl,
  getMemoirBookOutput,
  getMemoirBookOutputVersion,
  readMemoirBookShare, readMemoirSharedAudioPlan, downloadMemoirSharedAudio,
  previewMemoirBookShare,
} from "../../services/memoir-book-api";
import { getMemoirChapterWorkspace } from "../../services/memoir-chapter-workspace-api";
import { routeAuthError } from "../../services/navigation";
import { ApiError, cleanBaseUrl, readBackendConfig } from "../../services/request";
import { sharedReadingKey, readSharedReadingProgress, saveSharedReadingProgress, type SharedReadingProgress } from "../../stores/reading-progress";
import { AudioPlaybackController } from "../../services/audio-playback";
import { scrollPageTo, setPageNavigationTitle } from "../../services/page-navigation";
import { readBookTTSRequest } from "../../services/tts-settings";
import {
  VoiceAssistantTTSProgressRuntime,
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
  bookCoverImageUrl,
  selectedBookCoverStyle,
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

import { readerParagraphRanges, sentenceHighlight, type ReaderParagraphRange } from "./reader-highlight";

type BookReaderTocItem = BookReadingData["bookPreviewTocItems"][number] & {
  expanded: boolean;
  storyCountText: string;
};

type ReaderData = Omit<BookReadingData, "bookPreviewTocItems"> & {
  bookPreviewTocItems: BookReaderTocItem[];
};

type BookReaderData = ReaderData & BookTTSState & ShareLikeData & typeof INITIAL_EXCERPT_DATA & {
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
  sharePreview: boolean;
  shareScope: "book" | "chapter" | "story";
  shareImages: { id: string; chapterId: string; sectionId: string; placement: string; paragraphBefore: number; paragraphCount: number; position: string; portrait: boolean; aspectRatio: number; galleryHeight: number; url: string; caption?: string; disclosure: string }[];
  imageGallerySections: Record<string, boolean>;
  sharedBookEdition: boolean;
  editionTocOpen: boolean;
  resumeAvailable: boolean;
  editionArt: string;
  editionStyle: string;
  coverCaption: string;
  shareCoverUrl: string;
  shareByline: string;
  sharePreface: string;
  shareAfterword: string;
  shareRetryable: boolean;
  readerChapterIndex: number;
  readerChapterTotal: number;
  nextChapterTitle: string;
  readerTitle: string;
  readerSubtitle: string;
  readerMetaText: string;
  readerUpdateNotice: string;
  readerHighlightChapterIndex: number;
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

interface ReaderTouchPoint {
  identifier: number;
  clientX: number;
  clientY: number;
}
interface ReaderTouchEvent {
  timeStamp: number;
  touches: ReaderTouchPoint[];
  changedTouches: ReaderTouchPoint[];
}
interface ReaderSwipeStart extends ReaderTouchPoint {
  timeStamp: number;
  chapterIndex: number;
  versionId: string;
}

const PAGE_CLASS = "book-reader-page ui-page-shell ui-accessible-page";
const FLAT_READER_PAGE_CHARS = 6000;

async function readingImageUrl(
  image: Pick<MemoirBookManuscriptIllustration, "image_url" | "image_asset_id">,
  privateProjectId = "",
): Promise<string> {
  const url = image.image_url || "";
  const absoluteUrl = url.startsWith("/api/mobile/memoir/")
    ? `${cleanBaseUrl(readBackendConfig().httpBaseUrl)}${url}`
    : privateProjectId && image.image_asset_id
      ? `${memoirImageAssetFileUrl(privateProjectId, image.image_asset_id)}?variant=reading`
      : "";
  if (!absoluteUrl || !privateProjectId) return absoluteUrl;
  try {
    return await downloadAuthenticatedFile(absoluteUrl, { failureMessage: "配图暂时打不开", domainLabel: "阅读配图" });
  } catch {
    return ""; // A missing illustration must not hide the saved text.
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
    familyNotes: workspace.family_notes,
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
  excerptImageController: null as import("../../services/transcript-material-image").TranscriptMaterialImageController | null,
  excerptSource: "",
  excerptVersionId: "",
  excerptToken: "",
  excerptGeneration: 0,
  excerptEditor: null as WechatMiniprogram.EditorContext | null,
  audioPlayback: pageRuntime(() => new AudioPlaybackController()),
  ttsProgress: pageRuntime(() => new VoiceAssistantTTSProgressRuntime()),
  playToken: 0,
  readerParagraphRanges: [] as ReaderParagraphRange[],
  readerHighlightKey: "",
  readerLocatePlayback: false,
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
  readerSwipeStart: null as ReaderSwipeStart | null,
  readingProgress: null as SharedReadingProgress | null,


  data: {
    ...INITIAL_EXCERPT_DATA,
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
    sharedBookEdition: false, editionTocOpen: false, resumeAvailable: false, editionArt: "", editionStyle: "", coverCaption: "",
    sharePreview: false, shareScope: "book", shareImages: [], imageGallerySections: {}, shareCoverUrl: "", shareByline: "",
    sharePreface: "", shareAfterword: "", shareRetryable: false,
    readerChapterIndex: 0,
    readerChapterTotal: 0, nextChapterTitle: "",
    readerHighlightChapterIndex: -1,
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
  ...readingExcerptMethods,

  async onLoad(query: PageQuery = {}) {
    const projectId = queryParam(query, "projectId");
    const chapterId = queryParam(query, "chapterId");
    this.excerptToken = queryParam(query, "excerptToken");
    if (this.excerptToken) {
      this.setData({ excerptMode: "shared", responseToken: this.excerptToken });
      setPageNavigationTitle("选文分享");
      await this.loadExcerptSnapshot();
      return;
    }
    if (queryParam(query, "excerptSelect") === "1") {
      this.setData({ excerptMode: "select" });
      hideMiniProgramShareMenu();
    }
    const sectionId = queryParam(query, "sectionId");
    const versionId = queryParam(query, "versionId");
    const shareToken = queryParam(query, "shareToken");
    const sharePreview = queryParam(query, "sharePreview") === "1";
    this.readingToken = shareToken;
    this.sharedReadingShown = false;
    this.setData({ responseToken: shareToken });
    const readerMode = chapterId ? "chapter" : "book";
    this.setData({ projectId, chapterId, sectionId, versionId, readerMode, sharePreview, readerShared: Boolean(shareToken) || sharePreview });
    if (sharePreview) {
      await this.loadSharedBookReader(() => previewMemoirBookShare(projectId, chapterId || undefined, sectionId || undefined));
      return;
    }
    if (shareToken) {
      await this.loadSharedBookReader(() => readMemoirBookShare(shareToken));
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
    this.setData(accessibilityPagePatch(PAGE_CLASS));
    this.layoutReadingImages(this.data.shareImages);
    if (this.data.excerptMode) {
      if (this.data.excerptMode === "compose") this.resumeExcerptImage();
      if (this.data.excerptMode === "shared" && !this.data.loading) void this.loadExcerptSnapshot();
      else if (this.data.excerptMode === "select" && !this.excerptSource && !this.data.loading) void this.loadChapterReader(this.data.projectId, this.data.chapterId);
      else if (this.data.excerptMode === "select" && !this.data.loading && !this.excerptEditor) this.onExcerptEditorReady();
      return;
    }
    const returningToShare = Boolean(this.readingToken && this.sharedReadingShown);
    if (this.readingToken) this.sharedReadingShown = true;
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
    if (this.data.excerptMode) return excerptShareMessage(this.data.excerptImageReady ? this.excerptToken : "", this.data.excerptTitle);
    const token = this.data.error ? "" : this.readingToken;
    return readingShareMessage(this.data.readerTitle, token, token ? readingShareImageUrl(token, undefined, this.readerAudioVersionId) : "");
  },

  openShareHome() { wx.reLaunch({ url: "/pages/home/index" }); },

  async retrySharedReading() {
    if (this.data.sharePreview) await this.loadSharedBookReader(() => previewMemoirBookShare(this.data.projectId, this.data.chapterId || undefined, this.data.sectionId || undefined));
    else if (this.readingToken) {
      await this.loadSharedBookReader(() => readMemoirBookShare(this.readingToken));
      if (!this.data.error) await this.loadShareLikes({ reading_token: this.readingToken });
    }
  },

  onResponseUnavailable() {
    if (this.data.excerptMode === "shared") void this.loadExcerptSnapshot();
    else void this.retrySharedReading();
  },

  onShareUnavailable(error: Error) {
    this.sharedReadGeneration += 1;
    this.stopPlayback();
    this.readerText = "";
    this.readerAudioVersionId = "";
    this.bookChapters = [];
    this.flatPages = [];
    hideMiniProgramShareMenu();
    this.setData({ ...readerData(null), readerChapterIndex: 0, readerChapterTotal: 0,
      shareImages: [], imageGallerySections: {}, shareCoverUrl: "", shareByline: "", sharePreface: "", shareAfterword: "",
      loading: false, shareRetryable: !(error instanceof ApiError && [404, 410].includes(error.statusCode)), error: error.message });
  },

  onHide() {
    this.cancelReaderSwipe();
    if (this.data.excerptMode) this.invalidateExcerptRequests();
    this.saveReadingProgress();
    this.stopPlayback();
  },

  onUnload() {
    this.cancelReaderSwipe();
    this.excerptImageController?.dispose();
    if (this.data.excerptMode) this.invalidateExcerptRequests();
    this.saveReadingProgress();
    this.stopPlayback();
  },

  toggleEditionToc() {
    this.setData({ editionTocOpen: !this.data.editionTocOpen });
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
    this.cancelReaderSwipe();
    if (index < 0 || index >= this.data.readerChapterTotal) return;
    this.stopPlayback();
    this.readingScrollTop = 0;
    this.setData({ ...this.readerChapterPatch(index), editionTocOpen: false, resumeAvailable: false }, () => scrollPageTo({
      selector: sectionIndex === undefined ? "#reader-body" : `#reader-section-${sectionIndex}`,
      duration: 280,
    }));
  },

  readerChapterPatch(index: number) {
    const chapter = this.bookChapters[index];
    return {
      readerChapterIndex: index,
      nextChapterTitle: this.bookChapters[index + 1]?.title || "",
      bookPreviewChapters: chapter ? [compactReaderChapter(chapter)] : [],
      bookContent: chapter ? "" : this.flatPages[index] || "",
    };
  },

  canSwipeReaderChapter() {
    return this.data.readerMode === "book" && !this.data.excerptMode
      && !this.data.loading && !this.data.error && this.data.readerChapterTotal > 1;
  },

  onReaderTouchStart(event: ReaderTouchEvent) {
    this.cancelReaderSwipe();
    const touch = event.touches[0];
    // Leave the system's left-edge back gesture alone. Gesture state stays off setData.
    if (!this.canSwipeReaderChapter() || event.touches.length !== 1 || touch.clientX <= 24) return;
    this.readerSwipeStart = { ...touch, timeStamp: event.timeStamp,
      chapterIndex: this.data.readerChapterIndex, versionId: this.readerAudioVersionId };
  },

  onReaderTouchMove(event: ReaderTouchEvent) {
    const start = this.readerSwipeStart;
    if (!start) return;
    const touch = event.touches[0];
    if (event.touches.length !== 1 || touch.identifier !== start.identifier) {
      this.cancelReaderSwipe();
      return;
    }
    const dx = Math.abs(touch.clientX - start.clientX);
    const dy = Math.abs(touch.clientY - start.clientY);
    // Once a gesture becomes vertical, returning sideways must not turn a chapter.
    if (dy >= 12 && dy >= dx) this.cancelReaderSwipe();
  },

  onReaderTouchEnd(event: ReaderTouchEvent) {
    const start = this.readerSwipeStart;
    this.cancelReaderSwipe();
    const touch = event.changedTouches[0];
    if (!start || !this.canSwipeReaderChapter() || event.touches.length
      || event.changedTouches.length !== 1 || touch.identifier !== start.identifier
      || event.timeStamp - start.timeStamp > 1000
      || start.chapterIndex !== this.data.readerChapterIndex || start.versionId !== this.readerAudioVersionId) return;
    const dx = touch.clientX - start.clientX;
    const dy = touch.clientY - start.clientY;
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 2) return;
    if (dx < 0) this.nextReaderChapter();
    else this.previousReaderChapter();
  },

  cancelReaderSwipe() {
    this.readerSwipeStart = null;
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
    scrollPageTo({ duration: 280, scrollTop: 0 });
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
    if (this.data.resumeAvailable) this.setData({ resumeAvailable: false });
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
          this.prepareReaderHighlight(plan.content, this.bookChapters.length ? this.data.readerChapterIndex : undefined);
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
      if (token === this.playToken) {
        this.prepareReaderHighlight(this.readerText);
        await playBookAudioPlan(this, plan, token, this.readerText, prepare, target);
      }
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
    if (this.data.resumeAvailable) this.restoreSharedReadingPosition(false);
  },

  onPageScroll(event: { scrollTop: number }) { this.readingScrollTop = event.scrollTop; },

  saveReadingProgress() {
    if (!this.readingProgressKey || this.data.error || this.data.loading) return;
    // Scrolling the cover does not replace a bookmark waiting to be resumed.
    if (this.data.resumeAvailable) return;
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
    const openingCover = !this.sharedReadingObjectId || this.data.resumeAvailable;
    this.sharedReadingObjectId = output.id;
    this.restoreSharedReadingPosition(openingCover);
  },

  restoreSharedReadingPosition(openingCover = true) {
    this.readingProgressKey = sharedReadingKey(this.sharedReadingObjectId, this.data.shareScope);
    const saved = readSharedReadingProgress(this.readingProgressKey);
    this.readingProgress = saved;
    this.readingScrollTop = 0;
    this.setData({ resumeAvailable: false });
    if (!saved) {
      if (this.data.readerChapterIndex !== 0) this.showReaderChapter(0);
      scrollPageTo({ duration: 280, scrollTop: 0 });
      return;
    }
    const sameVersion = saved.versionId === this.readerAudioVersionId;
    const index = saved.chapterId ? this.bookChapters.findIndex(chapter => chapter.chapterId === saved.chapterId)
      : (sameVersion ? Math.min(saved.chapterIndex, this.data.readerChapterTotal - 1) : 0);
    const scrollTop = sameVersion && index >= 0 ? saved.scrollTop : 0;
    if (!sameVersion) this.readingProgress = { ...saved, audioHash: "", audioIndex: 0, audioSeconds: 0 };
    const resumeAvailable = this.data.sharedBookEdition && openingCover && (index > 0 || scrollTop > 0);
    this.readingScrollTop = resumeAvailable ? 0 : scrollTop;
    this.setData({
      ...this.readerChapterPatch(resumeAvailable ? 0 : Math.max(0, index)),
      resumeAvailable, editionTocOpen: false,
    }, () => scrollPageTo({ duration: 280, scrollTop: resumeAvailable ? 0 : scrollTop }));
  },

  onBookAudioPosition(index: number, seconds: number) {
    if (!this.readingProgress) return;
    this.readingProgress.audioIndex = index;
    this.readingProgress.audioSeconds = seconds;
  },

  prepareReaderHighlight(text: string, onlyChapter?: number) {
    this.readerLocatePlayback = true;
    this.readerParagraphRanges = readerParagraphRanges(text, this.bookChapters, this.flatPages, onlyChapter);
  },

  onBookTTSOffset(currentTextStart: number | null) {
    if (currentTextStart === null) {
      this.clearReaderHighlight();
      return;
    }
    const range = this.readerParagraphRanges.find(
      (item) => currentTextStart >= item.start && currentTextStart < item.end,
    );
    if (!range) {
      this.clearReaderHighlight();
      return;
    }
    const highlight = sentenceHighlight(range, currentTextStart);
    const key = [
      range.chapterIndex,
      range.sectionId,
      range.paragraphIndex,
      highlight.before.length,
      highlight.current.length,
    ].join(":");
    if (key === this.readerHighlightKey) {
      return;
    }
    this.readerHighlightKey = key;
    const changedChapter = range.chapterIndex !== this.data.readerChapterIndex;
    const locatePlayback = this.readerLocatePlayback || changedChapter;
    const playbackToken = this.playToken;
    this.readerLocatePlayback = false;
    this.setData({
      ...(changedChapter ? this.readerChapterPatch(range.chapterIndex) : {}),
      readerHighlightChapterIndex: range.chapterIndex,
      readerHighlightSectionId: range.sectionId,
      readerHighlightParagraphIndex: range.paragraphIndex,
      readerHighlightBefore: highlight.before,
      readerHighlightCurrent: highlight.current,
      readerHighlightAfter: highlight.after,
    }, () => {
      if (locatePlayback && this.playToken === playbackToken && this.readerHighlightKey === key) {
        wx.createSelectorQuery().select(".reader-tts-shell").fields({
          size: true,
          computedStyle: ["top"],
        }, (player) => {
          if (!player?.height || this.playToken !== playbackToken || this.readerHighlightKey !== key) return;
          // Leave the sentence below the sticky player, including its inset and a reading gap.
          scrollPageTo({
            selector: "#reader-spoken-sentence",
            offsetTop: -(player.height + (parseFloat(player.top || "0") || 0) + 12),
            duration: 280,
          });
        }).exec();
      }
    });
  },

  clearReaderHighlight() {
    if (!this.readerHighlightKey && !this.data.readerHighlightCurrent) {
      return;
    }
    this.readerHighlightKey = "";
    this.setData({
      readerHighlightChapterIndex: -1,
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
    this.readerParagraphRanges = [];
    this.readerLocatePlayback = false;
  },

  async loadBookReader(projectId: string, versionId = "") {
    const generation = ++this.sharedReadGeneration;
    this.stopPlayback();
    this.setData({ loading: true, error: "", playbackError: "" });
    try {
      const output = await getMemoirBookOutput(projectId);
      const latestVersion = output.latest_version || null;
      const version = versionId && versionId !== latestVersion?.id
        ? await getMemoirBookOutputVersion(projectId, versionId)
        : latestVersion;
      if (generation !== this.sharedReadGeneration) return;
      this.applyBookReaderOutput(output, version);
      this.setData({ loading: false });
      await this.applyReadingPresentation(version, true, generation);
    } catch (error) {
      if (generation !== this.sharedReadGeneration) return;
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "成书打开失败" });
    } finally {
      if (generation === this.sharedReadGeneration) this.setData({ loading: false });
    }
  },

  applyBookReaderOutput(output: MemoirBookOutput, version: MemoirBookOutputVersion | null = null) {
    const data = readerData(output, version);
    this.readerText = trimText(data.bookContent);
    this.readerAudioVersionId = (version || output.latest_version)?.id || "";
    this.bookChapters = data.bookPreviewChapters;
    this.flatPages = this.bookChapters.length ? [] : flatReaderPages(data.bookContent);
    this.setData({
      ...data,
      ...this.readerChapterPatch(0),
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
      const scope = output.share_scope || "book";
      const style = selectedBookCoverStyle(output);
      if (scope === "book") setPageNavigationTitle("全书阅读");
      this.setData({
        shareScope: scope, sharedBookEdition: scope === "book", readerMode: scope === "book" ? "book" : "chapter",
        ...(scope === "book" ? {
          readerSubtitle: trimText(output.latest_version?.manuscript?.profile.subtitle || ""),
          editionStyle: style,
        } : {}),
      });
      const presentation = this.applyReadingPresentation(
        output.latest_version || null, this.data.sharePreview, generation, scope === "book" ? bookCoverImageUrl(style) : "",
      );
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

  async applyReadingPresentation(version: MemoirBookOutputVersion | null, privateImages: boolean, generation: number, templateArt = "") {
    const manuscript = version?.manuscript;
    const cover = manuscript?.render_settings.cover_image;
    this.setData({
      editionArt: cover ? "" : templateArt,
      shareImages: [], imageGallerySections: {}, shareCoverUrl: "",
      shareByline: [manuscript?.profile.narrator_name ? `${manuscript.profile.narrator_name} 口述` : "", manuscript?.profile.prepared_by || ""].filter(Boolean).join(" · "),
      sharePreface: (manuscript?.front_matter || []).filter(item => item.kind === "preface" && item.enabled).map(item => item.content).join("\n\n"),
      shareAfterword: manuscript?.back_matter.afterword.enabled ? manuscript.back_matter.afterword.content : "",
    });
    await this.loadReadingImages(manuscript?.chapters || [], privateImages, generation);
    const coverUrl = cover ? await readingImageUrl(cover, privateImages ? this.data.projectId : "") : "";
    if (generation !== this.sharedReadGeneration) return;
    this.setData({
      editionArt: coverUrl ? "" : templateArt,
      shareCoverUrl: coverUrl, coverCaption: cover?.disclosure ? "AI 意象插图" : (cover?.caption || ""),
    });
  },

  async applyExcerptReading(excerpt: MemoirExcerptShare, generation: number) {
    // The public snapshot is the entire reading scope; never fetch its source chapter.
    const chapter: BookOutputChapterPreviewView = {
      id: "excerpt", chapterId: "excerpt", index: 1, indexText: "", title: excerpt.title,
      content: excerpt.content, excerpt: "", metaText: "",
      sections: [{ id: "excerpt", index: 1, indexText: "", title: excerpt.title,
        content: excerpt.content, paragraphs: readingParagraphs(excerpt.content), excerpt: "", metaText: "" }],
    };
    this.bookChapters = [chapter];
    this.flatPages = [];
    this.readerText = "";
    this.readerAudioVersionId = "";
    this.setData({
      ...readerData(null), readerMode: "chapter", readerShared: true, shareScope: "story",
      readerTitle: excerpt.title, readerSubtitle: "", readerMetaText: "",
      bookPreviewChapters: [compactReaderChapter(chapter)], readerChapterIndex: 0, readerChapterTotal: 1,
      shareImages: [], imageGallerySections: {}, shareCoverUrl: "", shareByline: "", sharePreface: "", shareAfterword: "",
    });
    await this.loadReadingImages([{ chapter_id: "excerpt", illustrations: excerpt.image_url ? [{
      image_asset_id: "excerpt", image_url: excerpt.image_url, placement: "chapter_inline", section_id: "excerpt",
      inline_position: "before", disclosure: excerpt.image_disclosure || "", caption: excerpt.image_caption || "",
    }] : [] }], false, generation);
  },

  async loadReadingImages(chapters: { chapter_id: string; illustrations: Pick<MemoirBookManuscriptIllustration, "image_asset_id" | "image_url" | "placement" | "section_id" | "inline_position" | "disclosure" | "caption">[] }[], privateImages: boolean, generation: number) {
    const images = await Promise.all(chapters.flatMap(chapter => chapter.illustrations.map(async item => {
      const readerChapter = this.bookChapters.find(c => c.chapterId === chapter.chapter_id);
      const sectionId = item.placement === "chapter_opening" || !readerChapter?.sections.length ? "" : item.section_id || "";
      const section = readerChapter?.sections.find(s => s.id === sectionId);
      const count = section?.paragraphs.length || 0;
      const position = item.inline_position || "after";
      const url = await readingImageUrl(item, privateImages ? this.data.projectId : "");
      return { id: item.image_asset_id, chapterId: chapter.chapter_id, sectionId, placement: item.placement,
        paragraphBefore: position === "before" ? 0 : position === "middle" ? Math.ceil(count / 2) : count,
        paragraphCount: count, position, portrait: true, aspectRatio: 16 / 9, galleryHeight: 0,
        url, caption: item.disclosure ? "AI 意象插图" : (item.caption || "").replace(/\s+/g, " ").trim(), disclosure: item.disclosure };
    })));
    if (generation === this.sharedReadGeneration) this.layoutReadingImages(images.filter(item => Boolean(item.url)));
  },

  onReadingImageLoad(event: PageDatasetEvent<{ id: string; url: string }> & { detail: { width: number; height: number } }) {
    const id = datasetString(event, "id");
    const url = datasetString(event, "url");
    if (event.detail.width <= 0 || event.detail.height <= 0) return;
    const aspectRatio = event.detail.height / event.detail.width;
    if (!this.data.shareImages.some(image => image.id === id && image.url === url && image.aspectRatio !== aspectRatio)) return;
    this.layoutReadingImages(this.data.shareImages.map(image => image.id === id && image.url === url
      ? { ...image, portrait: aspectRatio > 1, aspectRatio } : image));
  },

  layoutReadingImages(images: BookReaderData["shareImages"]) {
    // Work in rpx: page/card padding leaves 626 of 750 units for the text.
    const contentWidth = 626;
    const imageGap = 20;
    const positioned = images.map(image => {
      const count = image.paragraphCount;
      const index = image.position === "before" ? 0 : image.position === "middle"
        ? (image.portrait ? Math.floor(count / 2) : Math.ceil(count / 2)) : count;
      return { ...image, galleryHeight: Math.min((contentWidth - imageGap) / 2 * image.aspectRatio, contentWidth * 0.65), paragraphBefore: image.portrait ? Math.min(index, Math.max(0, count - 1)) : index };
    });
    const imageGallerySections: Record<string, boolean> = {};
    const large = this.data.pageClass.includes("large-text");
    const fontSize = large ? 38 : 32;
    const lineHeight = fontSize * (large ? 2 : 1.8);
    const lineUnits = (contentWidth * 0.65 - imageGap) / fontSize;
    for (const chapter of this.bookChapters) for (const section of chapter.sections) {
      const portraits = positioned.filter(image => image.chapterId === chapter.chapterId && image.sectionId === section.id && image.portrait)
        .sort((a, b) => b.paragraphBefore - a.paragraphBefore);
      let requiredUnits = 0;
      for (const image of portraits) {
        const caption = image.caption || (image.disclosure ? "AI 意象插图" : "");
        const captionSize = large ? 32 : 29;
        const captionUnits = Array.from(caption).reduce((sum, char) => sum + (char.charCodeAt(0) < 128 ? 0.55 : 1), 0);
        const captionLines = Math.ceil(captionUnits / Math.floor(contentWidth * 0.35 / captionSize));
        const imageHeight = contentWidth * 0.35 * image.aspectRatio + 28 + (caption ? captionLines * captionSize * 1.5 + 8 : 0);
        requiredUnits += Math.ceil(imageHeight / lineHeight) * lineUnits;
        const remainingUnits = Array.from(section.paragraphs.slice(image.paragraphBefore).join("").replace(/\s/g, ""))
          .reduce((sum, char) => sum + (char.charCodeAt(0) < 128 ? 0.55 : 1), 0);
        if (remainingUnits < requiredUnits) {
          imageGallerySections[section.id] = true;
          break;
        }
      }
    }
    this.setData({ shareImages: positioned.sort((a, b) => a.paragraphBefore - b.paragraphBefore), imageGallerySections });
  },

  async loadChapterReader(projectId: string, chapterId: string) {
    const generation = ++this.sharedReadGeneration;
    this.stopPlayback();
    this.excerptEditor = null;
    this.excerptSource = "";
    this.setData({ loading: true, error: "", playbackError: "", shareImages: [], bookPreviewChapters: [] });
    try {
      const workspace = await getMemoirChapterWorkspace(projectId, chapterId);
      if (generation !== this.sharedReadGeneration) return;
      const data = chapterReaderData(workspace);
      this.excerptSource = workspace.draft_content || "";
      this.excerptVersionId = workspace.draft_version_id || "";
      this.readerAudioVersionId = workspace.draft_version_id || "";
      this.readerText = trimText(data.bookContent);
      this.bookChapters = data.bookPreviewChapters;
      this.flatPages = [];
      this.setData({
        ...data,
        bookContent: this.bookChapters.length ? "" : data.bookContent,
        bookPreviewChapters: this.bookChapters.map(compactReaderChapter),
        readerChapterIndex: 0,
        readerChapterTotal: 1,
      });
      if (!this.readerText) {
        this.setData({ error: "本章正文还没有生成。" });
      }
      this.setData({ loading: false });
      await this.loadReadingImages([{ chapter_id: chapterId, illustrations: workspace.illustrations || [] }], true, generation);
    } catch (error) {
      if (generation !== this.sharedReadGeneration) return;
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "整章预览打开失败",
      });
    } finally {
      if (generation === this.sharedReadGeneration) this.setData({ loading: false });
    }
  },
});
