import { create } from 'zustand';
import type { Project } from '../types';
import type { AiModelValue } from '../constants/aiModels';

interface AppState {
  defaultAiModel: AiModelValue;
  isAdmin: boolean;
  currentProject: Project | null;
  currentDocumentId: string | null;
  currentReferenceId: string | null;

  setDefaultAiModel: (model: AiModelValue) => void;
  setIsAdmin: (value: boolean) => void;
  setCurrentProject: (project: Project | null) => void;
  setCurrentDocumentId: (docId: string | null) => void;
  setCurrentReferenceId: (refId: string | null) => void;
  closeProject: () => void;
  reset: () => void;
}

export const useAppStore = create<AppState>((set) => ({
  defaultAiModel: 'deepseek-flash',
  isAdmin: false,
  currentProject: null,
  currentDocumentId: null,
  currentReferenceId: null,

  setDefaultAiModel: (model) => set({ defaultAiModel: model }),
  setIsAdmin: (value) => set({ isAdmin: value }),
  setCurrentProject: (project) => set({ currentProject: project }),
  setCurrentDocumentId: (docId) => set({ currentDocumentId: docId, currentReferenceId: null }),
  setCurrentReferenceId: (refId) => set({ currentReferenceId: refId, currentDocumentId: null }),
  closeProject: () => set({
    currentProject: null,
    currentDocumentId: null,
    currentReferenceId: null,
  }),
  reset: () => set({
    isAdmin: false,
    defaultAiModel: 'deepseek-flash',
    currentProject: null,
    currentDocumentId: null,
    currentReferenceId: null,
  }),
}));
