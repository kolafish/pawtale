import { useCallback, useEffect, useRef, useState } from 'react';
import { projectsApi } from '../api';
import { useAppStore } from '../stores/appStore';
import type { ReferenceItem } from '../types';

export type RefSaveStatus = 'saved' | 'saving' | 'unsaved';

export function useReferenceEditor() {
  const { currentProject, currentReferenceId, setCurrentProject } = useAppStore();

  const [referenceData, setReferenceData] = useState<ReferenceItem | null>(null);
  const [saveStatus, setSaveStatus] = useState<RefSaveStatus>('saved');
  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSavedRef = useRef<string>(''); // JSON snapshot for dirty check
  const isLoadingRef = useRef(false);

  // ─── Load reference ───
  const loadReference = useCallback(async (refId: string) => {
    if (!currentProject) return;
    isLoadingRef.current = true;
    try {
      const refs = await projectsApi.listReferences(currentProject.id);
      const ref = refs.find((r) => r.id === refId) || null;
      setReferenceData(ref);
      lastSavedRef.current = ref ? JSON.stringify(ref) : '';
      setSaveStatus('saved');
    } finally {
      isLoadingRef.current = false;
    }
  }, [currentProject]);

  // Load when currentReferenceId changes
  useEffect(() => {
    if (currentReferenceId) {
      loadReference(currentReferenceId);
    } else {
      setReferenceData(null);
      lastSavedRef.current = '';
      setSaveStatus('saved');
    }
  }, [currentReferenceId, loadReference]);

  // ─── Save ───
  const syncProjectReference = useCallback((saved: ReferenceItem) => {
    const project = useAppStore.getState().currentProject;
    if (!project) return;

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
  }, [setCurrentProject]);

  const doSave = useCallback(async () => {
    if (!currentProject || !referenceData || isLoadingRef.current) return;
    const current = JSON.stringify(referenceData);
    if (current === lastSavedRef.current) return;
    setSaveStatus('saving');
    const saved = await projectsApi.updateReference(currentProject.id, referenceData.id, {
      name: referenceData.name,
      content: referenceData.content,
      metadata_json: referenceData.metadata_json,
    });
    lastSavedRef.current = JSON.stringify(saved);
    setReferenceData(saved);
    syncProjectReference(saved);
    setSaveStatus('saved');
  }, [currentProject, referenceData, syncProjectReference]);

  // Auto-save (3s debounce)
  useEffect(() => {
    if (!currentProject || !referenceData || isLoadingRef.current) return;
    const current = JSON.stringify(referenceData);
    if (current === lastSavedRef.current) return;
    setSaveStatus('unsaved');
    if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    autoSaveTimerRef.current = setTimeout(() => doSave(), 3000);
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
    setReferenceData((prev) => prev ? { ...prev, ...patch } : prev);
  }, []);

  const updateMeta = useCallback((key: string, value: unknown) => {
    setReferenceData((prev) =>
      prev ? { ...prev, metadata_json: { ...prev.metadata_json, [key]: value } } : prev,
    );
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
