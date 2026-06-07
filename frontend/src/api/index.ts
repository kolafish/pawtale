import axios from 'axios';
import type {
  User,
  AIDebugInfo,
  Project,
  ProjectType,
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
  ReferenceType,
  MemoirCoverageItem,
  MemoirEventMemory,
  MemoirInterviewState,
  MemoirMemoryStatus,
  MemoirMemoryType,
  MemoirOpenQuestion,
  MemoirOpenQuestionStatus,
  MemoirOutput,
  MemoirPersonMemory,
  MemoirPlaceMemory,
  MemoirQuestionCandidate,
  MemoirQuestionResponse,
  MemoirSessionSummary,
  MemoirThemeMemory,
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

// Projects API
export const projectsApi = {
  list: () => api.get<ProjectListItem[]>('/projects').then(r => r.data),
  create: (data: { type: ProjectType; title: string; description?: string; word_count_target?: number; daily_word_goal?: number }) =>
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
  createReference: (projectId: string, data: { type: ReferenceType; name: string; content?: string; metadata_json?: Record<string, unknown> }) =>
    api.post<ReferenceItem>(`/projects/${projectId}/references`, data).then(r => r.data),
  updateReference: (projectId: string, refId: string, data: { name?: string; content?: string; metadata_json?: Record<string, unknown> }) =>
    api.put<ReferenceItem>(`/projects/${projectId}/references/${refId}`, data).then(r => r.data),
  deleteReference: (projectId: string, refId: string) =>
    api.delete(`/projects/${projectId}/references/${refId}`),

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
    api.post<{ document_id: string; content: string; generated_chunk: string; ai_model: string; debug_info?: AIDebugInfo }>(
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
      debug_info?: AIDebugInfo;
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

const memoirProjectConfig = (projectId?: string) => (
  projectId ? { params: { project_id: projectId } } : undefined
);

export const memoirApi = {
  currentSession: (projectId?: string) =>
    api.get<MemoirInterviewState>('/mobile/memoir/sessions/current', memoirProjectConfig(projectId)).then(r => r.data),
  processSession: (sessionId: string, data: { ai_model?: string | null } = {}) =>
    api.post(`/mobile/memoir/sessions/${sessionId}/process`, data).then(r => r.data),
  getSessionSummary: (sessionId: string) =>
    api.get<MemoirSessionSummary | null>(`/mobile/memoir/sessions/${sessionId}/summary`).then(r => r.data),
  listPeople: (projectId?: string) =>
    api.get<MemoirPersonMemory[]>('/mobile/memoir/memories/people', memoirProjectConfig(projectId)).then(r => r.data),
  listEvents: (projectId?: string) =>
    api.get<MemoirEventMemory[]>('/mobile/memoir/memories/events', memoirProjectConfig(projectId)).then(r => r.data),
  listPlaces: (projectId?: string) =>
    api.get<MemoirPlaceMemory[]>('/mobile/memoir/memories/places', memoirProjectConfig(projectId)).then(r => r.data),
  listThemes: (projectId?: string) =>
    api.get<MemoirThemeMemory[]>('/mobile/memoir/memories/themes', memoirProjectConfig(projectId)).then(r => r.data),
  updateMemory: (
    memoryType: Exclude<MemoirMemoryType, 'open_question'>,
    memoryId: string,
    data: { status?: MemoirMemoryStatus; [key: string]: unknown },
    projectId?: string,
  ) => api.patch(`/mobile/memoir/memories/${memoryType}/${memoryId}`, data, memoirProjectConfig(projectId)).then(r => r.data),
  listOpenQuestions: (projectId?: string) =>
    api.get<MemoirOpenQuestion[]>('/mobile/memoir/open-questions', memoirProjectConfig(projectId)).then(r => r.data),
  updateOpenQuestion: (
    questionId: string,
    data: { status?: MemoirOpenQuestionStatus; reason?: string },
    projectId?: string,
  ) => api.patch<MemoirOpenQuestion>(`/mobile/memoir/open-questions/${questionId}`, data, memoirProjectConfig(projectId)).then(r => r.data),
  getCoverage: (projectId?: string) =>
    api.get<MemoirCoverageItem[]>('/mobile/memoir/coverage', memoirProjectConfig(projectId)).then(r => r.data),
  recalculateCoverage: (projectId?: string) =>
    api.post<MemoirCoverageItem[]>('/mobile/memoir/coverage/recalculate', undefined, memoirProjectConfig(projectId)).then(r => r.data),
  listQuestionCandidates: (projectId?: string) =>
    api.get<MemoirQuestionCandidate[]>('/mobile/memoir/question-candidates', memoirProjectConfig(projectId)).then(r => r.data),
  generateQuestionCandidates: (projectId?: string) =>
    api.post<MemoirQuestionCandidate[]>('/mobile/memoir/question-candidates/generate', undefined, memoirProjectConfig(projectId)).then(r => r.data),
  selectQuestionCandidate: (candidateId: string) =>
    api.post<MemoirQuestionResponse>(`/mobile/memoir/question-candidates/${candidateId}/select`).then(r => r.data),
  listOutputs: (projectId?: string) =>
    api.get<MemoirOutput[]>('/mobile/memoir/outputs', memoirProjectConfig(projectId)).then(r => r.data),
};
