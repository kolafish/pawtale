import type { ArticleListItem } from "../../domain/article";
import {
  createArticle,
  deleteArticle,
  listArticles,
} from "../../services/articles";
import { definePage } from "../../services/page-definition";

interface ArticleItem extends ArticleListItem {
  updatedText: string;
  wordCountText: string;
}

interface ArticlesData {
  loading: boolean;
  busy: boolean;
  error: string;
  deletingId: string;
  articles: ArticleItem[];
}

interface DatasetEvent {
  currentTarget: { dataset: { id?: string } };
}

function displayDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function articleItems(items: ArticleListItem[]): ArticleItem[] {
  return items.map((item) => ({
    ...item,
    updatedText: displayDate(item.updated_at),
    wordCountText: `${item.total_word_count || 0} 字`,
  }));
}

function confirmDelete(title: string): Promise<boolean> {
  return new Promise((resolve) => {
    wx.showModal({
      title: "删除文章",
      content: `确定删除「${title}」吗？删除后不可恢复。`,
      confirmText: "删除",
      confirmColor: "#A33D3D",
      success(result: { confirm?: boolean }) {
        resolve(Boolean(result.confirm));
      },
      fail() {
        resolve(false);
      },
    });
  });
}

definePage<ArticlesData>()({
  data: {
    loading: false,
    busy: false,
    error: "",
    deletingId: "",
    articles: [] as ArticleItem[],
  },

  async onShow() {
    await this.loadArticles();
  },

  async onPullDownRefresh() {
    await this.loadArticles();
    wx.stopPullDownRefresh();
  },

  async loadArticles() {
    this.setData({ loading: true, error: "" });
    try {
      this.setData({ articles: articleItems(await listArticles()) });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "文章加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  async createArticle() {
    if (this.data.busy) {
      return;
    }
    this.setData({ busy: true, error: "" });
    try {
      const article = await createArticle();
      wx.navigateTo({ url: `/pages/editor/index?projectId=${article.id}` });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "新建文章失败" });
    } finally {
      this.setData({ busy: false });
    }
  },

  openArticle(event: DatasetEvent) {
    const id = event.currentTarget.dataset.id;
    if (id && !this.data.busy) {
      wx.navigateTo({ url: `/pages/editor/index?projectId=${id}` });
    }
  },

  async deleteArticle(event: DatasetEvent) {
    const id = event.currentTarget.dataset.id;
    const article = this.data.articles.find((item) => item.id === id);
    if (!article || this.data.busy || !(await confirmDelete(article.title))) {
      return;
    }
    this.setData({ busy: true, deletingId: article.id, error: "" });
    try {
      await deleteArticle(article.id);
      this.setData({ articles: this.data.articles.filter((item) => item.id !== article.id) });
      wx.showToast({ title: "已删除", icon: "success" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "删除失败" });
    } finally {
      this.setData({ busy: false, deletingId: "" });
    }
  },
});
