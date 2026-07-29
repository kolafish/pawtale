import { ApiError } from "../../services/request";
import {
  getArticle,
  getArticleDocument,
  updateArticleContent,
  updateArticleTitle,
} from "../../services/articles";
import {
  ArticleDictationClient,
  type DictationState,
} from "../../services/dictation";
import { definePage } from "../../services/page-definition";
import { ensureRecorderAuthorization } from "../../services/recorder-authorization";

type SaveState = "saved" | "unsaved" | "saving" | "conflict";

interface EditorData {
  loading: boolean;
  error: string;
  projectId: string;
  documentId: string;
  title: string;
  content: string;
  contentRevision: number;
  wordCount: number;
  saveState: SaveState;
  saveText: string;
  cursorPosition: number;
  dictationState: DictationState;
  dictationStatusText: string;
  dictationButtonText: string;
  dictationActive: boolean;
  dictationDisabled: boolean;
}

interface InputEvent {
  detail: { value: string; cursor?: number };
}

function countWords(text: string): number {
  const chinese = text.match(/[\u4e00-\u9fff]/g)?.length || 0;
  const english = text.match(/[a-zA-Z]+/g)?.length || 0;
  return chinese + english;
}

function saveText(state: SaveState): string {
  return {
    saved: "已保存",
    unsaved: "等待保存",
    saving: "正在保存",
    conflict: "内容有冲突",
  }[state];
}

function dictationView(state: DictationState): Pick<
  EditorData,
  "dictationState" | "dictationStatusText" | "dictationButtonText"
  | "dictationActive" | "dictationDisabled"
> {
  const views = {
    idle: ["准备好后，点击开始口述", "开始口述", false, false],
    connecting: ["正在连接语音识别", "连接中", false, true],
    ready: ["可以继续口述", "开始口述", false, false],
    starting: ["正在打开麦克风", "启动中", true, true],
    recording: ["正在听你说", "停止口述", true, false],
    stopping: ["正在整理最后一段文字", "识别中", true, true],
    error: ["本次口述未完成，可以重试", "重新口述", false, false],
  } satisfies Record<DictationState, [string, string, boolean, boolean]>;
  const [dictationStatusText, dictationButtonText, dictationActive, dictationDisabled] =
    views[state];
  return {
    dictationState: state,
    dictationStatusText,
    dictationButtonText,
    dictationActive,
    dictationDisabled,
  };
}

export function insertDictationText(
  prefix: string,
  spokenText: string,
  suffix: string,
): { content: string; cursor: number } {
  const spoken = spokenText.trim();
  const leftSpace = /[a-zA-Z0-9]$/.test(prefix) && /^[a-zA-Z0-9]/.test(spoken) ? " " : "";
  const rightSpace = /[a-zA-Z0-9]$/.test(spoken) && /^[a-zA-Z0-9]/.test(suffix) ? " " : "";
  const insertion = `${leftSpace}${spoken}${rightSpace}`;
  return {
    content: `${prefix}${insertion}${suffix}`,
    cursor: prefix.length + insertion.length,
  };
}

