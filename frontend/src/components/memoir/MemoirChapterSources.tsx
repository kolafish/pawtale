import { Search, Volume2, X } from 'lucide-react';
import { useState } from 'react';

import { memoirMaterialEditorApi } from '../../api';
import type { MemoirBookPlanChapter, MemoirMaterialItem } from '../../types';

interface MemoirChapterSourcesProps {
  chapter: MemoirBookPlanChapter;
  materials: MemoirMaterialItem[];
}

export function MemoirChapterSources({ chapter, materials }: MemoirChapterSourcesProps) {
  const [search, setSearch] = useState('');
  const [openAudioId, setOpenAudioId] = useState<string | null>(null);
  const assignedSources = materials.filter(material => material.section_assignment?.chapter_id === chapter.id);
  const uncovered = materials.filter(material => !material.section_assignment);
  const query = search.trim().toLocaleLowerCase('zh-CN');
  const matches = (source: MemoirMaterialItem) => !query || source.display_text.toLocaleLowerCase('zh-CN').includes(query);
  const filteredAssigned = assignedSources.filter(matches);
  const filteredUncovered = uncovered.filter(matches);

  const renderSource = (source: MemoirMaterialItem, index: number) => (
    <SourceItem
      key={source.id}
      source={source}
      index={index}
      audioOpen={openAudioId === source.id}
      onToggleAudio={() => setOpenAudioId(current => current === source.id ? null : source.id)}
    />
  );
  return (
    <section className="flex h-full min-h-0 flex-col bg-[#f7f8f7]">
      <header className="border-b border-black/6 bg-white px-4 py-4 sm:px-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[11px] font-medium text-[#69736e] tabular-nums">第 {chapter.sort_order + 1} 章</p>
            <h2 className="mt-1 text-lg font-semibold text-[#202824]">{chapter.title}</h2>
          </div>
          <div className="flex items-center gap-2 text-xs text-[#69736e]">
            <span className="rounded bg-[#eef4f1] px-2 py-1 text-[#2d705b]">已归入 {assignedSources.length} 段</span>
            <span className="rounded bg-[#f4f1eb] px-2 py-1 text-[#846633]">未归入 {uncovered.length} 段</span>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-xs text-[#69736e]">
          {chapter.sections.map(section => (
            <span key={section.id} className="rounded bg-[#eef0ef] px-2 py-1">
              {section.title} · {section.material_count} 段{section.include_in_book ? '' : ' · 不收入本书'}
            </span>
          ))}
        </div>
        <div className="relative mt-4 max-w-xl">
          <Search size={16} aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[#69736e]" />
          <input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索当前章节或未归入的素材" className="h-10 w-full rounded-md border border-black/8 bg-[#f7f8f7] pl-9 pr-10 text-sm text-[#202824] outline-none focus:border-[#3c7a67]" />
          {search && <button type="button" onClick={() => setSearch('')} aria-label="清除搜索" className="absolute right-1 top-1/2 inline-flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-md text-[#69736e]"><X size={15} /></button>}
        </div>
      </header>
      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto xl:grid-cols-2 xl:overflow-hidden">
        <SourceSection title="本章素材" count={filteredAssigned.length} emptyText={search ? '本章没有匹配的素材' : '本章还没有素材'} className="xl:min-h-0 xl:overflow-y-auto xl:border-r xl:border-black/6">
          {filteredAssigned.map(renderSource)}
        </SourceSection>
        <SourceSection title="未归入目录" count={filteredUncovered.length} emptyText={search ? '没有匹配的未归入素材' : '所有素材都已归入目录'} className="xl:min-h-0 xl:overflow-y-auto">
          {filteredUncovered.map(renderSource)}
        </SourceSection>
      </div>
    </section>
  );
}

function SourceSection({
  title,
  count,
  emptyText,
  className,
  children,
}: {
  title: string;
  count: number;
  emptyText: string;
  className: string;
  children: React.ReactNode;
}) {
  const hasItems = count > 0;
  return (
    <section className={className}>
      <div className="sticky top-0 z-10 flex h-11 items-center border-b border-black/5 bg-[#f1f3f2]/95 px-4 text-xs font-semibold text-[#59635e] backdrop-blur-sm">
        <span>{title}</span>
        <span className="ml-auto font-normal text-[#69736e] tabular-nums">{count}</span>
      </div>
      {hasItems ? (
        <div className="space-y-2 p-3 sm:p-4">{children}</div>
      ) : (
        <p className="px-6 py-12 text-center text-sm text-[#69736e]">{emptyText}</p>
      )}
    </section>
  );
}

function SourceItem({
  source,
  audioOpen,
  onToggleAudio,
  index,
}: {
  source: MemoirMaterialItem;
  audioOpen: boolean;
  onToggleAudio: () => void;
  index: number;
}) {
  return (
    <article className="rounded-md bg-white p-3 shadow-[0_1px_3px_rgba(23,43,35,0.08),0_0_0_1px_rgba(0,0,0,0.04)]">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 inline-flex h-6 min-w-6 items-center justify-center rounded bg-[#eef0ef] px-1 text-[10px] font-medium text-[#69736e] tabular-nums">
          {index + 1}
        </span>
        <p className="min-w-0 flex-1 text-sm leading-6 text-[#37413c] [text-wrap:pretty]">
          {source.display_text || '暂无文字'}
        </p>
      </div>
      {audioOpen && source.audio_file_id && (
        <audio
          controls
          autoPlay
          preload="metadata"
          src={memoirMaterialEditorApi.audioPlaybackUrl(source.audio_file_id)}
          className="mt-3 h-10 w-full"
        />
      )}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-black/5 pt-2">
        <button
          type="button"
          onClick={onToggleAudio}
          disabled={!source.audio_file_id}
          className="inline-flex h-10 items-center justify-center gap-1.5 rounded-md px-2 text-xs text-[#69736e] transition-[background-color,color,transform] hover:bg-black/5 hover:text-[#29322e] active:scale-[0.96] disabled:opacity-35"
        >
          <Volume2 size={14} />
          {audioOpen ? '收起音频' : '听原音'}
        </button>
        <div className="flex min-w-0 flex-wrap items-center justify-end gap-1">
          <span className="text-xs text-[#69736e]">{source.section_assignment?.section_title ?? '未归入小节'}</span>
        </div>
      </div>
    </article>
  );
}
