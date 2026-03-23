import axios from 'axios';
import type {
  Character,
  CharacterAutoComplete,
  Scene,
  Story,
  StoryGenerateRequest,
  StoryBranchRequest,
  StoryGenerateResponse,
  StoryNodeUpdateRequest,
  StoryExportResponse,
  SceneAIGenerateRequest,
  SceneAIGenerateResponse,
  StoryOutline,
  StoryOutlineCreate,
  StoryOutlineUpdate,
  StoryOutlineGenerateRequest,
  OutlineCharacter,
  OutlineCharacterCreate,
  OutlineCharacterUpdate,
  OutlineScene,
  OutlineSceneCreate,
  OutlineSceneUpdate,
  StorySwitchSceneRequest,
  StoryUpdateCharactersRequest,
  OutlineEnhanceRequest,
  OutlineEnhanceResponse,
  OutlineTemplatesResponse,
  RegenerateChoicesRequest,
  RegenerateChoicesResponse,
  PolishChaptersRequest,
  PolishChaptersResponse,
  StoryWorkspace,
  StoryWorkspaceCreateRequest,
  StoryWorkspaceUpdateRequest,
  StoryWorkspaceAIContinueRequest,
  StoryWorkspaceAIContinueResponse,
  StoryWorkspaceExtractRequest,
  StoryWorkspaceExtractResponse,
  StoryWorkspaceExtractionUpdateRequest,
  Project,
  ProjectListItem,
  WritingDocument,
  ReferenceItem,
  WritingSession,
  ProjectExportResponse,
  DocumentGroup,
  MultiReviseResponse,
} from '../types';

const api = axios.create({
  baseURL: '/api',
});

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

// Stories API
export const storiesApi = {
  getAll: () => api.get<Story[]>('/stories').then(res => res.data),
  getById: (id: string) => api.get<Story>(`/stories/${id}`).then(res => res.data),
  generate: (data: StoryGenerateRequest) =>
    api.post<StoryGenerateResponse>('/stories/generate', data).then(res => res.data),
  chooseBranch: (storyId: string, request: StoryBranchRequest) =>
    api.post<StoryGenerateResponse>(`/stories/${storyId}/choose`, request).then(res => res.data),
  gotoNode: (storyId: string, nodeId: string) =>
    api.post<StoryGenerateResponse>(`/stories/${storyId}/goto/${nodeId}`).then(res => res.data),
  update: (
    id: string,
    data: {
      title?: string;
      mood?: string;
      writing_style?: string;
      chapter_length?: string;
      ai_model?: string;
      scene_id?: string | null;
      outline_id?: string | null;
    }
  ) =>
    api.put<Story>(`/stories/${id}`, data).then(res => res.data),
  toggleFavorite: (id: string) => api.put(`/stories/${id}/favorite`),
  delete: (id: string) => api.delete(`/stories/${id}`),
  // 节点操作
  updateNode: (storyId: string, nodeId: string, data: StoryNodeUpdateRequest) =>
    api.put<StoryGenerateResponse>(`/stories/${storyId}/nodes/${nodeId}`, data).then(res => res.data),
  deleteNode: (storyId: string, nodeId: string) =>
    api.delete(`/stories/${storyId}/nodes/${nodeId}`).then(res => res.data),
  regenerateNode: (storyId: string, nodeId: string, request?: { ai_model?: string; debug?: boolean }) =>
    api.post<StoryGenerateResponse>(`/stories/${storyId}/nodes/${nodeId}/regenerate`, request).then(res => res.data),
  reviseNode: (storyId: string, nodeId: string, instruction: string, aiModel?: string, debug?: boolean) =>
    api.post<StoryGenerateResponse>(
      `/stories/${storyId}/nodes/${nodeId}/revise`,
      { instruction, ai_model: aiModel, debug }
    ).then(res => res.data),
  // 重新生成分支选项
  regenerateChoices: (storyId: string, nodeId: string, request: RegenerateChoicesRequest) =>
    api.post<RegenerateChoicesResponse>(`/stories/${storyId}/nodes/${nodeId}/regenerate-choices`, request).then(res => res.data),
  // 润色章节
  polishChapters: (storyId: string, request: PolishChaptersRequest) =>
    api.post<PolishChaptersResponse>(`/stories/${storyId}/polish`, request).then(res => res.data),
  applyPolish: (storyId: string, request: PolishChaptersResponse) =>
    api.post(`/stories/${storyId}/apply-polish`, request).then(res => res.data),
  // 导出
  export: (storyId: string) =>
    api.get<StoryExportResponse>(`/stories/${storyId}/export`).then(res => res.data),
  // 场景切换
  switchScene: (storyId: string, request: StorySwitchSceneRequest) =>
    api.post<StoryGenerateResponse>(`/stories/${storyId}/switch-scene`, request).then(res => res.data),
  // 角色管理
  updateCharacters: (storyId: string, request: StoryUpdateCharactersRequest) =>
    api.post<Story>(`/stories/${storyId}/update-characters`, request).then(res => res.data),
  // 编辑器优先工作区
  createWorkspace: (request: StoryWorkspaceCreateRequest) =>
    api.post<StoryWorkspace>('/stories/workspace', request).then(res => res.data),
  getWorkspace: (storyId: string) =>
    api.get<StoryWorkspace>(`/stories/${storyId}/workspace`).then(res => res.data),
  updateWorkspace: (storyId: string, request: StoryWorkspaceUpdateRequest) =>
    api.put<StoryWorkspace>(`/stories/${storyId}/workspace`, request).then(res => res.data),
  continueWorkspace: (storyId: string, request: StoryWorkspaceAIContinueRequest) =>
    api.post<StoryWorkspaceAIContinueResponse>(`/stories/${storyId}/workspace/ai-continue`, request).then(res => res.data),
  extractWorkspace: (storyId: string, request: StoryWorkspaceExtractRequest) =>
    api.post<StoryWorkspaceExtractResponse>(`/stories/${storyId}/workspace/extract`, request).then(res => res.data),
  updateWorkspaceExtraction: (storyId: string, request: StoryWorkspaceExtractionUpdateRequest) =>
    api.put<StoryWorkspace>(`/stories/${storyId}/workspace/extracted`, request).then(res => res.data),
};

