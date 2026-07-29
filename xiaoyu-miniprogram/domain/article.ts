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
