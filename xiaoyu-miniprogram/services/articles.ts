import type {
  ArticleDocument,
  ArticleListItem,
  ArticleProject,
} from "../domain/article";
import { requestWithBackendSession } from "./auth";

export function listArticles(): Promise<ArticleListItem[]> {
  return requestWithBackendSession<ArticleListItem[]>("/api/projects?type=article");
}

export function createArticle(title = "未命名文章"): Promise<ArticleProject> {
  return requestWithBackendSession<ArticleProject>("/api/projects", {
    method: "POST",
    data: { type: "article", title },
    timeoutProfile: "mutation",
  });
}

export function getArticle(projectId: string): Promise<ArticleProject> {
  return requestWithBackendSession<ArticleProject>(`/api/projects/${projectId}`);
}

export function updateArticleTitle(
  projectId: string,
  title: string,
): Promise<ArticleProject> {
  return requestWithBackendSession<ArticleProject>(`/api/projects/${projectId}`, {
    method: "PUT",
    data: { title },
    timeoutProfile: "mutation",
  });
}

export function deleteArticle(projectId: string): Promise<unknown> {
  return requestWithBackendSession(`/api/projects/${projectId}`, {
    method: "DELETE",
    timeoutProfile: "mutation",
  });
}

export function getArticleDocument(
  projectId: string,
  documentId: string,
): Promise<ArticleDocument> {
  return requestWithBackendSession<ArticleDocument>(
    `/api/projects/${projectId}/documents/${documentId}`,
  );
}

export function updateArticleContent(
  projectId: string,
  documentId: string,
  content: string,
  expectedContentRevision: number,
): Promise<ArticleDocument> {
  return requestWithBackendSession<ArticleDocument>(
    `/api/projects/${projectId}/documents/${documentId}`,
    {
      method: "PUT",
      data: {
        content,
        expected_content_revision: expectedContentRevision,
      },
      timeoutProfile: "mutation",
    },
  );
}
