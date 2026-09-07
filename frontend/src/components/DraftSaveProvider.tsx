import { useEffect, useState, type ReactNode } from 'react';
import { createDraftSaves, DraftSavesContext } from '../hooks/project/draftSaves';

export function DraftSaveProvider({ children }: { children: ReactNode }) {
  const [drafts] = useState(createDraftSaves);
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!drafts.isDirty()) return;
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [drafts]);
  return <DraftSavesContext.Provider value={drafts}>{children}</DraftSavesContext.Provider>;
}
