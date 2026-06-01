import { projectsApi } from '../../api';
import type { Project } from '../../types';

interface UseProjectStructureArgs {
  currentProject: Project | null;
  currentDocumentId: string | null;
  refreshProject: () => Promise<void>;
  loadDocument: (docId: string) => Promise<void>;
  markCurrentDocumentRenamed: (name: string) => void;
  resetDraft: () => void;
}

export function useProjectStructure({
  currentProject,
  currentDocumentId,
  refreshProject,
  loadDocument,
  markCurrentDocumentRenamed,
  resetDraft,
}: UseProjectStructureArgs) {
  const createGroup = async (name: string) => {
    if (!currentProject) return;
    await projectsApi.createGroup(currentProject.id, name);
    await refreshProject();
  };

  const renameGroup = async (groupId: string, name: string) => {
    if (!currentProject) return;
    await projectsApi.updateGroup(currentProject.id, groupId, name);
    await refreshProject();
  };

  const deleteGroup = async (groupId: string) => {
    if (!currentProject) return;
    await projectsApi.deleteGroup(currentProject.id, groupId);
    await refreshProject();
  };

  const createDocument = async (groupId: string, name?: string) => {
    if (!currentProject) return;
    const doc = await projectsApi.createDocument(currentProject.id, groupId, name);
    await refreshProject();
    await loadDocument(doc.id);
  };

  const renameDocument = async (docId: string, name: string) => {
    if (!currentProject) return;
    await projectsApi.updateDocument(currentProject.id, docId, { name });
    await refreshProject();
    if (docId === currentDocumentId) {
      markCurrentDocumentRenamed(name);
    }
  };

  const deleteDocument = async (docId: string) => {
    if (!currentProject) return;
    await projectsApi.deleteDocument(currentProject.id, docId);
    if (docId === currentDocumentId) {
      const remaining = currentProject.groups
        .flatMap((group) => group.documents)
        .filter((doc) => doc.id !== docId);
      if (remaining.length > 0) {
        await loadDocument(remaining[0].id);
      } else {
        resetDraft();
      }
    }
    await refreshProject();
  };

  const reorderDocuments = async (items: { id: string; group_id: string; sort_order: number }[]) => {
    if (!currentProject) return;
    await projectsApi.reorderDocuments(currentProject.id, items);
    await refreshProject();
  };

  const reorderGroups = async (items: { id: string; sort_order: number }[]) => {
    if (!currentProject) return;
    await projectsApi.reorderGroups(currentProject.id, items);
    await refreshProject();
  };

  return {
    createGroup,
    renameGroup,
    deleteGroup,
    createDocument,
    renameDocument,
    deleteDocument,
    reorderDocuments,
    reorderGroups,
  };
}
