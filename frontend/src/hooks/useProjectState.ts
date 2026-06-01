import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { projectsApi } from '../api';
import { useAppStore } from '../stores/appStore';
import type { AIDebugInfo } from '../types';

export type SaveStatus = 'saved' | 'saving' | 'unsaved';

type DocumentSnapshot = {
  name: string;
  content: string;
  synopsis: string;
  status: string;
  label: string;
};

function createSnapshot(
  name: string,
  content: string,
  synopsis: string,
  status: string,
  label: string,
): DocumentSnapshot {
  return { name, content, synopsis, status, label };
}

function emptySnapshot(): DocumentSnapshot {
  return createSnapshot('', '', '', 'draft', '');
}

function snapshotsEqual(a: DocumentSnapshot, b: DocumentSnapshot): boolean {
  return a.name === b.name &&
    a.content === b.content &&
    a.synopsis === b.synopsis &&
    a.status === b.status &&
    a.label === b.label;
}

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
  const currentSnapshotRef = useRef<DocumentSnapshot>(emptySnapshot());
  const lastSavedSnapshotRef = useRef<DocumentSnapshot>(emptySnapshot());
  const isLoadingDocRef = useRef(false);
  const isReviewingRef = useRef(false);

  const getCurrentSnapshot = useCallback(
    () => createSnapshot(documentName, documentContent, synopsis, status, label),
    [documentName, documentContent, synopsis, status, label],
  );

  useEffect(() => {
    currentSnapshotRef.current = getCurrentSnapshot();
  }, [getCurrentSnapshot]);

  const updateDocumentName = useCallback((name: string) => {
    currentSnapshotRef.current = { ...currentSnapshotRef.current, name };
    setDocumentName(name);
  }, []);

  const updateDocumentContent = useCallback((content: string) => {
    currentSnapshotRef.current = { ...currentSnapshotRef.current, content };
    setDocumentContent(content);
  }, []);

  const updateSynopsis = useCallback((nextSynopsis: string) => {
    currentSnapshotRef.current = { ...currentSnapshotRef.current, synopsis: nextSynopsis };
    setSynopsis(nextSynopsis);
  }, []);

  const updateStatus = useCallback((nextStatus: string) => {
    currentSnapshotRef.current = { ...currentSnapshotRef.current, status: nextStatus };
    setStatus(nextStatus);
  }, []);

  const updateLabel = useCallback((nextLabel: string) => {
    currentSnapshotRef.current = { ...currentSnapshotRef.current, label: nextLabel };
    setLabel(nextLabel);
  }, []);

  // ─── Refresh project data ───
  const refreshProject = useCallback(async () => {
    if (!currentProject) return;
    const updated = await projectsApi.get(currentProject.id);
    setCurrentProject(updated);
  }, [currentProject, setCurrentProject]);

	  // ─── Save mutation ───
  const saveMutation = useMutation({
    mutationFn: async ({ projectId, docId, snapshot }: {
      projectId: string;
      docId: string;
      snapshot: DocumentSnapshot;
    }) => projectsApi.updateDocument(projectId, docId, {
      name: snapshot.name,
      content: snapshot.content,
      synopsis: snapshot.synopsis,
      status: snapshot.status,
      label: snapshot.label,
    }),
    onSuccess: (_doc, variables) => {
      lastSavedSnapshotRef.current = variables.snapshot;
      setSaveStatus(
        snapshotsEqual(currentSnapshotRef.current, variables.snapshot) ? 'saved' : 'unsaved',
      );
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
      const snapshot = { ...currentSnapshotRef.current, content: result.content };
      currentSnapshotRef.current = snapshot;
      lastSavedSnapshotRef.current = snapshot;
      setDocumentContent(result.content);
      setDebugInfo(result.debug_info || null);
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
      const snapshot = createSnapshot(doc.name, doc.content, doc.synopsis || '', doc.status, doc.label || '');
      currentSnapshotRef.current = snapshot;
      lastSavedSnapshotRef.current = snapshot;
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
      const snapshot = getCurrentSnapshot();
      if (!snapshotsEqual(snapshot, lastSavedSnapshotRef.current)) {
        setSaveStatus('saving');
        await saveMutation.mutateAsync({
          projectId: currentProject.id,
          docId: currentDocumentId,
          snapshot,
        });
      }
    }
    await loadDocument(docId);
  }, [currentProject, currentDocumentId, getCurrentSnapshot, saveMutation, loadDocument]);

  // ─── Auto-save ───
  const doSave = useCallback(async () => {
    if (!currentProject || !currentDocumentId || isLoadingDocRef.current) return;
    const snapshot = getCurrentSnapshot();
    if (snapshotsEqual(snapshot, lastSavedSnapshotRef.current)) return;
    setSaveStatus('saving');
    await saveMutation.mutateAsync({
      projectId: currentProject.id,
      docId: currentDocumentId,
      snapshot,
    });
  }, [currentProject, currentDocumentId, getCurrentSnapshot, saveMutation]);

  useEffect(() => {
    if (!currentProject || !currentDocumentId || isLoadingDocRef.current) return;
    if (isReviewingRef.current) return; // Pause auto-save during diff review
    const snapshot = getCurrentSnapshot();
    if (snapshotsEqual(snapshot, lastSavedSnapshotRef.current)) return;
    setSaveStatus('unsaved');
    if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    autoSaveTimerRef.current = setTimeout(() => doSave(), 3000);
    return () => {
      if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    };
  }, [getCurrentSnapshot, currentProject, currentDocumentId, doSave]);

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
    if (docId === currentDocumentId) {
      const snapshot = { ...currentSnapshotRef.current, name };
      currentSnapshotRef.current = snapshot;
      lastSavedSnapshotRef.current = snapshot;
      setDocumentName(name);
      setSaveStatus('saved');
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
        await loadDocument(remaining[0].id);
      } else {
        setCurrentDocumentId(null);
        setDocumentName('');
        setDocumentContent('');
        setSynopsis('');
        setStatus('draft');
        setLabel('');
        currentSnapshotRef.current = emptySnapshot();
        lastSavedSnapshotRef.current = emptySnapshot();
        setSaveStatus('saved');
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
    // Save current document if dirty before switching
    if (currentProject && currentDocumentId) {
      const snapshot = getCurrentSnapshot();
      if (!snapshotsEqual(snapshot, lastSavedSnapshotRef.current)) {
        setSaveStatus('saving');
        await saveMutation.mutateAsync({
          projectId: currentProject.id,
          docId: currentDocumentId,
          snapshot,
        });
      }
    }
    setCurrentReferenceId(refId);
  };

  return {
    // Project
    currentProject,
    closeProject,
    refreshProject,

    // Document
    currentDocumentId,
    documentName,
    setDocumentName: updateDocumentName,
    documentContent,
    setDocumentContent: updateDocumentContent,
    synopsis,
    setSynopsis: updateSynopsis,
    status,
    setStatus: updateStatus,
    label,
    setLabel: updateLabel,
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
    reorderGroups,

    // Reference operations
    createReference,
    renameReference,
    deleteReference,
    selectReference,
  };
}
