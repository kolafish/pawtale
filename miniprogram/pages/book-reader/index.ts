import type {
  MemoirBookAudioResponse,
  MemoirBookOutput,
  MemoirBookOutputVersion,
} from "../../domain/voice-chat";
import { definePage } from "../../services/page-definition";
import {
  createMemoirBookOutputTTS,
  getMemoirBookOutput,
  getMemoirBookOutputVersion,
} from "../../services/memoir-voice-api";
import { routeAuthError } from "../../services/navigation";
import { AudioPlaybackController } from "../../services/audio-playback";
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
  trimText,
  type BookData,
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
  versionId: string;
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

definePage<BookReaderData>()({
  audioPlayback: new AudioPlaybackController(),
  ttsProgress: new VoiceAssistantTTSProgressRuntime(),
  playToken: 0,

  data: {
    loading: false,
    error: "",
    playbackError: "",
    projectId: "",
    versionId: "",
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
    const versionId = queryParam(query, "versionId");
    this.setData({ projectId, versionId });
    if (!projectId) {
      this.setData({ error: "缺少成书信息，暂时无法打开。" });
      return;
    }
    await this.loadBookReader(projectId, versionId);
  },

  onShow() {
    enableMiniProgramShareMenu();
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

  async playBookAudio() {
    const target = "book";
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
      const response = await createMemoirBookOutputTTS(
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
          playbackError: error instanceof Error ? error.message : "全书朗读失败",
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
});
