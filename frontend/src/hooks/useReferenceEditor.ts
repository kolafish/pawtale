import { useCallback, useEffect, useRef, useState } from 'react';
import { projectsApi } from '../api';
import { useAppStore } from '../stores/appStore';
import type { ReferenceItem } from '../types';

export type RefSaveStatus = 'saved' | 'saving' | 'unsaved';

export function useReferenceEditor() {
  const { currentProject, currentReferenceId } = useAppStore();
  const [isExporting, setIsExporting] = useState(false);

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
  const doSave = useCallback(async () => {
    if (!currentProject || !referenceData || isLoadingRef.current) return;
    const current = JSON.stringify(referenceData);
    if (current === lastSavedRef.current) return;
    setSaveStatus('saving');
    await projectsApi.updateReference(currentProject.id, referenceData.id, {
      name: referenceData.name,
      content: referenceData.content,
      metadata_json: referenceData.metadata_json,
    });
    lastSavedRef.current = JSON.stringify(referenceData);
    setSaveStatus('saved');
  }, [currentProject, referenceData]);

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

  // ─── Sync from global ───
  const syncFromGlobal = useCallback(async () => {
    if (!currentProject || !referenceData) return;
    const updated = await projectsApi.syncGlobal(currentProject.id, referenceData.id);
    setReferenceData(updated);
    lastSavedRef.current = JSON.stringify(updated);
    setSaveStatus('saved');
  }, [currentProject, referenceData]);

  // ─── Export to global library ───
  const exportToGlobal = useCallback(async () => {
    if (!currentProject || !referenceData) return;
    setIsExporting(true);
    try {
      // Save first to ensure latest data
      await doSave();
      // Preview for matches
      const preview = await projectsApi.exportGlobal(currentProject.id, referenceData.id, { action: 'preview' });
      let action: 'create' | 'update' | null = null;
      let globalId: string | undefined;

      if (preview.match) {
        const ok = confirm(`发现同名全局记录「${preview.match.global_name}」，是否更新？\n\n点击「确定」更新已有记录，「取消」新建一个。`);
        if (ok) {
          action = 'update';
          globalId = preview.match.global_id;
        } else {
          action = 'create';
        }
      } else {
        action = 'create';
      }

      const result = await projectsApi.exportGlobal(currentProject.id, referenceData.id, {
        action,
        global_id: globalId,
      });

      // Reload reference to pick up the new global link
      await loadReference(referenceData.id);
      alert(result.status === 'created' ? '已创建全局记录' : '已更新全局记录');
    } catch (e: any) {
      const msg = e?.response?.data?.detail || (e instanceof Error ? e.message : String(e));
      alert(`导出失败: ${msg}`);
    } finally {
      setIsExporting(false);
    }
  }, [currentProject, referenceData, doSave, loadReference]);

  return {
    referenceData,
    referenceType: referenceData?.type || null,
    saveStatus,
    isSaving: saveStatus === 'saving',
    isExporting,
    updateField,
    updateMeta,
    handleSave: doSave,
    syncFromGlobal,
    exportToGlobal,
    reloadReference: () => currentReferenceId ? loadReference(currentReferenceId) : undefined,
  };
}
