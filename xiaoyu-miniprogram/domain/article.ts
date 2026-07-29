export interface ArticleListItem {
  id: string;
  type: "article";
  title: string;
  can_edit: boolean;
  total_word_count: number;
  document_count: number;
  updated_at: string;
}

export interface ArticleDocumentMeta {
  id: string;
  group_id: string;
  name: string;
  word_count: number;
  content_revision: number;
  updated_at: string;
}

export interface ArticleGroup {
  id: string;
  name: string;
  documents: ArticleDocumentMeta[];
}

export interface ArticleProject {
  id: string;
  type: "article";
  title: string;
  total_word_count: number;
  groups: ArticleGroup[];
  updated_at: string;
}

export interface ArticleDocument {
  id: string;
  group_id: string;
  name: string;
  content: string;
  word_count: number;
  content_revision: number;
  updated_at: string;
}

export interface ArticleAIContinueResponse {
  content: string;
  content_revision: number;
  generated_chunk: string;
}

export interface ArticleAIReviseResponse {
  revised_text: string;
  original_text: string;
  content_revision: number;
}

export interface ArticleAIReviewSuggestion {
  excerpt: string;
  issue: string;
  suggestion: string;
}

export interface ArticleAIReviewResponse {
  summary: string;
  suggestions: ArticleAIReviewSuggestion[];
  content_revision: number;
}
