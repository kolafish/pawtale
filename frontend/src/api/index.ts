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
  OutlineEnhanceResponse,
  OutlineTemplatesResponse,
  RegenerateChoicesRequest,
  RegenerateChoicesResponse,
  PolishChaptersRequest,
  PolishChaptersResponse,
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
  autoComplete: (data: { name?: string; gender?: string; inspiration?: string }) =>
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
  update: (id: string, data: { title?: string; mood?: string; writing_style?: string; chapter_length?: string }) =>
    api.put<Story>(`/stories/${id}`, data).then(res => res.data),
  toggleFavorite: (id: string) => api.put(`/stories/${id}/favorite`),
  delete: (id: string) => api.delete(`/stories/${id}`),
  // 节点操作
  updateNode: (storyId: string, nodeId: string, data: StoryNodeUpdateRequest) =>
    api.put<StoryGenerateResponse>(`/stories/${storyId}/nodes/${nodeId}`, data).then(res => res.data),
  deleteNode: (storyId: string, nodeId: string) =>
    api.delete(`/stories/${storyId}/nodes/${nodeId}`).then(res => res.data),
  regenerateNode: (storyId: string, nodeId: string) =>
    api.post<StoryGenerateResponse>(`/stories/${storyId}/nodes/${nodeId}/regenerate`).then(res => res.data),
  reviseNode: (storyId: string, nodeId: string, instruction: string) =>
    api.post<StoryGenerateResponse>(`/stories/${storyId}/nodes/${nodeId}/revise`, { instruction }).then(res => res.data),
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
  aiEnhance: (outlineId: string) =>
    api.post<OutlineEnhanceResponse>(`/outlines/${outlineId}/ai-enhance`, {}).then(res => res.data),
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