definePage<EditorData>()({
  data: {
    loading: true,
    error: "",
    projectId: "",
    documentId: "",
    title: "",
    content: "",
    contentRevision: 0,
    wordCount: 0,
    saveState: "saved" as SaveState,
    saveText: saveText("saved"),
    cursorPosition: 0,
    ...dictationView("idle"),
  },

  saveTimer: null as ReturnType<typeof setTimeout> | null,
  savePromise: null as Promise<void> | null,
  savedTitle: "",
  savedContent: "",
  dictationClient: null as ArticleDictationClient | null,
  dictationPrefix: "",
  dictationSuffix: "",
  dictationLatestText: "",
  dictationFinalReceived: false,
  finishPromise: null as Promise<void> | null,

  async onLoad(options: { projectId?: string }) {
    if (!options.projectId) {
      this.setData({ loading: false, error: "缺少文章编号" });
      return;
    }
    this.setData({ projectId: options.projectId });
    await this.loadArticle();
  },

  onHide() {
    void this.finishPendingWork();
  },

  onUnload() {
    void this.finishPendingWork();
  },

  async loadArticle() {
    this.setData({ loading: true, error: "" });
    try {
      const article = await getArticle(this.data.projectId);
      if (article.type !== "article") {
        throw new Error("这不是文章项目");
      }
      const meta = article.groups.flatMap((group) => group.documents)[0];
      if (!meta) {
        throw new Error("文章正文不存在");
      }
      const document = await getArticleDocument(article.id, meta.id);
      this.savedTitle = article.title;
      this.savedContent = document.content;
      this.setData({
        documentId: document.id,
        title: article.title,
        content: document.content,
        contentRevision: document.content_revision,
        wordCount: document.word_count,
        cursorPosition: document.content.length,
        saveState: "saved",
        saveText: saveText("saved"),
      });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "文章加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  onTitleInput(event: InputEvent) {
    this.setData({ title: event.detail.value });
    this.scheduleSave();
  },

  onContentInput(event: InputEvent) {
    const content = event.detail.value;
    this.setData({
      content,
      wordCount: countWords(content),
      cursorPosition: event.detail.cursor ?? content.length,
    });
    this.scheduleSave();
  },

  onTitleBlur() {
    void this.saveNow();
  },

  async toggleDictation() {
    if (this.data.dictationState === "recording") {
      await this.stopDictation();
      return;
    }
    if (this.data.dictationDisabled || this.data.saveState === "conflict") {
      return;
    }
    await this.startDictation();
  },

  async startDictation() {
    this.setData({ error: "" });
    try {
      await ensureRecorderAuthorization();
      if (this.data.dictationState === "error") {
        await this.releaseDictationClient();
      }
      const cursor = Math.max(0, Math.min(this.data.cursorPosition, this.data.content.length));
      this.dictationPrefix = this.data.content.slice(0, cursor);
      this.dictationSuffix = this.data.content.slice(cursor);
      this.dictationLatestText = "";
      this.dictationFinalReceived = false;
      if (!this.dictationClient) {
        this.dictationClient = new ArticleDictationClient({
          projectId: this.data.projectId,
          documentId: this.data.documentId,
          callbacks: {
            onState: (state) => this.applyDictationState(state),
            onTranscript: (text, final) => this.applyDictationTranscript(text, final),
            onError: (message) => this.handleDictationError(message),
          },
        });
      }
      await this.dictationClient.connect();
      await this.dictationClient.start();
    } catch (error) {
      const message = error instanceof Error ? error.message : "口述启动失败";
      this.setData({ error: message, ...dictationView("error") });
    }
  },

  async stopDictation() {
    try {
      await this.dictationClient?.stop();
    } catch (error) {
      this.handleDictationError(error instanceof Error ? error.message : "口述停止失败");
    }
  },

  applyDictationState(state: DictationState) {
    if (state === "ready" && this.dictationLatestText && !this.dictationFinalReceived) {
      this.scheduleSave();
    }
    this.setData(dictationView(state));
  },

  applyDictationTranscript(text: string, final: boolean) {
    this.dictationLatestText = text.trim();
    this.dictationFinalReceived = this.dictationFinalReceived || final;
    const merged = insertDictationText(
      this.dictationPrefix,
      this.dictationLatestText,
      this.dictationSuffix,
    );
    this.setData({
      content: merged.content,
      cursorPosition: merged.cursor,
      wordCount: countWords(merged.content),
    });
    if (final) {
      this.scheduleSave();
    }
  },

  handleDictationError(message: string) {
    if (this.dictationLatestText) {
      this.scheduleSave();
    }
    this.setData({ error: message, ...dictationView("error") });
  },

  async releaseDictationClient() {
    const client = this.dictationClient;
    this.dictationClient = null;
    if (client) {
      await client.close();
    }
  },

  finishPendingWork(): Promise<void> {
    if (this.finishPromise) {
      return this.finishPromise;
    }
    this.finishPromise = (async () => {
      await this.releaseDictationClient();
      await this.saveNow();
    })().finally(() => {
      this.finishPromise = null;
    });
    return this.finishPromise;
  },

  scheduleSave() {
    if (this.data.saveState === "conflict") {
      return;
    }
    if (this.saveTimer) {
      clearTimeout(this.saveTimer);
    }
    this.setData({ saveState: "unsaved", saveText: saveText("unsaved") });
    this.saveTimer = setTimeout(() => {
      this.saveTimer = null;
      void this.saveNow();
    }, 900);
  },

  async saveNow() {
    if (this.saveTimer) {
      clearTimeout(this.saveTimer);
      this.saveTimer = null;
    }
    if (this.data.loading || this.data.saveState === "conflict" || !this.data.documentId) {
      return;
    }
    if (this.savePromise) {
      await this.savePromise;
      if (
        this.data.title !== this.savedTitle
        || this.data.content !== this.savedContent
      ) {
        await this.saveNow();
      }
      return;
    }

    const title = this.data.title.trim() || "未命名文章";
    const content = this.data.content;
    const titleChanged = title !== this.savedTitle;
    const contentChanged = content !== this.savedContent;
    if (!titleChanged && !contentChanged) {
      this.setData({ saveState: "saved", saveText: saveText("saved") });
      return;
    }

    this.setData({ saveState: "saving", saveText: saveText("saving"), error: "" });
    let savedSuccessfully = false;
    this.savePromise = (async () => {
      try {
        if (titleChanged) {
          await updateArticleTitle(this.data.projectId, title);
          this.savedTitle = title;
        }
        if (contentChanged) {
          const document = await updateArticleContent(
            this.data.projectId,
            this.data.documentId,
            content,
            this.data.contentRevision,
          );
          this.savedContent = content;
          this.setData({ contentRevision: document.content_revision });
        }
        savedSuccessfully = true;
        const saved = this.data.title.trim() === this.savedTitle
          && this.data.content === this.savedContent;
        this.setData({
          saveState: saved ? "saved" : "unsaved",
          saveText: saveText(saved ? "saved" : "unsaved"),
        });
      } catch (error) {
        if (error instanceof ApiError && error.statusCode === 409) {
          this.setData({
            saveState: "conflict",
            saveText: saveText("conflict"),
            error: "这篇文章已在其他位置修改。请重新载入最新内容后再继续。",
          });
          return;
        }
        this.setData({
          saveState: "unsaved",
          saveText: saveText("unsaved"),
          error: error instanceof Error ? error.message : "保存失败",
        });
      } finally {
        this.savePromise = null;
      }
    })();
    await this.savePromise;
    if (
      savedSuccessfully
      && this.data.saveState === "unsaved"
      && (this.data.title !== this.savedTitle || this.data.content !== this.savedContent)
    ) {
      this.scheduleSave();
    }
  },

  async reloadArticle() {
    if (this.saveTimer) {
      clearTimeout(this.saveTimer);
      this.saveTimer = null;
    }
    await this.releaseDictationClient();
    await this.loadArticle();
  },
});
