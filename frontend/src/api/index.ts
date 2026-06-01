import axios from 'axios';
import type {
  User,
  Character,
  CharacterAutoComplete,
  Scene,
  SceneAIGenerateRequest,
  SceneAIGenerateResponse,
  Project,
  ProjectListItem,
  WritingDocument,
  ReferenceItem,
  WritingSession,
  ProjectExportResponse,
  DocumentGroup,
  MultiReviseResponse,
  ProjectAnalyzeResponse,
  AnalyzeApplyRequest,
  AnalyzeApplyResponse,
  ExportGlobalRequest,
  ExportGlobalResponse,
  ImportPreviewResponse,
} from '../types';

const api = axios.create({
  baseURL: '/api',
  withCredentials: true,
});

// Auth API
export const authApi = {
  me: () => api.get<User>('/auth/me').then(res => res.data),
  login: (data: { email: string; password: string }) =>
    api.post<{ user: User }>('/auth/login', data).then(res => res.data),
  register: (data: { email: string; password: string; display_name?: string }) =>
    api.post<{ user: User }>('/auth/register', data).then(res => res.data),
  logout: () => api.post('/auth/logout'),
};

// Characters API
export const charactersApi = {
  getAll: () => api.get<Character[]>('/characters').then(res => res.data),
  getById: (id: string) => api.get<Character>(`/characters/${id}`).then(res => res.data),
  create: (data: Partial<Character>) => api.post<Character>('/characters', data).then(res => res.data),
  update: (id: string, data: Partial<Character>) => api.put<Character>(`/characters/${id}`, data).then(res => res.data),
  delete: (id: string) => api.delete(`/characters/${id}`),
  generateAvatar: (id: string, style: string) =>
    api.post<{ avatar_url: string; base_image_url: string }>(`/characters/${id}/avatar`, { style }).then(res => res.data),
  autoComplete: (data: { name?: string; gender?: string; inspiration?: string; ai_model?: string }) =>
    api.post<CharacterAutoComplete>('/characters/auto-complete', data).then(res => res.data),
};

// Scenes API
export const scenesApi = {
  getAll: () => api.get<Scene[]>('/scenes').then(res => res.data),
  getById: (id: string) => api.get<Scene>(`/scenes/${id}`).then(res => res.data),
  create: (data: Partial<Scene>) => api.post<Scene>('/scenes', data).then(res => res.data),
  update: (id: string, data: Partial<Scene>) => api.put<Scene>(`/scenes/${id}`, data).then(res => res.data),
  delete: (id: string) => api.delete(`/scenes/${id}`),
  initPresets: () => api.post('/scenes/init-presets'),
  // AI 生成场景
  aiGenerate: (data: SceneAIGenerateRequest) =>
    api.post<SceneAIGenerateResponse>('/scenes/ai-generate', data).then(res => res.data),
};

