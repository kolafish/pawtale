import { useQuery } from '@tanstack/react-query';
import { BookOpen, Loader2, RefreshCw } from 'lucide-react';
import { useState } from 'react';

import { memoirBookApi, memoirMaterialEditorApi } from '../../api';
import type { Project } from '../../types';
import { getErrorMessage } from '../../utils/apiError';
import { MemoirChapterList } from './MemoirChapterList';
import { MemoirChapterSources } from './MemoirChapterSources';

export function MemoirChapterWorkspace({ project }: { project: Project }) {
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(null);
  const planQuery = useQuery({
    queryKey: ['memoirBookPlan', project.id],
    queryFn: () => memoirBookApi.getPlan(project.id),
  });
  const materialsQuery = useQuery({
    queryKey: ['memoirMaterials', project.id, 'chapterSources'],
    queryFn: () => memoirMaterialEditorApi.listAll(project.id),
  });
  const plan = planQuery.data;
  const materials = materialsQuery.data ?? [];
  const chapters = plan?.chapters ?? [];
  const selectedChapter = chapters.find(chapter => chapter.id === selectedChapterId) ?? chapters[0];
  const uncoveredCount = materials.filter(material => !material.section_assignment).length;

  if (planQuery.isLoading || materialsQuery.isLoading) {
    return <div className="flex h-full items-center justify-center gap-2 text-sm text-[#69736e]"><Loader2 size={20} className="animate-spin" />正在读取章节目录</div>;
  }
  if (planQuery.isError || materialsQuery.isError) {
    return (
      <div className="flex h-full flex-col items-center justify-center bg-[#f7f8f7] px-6 text-center text-[#69736e]">
        <BookOpen size={24} />
        <p className="mt-3 text-sm">{getErrorMessage(planQuery.error ?? materialsQuery.error, '章节目录读取失败')}</p>
        <button type="button" onClick={() => { planQuery.refetch(); materialsQuery.refetch(); }} className="mt-4 inline-flex h-10 items-center gap-2 rounded-md bg-white px-3 text-sm text-[#46504b]">
          <RefreshCw size={15} />重新加载
        </button>
      </div>
    );
  }
  if (!plan || !selectedChapter) {
    return (
      <div className="flex h-full flex-col items-center justify-center bg-[#f7f8f7] px-6 text-center">
        <BookOpen size={24} className="text-[#69736e]" />
        <h1 className="mt-5 text-xl font-semibold text-[#202824]">还没有章节目录</h1>
        <p className="mt-2 text-sm text-[#69736e]">当前共有 {materials.length} 段访谈素材，可在素材校对中查看。</p>
      </div>
    );
  }
  return (
    <div className="flex h-full min-h-0 flex-col bg-[#f7f8f7]">
      <div className="flex min-h-14 flex-wrap items-center gap-3 border-b border-black/6 bg-white px-4 py-2 sm:px-5">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-base font-semibold text-[#202824]">章节目录</h1>
            <span className="rounded bg-[#eef0ef] px-2 py-0.5 text-[11px] text-[#69736e]">{plan.status === 'confirmed' ? '已确认' : '草稿'}</span>
          </div>
          <p className="mt-0.5 truncate text-xs text-[#69736e]">{plan.title || project.title} · {chapters.length} 章 · 已归入 {materials.length - uncoveredCount}/{materials.length} 段</p>
        </div>
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto lg:grid-cols-[360px_minmax(0,1fr)] lg:overflow-hidden">
        <aside className="max-h-[42vh] min-h-[260px] overflow-y-auto border-b border-black/6 bg-white lg:max-h-none lg:min-h-0 lg:border-b-0 lg:border-r">
          <MemoirChapterList chapters={chapters} selectedChapterId={selectedChapter.id} onSelect={setSelectedChapterId} />
        </aside>
        <div className="min-h-[520px] min-w-0 lg:min-h-0">
          <MemoirChapterSources chapter={selectedChapter} materials={materials} />
        </div>
      </div>
    </div>
  );
}
