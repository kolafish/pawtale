import type {
  ArticleAIContinueResponse,
  ArticleAIReviewResponse,
  ArticleAIReviseResponse,
  ArticleDocument,
  ArticleListItem,
  ArticleProject,
} from "../domain/article";
import { requestWithBackendSession } from "./auth";

export async function listArticles(): Promise<ArticleListItem[]> {
  const articles = await requestWithBackendSession<ArticleListItem[]>(
    "/api/projects?type=article",
  );
  return articles.filter((article) => article.can_edit);
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

export function continueArticle(
  projectId: string,
  documentId: string,
  expectedContentRevision: number,
  instruction: string,
): Promise<ArticleAIContinueResponse> {
  return requestWithBackendSession<ArticleAIContinueResponse>(
    `/api/projects/${projectId}/documents/${documentId}/ai-continue`,
    {
      method: "POST",
      data: {
        ...(instruction ? { instruction } : {}),
        expected_content_revision: expectedContentRevision,
      },
      timeoutProfile: "generation",
    },
  );
}

export function reviseArticle(
  projectId: string,
  documentId: string,
  expectedContentRevision: number,
  instruction: string,
): Promise<ArticleAIReviseResponse> {
  return requestWithBackendSession<ArticleAIReviseResponse>(
    `/api/projects/${projectId}/documents/${documentId}/ai-revise`,
    {
      method: "POST",
      data: {
        instruction,
        expected_content_revision: expectedContentRevision,
      },
      timeoutProfile: "generation",
    },
  );
}

export function reviewArticle(
  projectId: string,
  documentId: string,
  expectedContentRevision: number,
): Promise<ArticleAIReviewResponse> {
  return requestWithBackendSession<ArticleAIReviewResponse>(
    `/api/projects/${projectId}/documents/${documentId}/ai-review`,
    {
      method: "POST",
      data: { expected_content_revision: expectedContentRevision },
      timeoutProfile: "generation",
    },
  );
}
