import { ApiError } from "../../services/request";
import {
  getArticle,
  getArticleDocument,
  updateArticleContent,
  updateArticleTitle,
} from "../../services/articles";
import { definePage } from "../../services/page-definition";

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
}

interface InputEvent {
  detail: { value: string };
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
  },

  saveTimer: null as ReturnType<typeof setTimeout> | null,
  savePromise: null as Promise<void> | null,
  savedTitle: "",
  savedContent: "",

  async onLoad(options: { projectId?: string }) {
    if (!options.projectId) {
      this.setData({ loading: false, error: "缺少文章编号" });
      return;
    }
    this.setData({ projectId: options.projectId });
    await this.loadArticle();
  },

  onHide() {
    void this.saveNow();
  },

  onUnload() {
    if (this.saveTimer) {
      clearTimeout(this.saveTimer);
      this.saveTimer = null;
    }
    void this.saveNow();
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
    this.setData({ content, wordCount: countWords(content) });
    this.scheduleSave();
  },

  onTitleBlur() {
    void this.saveNow();
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
    await this.loadArticle();
  },
});
