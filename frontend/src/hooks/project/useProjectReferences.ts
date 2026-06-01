import { projectsApi } from '../../api';
import { DEFAULT_REFERENCE_NAMES, type ReferenceType } from '../../domain/references';
import { useAppStore } from '../../stores/appStore';
import type { Project } from '../../types';

interface UseProjectReferencesArgs {
  currentProject: Project | null;
  refreshProject: () => Promise<void>;
  saveCurrentIfDirty: () => Promise<void>;
}

export function useProjectReferences({
  currentProject,
  refreshProject,
  saveCurrentIfDirty,
}: UseProjectReferencesArgs) {
  const { setCurrentReferenceId } = useAppStore();

  const createReference = async (type: ReferenceType) => {
    if (!currentProject) return;
    await saveCurrentIfDirty();
    const ref = await projectsApi.createReference(currentProject.id, {
      type,
      name: DEFAULT_REFERENCE_NAMES[type],
    });
    await refreshProject();
    setCurrentReferenceId(ref.id);
  };

  const renameReference = async (refId: string, name: string) => {
    if (!currentProject) return;
    await projectsApi.updateReference(currentProject.id, refId, { name });
    await refreshProject();
  };

  const deleteReference = async (refId: string) => {
    if (!currentProject) return;
    await projectsApi.deleteReference(currentProject.id, refId);
    const { currentReferenceId: activeRefId } = useAppStore.getState();
    if (refId === activeRefId) {
      setCurrentReferenceId(null);
    }
    await refreshProject();
  };

  const selectReference = async (refId: string) => {
    const { currentReferenceId: activeRefId } = useAppStore.getState();
    if (refId === activeRefId) return;
    await saveCurrentIfDirty();
    setCurrentReferenceId(refId);
  };

  return {
    createReference,
    renameReference,
    deleteReference,
    selectReference,
  };
}
