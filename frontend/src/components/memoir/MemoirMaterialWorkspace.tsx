import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import axios from 'axios';
import { Headphones, Loader2, RefreshCw } from 'lucide-react';
import {
  useCallback,
  useDeferredValue,
  useEffect,
  lazy,
  useMemo,
  Suspense,
  useState,
} from 'react';

import { memoirMaterialEditorApi } from '../../api';
import type {
  MemoirMaterialCorrectionStatus,
  MemoirMaterialSort,
  Project,
} from '../../types';
import { MemoirMaterialList } from './MemoirMaterialList';
import { MemoirTranscriptEditor } from './MemoirTranscriptEditor';


const MemoirAdvancedToolsDrawer = lazy(async () => ({
  default: (await import('./MemoirAdvancedToolsDrawer')).MemoirAdvancedToolsDrawer,
}));


interface MemoirMaterialWorkspaceProps {
  project: Project;
  onUnsavedChange?: (hasUnsavedChanges: boolean) => void;
}

const PAGE_SIZE = 50;

function initialMaterialId() {
  if (typeof window === 'undefined') return null;
  return new URL(window.location.href).searchParams.get('material');
}

function updateMaterialUrl(materialId: string | null) {
  if (typeof window === 'undefined') return;
  const url = new URL(window.location.href);
  url.searchParams.set('view', 'materials');
  if (materialId) {
    url.searchParams.set('material', materialId);
  } else {
    url.searchParams.delete('material');
  }
  window.history.replaceState(window.history.state, '', url);
}

function requestErrorMessage(error: unknown) {
  if (!axios.isAxiosError(error)) return '保存失败，请稍后重试。';
  const detail = error.response?.data?.detail;
  return typeof detail === 'string' && detail.trim() ? detail : '保存失败，请稍后重试。';
}