// Projects API
export const projectsApi = {
  list: () => api.get<ProjectListItem[]>('/projects').then(r => r.data),
  create: (data: { title: string; description?: string; word_count_target?: number; daily_word_goal?: number }) =>
    api.post<Project>('/projects', data).then(r => r.data),
  get: (id: string) => api.get<Project>(`/projects/${id}`).then(r => r.data),
  update: (id: string, data: { title?: string; description?: string; word_count_target?: number; daily_word_goal?: number }) =>
    api.put<Project>(`/projects/${id}`, data).then(r => r.data),
  delete: (id: string) => api.delete(`/projects/${id}`),

  // Groups
  createGroup: (projectId: string, name: string) =>
    api.post<DocumentGroup>(`/projects/${projectId}/groups`, { name }).then(r => r.data),
  updateGroup: (projectId: string, groupId: string, name: string) =>
    api.put<DocumentGroup>(`/projects/${projectId}/groups/${groupId}`, { name }).then(r => r.data),
  deleteGroup: (projectId: string, groupId: string) =>
    api.delete(`/projects/${projectId}/groups/${groupId}`),
  reorderGroups: (projectId: string, items: { id: string; sort_order: number }[]) =>
    api.put(`/projects/${projectId}/groups/reorder`, items),

  // Documents
  createDocument: (projectId: string, groupId: string, name?: string) =>
    api.post<WritingDocument>(`/projects/${projectId}/groups/${groupId}/documents`, { name: name || '未命名文档' }).then(r => r.data),
  getDocument: (projectId: string, docId: string) =>
    api.get<WritingDocument>(`/projects/${projectId}/documents/${docId}`).then(r => r.data),
  updateDocument: (projectId: string, docId: string, data: { name?: string; content?: string; synopsis?: string; status?: string; label?: string; word_count_target?: number }) =>
    api.put<WritingDocument>(`/projects/${projectId}/documents/${docId}`, data).then(r => r.data),
  deleteDocument: (projectId: string, docId: string) =>
    api.delete(`/projects/${projectId}/documents/${docId}`),
  reorderDocuments: (projectId: string, items: { id: string; group_id: string; sort_order: number }[]) =>
    api.put(`/projects/${projectId}/documents/reorder`, items),

  // References
  listReferences: (projectId: string) =>
    api.get<ReferenceItem[]>(`/projects/${projectId}/references`).then(r => r.data),
  createReference: (projectId: string, data: { type: string; name: string; content?: string; metadata_json?: Record<string, any> }) =>
    api.post<ReferenceItem>(`/projects/${projectId}/references`, data).then(r => r.data),
  updateReference: (projectId: string, refId: string, data: { name?: string; content?: string; metadata_json?: Record<string, any> }) =>
    api.put<ReferenceItem>(`/projects/${projectId}/references/${refId}`, data).then(r => r.data),
  deleteReference: (projectId: string, refId: string) =>
    api.delete(`/projects/${projectId}/references/${refId}`),
  importGlobal: (projectId: string, data: { character_ids?: string[]; scene_ids?: string[] }) =>
    api.post<ReferenceItem[]>(`/projects/${projectId}/references/import-global`, data).then(r => r.data),
  syncGlobal: (projectId: string, refId: string) =>
    api.post<ReferenceItem>(`/projects/${projectId}/references/${refId}/sync-global`).then(r => r.data),
  exportGlobal: (projectId: string, refId: string, data: ExportGlobalRequest) =>
    api.post<ExportGlobalResponse>(`/projects/${projectId}/references/${refId}/export-global`, data).then(r => r.data),
  importPreview: (projectId: string, data: { character_ids?: string[]; scene_ids?: string[] }) =>
    api.post<ImportPreviewResponse>(`/projects/${projectId}/references/import-preview`, data).then(r => r.data),

  // Document-Reference links
  getDocumentReferences: (projectId: string, docId: string) =>
    api.get<ReferenceItem[]>(`/projects/${projectId}/documents/${docId}/references`).then(r => r.data),
  setDocumentReferences: (projectId: string, docId: string, referenceIds: string[]) =>
    api.put(`/projects/${projectId}/documents/${docId}/references`, { reference_ids: referenceIds }),

  // Writing sessions
  listSessions: (projectId: string) =>
    api.get<WritingSession[]>(`/projects/${projectId}/sessions`).then(r => r.data),
  createSession: (projectId: string, data: { date: string; words_written: number }) =>
    api.post<WritingSession>(`/projects/${projectId}/sessions`, data).then(r => r.data),

  // Export
  export: (projectId: string) =>
    api.get<ProjectExportResponse>(`/projects/${projectId}/export`).then(r => r.data),

  // AI Continue
  aiContinue: (projectId: string, docId: string, data: { instruction?: string; ai_model?: string; debug?: boolean }) =>
    api.post<{ document_id: string; content: string; generated_chunk: string; ai_model: string; debug_info?: any }>(
      `/projects/${projectId}/documents/${docId}/ai-continue`, data
    ).then(r => r.data),

  // AI Revise
  aiRevise: (projectId: string, docId: string, data: {
    instruction: string;
    selected_text?: string;
    full_content?: string;
    ai_model?: string;
    debug?: boolean;
  }) =>
    api.post<{
      document_id: string;
      revised_text: string;
      original_text: string;
      scope: string;
      ai_model: string;
      debug_info?: any;
    }>(`/projects/${projectId}/documents/${docId}/ai-revise`, data).then(r => r.data),

  // Project Analysis
  analyzeProject: (projectId: string, data: { ai_model?: string; debug?: boolean }) =>
    api.post<ProjectAnalyzeResponse>(`/projects/${projectId}/analyze`, data).then(r => r.data),
  applyAnalysis: (projectId: string, data: AnalyzeApplyRequest) =>
    api.post<AnalyzeApplyResponse>(`/projects/${projectId}/analyze/apply`, data).then(r => r.data),

  // AI Revise Multi-model
  aiReviseMulti: (projectId: string, docId: string, data: {
    instruction: string;
    selected_text?: string;
    full_content?: string;
    models: string[];
    debug?: boolean;
  }) =>
    api.post<MultiReviseResponse>(
      `/projects/${projectId}/documents/${docId}/ai-revise-multi`, data
    ).then(r => r.data),
};
