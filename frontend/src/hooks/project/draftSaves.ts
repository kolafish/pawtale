import { createContext, useContext, useEffect, useRef } from 'react';

interface DraftSave {
  save(): Promise<void>;
  isDirty(): boolean;
}

export function createDraftSaves() {
  const drafts = new Set<DraftSave>();
  return {
    register(draft: DraftSave) {
      drafts.add(draft);
      return () => { drafts.delete(draft); };
    },
    async flush() {
      for (const draft of drafts) await draft.save();
    },
    isDirty: () => [...drafts].some((draft) => draft.isDirty()),
  };
}

export const DraftSavesContext = createContext<ReturnType<typeof createDraftSaves> | null>(null);

export function useDraftSaves() {
  const drafts = useContext(DraftSavesContext);
  if (!drafts) throw new Error('DraftSaveProvider is required');
  return drafts;
}

export function useRegisterDraftSave(save: DraftSave['save'], isDirty: DraftSave['isDirty']) {
  const drafts = useDraftSaves();
  const latest = useRef({ save, isDirty });
  useEffect(() => { latest.current = { save, isDirty }; }, [save, isDirty]);
  useEffect(() => drafts.register({
    save: () => latest.current.save(),
    isDirty: () => latest.current.isDirty(),
  }), [drafts]);
}
