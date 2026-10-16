import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { projectsApi } from '../../api';
import type { Project } from '../../types';
import { useAppStore } from '../../stores/appStore';
import { useRegisterDraftSave } from './draftSaves';
import { useTextHistory, type TextEditOptions } from '../useTextHistory';

export type SaveStatus = 'saved' | 'saving' | 'unsaved' | 'conflict';

export type DocumentSnapshot = {
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
  const englishWords = stripped.split(/\s+/).filter((word) => word.length > 0).length;
  return chineseChars + englishWords;
}

interface UseDocumentDraftArgs {
  currentProject: Project | null;
  currentDocumentId: string | null;
  setCurrentDocumentId: (docId: string | null) => void;
  refreshProject: () => Promise<void>;
}

export function useDocumentDraft({
  currentProject,
  currentDocumentId,
  setCurrentDocumentId,
  refreshProject,
}: UseDocumentDraftArgs) {
  const [documentName, setDocumentName] = useState('');
  const contentHistory = useTextHistory();
  const { text: documentContent, setText: setDocumentContent, reset: resetContent } = contentHistory;
  const [synopsis, setSynopsis] = useState('');
  const [status, setStatus] = useState('draft');
  const [label, setLabel] = useState('');
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('saved');

  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const currentSnapshotRef = useRef<DocumentSnapshot>(emptySnapshot());
  const lastSavedSnapshotRef = useRef<DocumentSnapshot>(emptySnapshot());
  const contentRevisionRef = useRef(0);
  const isLoadingDocRef = useRef(false);
  const isReviewingRef = useRef(false);
  const loadSequenceRef = useRef(0);
  const savePromiseRef = useRef<Promise<void> | null>(null);
  const conflictRef = useRef(false);

  const getCurrentSnapshot = useCallback(
    () => createSnapshot(documentName, documentContent, synopsis, status, label),
    [documentName, documentContent, synopsis, status, label],
  );

  useEffect(() => {
    currentSnapshotRef.current = getCurrentSnapshot();
  }, [getCurrentSnapshot]);

  const saveMutation = useMutation({
    mutationFn: async ({ projectId, docId, snapshot, revision }: {
      projectId: string;
      docId: string;
      snapshot: DocumentSnapshot;
      revision: number;
    }) => projectsApi.updateDocument(projectId, docId, {
      name: snapshot.name,
      content: snapshot.content,
      synopsis: snapshot.synopsis,
      status: snapshot.status,
      label: snapshot.label,
      expected_content_revision: revision,
    }),
    onSuccess: (doc, variables) => {
      const active = useAppStore.getState();
      if (active.currentProject?.id !== variables.projectId || active.currentDocumentId !== variables.docId) return;
      contentRevisionRef.current = doc.content_revision;
      lastSavedSnapshotRef.current = variables.snapshot;
      setSaveStatus(
        snapshotsEqual(currentSnapshotRef.current, variables.snapshot) ? 'saved' : 'unsaved',
      );
      refreshProject();
    },
    onError: (error) => {
      const status = (
        error as { response?: { status?: number } }
      ).response?.status;
      setSaveStatus(status === 409 ? 'conflict' : 'unsaved');
      conflictRef.current = status === 409;
    },
  });

  const updateDocumentName = useCallback((name: string) => {
    currentSnapshotRef.current = { ...currentSnapshotRef.current, name };
    setDocumentName(name);
  }, []);

  const updateDocumentContent = useCallback((content: string, options?: TextEditOptions) => {
    currentSnapshotRef.current = { ...currentSnapshotRef.current, content };
    setDocumentContent(content, options);
  }, [setDocumentContent]);

  const undoDocumentContent = () => {
    const previous = contentHistory.undo();
    if (previous) currentSnapshotRef.current = { ...currentSnapshotRef.current, content: previous.text };
    return previous;
  };

  const redoDocumentContent = () => {
    const next = contentHistory.redo();
    if (next) currentSnapshotRef.current = { ...currentSnapshotRef.current, content: next.text };
    return next;
  };

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

  const markSnapshotSaved = useCallback((snapshot: DocumentSnapshot, contentRevision?: number) => {
    currentSnapshotRef.current = snapshot;
    lastSavedSnapshotRef.current = snapshot;
    if (contentRevision !== undefined) {
      contentRevisionRef.current = contentRevision;
    }
    setSaveStatus('saved');
    conflictRef.current = false;
  }, []);

  const applyContinuedContent = useCallback((content: string, contentRevision: number, base: {
    projectId: string; docId: string; content: string; generation: number;
  }) => {
    const active = useAppStore.getState();
    if (active.currentProject?.id !== base.projectId || active.currentDocumentId !== base.docId
      || base.generation !== loadSequenceRef.current) return;
    if (currentSnapshotRef.current.content !== base.content) {
      conflictRef.current = true;
      setSaveStatus('conflict');
      return;
    }
    const snapshot = { ...currentSnapshotRef.current, content };
    currentSnapshotRef.current = snapshot;
    lastSavedSnapshotRef.current = { ...lastSavedSnapshotRef.current, content };
    contentRevisionRef.current = contentRevision;
    setSaveStatus(snapshotsEqual(snapshot, lastSavedSnapshotRef.current) ? 'saved' : 'unsaved');
    setDocumentContent(content);
  }, [setDocumentContent]);

  const markCurrentDocumentRenamed = useCallback((docId: string, name: string) => {
    if (useAppStore.getState().currentDocumentId !== docId) return;
    const snapshot = { ...currentSnapshotRef.current, name };
    currentSnapshotRef.current = snapshot;
    lastSavedSnapshotRef.current = { ...lastSavedSnapshotRef.current, name };
    setSaveStatus(snapshotsEqual(snapshot, lastSavedSnapshotRef.current) ? 'saved' : 'unsaved');
    setDocumentName(name);
  }, []);

  const resetDraft = useCallback(() => {
    setCurrentDocumentId(null);
    setDocumentName('');
    resetContent('');
    setSynopsis('');
    setStatus('draft');
    setLabel('');
    markSnapshotSaved(emptySnapshot(), 0);
  }, [markSnapshotSaved, resetContent, setCurrentDocumentId]);

  const setIsReviewing = useCallback((value: boolean) => {
    isReviewingRef.current = value;
  }, []);

  const saveCurrentIfDirty = useCallback(async () => {
    if (savePromiseRef.current) return savePromiseRef.current;
    if (!currentProject || !currentDocumentId || isLoadingDocRef.current) return;
    const save = async () => {
      while (conflictRef.current || !snapshotsEqual(currentSnapshotRef.current, lastSavedSnapshotRef.current)) {
        if (conflictRef.current) throw new Error('正文存在冲突，请保留当前输入后重新加载。');
        const active = useAppStore.getState();
        if (active.currentProject?.id !== currentProject.id || active.currentDocumentId !== currentDocumentId) return;
        setSaveStatus('saving');
        await saveMutation.mutateAsync({
          projectId: currentProject.id,
          docId: currentDocumentId,
          snapshot: currentSnapshotRef.current,
          revision: contentRevisionRef.current,
        });
      }
    };
    const pending = save();
    savePromiseRef.current = pending;
    try {
      await pending;
    } finally {
      if (savePromiseRef.current === pending) savePromiseRef.current = null;
    }
  }, [currentProject, currentDocumentId, saveMutation]);

  useRegisterDraftSave(saveCurrentIfDirty, () => Boolean(currentDocumentId) && (
    conflictRef.current || !snapshotsEqual(currentSnapshotRef.current, lastSavedSnapshotRef.current)
  ));

  const loadDocument = useCallback(async (docId: string) => {
    if (!currentProject) return;
    const sequence = ++loadSequenceRef.current;
    isLoadingDocRef.current = true;
    try {
      const doc = await projectsApi.getDocument(currentProject.id, docId);
      if (sequence !== loadSequenceRef.current || useAppStore.getState().currentProject?.id !== currentProject.id) return;
      setDocumentName(doc.name);
      resetContent(doc.content);
      setSynopsis(doc.synopsis || '');
      setStatus(doc.status);
      setLabel(doc.label || '');
      markSnapshotSaved(
        createSnapshot(doc.name, doc.content, doc.synopsis || '', doc.status, doc.label || ''),
        doc.content_revision,
      );
      setCurrentDocumentId(docId);
    } finally {
      if (sequence === loadSequenceRef.current) isLoadingDocRef.current = false;
    }
  }, [currentProject, markSnapshotSaved, resetContent, setCurrentDocumentId]);

  const switchDocument = useCallback(async (docId: string) => {
    if (docId === currentDocumentId) return;
    await saveCurrentIfDirty();
    await loadDocument(docId);
  }, [currentDocumentId, loadDocument, saveCurrentIfDirty]);

  useEffect(() => {
    if (!currentProject || !currentDocumentId || isLoadingDocRef.current) return;
    if (isReviewingRef.current || conflictRef.current) return;
    const snapshot = getCurrentSnapshot();
    if (snapshotsEqual(snapshot, lastSavedSnapshotRef.current)) {
      setSaveStatus('saved');
      return;
    }
    setSaveStatus('unsaved');
    if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    autoSaveTimerRef.current = setTimeout(() => {
      void saveCurrentIfDirty().catch(() => undefined);
    }, 3000);
    return () => {
      if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    };
  }, [currentProject, currentDocumentId, getCurrentSnapshot, saveCurrentIfDirty]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === 's') {
        event.preventDefault();
        saveCurrentIfDirty();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [saveCurrentIfDirty]);

  useEffect(() => {
    if (!currentProject) return;
    const firstDoc = currentProject.groups?.[0]?.documents?.[0];
    if (firstDoc && !currentDocumentId) {
      loadDocument(firstDoc.id);
    } else if (currentDocumentId) {
      loadDocument(currentDocumentId);
    }
    return () => { loadSequenceRef.current += 1; };
  }, [currentProject?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    documentName,
    setDocumentName: updateDocumentName,
    documentContent,
    setDocumentContent: updateDocumentContent,
    canUndoContent: contentHistory.canUndo,
    canRedoContent: contentHistory.canRedo,
    undoDocumentContent,
    redoDocumentContent,
    setContentSelection: contentHistory.setSelection,
    breakContentHistoryGroup: contentHistory.breakGroup,
    synopsis,
    setSynopsis: updateSynopsis,
    status,
    setStatus: updateStatus,
    label,
    setLabel: updateLabel,
    saveStatus,
    isSaving: saveMutation.isPending,
    wordCount: countWords(documentContent),
    loadDocument,
    switchDocument,
    handleSave: saveCurrentIfDirty,
    saveCurrentIfDirty,
    applyContinuedContent,
    markCurrentDocumentRenamed,
    resetDraft,
    setIsReviewing,
    getContentRevision: () => contentRevisionRef.current,
    getContent: () => currentSnapshotRef.current.content,
    getLoadSequence: () => loadSequenceRef.current,
  };
}
