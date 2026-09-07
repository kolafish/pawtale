import { useCallback, useEffect, useRef, useState } from 'react';
import { projectsApi } from '../api';
import { useAppStore } from '../stores/appStore';
import type { ReferenceItem } from '../types';
import { useRegisterDraftSave } from './project/draftSaves';

export type RefSaveStatus = 'saved' | 'saving' | 'unsaved';

export function useReferenceEditor() {
  const { currentProject, currentReferenceId, setCurrentProject } = useAppStore();

  const [referenceData, setReferenceData] = useState<ReferenceItem | null>(null);
  const [saveStatus, setSaveStatus] = useState<RefSaveStatus>('saved');
  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSavedRef = useRef<string>(''); // JSON snapshot for dirty check
  const isLoadingRef = useRef(false);
  const currentRef = useRef<ReferenceItem | null>(null);
  const savePromiseRef = useRef<Promise<void> | null>(null);
  const loadSequenceRef = useRef(0);
  const projectId = currentProject?.id;

  // ─── Load reference ───
  const loadReference = useCallback(async (refId: string) => {
    if (!projectId) return;
    const sequence = ++loadSequenceRef.current;
    isLoadingRef.current = true;
    try {
      const refs = await projectsApi.listReferences(projectId);
      if (sequence !== loadSequenceRef.current || useAppStore.getState().currentProject?.id !== projectId) return;
      const ref = refs.find((r) => r.id === refId) || null;
      currentRef.current = ref;
      setReferenceData(ref);
      lastSavedRef.current = ref ? JSON.stringify(ref) : '';
      setSaveStatus('saved');
    } finally {
      if (sequence === loadSequenceRef.current) isLoadingRef.current = false;
    }
  }, [projectId]);

  // Load when currentReferenceId changes
  useEffect(() => {
    if (currentReferenceId) {
      loadReference(currentReferenceId);
    } else {
      currentRef.current = null;
      setReferenceData(null);
      lastSavedRef.current = '';
      setSaveStatus('saved');
    }
    return () => { loadSequenceRef.current += 1; };
  }, [currentReferenceId, loadReference]);

  // ─── Save ───
  const syncProjectReference = useCallback((saved: ReferenceItem) => {
    const project = useAppStore.getState().currentProject;
    if (!project || project.id !== projectId) return;

    setCurrentProject({
      ...project,
      references: project.references.map((ref) =>
        ref.id === saved.id
          ? {
              id: saved.id,
              type: saved.type,
              name: saved.name,
              sort_order: saved.sort_order,
            }
          : ref,
      ),
    });
  }, [projectId, setCurrentProject]);

  const doSave = useCallback(async () => {
    if (savePromiseRef.current) return savePromiseRef.current;
    if (!projectId || isLoadingRef.current) return;
    const save = async () => {
      while (currentRef.current && JSON.stringify(currentRef.current) !== lastSavedRef.current) {
        const snapshot = currentRef.current;
        const active = useAppStore.getState();
        if (active.currentProject?.id !== projectId || active.currentReferenceId !== snapshot.id) return;
        setSaveStatus('saving');
        const saved = await projectsApi.updateReference(projectId, snapshot.id, {
          name: snapshot.name, content: snapshot.content, metadata_json: snapshot.metadata_json,
        });
        if (currentRef.current?.id !== saved.id) return;
        lastSavedRef.current = JSON.stringify(saved);
        if (currentRef.current === snapshot) {
          currentRef.current = saved;
          setReferenceData(saved);
        }
        syncProjectReference(saved);
      }
      setSaveStatus('saved');
    };
    const pending = save();
    savePromiseRef.current = pending;
    try {
      await pending;
    } catch (error) {
      setSaveStatus('unsaved');
      throw error;
    } finally {
      if (savePromiseRef.current === pending) savePromiseRef.current = null;
    }
  }, [projectId, syncProjectReference]);

  useRegisterDraftSave(doSave, () => Boolean(currentRef.current)
    && JSON.stringify(currentRef.current) !== lastSavedRef.current);

  // Auto-save (3s debounce)
  useEffect(() => {
    if (!currentProject || !referenceData || isLoadingRef.current) return;
    const current = JSON.stringify(referenceData);
    if (current === lastSavedRef.current) return;
    setSaveStatus('unsaved');
    if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    autoSaveTimerRef.current = setTimeout(() => { void doSave().catch(() => undefined); }, 3000);
    return () => {
      if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    };
  }, [referenceData, currentProject, doSave]);

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

  // ─── Update helpers ───
  const updateField = useCallback((patch: Partial<ReferenceItem>) => {
    if (!currentRef.current) return;
    currentRef.current = { ...currentRef.current, ...patch };
    setReferenceData(currentRef.current);
  }, []);

  const updateMeta = useCallback((key: string, value: unknown) => {
    if (!currentRef.current) return;
    currentRef.current = {
      ...currentRef.current, metadata_json: { ...currentRef.current.metadata_json, [key]: value },
    };
    setReferenceData(currentRef.current);
  }, []);

  return {
    referenceData,
    referenceType: referenceData?.type || null,
    saveStatus,
    isSaving: saveStatus === 'saving',
    updateField,
    updateMeta,
    handleSave: doSave,
    reloadReference: () => currentReferenceId ? loadReference(currentReferenceId) : undefined,
  };
}
