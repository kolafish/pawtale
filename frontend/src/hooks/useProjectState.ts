import { useCallback, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { projectsApi } from '../api';
import { useAppStore } from '../stores/appStore';
import type { AIDebugInfo, ProjectExportDownloadFormat } from '../types';
import { saveBlob } from '../utils/download';
import { useDocumentDraft } from './project/useDocumentDraft';
import { useProjectReferences } from './project/useProjectReferences';
import { useProjectStructure } from './project/useProjectStructure';

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
  const [exportingFormat, setExportingFormat] = useState<ProjectExportDownloadFormat | null>(null);

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

  const structure = useProjectStructure({
    currentProject,
    currentDocumentId,
    refreshProject,
    loadDocument: draft.loadDocument,
    markCurrentDocumentRenamed: draft.markCurrentDocumentRenamed,
    resetDraft: draft.resetDraft,
  });

  const references = useProjectReferences({
    currentProject,
    refreshProject,
    saveCurrentIfDirty: draft.saveCurrentIfDirty,
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

  const handleExport = async (format: ProjectExportDownloadFormat) => {
    if (!currentProject || exportingFormat) return;
    setExportingFormat(format);
    try {
      const download = await projectsApi.downloadExport(currentProject.id, format);
      const extension = format === 'markdown' ? 'md' : format;
      saveBlob(download.blob, download.filename || `${currentProject.title}.${extension}`);
    } finally {
      setExportingFormat(null);
    }
  };

  const closeProject = () => {
    clearCurrentProject();
    queryClient.invalidateQueries({ queryKey: ['projects'] });
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
    exportingFormat,
    handleExport,

    // Review mode (for AI revision)
    setIsReviewing: draft.setIsReviewing,

    // Binder operations
    createGroup: structure.createGroup,
    renameGroup: structure.renameGroup,
    deleteGroup: structure.deleteGroup,
    createDocument: structure.createDocument,
    renameDocument: structure.renameDocument,
    deleteDocument: structure.deleteDocument,
    reorderDocuments: structure.reorderDocuments,
    reorderGroups: structure.reorderGroups,

    // Reference operations
    createReference: references.createReference,
    renameReference: references.renameReference,
    deleteReference: references.deleteReference,
    selectReference: references.selectReference,
  };
}