// Outlines API
export const outlinesApi = {
  getAll: () => api.get<StoryOutline[]>('/outlines').then(res => res.data),
  getById: (id: string) => api.get<StoryOutline>(`/outlines/${id}`).then(res => res.data),
  create: (data: StoryOutlineCreate) => api.post<StoryOutline>('/outlines', data).then(res => res.data),
  update: (id: string, data: StoryOutlineUpdate) => api.put<StoryOutline>(`/outlines/${id}`, data).then(res => res.data),
  delete: (id: string) => api.delete(`/outlines/${id}`),
  // 角色管理
  addCharacter: (outlineId: string, data: OutlineCharacterCreate) =>
    api.post<OutlineCharacter>(`/outlines/${outlineId}/characters`, data).then(res => res.data),
  updateCharacter: (outlineId: string, characterId: string, data: OutlineCharacterUpdate) =>
    api.put<OutlineCharacter>(`/outlines/${outlineId}/characters/${characterId}`, data).then(res => res.data),
  removeCharacter: (outlineId: string, characterId: string) =>
    api.delete(`/outlines/${outlineId}/characters/${characterId}`),
  // 场景管理
  addScene: (outlineId: string, data: OutlineSceneCreate) =>
    api.post<OutlineScene>(`/outlines/${outlineId}/scenes`, data).then(res => res.data),
  updateScene: (outlineId: string, sceneId: string, data: OutlineSceneUpdate) =>
    api.put<OutlineScene>(`/outlines/${outlineId}/scenes/${sceneId}`, data).then(res => res.data),
  removeScene: (outlineId: string, sceneId: string) =>
    api.delete(`/outlines/${outlineId}/scenes/${sceneId}`),
  // 生成故事
  generateStory: (outlineId: string, request: StoryOutlineGenerateRequest) =>
    api.post<StoryGenerateResponse>(`/outlines/${outlineId}/generate`, request).then(res => res.data),
  // AI 增强
  aiEnhance: (outlineId: string, request?: OutlineEnhanceRequest) =>
    api.post<OutlineEnhanceResponse>(`/outlines/${outlineId}/ai-enhance`, request || {}).then(res => res.data),
  // 模板
  getTemplates: () =>
    api.get<OutlineTemplatesResponse>('/outlines/templates').then(res => res.data),
  // 引导式创建
  guidedCreate: (data: {
    story_goal: string;
    character_tension: string;
    central_conflict: string;
    character_ids?: string[];
    mood?: string;
    ai_model?: string;
  }) =>
    api.post('/outlines/guided-create', data).then(res => res.data),
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
