import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { projectsApi } from '../api';
import { useAppStore } from '../stores/appStore';
import type { AIDebugInfo } from '../types';

export type SaveStatus = 'saved' | 'saving' | 'unsaved';

function countWords(text: string): number {
  if (!text) return 0;
  const chineseChars = (text.match(/[\u4e00-\u9fff\u3400-\u4dbf]/g) || []).length;
  const stripped = text.replace(/[\u4e00-\u9fff\u3400-\u4dbf]/g, ' ');
  const englishWords = stripped.split(/\s+/).filter((w) => w.length > 0).length;
  return chineseChars + englishWords;
}

export function useProjectState() {
  const queryClient = useQueryClient();
  const {
    currentProject, setCurrentProject,
    currentDocumentId, setCurrentDocumentId,
    defaultAiModel,
  } = useAppStore();

  // Document editor state
  const [documentName, setDocumentName] = useState('');
  const [documentContent, setDocumentContent] = useState('');
  const [synopsis, setSynopsis] = useState('');
  const [status, setStatus] = useState('draft');
  const [label, setLabel] = useState('');

  // AI state
  const [aiModel, setAiModel] = useState(defaultAiModel);
  const [continueInstruction, setContinueInstruction] = useState('');
  const [debugMode, setDebugMode] = useState(false);
  const [debugInfo, setDebugInfo] = useState<AIDebugInfo | null>(null);

  // Save state
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('saved');
  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSavedContentRef = useRef('');
  const lastSavedNameRef = useRef('');
  const isLoadingDocRef = useRef(false);
  const isReviewingRef = useRef(false);

  // ─── Refresh project data ───
  const refreshProject = useCallback(async () => {
    if (!currentProject) return;
    const updated = await projectsApi.get(currentProject.id);
    setCurrentProject(updated);
  }, [currentProject, setCurrentProject]);

  // ─── Save mutation ───
  const saveMutation = useMutation({
    mutationFn: async ({ projectId, docId, payload }: {
      projectId: string;
      docId: string;
      payload: { name?: string; content?: string; synopsis?: string; status?: string; label?: string };
    }) => projectsApi.updateDocument(projectId, docId, payload),
    onSuccess: () => {
      setSaveStatus('saved');
      lastSavedContentRef.current = documentContent;
      lastSavedNameRef.current = documentName;
      refreshProject();
    },
  });

  // ─── AI continue mutation ───
  const continueMutation = useMutation({
    mutationFn: async ({ projectId, docId, payload }: {
      projectId: string;
      docId: string;
      payload: { instruction?: string; ai_model?: string; debug?: boolean };
    }) => projectsApi.aiContinue(projectId, docId, payload),
    onSuccess: (result) => {
      setDocumentContent(result.content);
      setDebugInfo(result.debug_info || null);
      lastSavedContentRef.current = result.content;
      setSaveStatus('saved');
      refreshProject();
    },
  });

  // ─── Load document ───
  const loadDocument = useCallback(async (docId: string) => {
    if (!currentProject) return;
    isLoadingDocRef.current = true;
    try {
      const doc = await projectsApi.getDocument(currentProject.id, docId);
      setDocumentName(doc.name);
      setDocumentContent(doc.content);
      setSynopsis(doc.synopsis || '');
      setStatus(doc.status);
      setLabel(doc.label || '');
      lastSavedContentRef.current = doc.content;
      lastSavedNameRef.current = doc.name;
      setSaveStatus('saved');
      setCurrentDocumentId(docId);
    } finally {
      isLoadingDocRef.current = false;
    }
  }, [currentProject, setCurrentDocumentId]);

  // ─── Switch document (save current first) ───
  const switchDocument = useCallback(async (docId: string) => {
    if (docId === currentDocumentId) return;
    // Save current if dirty
    if (currentProject && currentDocumentId) {
      const isDirty = documentContent !== lastSavedContentRef.current ||
                      documentName !== lastSavedNameRef.current;
      if (isDirty) {
        setSaveStatus('saving');
        await saveMutation.mutateAsync({
          projectId: currentProject.id,
          docId: currentDocumentId,
          payload: { name: documentName, content: documentContent, synopsis, status, label: label || undefined },
        });
      }
    }
    await loadDocument(docId);
  }, [currentProject, currentDocumentId, documentContent, documentName, synopsis, status, label, saveMutation, loadDocument]);

  // ─── Auto-save ───
  const doSave = useCallback(async () => {
    if (!currentProject || !currentDocumentId || isLoadingDocRef.current) return;
    if (documentContent === lastSavedContentRef.current && documentName === lastSavedNameRef.current) return;
    setSaveStatus('saving');
    await saveMutation.mutateAsync({
      projectId: currentProject.id,
      docId: currentDocumentId,
      payload: { name: documentName, content: documentContent, synopsis, status, label: label || undefined },
    });
  }, [currentProject, currentDocumentId, documentContent, documentName, synopsis, status, label, saveMutation]);

  useEffect(() => {
    if (!currentProject || !currentDocumentId || isLoadingDocRef.current) return;
    if (isReviewingRef.current) return; // Pause auto-save during diff review
    if (documentContent === lastSavedContentRef.current && documentName === lastSavedNameRef.current) return;
    setSaveStatus('unsaved');
    if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    autoSaveTimerRef.current = setTimeout(() => doSave(), 3000);
    return () => {
      if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    };
  }, [documentContent, documentName, currentProject, currentDocumentId, doSave]);

  // Cmd+S
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault();
        doSave();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [doSave]);

  // Load first document when project opens
  useEffect(() => {
    if (!currentProject) return;
    const firstDoc = currentProject.groups?.[0]?.documents?.[0];
    if (firstDoc && !currentDocumentId) {
      loadDocument(firstDoc.id);
    } else if (currentDocumentId) {
      loadDocument(currentDocumentId);
    }
  }, [currentProject?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // ─── Computed ───
  const wordCount = countWords(documentContent);
  const totalWordCount = currentProject?.total_word_count ?? 0;

  // ─── Handlers ───
  const handleSave = () => doSave();

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
    setCurrentProject(null);
    setCurrentDocumentId(null);
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
    await loadDocument(doc.id);
  };

  const renameDocument = async (docId: string, name: string) => {
    if (!currentProject) return;
    await projectsApi.updateDocument(currentProject.id, docId, { name });
    await refreshProject();
    if (docId === currentDocumentId) setDocumentName(name);
  };

  const deleteDocument = async (docId: string) => {
    if (!currentProject) return;
    await projectsApi.deleteDocument(currentProject.id, docId);
    if (docId === currentDocumentId) {
      // Switch to another document
      const allDocs = currentProject.groups.flatMap((g) => g.documents);
      const remaining = allDocs.filter((d) => d.id !== docId);
      if (remaining.length > 0) {
        await loadDocument(remaining[0].id);
      } else {
        setCurrentDocumentId(null);
        setDocumentName('');
        setDocumentContent('');
      }
    }
    await refreshProject();
  };

  const reorderDocuments = async (items: { id: string; group_id: string; sort_order: number }[]) => {
    if (!currentProject) return;
    await projectsApi.reorderDocuments(currentProject.id, items);
    await refreshProject();
  };

  return {
    // Project
    currentProject,
    closeProject,
    refreshProject,

    // Document
    currentDocumentId,
    documentName,
    setDocumentName,
    documentContent,
    setDocumentContent,
    synopsis,
    setSynopsis,
    status,
    setStatus,
    label,
    setLabel,
    switchDocument,
    loadDocument,

    // Save
    saveStatus,
    isSaving: saveMutation.isPending,
    handleSave,

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
    wordCount,
    totalWordCount,

    // Export
    handleExportTxt,

    // Review mode (for AI revision)
    setIsReviewing: (v: boolean) => { isReviewingRef.current = v; },

    // Binder operations
    createGroup,
    renameGroup,
    deleteGroup,
    createDocument,
    renameDocument,
    deleteDocument,
    reorderDocuments,
  };
}
