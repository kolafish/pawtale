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
  ProjectExportDownload,
  ProjectExportDownloadFormat,
  ProjectExportResponse,
  GitSyncStatus,
  GitHubSetupUrl,
  DocumentGroup,
  ProjectGitSyncConfig,
  ProjectGitSyncRunResponse,
  MultiReviseResponse,
  ProjectAnalyzeResponse,
  AnalyzeApplyRequest,
  AnalyzeApplyResponse,
  ReferenceType,
  AuditDashboardFilters,
  AuditDashboardResponse,
  AuditUserDayResponse,
  MemoirChapterSourceEditor,
  MemoirBatchTranscriptCleanupConfirmResponse,
  MemoirBatchTranscriptCleanupPreviewResponse,
  MemoirDialectGlossaryEntry,
  MemoirDialectGlossaryResponse,
  MemoirInterviewEditorSessionDetail,
  MemoirInterviewEditorSessionList,
  MemoirInterviewEditorSessionReview,
  MemoirInterviewType,
} from '../types';
import { parseContentDispositionFilename } from '../utils/download';

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

// User audit API
export const auditApi = {
  dashboard: (filters: AuditDashboardFilters) =>
    api.get<AuditDashboardResponse>('/audit/dashboard', { params: filters }).then(r => r.data),
  userDay: (userId: string, day: string) =>
    api.get<AuditUserDayResponse>(`/audit/users/${encodeURIComponent(userId)}/days/${day}`).then(r => r.data),
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
  downloadExport: async (projectId: string, format: ProjectExportDownloadFormat): Promise<ProjectExportDownload> => {
    const response = await api.get<Blob>(`/projects/${projectId}/export/download`, {
      params: { format },
      responseType: 'blob',
    });
    const header = response.headers['content-disposition'];
    const contentDisposition = Array.isArray(header) ? header.join(';') : header;
    return {
      blob: response.data,
      filename: parseContentDispositionFilename(contentDisposition) ?? undefined,
    };
  },

  // GitHub one-way sync
  createGitHubSetupUrl: () =>
    api.post<GitHubSetupUrl>('/github/setup-url').then(r => r.data),
  getGitSync: (projectId: string) =>
    api.get<GitSyncStatus>(`/projects/${projectId}/git-sync`).then(r => r.data),
  saveGitSync: (
    projectId: string,
    data: {
      installation_id: number;
      repository_owner: string;
      repository_name: string;
      branch: string;
      base_path: string;
    },
  ) => api.put<ProjectGitSyncConfig>(`/projects/${projectId}/git-sync`, data).then(r => r.data),
  deleteGitSync: (projectId: string) =>
    api.delete(`/projects/${projectId}/git-sync`),
  runGitSync: (projectId: string) =>
    api.post<ProjectGitSyncRunResponse>(`/projects/${projectId}/git-sync/run`).then(r => r.data),

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

// Memoir interview editor API
export const memoirInterviewEditorApi = {
  listSessions: (
    projectId: string,
    options: {
      interviewType?: MemoirInterviewType | null;
      effectiveOnly?: boolean;
      limit?: number;
      offset?: number;
    } = {},
  ) =>
    api.get<MemoirInterviewEditorSessionList>(
      `/memoir/projects/${projectId}/interview-editor/sessions`,
      {
        params: {
          ...(options.interviewType ? { interview_type: options.interviewType } : {}),
          ...(options.effectiveOnly !== undefined ? { effective_only: options.effectiveOnly } : {}),
          ...(options.limit !== undefined ? { limit: options.limit } : {}),
          ...(options.offset !== undefined ? { offset: options.offset } : {}),
        },
      },
    ).then(r => r.data),
  getSession: (projectId: string, sessionId: string) =>
    api.get<MemoirInterviewEditorSessionDetail>(
      `/memoir/projects/${projectId}/interview-editor/sessions/${sessionId}`,
    ).then(r => r.data),
  getSessionReview: (projectId: string, sessionId: string) =>
    api.get<MemoirInterviewEditorSessionReview>(
      `/memoir/projects/${projectId}/interview-editor/sessions/${sessionId}/review`,
    ).then(r => r.data),
  updateAnswerTranscript: (utteranceId: string, transcriptEdited: string) =>
    api.patch<unknown>(
      `/mobile/memoir/voice-transcripts/${utteranceId}`,
      { transcript_edited: transcriptEdited },
    ).then(r => r.data),
  createQuestion: (projectId: string, voiceSessionId: string, text: string) =>
    api.post<MemoirInterviewEditorSessionDetail>(
      `/memoir/projects/${projectId}/interview-editor/questions`,
      { voice_session_id: voiceSessionId, text },
    ).then(r => r.data),
  updateQuestionText: (projectId: string, messageId: string, text: string) =>
    api.patch<MemoirInterviewEditorSessionDetail>(
      `/memoir/projects/${projectId}/interview-editor/questions/${messageId}`,
      { text },
    ).then(r => r.data),
  convertQuestionToAnswer: (
    projectId: string,
    messageId: string,
    parentQuestionMessageId: string | null,
    reason?: string,
  ) =>
    api.post<MemoirInterviewEditorSessionDetail>(
      `/memoir/projects/${projectId}/interview-editor/questions/${messageId}/convert-to-answer`,
      { parent_question_message_id: parentQuestionMessageId, reason },
    ).then(r => r.data),
  updateAnswerParent: (
    projectId: string,
    messageId: string,
    parentQuestionMessageId: string | null,
    reason?: string,
  ) =>
    api.patch<MemoirInterviewEditorSessionDetail>(
      `/memoir/projects/${projectId}/interview-editor/answers/${messageId}/parent`,
      { parent_question_message_id: parentQuestionMessageId, reason },
    ).then(r => r.data),
  audioPlaybackUrl: (audioFileId: string) =>
    `/api/mobile/memoir/audio-files/${encodeURIComponent(audioFileId)}/playback`,
};

// Memoir chapter source editor API
export const memoirChapterSourceEditorApi = {
  get: (projectId: string) =>
    api.get<MemoirChapterSourceEditor>(
      `/memoir/projects/${projectId}/chapter-source-editor`,
    ).then(r => r.data),
  assign: (
    projectId: string,
    assignments: Array<{ chapter_id: string; utterance_ids: string[] }>,
    aiModel?: string,
  ) =>
    api.post<MemoirChapterSourceEditor>(
      `/memoir/projects/${projectId}/chapter-source-editor/assignments`,
      { assignments, ai_model: aiModel },
    ).then(r => r.data),
  move: (
    projectId: string,
    sourceChapterId: string,
    targetChapterId: string,
    utteranceId: string,
    aiModel?: string,
  ) =>
    api.post<MemoirChapterSourceEditor>(
      `/memoir/projects/${projectId}/chapter-source-editor/moves`,
      {
        source_chapter_id: sourceChapterId,
        target_chapter_id: targetChapterId,
        utterance_id: utteranceId,
        ai_model: aiModel,
      },
    ).then(r => r.data),
  remove: (
    projectId: string,
    chapterId: string,
    utteranceIds: string[],
    aiModel?: string,
  ) =>
    api.post<MemoirChapterSourceEditor>(
      `/memoir/projects/${projectId}/chapter-source-editor/removals`,
      { chapter_id: chapterId, utterance_ids: utteranceIds, ai_model: aiModel },
    ).then(r => r.data),
};

// Memoir transcript cleanup editor API
export const memoirTranscriptCleanupApi = {
  getGlossary: (projectId: string) =>
    api.get<MemoirDialectGlossaryResponse>(
      `/memoir/projects/${projectId}/transcript-cleanup/dialect-glossary`,
    ).then(r => r.data),
  updateGlossary: (projectId: string, entries: MemoirDialectGlossaryEntry[]) =>
    api.put<MemoirDialectGlossaryResponse>(
      `/memoir/projects/${projectId}/transcript-cleanup/dialect-glossary`,
      { entries },
    ).then(r => r.data),
  preview: (projectId: string, utteranceIds: string[], aiModel?: string) =>
    api.post<MemoirBatchTranscriptCleanupPreviewResponse>(
      `/memoir/projects/${projectId}/transcript-cleanup/preview`,
      { utterance_ids: utteranceIds, ai_model: aiModel },
    ).then(r => r.data),
  confirm: (
    projectId: string,
    items: Array<{
      utterance_id: string;
      cleaned_text: string;
      provider?: string | null;
      created_by: string;
    }>,
    aiModel?: string,
  ) =>
    api.post<MemoirBatchTranscriptCleanupConfirmResponse>(
      `/memoir/projects/${projectId}/transcript-cleanup/confirm`,
      { items, ai_model: aiModel },
    ).then(r => r.data),
};
