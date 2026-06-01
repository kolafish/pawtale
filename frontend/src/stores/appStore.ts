import { create } from 'zustand';
import type { Project } from '../types';

interface AppState {
  defaultAiModel: 'deepseek' | 'claude-opus-4' | 'claude-sonnet-4';
  currentProject: Project | null;
  currentDocumentId: string | null;
  currentReferenceId: string | null;

  setDefaultAiModel: (model: 'deepseek' | 'claude-opus-4' | 'claude-sonnet-4') => void;
  setCurrentProject: (project: Project | null) => void;
  setCurrentDocumentId: (docId: string | null) => void;
  setCurrentReferenceId: (refId: string | null) => void;
  closeProject: () => void;
  reset: () => void;
}

export const useAppStore = create<AppState>((set) => ({
  defaultAiModel: 'claude-opus-4',
  currentProject: null,
  currentDocumentId: null,
  currentReferenceId: null,

  setDefaultAiModel: (model) => set({ defaultAiModel: model }),
  setCurrentProject: (project) => set({ currentProject: project }),
  setCurrentDocumentId: (docId) => set({ currentDocumentId: docId, currentReferenceId: null }),
  setCurrentReferenceId: (refId) => set({ currentReferenceId: refId, currentDocumentId: null }),
  closeProject: () => set({
    currentProject: null,
    currentDocumentId: null,
    currentReferenceId: null,
  }),
  reset: () => set({
    currentProject: null,
    currentDocumentId: null,
    currentReferenceId: null,
  }),
}));
