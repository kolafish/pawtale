import type { MemoirBookOutput } from "../../domain/voice-chat";
import { definePage } from "../../services/page-definition";
import { getMemoirBookOutput } from "../../services/memoir-voice-api";
import { routeAuthError } from "../../services/navigation";
import { queryParam, type PageQuery } from "../../services/page-input";
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

type BookReaderData = Pick<
  BookData,
  | "bookOutput"
  | "bookContent"
  | "bookCharCount"
  | "bookPreviewTitle"
  | "bookPreviewTocItems"
  | "bookPreviewChapters"
  | "bookManuscriptTitle"
  | "bookManuscriptSubtitle"
  | "bookVersionText"
  | "bookStaleNotice"
> & {
  loading: boolean;
  error: string;
  projectId: string;
  readerTitle: string;
  readerSubtitle: string;
};

function readerData(output: MemoirBookOutput | null): Pick<
  BookReaderData,
  | "bookOutput"
  | "bookContent"
  | "bookCharCount"
  | "bookPreviewTitle"
  | "bookPreviewTocItems"
  | "bookPreviewChapters"
  | "bookManuscriptTitle"
  | "bookManuscriptSubtitle"
  | "bookVersionText"
  | "bookStaleNotice"
  | "readerTitle"
  | "readerSubtitle"
> {
  const data = bookOutputData(output);
  const readerTitle = (
    trimText(data.bookManuscriptTitle)
    || trimText(data.bookPreviewTitle)
    || "家庭回忆录"
  );
  return {
    bookOutput: data.bookOutput,
    bookContent: data.bookContent,
    bookCharCount: data.bookCharCount,
    bookPreviewTitle: data.bookPreviewTitle,
    bookPreviewTocItems: data.bookPreviewTocItems,
    bookPreviewChapters: data.bookPreviewChapters,
    bookManuscriptTitle: data.bookManuscriptTitle,
    bookManuscriptSubtitle: data.bookManuscriptSubtitle,
    bookVersionText: data.bookVersionText,
    bookStaleNotice: data.bookStaleNotice,
    readerTitle,
    readerSubtitle: trimText(data.bookManuscriptSubtitle || "根据口述访谈整理"),
  };
}

definePage<BookReaderData>()({
  data: {
    loading: false,
    error: "",
    projectId: "",
    ...readerData(null),
  },

  async onLoad(query: PageQuery = {}) {
    const projectId = queryParam(query, "projectId");
    this.setData({ projectId });
    if (!projectId) {
      this.setData({ error: "缺少成书信息，暂时无法打开。" });
      return;
    }
    await this.loadBookReader(projectId);
  },

  onShow() {
    enableMiniProgramShareMenu();
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

  async loadBookReader(projectId: string) {
    this.setData({ loading: true, error: "" });
    try {
      const output = await getMemoirBookOutput(projectId);
      this.setData(readerData(output));
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
