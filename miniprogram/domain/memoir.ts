export type ProjectType = "novel" | "article" | "memoir";

export interface User {
  id: string;
  email: string;
  password_login_enabled: boolean;
  display_name: string;
  avatar_url?: string | null;
  storage_owner_id?: string;
  created_at: string;
  updated_at?: string;
}

export interface Project {
  id: string;
  user_id?: string | null;
  type: ProjectType;
  title: string;
  description?: string | null;
  total_word_count: number;
  status?: "active" | "archived";
  elder_name?: string | null;
  metadata_json: Record<string, unknown>;
  is_default?: boolean;
  last_opened_at?: string | null;
  groups: Array<{
    id: string;
    name: string;
    sort_order: number;
    documents: Array<{
      id: string;
      group_id: string;
      name: string;
      status: string;
      word_count: number;
      sort_order: number;
      updated_at: string;
    }>;
  }>;
  references: Array<{
    id: string;
    type: string;
    name: string;
    sort_order: number;
  }>;
  created_at: string;
  updated_at: string;
}

export interface MemoirIdentity extends User {
  active_project_id: string;
  active_project: Project | null;
  projects: Project[];
}

export interface MemoirProjectListResponse {
  active_project_id: string;
  active_project: Project | null;
  projects: Project[];
}
