import { create } from 'zustand';
import type { Character, Scene, Story, StoryGenerateResponse, StoryOutline } from '../types';

interface AppState {
  // 当前选中
  selectedCharacters: Character[];
  selectedScene: Scene | null;
  currentStory: Story | null;
  currentNode: StoryGenerateResponse | null;

  // 大纲相关
  currentOutline: StoryOutline | null;

  // 故事中的场景和角色
  currentSceneInStory: Scene | null;
  presentCharacters: Character[];

  // 视图模式
  viewMode: 'dual' | 'edit' | 'preview';
  editTab: 'characters' | 'scenes' | 'stories' | 'outlines';

  // 加载状态
  isGenerating: boolean;
  defaultAiModel: 'deepseek' | 'claude-opus-4' | 'claude-sonnet-4';

  // Actions
  setSelectedCharacters: (characters: Character[]) => void;
  addSelectedCharacter: (character: Character) => void;
  removeSelectedCharacter: (characterId: string) => void;
  setSelectedScene: (scene: Scene | null) => void;
  setCurrentStory: (story: Story | null) => void;
  setCurrentNode: (node: StoryGenerateResponse | null) => void;
  setCurrentOutline: (outline: StoryOutline | null) => void;
  setCurrentSceneInStory: (scene: Scene | null) => void;
  setPresentCharacters: (characters: Character[]) => void;
  addPresentCharacter: (character: Character) => void;
  removePresentCharacter: (characterId: string) => void;
  setViewMode: (mode: 'dual' | 'edit' | 'preview') => void;
  setEditTab: (tab: 'characters' | 'scenes' | 'stories' | 'outlines') => void;
  setIsGenerating: (isGenerating: boolean) => void;
  setDefaultAiModel: (model: 'deepseek' | 'claude-opus-4' | 'claude-sonnet-4') => void;
  reset: () => void;
}

export const useAppStore = create<AppState>((set) => ({
  selectedCharacters: [],
  selectedScene: null,
  currentStory: null,
  currentNode: null,
  currentOutline: null,
  currentSceneInStory: null,
  presentCharacters: [],
  viewMode: 'dual',
  editTab: 'characters',
  isGenerating: false,
  defaultAiModel: 'deepseek',

  setSelectedCharacters: (characters) => set({ selectedCharacters: characters }),
  addSelectedCharacter: (character) => set((state) => ({
    selectedCharacters: state.selectedCharacters.some(c => c.id === character.id)
      ? state.selectedCharacters
      : [...state.selectedCharacters, character]
  })),
  removeSelectedCharacter: (characterId) => set((state) => ({
    selectedCharacters: state.selectedCharacters.filter(c => c.id !== characterId)
  })),
  setSelectedScene: (scene) => set({ selectedScene: scene }),
  setCurrentStory: (story) => set({ currentStory: story }),
  setCurrentNode: (node) => set({ currentNode: node }),
  setCurrentOutline: (outline) => set({ currentOutline: outline }),
  setCurrentSceneInStory: (scene) => set({ currentSceneInStory: scene }),
  setPresentCharacters: (characters) => set({ presentCharacters: characters }),
  addPresentCharacter: (character) => set((state) => ({
    presentCharacters: state.presentCharacters.some(c => c.id === character.id)
      ? state.presentCharacters
      : [...state.presentCharacters, character]
  })),
  removePresentCharacter: (characterId) => set((state) => ({
    presentCharacters: state.presentCharacters.filter(c => c.id !== characterId)
  })),
  setViewMode: (mode) => set({ viewMode: mode }),
  setEditTab: (tab) => set({ editTab: tab }),
  setIsGenerating: (isGenerating) => set({ isGenerating }),
  setDefaultAiModel: (model) => set({ defaultAiModel: model }),
  reset: () => set({
    selectedCharacters: [],
    selectedScene: null,
    currentStory: null,
    currentNode: null,
    currentOutline: null,
    currentSceneInStory: null,
    presentCharacters: [],
  }),
}));
