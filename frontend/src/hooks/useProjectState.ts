import { useCallback, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { projectsApi } from '../api';
import { useAppStore } from '../stores/appStore';
import type { AIDebugInfo, ProjectExportDownloadFormat } from '../types';
import { saveBlob } from '../utils/download';
import { useDocumentDraft } from './project/useDocumentDraft';
import { useProjectReferences } from './project/useProjectReferences';
import { useProjectStructure } from './project/useProjectStructure';
import { useDraftSaves } from './project/draftSaves';

export type { SaveStatus } from './project/useDocumentDraft';

export function useProjectState() {
  const queryClient = useQueryClient();
  const drafts = useDraftSaves();
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
    if (useAppStore.getState().currentProject?.id === currentProject.id) setCurrentProject(updated);
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
    saveCurrentIfDirty: drafts.flush,
  });

  const references = useProjectReferences({
    currentProject,
    refreshProject,
    saveCurrentIfDirty: drafts.flush,
  });

  // ─── AI continue mutation ───
  const continueMutation = useMutation({
    mutationFn: async ({ projectId, docId, payload }: {
      projectId: string;
      docId: string;
      content: string;
      generation: number;
      payload: {
        instruction?: string;
        ai_model?: string;
        debug?: boolean;
        expected_content_revision?: number;
      };
    }) => projectsApi.aiContinue(projectId, docId, payload),
    onSuccess: (result, variables) => {
      draft.applyContinuedContent(result.content, result.content_revision, variables);
      setDebugInfo(result.debug_info || null);
      refreshProject();
    },
  });
  const totalWordCount = currentProject?.total_word_count ?? 0;

  const handleContinue = async () => {
    if (!currentProject || !currentDocumentId) return;
    await drafts.flush();
    const active = useAppStore.getState();
    if (active.currentProject?.id !== currentProject.id || active.currentDocumentId !== currentDocumentId) return;
    await continueMutation.mutateAsync({
      projectId: currentProject.id,
      docId: currentDocumentId,
      content: draft.getContent(),
      generation: draft.getLoadSequence(),
      payload: {
        instruction: continueInstruction.trim() || undefined,
        ai_model: aiModel,
        debug: debugMode,
        expected_content_revision: draft.getContentRevision(),
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

  const closeProject = async () => {
    await drafts.flush();
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
    switchDocument: async (docId: string) => {
      await drafts.flush();
      await draft.switchDocument(docId);
    },
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
