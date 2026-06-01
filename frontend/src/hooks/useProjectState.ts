import { useCallback, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { projectsApi } from '../api';
import { useAppStore } from '../stores/appStore';
import type { AIDebugInfo } from '../types';
import { useDocumentDraft } from './project/useDocumentDraft';

export type { SaveStatus } from './project/useDocumentDraft';

export function useProjectState() {
  const queryClient = useQueryClient();
  const {
    currentProject, setCurrentProject,
    currentDocumentId, setCurrentDocumentId,
    closeProject: clearCurrentProject,
    defaultAiModel,
  } = useAppStore();

  // AI state
  const [aiModel, setAiModel] = useState(defaultAiModel);
  const [continueInstruction, setContinueInstruction] = useState('');
  const [debugMode, setDebugMode] = useState(false);
  const [debugInfo, setDebugInfo] = useState<AIDebugInfo | null>(null);

  const refreshProject = useCallback(async () => {
    if (!currentProject) return;
    const updated = await projectsApi.get(currentProject.id);
    setCurrentProject(updated);
  }, [currentProject, setCurrentProject]);

  const draft = useDocumentDraft({
    currentProject,
    currentDocumentId,
    setCurrentDocumentId,
    refreshProject,
  });

  // ─── AI continue mutation ───
  const continueMutation = useMutation({
    mutationFn: async ({ projectId, docId, payload }: {
      projectId: string;
      docId: string;
      payload: { instruction?: string; ai_model?: string; debug?: boolean };
    }) => projectsApi.aiContinue(projectId, docId, payload),
    onSuccess: (result) => {
      draft.replaceContentAsSaved(result.content);
      setDebugInfo(result.debug_info || null);
      refreshProject();
    },
  });
  const totalWordCount = currentProject?.total_word_count ?? 0;

  const handleContinue = async () => {
    if (!currentProject || !currentDocumentId) return;
    await continueMutation.mutateAsync({
      projectId: currentProject.id,
      docId: currentDocumentId,
      payload: {
        instruction: continueInstruction.trim() || undefined,
        ai_model: aiModel,
        debug: debugMode,
      },
    });
    setContinueInstruction('');
  };

  const handleExportTxt = async () => {
    if (!currentProject) return;
    const exportData = await projectsApi.export(currentProject.id);
    const text = exportData.sections
      .map((s) => {
        const docs = s.documents.map((d) => `${d.name}\n\n${d.content}`).join('\n\n---\n\n');
        return `【${s.group}】\n\n${docs}`;
      })
      .join('\n\n========\n\n');
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${currentProject.title}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const closeProject = () => {
    clearCurrentProject();
    queryClient.invalidateQueries({ queryKey: ['projects'] });
  };

  // ─── Binder operations ───
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
    await draft.loadDocument(doc.id);
  };

  const renameDocument = async (docId: string, name: string) => {
    if (!currentProject) return;
    await projectsApi.updateDocument(currentProject.id, docId, { name });
    await refreshProject();
    if (docId === currentDocumentId) {
      draft.markCurrentDocumentRenamed(name);
    }
  };

  const deleteDocument = async (docId: string) => {
    if (!currentProject) return;
    await projectsApi.deleteDocument(currentProject.id, docId);
    if (docId === currentDocumentId) {
      // Switch to another document
      const allDocs = currentProject.groups.flatMap((g) => g.documents);
      const remaining = allDocs.filter((d) => d.id !== docId);
      if (remaining.length > 0) {
        await draft.loadDocument(remaining[0].id);
      } else {
        draft.resetDraft();
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

  // ─── Reference operations (for Binder) ───
  const { setCurrentReferenceId } = useAppStore();

  const createReference = async (type: string) => {
    if (!currentProject) return;
    const nameMap: Record<string, string> = {
      character: '新角色', location: '新地点', note: '新笔记', outline: '新大纲',
    };
    const ref = await projectsApi.createReference(currentProject.id, { type, name: nameMap[type] || '未命名' });
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
    await draft.saveCurrentIfDirty();
    setCurrentReferenceId(refId);
  };

  return {
    // Project
    currentProject,
    closeProject,
    refreshProject,

    // Document
    currentDocumentId,
    documentName: draft.documentName,
    setDocumentName: draft.setDocumentName,
    documentContent: draft.documentContent,
    setDocumentContent: draft.setDocumentContent,
    synopsis: draft.synopsis,
    setSynopsis: draft.setSynopsis,
    status: draft.status,
    setStatus: draft.setStatus,
    label: draft.label,
    setLabel: draft.setLabel,
    switchDocument: draft.switchDocument,
    loadDocument: draft.loadDocument,

    // Save
    saveStatus: draft.saveStatus,
    isSaving: draft.isSaving,
    handleSave: draft.handleSave,

    // AI
    aiModel,
    setAiModel,
    continueInstruction,
    setContinueInstruction,
    debugMode,
    setDebugMode,
    debugInfo,
    isContinuing: continueMutation.isPending,
    handleContinue,

    // Computed
    wordCount: draft.wordCount,
    totalWordCount,

    // Export
    handleExportTxt,

    // Review mode (for AI revision)
    setIsReviewing: draft.setIsReviewing,

    // Binder operations
    createGroup,
    renameGroup,
    deleteGroup,
    createDocument,
    renameDocument,
    deleteDocument,
    reorderDocuments,
    reorderGroups,

    // Reference operations
    createReference,
    renameReference,
    deleteReference,
    selectReference,
  };
}