export function MemoirMaterialWorkspace({
  project,
  onUnsavedChange,
}: MemoirMaterialWorkspaceProps) {
  const queryClient = useQueryClient();
  const canEdit = project.can_edit_memoir_materials !== false;
  const [search, setSearch] = useState('');
  const deferredSearch = useDeferredValue(search.trim());
  const [correctionStatus, setCorrectionStatus] = useState<MemoirMaterialCorrectionStatus>('all');
  const [sort, setSort] = useState<MemoirMaterialSort>('chronological');
  const [selectedMaterialId, setSelectedMaterialId] = useState<string | null>(initialMaterialId);
  const [mobileEditorOpen, setMobileEditorOpen] = useState(false);
  const [draft, setDraft] = useState<{ materialId: string; text: string } | null>(null);
  const [advancedToolsOpen, setAdvancedToolsOpen] = useState(false);

  const materialsQuery = useInfiniteQuery({
    queryKey: ['memoirMaterials', project.id, deferredSearch, correctionStatus, sort],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => memoirMaterialEditorApi.list(project.id, {
      q: deferredSearch || undefined,
      correctionStatus,
      sort,
      limit: PAGE_SIZE,
      offset: pageParam,
    }),
    getNextPageParam: (lastPage) => {
      const nextOffset = lastPage.offset + lastPage.items.length;
      return nextOffset < lastPage.total ? nextOffset : undefined;
    },
  });

  const materials = useMemo(
    () => materialsQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [materialsQuery.data?.pages],
  );
  const total = materialsQuery.data?.pages[0]?.total ?? 0;
  const effectiveSelectedMaterialId = selectedMaterialId ?? materials[0]?.id ?? null;
  const selectedFromList = materials.find(
    (material) => material.id === effectiveSelectedMaterialId,
  ) ?? null;

  const materialDetailQuery = useQuery({
    queryKey: ['memoirMaterial', project.id, effectiveSelectedMaterialId],
    queryFn: () => memoirMaterialEditorApi.get(project.id, effectiveSelectedMaterialId as string),
    enabled: Boolean(effectiveSelectedMaterialId && !selectedFromList),
  });
  const selectedMaterial = selectedFromList ?? materialDetailQuery.data ?? null;
  const selectedIndex = selectedMaterial
    ? materials.findIndex((material) => material.id === selectedMaterial.id)
    : -1;
  const nextMaterial = selectedIndex >= 0 ? materials[selectedIndex + 1] ?? null : null;
  const draftText = selectedMaterial && draft?.materialId === selectedMaterial.id
    ? draft.text
    : selectedMaterial?.display_text ?? '';
  const hasUnsavedChanges = Boolean(
    selectedMaterial
    && draft?.materialId === selectedMaterial.id
    && draft.text !== selectedMaterial.display_text,
  );

  const selectWithoutPrompt = useCallback((materialId: string | null, openEditor: boolean) => {
    setSelectedMaterialId(materialId);
    setDraft(null);
    setMobileEditorOpen(openEditor);
    updateMaterialUrl(materialId);
  }, []);

  const confirmDiscard = useCallback(() => (
    !hasUnsavedChanges || window.confirm('当前校正文尚未保存，确定离开吗？')
  ), [hasUnsavedChanges]);

  const selectMaterial = useCallback((materialId: string) => {
    if (materialId === effectiveSelectedMaterialId) {
      if (selectedMaterialId !== materialId) {
        setSelectedMaterialId(materialId);
        updateMaterialUrl(materialId);
      }
      setMobileEditorOpen(true);
      return;
    }
    if (!confirmDiscard()) return;
    selectWithoutPrompt(materialId, true);
  }, [confirmDiscard, effectiveSelectedMaterialId, selectWithoutPrompt, selectedMaterialId]);

  useEffect(() => {
    const handlePopState = () => {
      if (new URL(window.location.href).searchParams.get('view') !== 'materials') return;
      if (!confirmDiscard()) {
        updateMaterialUrl(selectedMaterialId);
        return;
      }
      const materialId = initialMaterialId();
      setSelectedMaterialId(materialId);
      setDraft(null);
      setMobileEditorOpen(Boolean(materialId));
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, [confirmDiscard, selectedMaterialId]);

  useEffect(() => {
    if (!hasUnsavedChanges) return undefined;
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [hasUnsavedChanges]);

  useEffect(() => {
    onUnsavedChange?.(hasUnsavedChanges);
  }, [hasUnsavedChanges, onUnsavedChange]);

  useEffect(() => () => {
    onUnsavedChange?.(false);
  }, [onUnsavedChange]);

  const saveMutation = useMutation({
    mutationFn: ({ materialId, text }: { materialId: string; text: string; nextId: string | null }) =>
      memoirMaterialEditorApi.updateTranscript(project.id, materialId, text),
    onSuccess: (updated, variables) => {
      queryClient.setQueryData(['memoirMaterial', project.id, updated.id], updated);
      queryClient.invalidateQueries({ queryKey: ['memoirMaterials', project.id] });
      queryClient.invalidateQueries({ queryKey: ['memoirBookPlan', project.id] });
      queryClient.invalidateQueries({ queryKey: ['memoirChapterDraft', project.id] });
      queryClient.invalidateQueries({ queryKey: ['memoirBookOutput', project.id] });
      setDraft(null);
      if (variables.nextId) {
        selectWithoutPrompt(variables.nextId, true);
      }
    },
  });

  const canSave = Boolean(
    canEdit
    && selectedMaterial
    && hasUnsavedChanges
    && draftText.trim()
    && !saveMutation.isPending,
  );

  const save = (moveNext: boolean) => {
    if (!selectedMaterial || !canSave) return;
    saveMutation.mutate({
      materialId: selectedMaterial.id,
      text: draftText.trim(),
      nextId: moveNext ? nextMaterial?.id ?? null : null,
    });
  };

  const changeCorrectionStatus = (value: MemoirMaterialCorrectionStatus) => {
    if (value === correctionStatus || !confirmDiscard()) return;
    setCorrectionStatus(value);
    selectWithoutPrompt(null, false);
  };

  const changeSort = (value: MemoirMaterialSort) => {
    if (value === sort || !confirmDiscard()) return;
    setSort(value);
    selectWithoutPrompt(null, false);
  };

  const editorContent = selectedMaterial ? (
    <MemoirTranscriptEditor
      material={selectedMaterial}
      audioUrl={selectedMaterial.audio_file_id
        ? memoirMaterialEditorApi.audioPlaybackUrl(selectedMaterial.audio_file_id)
        : null}
      draftText={draftText}
      canEdit={canEdit}
      hasUnsavedChanges={hasUnsavedChanges}
      isSaving={saveMutation.isPending}
      canSave={canSave}
      hasNext={Boolean(nextMaterial)}
      saveError={
        saveMutation.isError && saveMutation.variables?.materialId === selectedMaterial.id
          ? requestErrorMessage(saveMutation.error)
          : null
      }
      onDraftChange={(text) => {
        saveMutation.reset();
        setDraft({ materialId: selectedMaterial.id, text });
      }}
      onSave={() => save(false)}
      onSaveAndNext={() => save(true)}
      onBack={() => {
        if (!confirmDiscard()) return;
        setDraft(null);
        setMobileEditorOpen(false);
      }}
    />
  ) : materialDetailQuery.isLoading ? (
    <div className="flex h-full items-center justify-center gap-2 text-sm text-[#69736e]">
      <Loader2 size={17} className="animate-spin" />
      正在读取素材
    </div>
  ) : materialDetailQuery.isError ? (
    <div className="flex h-full flex-col items-center justify-center px-6 text-center">
      <p className="text-sm text-[#7c3f3f]">这段素材读取失败</p>
      <button
        type="button"
        onClick={() => materialDetailQuery.refetch()}
        className="mt-3 inline-flex h-10 items-center justify-center gap-2 rounded-md bg-white px-3 text-sm text-[#46504b] shadow-[0_1px_6px_rgba(23,43,35,0.1)] transition-[background-color,transform] hover:bg-[#f3f5f4] active:scale-[0.96]"
      >
        <RefreshCw size={15} />
        重新加载
      </button>
    </div>
  ) : (
    <div className="flex h-full flex-col items-center justify-center px-6 text-center text-[#7a847f]">
      <Headphones size={28} strokeWidth={1.5} aria-hidden="true" />
      <p className="mt-3 text-sm font-medium text-[#46504b]">选择一段素材开始校对</p>
    </div>
  );

  return (
    <div className="relative h-full min-h-0 w-full">
      <div className="grid h-full min-h-0 w-full grid-cols-1 bg-[#f7f8f7] lg:grid-cols-[360px_minmax(0,1fr)]">
        <div className={`${mobileEditorOpen ? 'hidden' : 'min-h-0'} lg:block lg:min-h-0 lg:border-r lg:border-black/6`}>
          <MemoirMaterialList
          projectTitle={project.title}
          ownerName={project.owner_display_name}
          items={materials}
          total={total}
          selectedId={effectiveSelectedMaterialId}
          search={search}
          correctionStatus={correctionStatus}
          sort={sort}
          isLoading={materialsQuery.isLoading}
          isError={materialsQuery.isError}
          hasNextPage={materialsQuery.hasNextPage}
          isFetchingNextPage={materialsQuery.isFetchingNextPage}
          onSearchChange={setSearch}
          onCorrectionStatusChange={changeCorrectionStatus}
          onSortChange={changeSort}
          onSelect={selectMaterial}
          onRetry={() => materialsQuery.refetch()}
            onLoadMore={() => materialsQuery.fetchNextPage()}
            onOpenAdvancedTools={() => setAdvancedToolsOpen(true)}
          />
        </div>
        <div className={`${mobileEditorOpen ? 'min-h-0' : 'hidden'} min-w-0 lg:block lg:min-h-0`}>
          {editorContent}
        </div>
      </div>
      {advancedToolsOpen && (
        <Suspense fallback={(
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 text-sm text-white backdrop-blur-[1px]">
            <Loader2 size={17} className="mr-2 animate-spin" />
            正在打开高级工具
          </div>
        )}>
          <MemoirAdvancedToolsDrawer
            open
            project={project}
            selectedMaterialId={selectedMaterial?.id ?? null}
            onClose={() => setAdvancedToolsOpen(false)}
          />
        </Suspense>
      )}
    </div>
  );
}
