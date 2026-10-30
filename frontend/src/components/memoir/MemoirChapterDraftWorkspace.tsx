import { useQuery } from '@tanstack/react-query';
import { BookOpen, Loader2, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

import { memoirBookApi, memoirMaterialEditorApi } from '../../api';
import type { MemoirBookPlanChapter, Project } from '../../types';
import { getErrorMessage } from '../../utils/apiError';
import { MemoirChapterDraftEditor } from './MemoirChapterDraftEditor';
import { MemoirDraftChapterList } from './MemoirDraftChapterList';

interface MemoirChapterDraftWorkspaceProps {
  project: Project;
  onOpenChapters: () => void;
  onUnsavedChange?: (dirty: boolean) => void;
}

function initialChapterId() {
  return new URL(window.location.href).searchParams.get('chapter');
}

export function MemoirChapterDraftWorkspace({ project, onOpenChapters, onUnsavedChange }: MemoirChapterDraftWorkspaceProps) {
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(initialChapterId);
  const [mobileEditorOpen, setMobileEditorOpen] = useState(Boolean(initialChapterId()));
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
  const [editingChapter, setEditingChapter] = useState<MemoirBookPlanChapter | null>(null);
  const planQuery = useQuery({
    queryKey: ['memoirBookPlan', project.id],
    queryFn: () => memoirBookApi.getPlan(project.id),
  });
  const materialsQuery = useQuery({
    queryKey: ['memoirMaterials', project.id, 'chapterSources'],
    queryFn: () => memoirMaterialEditorApi.listAll(project.id),
  });
  const chapters = planQuery.data?.chapters ?? [];
  const selectedChapter = editingChapter ?? chapters.find(chapter => chapter.id === selectedChapterId) ?? chapters[0];
  const handleUnsavedChange = useCallback((dirty: boolean) => {
    setHasUnsavedChanges(dirty);
    setEditingChapter(dirty ? selectedChapter ?? null : null);
    onUnsavedChange?.(dirty);
  }, [onUnsavedChange, selectedChapter]);
  const draftQuery = useQuery({
    queryKey: ['memoirChapterDraft', project.id, selectedChapter?.id],
    queryFn: () => memoirBookApi.getChapterDraft(project.id, selectedChapter!.id),
    enabled: Boolean(selectedChapter),
  });

  useEffect(() => {
    const handlePopState = () => {
      if (new URL(window.location.href).searchParams.get('view') !== 'drafts') return;
      if (initialChapterId() === selectedChapter?.id) return;
      if (hasUnsavedChanges && !window.confirm('当前正文尚未保存，确定离开吗？')) {
        const url = new URL(window.location.href);
        if (selectedChapter) url.searchParams.set('chapter', selectedChapter.id);
        window.history.replaceState(window.history.state, '', url);
        return;
      }
      setEditingChapter(null);
      setSelectedChapterId(initialChapterId());
      setMobileEditorOpen(Boolean(initialChapterId()));
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, [hasUnsavedChanges, selectedChapter]);

  const selectChapter = (chapterId: string) => {
    if (chapterId !== selectedChapter?.id && hasUnsavedChanges && !window.confirm('当前正文尚未保存，确定离开吗？')) return;
    setEditingChapter(null);
    setSelectedChapterId(chapterId);
    setMobileEditorOpen(true);
    const url = new URL(window.location.href);
    url.searchParams.set('chapter', chapterId);
    window.history.replaceState(window.history.state, '', url);
  };
  if (planQuery.isLoading || materialsQuery.isLoading) {
    return <div className="flex h-full items-center justify-center gap-2 text-sm text-[#69736e]"><Loader2 size={20} className="animate-spin" />正在读取章节稿</div>;
  }
  if ((planQuery.isError && !planQuery.data) || (materialsQuery.isError && !materialsQuery.data)) {
    return (
      <div className="flex h-full flex-col items-center justify-center text-sm text-[#69736e]">
        <BookOpen size={24} />
        <p className="mt-3">{getErrorMessage(planQuery.error ?? materialsQuery.error, '章节稿读取失败')}</p>
        <button type="button" onClick={() => { planQuery.refetch(); materialsQuery.refetch(); }} className="mt-4 inline-flex h-10 items-center gap-2 rounded-md bg-white px-3"><RefreshCw size={15} />重新加载</button>
      </div>
    );
  }
  if (!selectedChapter) {
    return (
      <div className="flex h-full flex-col items-center justify-center text-sm text-[#69736e]">
        <BookOpen size={26} /><p className="mt-3">还没有章节目录</p>
        <button type="button" onClick={onOpenChapters} className="mt-4 h-10 rounded-md bg-[#3c7a67] px-4 text-white">打开章节目录</button>
      </div>
    );
  }
  const sources = (materialsQuery.data ?? []).filter(material => material.section_assignment?.chapter_id === selectedChapter.id);
  return (
    <div className="grid h-full min-h-0 grid-cols-1 bg-[#f7f8f7] lg:grid-cols-[320px_minmax(0,1fr)]">
      <div className={`${mobileEditorOpen ? 'hidden' : 'min-h-0'} lg:block lg:min-h-0 lg:border-r lg:border-black/6`}>
        <MemoirDraftChapterList chapters={chapters} selectedChapterId={selectedChapter.id} onSelect={selectChapter} />
      </div>
      <div className={`${mobileEditorOpen ? 'min-h-0' : 'hidden'} min-w-0 lg:block lg:min-h-0`}>
        {draftQuery.isLoading ? (
          <div className="flex h-full items-center justify-center gap-2 text-sm text-[#69736e]"><Loader2 size={18} className="animate-spin" />正在读取正文</div>
        ) : draftQuery.isError && !draftQuery.data ? (
          <div className="flex h-full flex-col items-center justify-center text-sm text-[#69736e]">
            <p>{getErrorMessage(draftQuery.error, '章节稿读取失败')}</p>
            <button type="button" onClick={() => draftQuery.refetch()} className="mt-4 h-10 rounded-md bg-white px-3">重新加载</button>
          </div>
        ) : (
          <MemoirChapterDraftEditor key={selectedChapter.id} chapter={selectedChapter} draft={draftQuery.data ?? null} sources={sources} onBack={() => setMobileEditorOpen(false)} canEdit={project.can_edit_memoir_materials !== false && planQuery.data?.status === 'confirmed'} onUnsavedChange={handleUnsavedChange} />
        )}
      </div>
    </div>
  );
}
