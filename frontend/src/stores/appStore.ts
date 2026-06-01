import { create } from 'zustand';
import type { Character, Scene, Project } from '../types';

interface AppState {
  selectedCharacters: Character[];
  selectedScene: Scene | null;
  defaultAiModel: 'deepseek' | 'claude-opus-4' | 'claude-sonnet-4';
  currentProject: Project | null;
  currentDocumentId: string | null;
  currentReferenceId: string | null;

  setSelectedCharacters: (characters: Character[]) => void;
  addSelectedCharacter: (character: Character) => void;
  removeSelectedCharacter: (characterId: string) => void;
  setSelectedScene: (scene: Scene | null) => void;
  setDefaultAiModel: (model: 'deepseek' | 'claude-opus-4' | 'claude-sonnet-4') => void;
  setCurrentProject: (project: Project | null) => void;
  setCurrentDocumentId: (docId: string | null) => void;
  setCurrentReferenceId: (refId: string | null) => void;
  reset: () => void;
}

export const useAppStore = create<AppState>((set) => ({
  selectedCharacters: [],
  selectedScene: null,
  defaultAiModel: 'claude-opus-4',
  currentProject: null,
  currentDocumentId: null,
  currentReferenceId: null,

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
  setDefaultAiModel: (model) => set({ defaultAiModel: model }),
  setCurrentProject: (project) => set({ currentProject: project }),
  setCurrentDocumentId: (docId) => set({ currentDocumentId: docId, currentReferenceId: null }),
  setCurrentReferenceId: (refId) => set({ currentReferenceId: refId, currentDocumentId: null }),
  reset: () => set({
    selectedCharacters: [],
    selectedScene: null,
    currentProject: null,
    currentDocumentId: null,
    currentReferenceId: null,
  }),
}));
