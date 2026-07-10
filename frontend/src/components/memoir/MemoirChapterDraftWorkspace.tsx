import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BookOpen, Loader2, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { memoirBookEditorApi } from '../../api';
import { useAppStore } from '../../stores/appStore';
import type {
  MemoirBookChapterDraft,
  MemoirBookEditorData,
  Project,
} from '../../types';
import { getErrorMessage } from '../../utils/apiError';
import { MemoirChapterDraftEditor } from './MemoirChapterDraftEditor';
import { MemoirDraftChapterList } from './MemoirDraftChapterList';


interface MemoirChapterDraftWorkspaceProps {
  project: Project;
  onUnsavedChange?: (hasUnsavedChanges: boolean) => void;
  onOpenChapters: () => void;
}

type DraftTextState = { chapterId: string; text: string } | null;

export function MemoirChapterDraftWorkspace({
  project,
  onUnsavedChange,
  onOpenChapters,
}: MemoirChapterDraftWorkspaceProps) {
  const queryClient = useQueryClient();
  const defaultAiModel = useAppStore(state => state.defaultAiModel);
  const canEdit = project.can_edit_memoir_materials !== false;
  const queryKey = useMemo(() => ['memoirBookEditor', project.id] as const, [project.id]);
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(initialChapterId);
  const [draftState, setDraftState] = useState<DraftTextState>(null);
  const [mobileEditorOpen, setMobileEditorOpen] = useState(Boolean(initialChapterId()));

  const editorQuery = useQuery({
    queryKey,
    queryFn: () => memoirBookEditorApi.get(project.id),
  });
  const data = editorQuery.data;
  const chapters = data?.plan?.chapters ?? [];
  const draftsByChapterId = useMemo(
    () => new Map((data?.chapter_drafts ?? []).map(draft => [draft.chapter_id, draft])),
    [data?.chapter_drafts],
  );
  const effectiveChapterId = chapters.some(chapter => chapter.id === selectedChapterId)
    ? selectedChapterId
    : chapters[0]?.id ?? null;
  const selectedIndex = chapters.findIndex(chapter => chapter.id === effectiveChapterId);
  const selectedChapter = selectedIndex >= 0 ? chapters[selectedIndex] : null;
  const selectedDraft = effectiveChapterId
    ? draftsByChapterId.get(effectiveChapterId) ?? null
    : null;
  const latestText = selectedDraft?.latest_version?.content ?? '';
  const draftText = selectedChapter && draftState?.chapterId === selectedChapter.id
    ? draftState.text
    : latestText;
  const hasUnsavedChanges = Boolean(
    selectedChapter
    && draftState?.chapterId === selectedChapter.id
    && draftState.text !== latestText,
  );
  const nextChapter = selectedIndex >= 0 ? chapters[selectedIndex + 1] ?? null : null;

  const confirmDiscard = useCallback(() => (
    !hasUnsavedChanges || window.confirm('当前章节稿尚未保存，确定离开吗？')
  ), [hasUnsavedChanges]);

  const selectWithoutPrompt = useCallback((chapterId: string | null, openEditor: boolean) => {
    setSelectedChapterId(chapterId);
    setDraftState(null);
    setMobileEditorOpen(openEditor);
    updateChapterUrl(chapterId);
  }, []);

  const selectChapter = (chapterId: string) => {
    if (chapterId === effectiveChapterId) {
      setSelectedChapterId(chapterId);
      setMobileEditorOpen(true);
      updateChapterUrl(chapterId);
      return;
    }
    if (!confirmDiscard()) return;
    selectWithoutPrompt(chapterId, true);
  };

  useEffect(() => {
    const handlePopState = () => {
      if (new URL(window.location.href).searchParams.get('view') !== 'drafts') return;
      if (!confirmDiscard()) {
        updateChapterUrl(selectedChapterId);
        return;
      }
      const chapterId = initialChapterId();
      setSelectedChapterId(chapterId);
      setDraftState(null);
      setMobileEditorOpen(Boolean(chapterId));
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, [confirmDiscard, selectedChapterId]);

  useEffect(() => {
    onUnsavedChange?.(hasUnsavedChanges);
  }, [hasUnsavedChanges, onUnsavedChange]);

  useEffect(() => () => {
    onUnsavedChange?.(false);
  }, [onUnsavedChange]);

  useEffect(() => {
    if (!hasUnsavedChanges) return undefined;
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [hasUnsavedChanges]);

  const updateDraftCache = (draft: MemoirBookChapterDraft) => {
    queryClient.setQueryData<MemoirBookEditorData>(queryKey, current => {
      if (!current) return current;
      const exists = current.chapter_drafts.some(item => item.chapter_id === draft.chapter_id);
      return {
        ...current,
        chapter_drafts: exists
          ? current.chapter_drafts.map(item => item.chapter_id === draft.chapter_id ? draft : item)
          : [...current.chapter_drafts, draft],
      };
    });
    setDraftState(null);
  };

  const saveMutation = useMutation({
    mutationFn: ({
      chapterId,
      content,
    }: {
      chapterId: string;
      content: string;
      nextChapterId: string | null;
    }) => memoirBookEditorApi.updateChapterDraft(project.id, chapterId, content),
    onSuccess: (draft, variables) => {
      updateDraftCache(draft);
      if (variables.nextChapterId) selectWithoutPrompt(variables.nextChapterId, true);
    },
  });
  const generateMutation = useMutation({
    mutationFn: ({
      chapterId,
      feedback,
      forceRegenerate,
    }: {
      chapterId: string;
      feedback?: string;
      forceRegenerate: boolean;
    }) => memoirBookEditorApi.generateChapterDraft(project.id, chapterId, {
      aiModel: defaultAiModel,
      feedback,
      forceRegenerate,
    }),
    onSuccess: updateDraftCache,
  });
  const confirmMutation = useMutation({
    mutationFn: (chapterId: string) => memoirBookEditorApi.confirmChapterDraft(project.id, chapterId),
    onSuccess: updateDraftCache,
  });

  const selectedError = [saveMutation, generateMutation, confirmMutation]
    .find(mutation => mutation.isError)?.error;
  const sourceById = useMemo(
    () => new Map((data?.chapter_source_editor.sources ?? []).map(source => [source.utterance_id, source])),
    [data?.chapter_source_editor.sources],
  );
  const selectedSources = selectedChapter?.source_refs
    .map(ref => sourceById.get(ref.utterance_id))
    .filter((source): source is NonNullable<typeof source> => Boolean(source)) ?? [];

  if (editorQuery.isLoading) {
    return <WorkspaceMessage icon={<Loader2 size={20} className="animate-spin" />} text="正在读取章节稿" />;
  }
  if (editorQuery.isError || !data) {
    return (
      <WorkspaceMessage
        icon={<BookOpen size={24} />}
        text="章节稿读取失败"
        action={(
          <button
            type="button"
            onClick={() => editorQuery.refetch()}
            className="mt-4 inline-flex h-10 items-center gap-2 rounded-md bg-white px-3 text-sm text-[#46504b] shadow-[0_1px_6px_rgba(23,43,35,0.1)] transition-[background-color,transform] hover:bg-[#f3f5f4] active:scale-[0.96]"
          >
            <RefreshCw size={15} />
            重新加载
          </button>
        )}
      />
    );
  }
  if (!data.plan || data.plan.status !== 'confirmed' || chapters.length === 0) {
    return (
      <WorkspaceMessage
        icon={<BookOpen size={26} />}
        text={data.plan ? '请先确认章节目录' : '请先建立章节目录'}
        action={(
          <button
            type="button"
            onClick={onOpenChapters}
            className="mt-4 inline-flex h-10 items-center rounded-md bg-[#3c7a67] px-4 text-sm font-medium text-white transition-[background-color,transform] hover:bg-[#326956] active:scale-[0.96]"
          >
            打开章节目录
          </button>
        )}
      />
    );
  }

  return (
    <div className="grid h-full min-h-0 grid-cols-1 bg-[#f7f8f7] lg:grid-cols-[320px_minmax(0,1fr)]">
      <div className={`${mobileEditorOpen ? 'hidden' : 'min-h-0'} lg:block lg:min-h-0 lg:border-r lg:border-black/6`}>
        <MemoirDraftChapterList
          chapters={chapters}
          draftsByChapterId={draftsByChapterId}
          selectedChapterId={effectiveChapterId}
          onSelect={selectChapter}
        />
      </div>
      <div className={`${mobileEditorOpen ? 'min-h-0' : 'hidden'} min-w-0 lg:block lg:min-h-0`}>
        {selectedChapter ? (
          <MemoirChapterDraftEditor
            chapter={selectedChapter}
            draft={selectedDraft}
            sources={selectedSources}
            draftText={draftText}
            canEdit={canEdit}
            hasUnsavedChanges={hasUnsavedChanges}
            isSaving={saveMutation.isPending}
            isGenerating={generateMutation.isPending}
            isConfirming={confirmMutation.isPending}
            hasNext={Boolean(nextChapter)}
            error={selectedError ? getErrorMessage(selectedError, '操作失败，请稍后重试。') : null}
            onDraftChange={text => {
              saveMutation.reset();
              generateMutation.reset();
              confirmMutation.reset();
              setDraftState({ chapterId: selectedChapter.id, text });
            }}
            onGenerate={(feedback, forceRegenerate) => {
              generateMutation.mutate({ chapterId: selectedChapter.id, feedback, forceRegenerate });
            }}
            onSave={() => saveMutation.mutate({
              chapterId: selectedChapter.id,
              content: draftText.trim(),
              nextChapterId: null,
            })}
            onSaveAndNext={() => saveMutation.mutate({
              chapterId: selectedChapter.id,
              content: draftText.trim(),
              nextChapterId: nextChapter?.id ?? null,
            })}
            onConfirm={() => confirmMutation.mutate(selectedChapter.id)}
            onBack={() => {
              if (!confirmDiscard()) return;
              setDraftState(null);
              setMobileEditorOpen(false);
              updateChapterUrl(null);
            }}
          />
        ) : (
          <WorkspaceMessage icon={<BookOpen size={24} />} text="选择一个章节开始编辑" />
        )}
      </div>
    </div>
  );
}

function initialChapterId() {
  if (typeof window === 'undefined') return null;
  return new URL(window.location.href).searchParams.get('chapter');
}

function updateChapterUrl(chapterId: string | null) {
  if (typeof window === 'undefined') return;
  const url = new URL(window.location.href);
  url.searchParams.set('view', 'drafts');
  if (chapterId) url.searchParams.set('chapter', chapterId);
  else url.searchParams.delete('chapter');
  window.history.replaceState(window.history.state, '', url);
}

function WorkspaceMessage({
  icon,
  text,
  action,
}: {
  icon: React.ReactNode;
  text: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center bg-[#f7f8f7] px-6 text-center text-[#7a847f]">
      {icon}
      <p className="mt-3 text-sm font-medium text-[#46504b]">{text}</p>
      {action}
    </div>
  );
}
