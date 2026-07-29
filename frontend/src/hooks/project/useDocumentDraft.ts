import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { projectsApi } from '../../api';
import type { Project } from '../../types';

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
  const [documentContent, setDocumentContent] = useState('');
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

  const getCurrentSnapshot = useCallback(
    () => createSnapshot(documentName, documentContent, synopsis, status, label),
    [documentName, documentContent, synopsis, status, label],
  );

  useEffect(() => {
    currentSnapshotRef.current = getCurrentSnapshot();
  }, [getCurrentSnapshot]);

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
      expected_content_revision: contentRevisionRef.current,
    }),
    onSuccess: (doc, variables) => {
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
    },
  });

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

  const markSnapshotSaved = useCallback((snapshot: DocumentSnapshot, contentRevision?: number) => {
    currentSnapshotRef.current = snapshot;
    lastSavedSnapshotRef.current = snapshot;
    if (contentRevision !== undefined) {
      contentRevisionRef.current = contentRevision;
    }
    setSaveStatus('saved');
  }, []);

  const replaceContentAsSaved = useCallback((content: string, contentRevision: number) => {
    const snapshot = { ...currentSnapshotRef.current, content };
    markSnapshotSaved(snapshot, contentRevision);
    setDocumentContent(content);
  }, [markSnapshotSaved]);

  const markCurrentDocumentRenamed = useCallback((name: string) => {
    const snapshot = { ...currentSnapshotRef.current, name };
    markSnapshotSaved(snapshot);
    setDocumentName(name);
  }, [markSnapshotSaved]);

  const resetDraft = useCallback(() => {
    setCurrentDocumentId(null);
    setDocumentName('');
    setDocumentContent('');
    setSynopsis('');
    setStatus('draft');
    setLabel('');
    markSnapshotSaved(emptySnapshot(), 0);
  }, [markSnapshotSaved, setCurrentDocumentId]);

  const setIsReviewing = useCallback((value: boolean) => {
    isReviewingRef.current = value;
  }, []);

  const saveCurrentIfDirty = useCallback(async () => {
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
      markSnapshotSaved(
        createSnapshot(doc.name, doc.content, doc.synopsis || '', doc.status, doc.label || ''),
        doc.content_revision,
      );
      setCurrentDocumentId(docId);
    } finally {
      isLoadingDocRef.current = false;
    }
  }, [currentProject, markSnapshotSaved, setCurrentDocumentId]);

  const switchDocument = useCallback(async (docId: string) => {
    if (docId === currentDocumentId) return;
    await saveCurrentIfDirty();
    await loadDocument(docId);
  }, [currentDocumentId, loadDocument, saveCurrentIfDirty]);

  useEffect(() => {
    if (!currentProject || !currentDocumentId || isLoadingDocRef.current) return;
    if (isReviewingRef.current) return;
    const snapshot = getCurrentSnapshot();
    if (snapshotsEqual(snapshot, lastSavedSnapshotRef.current)) return;
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
  }, [currentProject?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  return {
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
    saveStatus,
    isSaving: saveMutation.isPending,
    wordCount: countWords(documentContent),
    loadDocument,
    switchDocument,
    handleSave: saveCurrentIfDirty,
    saveCurrentIfDirty,
    replaceContentAsSaved,
    markCurrentDocumentRenamed,
    resetDraft,
    setIsReviewing,
    getContentRevision: () => contentRevisionRef.current,
  };
}
