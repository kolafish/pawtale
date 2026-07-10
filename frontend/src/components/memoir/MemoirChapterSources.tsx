import {
  BookPlus,
  Plus,
  Search,
  Trash2,
  Volume2,
  X,
} from 'lucide-react';
import { useMemo, useState } from 'react';

import { memoirChapterSourceEditorApi } from '../../api';
import type {
  MemoirChapterSourceEditor,
  MemoirChapterSourceEditorChapter,
  MemoirChapterSourceEditorSource,
} from '../../types';


interface MemoirChapterSourcesProps {
  editor: MemoirChapterSourceEditor;
  chapter: MemoirChapterSourceEditorChapter;
  disabled: boolean;
  onAssign: (utteranceId: string) => void;
  onMove: (utteranceId: string, targetChapterId: string) => void;
  onRemove: (utteranceId: string) => void;
  onCreateChapter: (utteranceId: string) => void;
}

export function MemoirChapterSources({
  editor,
  chapter,
  disabled,
  onAssign,
  onMove,
  onRemove,
  onCreateChapter,
}: MemoirChapterSourcesProps) {
  const [search, setSearch] = useState('');
  const [openAudioId, setOpenAudioId] = useState<string | null>(null);
  const sourceById = useMemo(
    () => new Map(editor.sources.map(source => [source.utterance_id, source])),
    [editor.sources],
  );
  const assignedSources = chapter.source_refs
    .map(ref => sourceById.get(ref.utterance_id))
    .filter((source): source is MemoirChapterSourceEditorSource => Boolean(source));
  const query = search.trim().toLocaleLowerCase('zh-CN');
  const matches = (source: MemoirChapterSourceEditorSource) => (
    !query || source.text.toLocaleLowerCase('zh-CN').includes(query)
  );
  const filteredAssigned = assignedSources.filter(matches);
  const filteredUncovered = editor.uncovered_sources.filter(matches);
  const otherChapters = editor.chapters.filter(item => item.id !== chapter.id);
  const canMoveOrRemove = assignedSources.length > 1;

  return (
    <section className="flex h-full min-h-0 flex-col bg-[#f7f8f7]">
      <header className="border-b border-black/6 bg-white px-4 py-4 sm:px-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[11px] font-medium text-[#7a847f] tabular-nums">
              第 {chapter.sort_order + 1} 章
            </p>
            <h2 className="mt-1 text-lg font-semibold text-[#202824] [text-wrap:balance]">
              {chapter.title}
            </h2>
          </div>
          <div className="flex items-center gap-2 text-xs text-[#69736e]">
            <span className="rounded bg-[#eef4f1] px-2 py-1 text-[#2d705b] tabular-nums">
              已归入 {assignedSources.length} 段
            </span>
            <span className="rounded bg-[#f4f1eb] px-2 py-1 text-[#846633] tabular-nums">
              未归入 {editor.uncovered_source_count} 段
            </span>
          </div>
        </div>

        <div className="relative mt-4 max-w-xl">
          <Search
            size={16}
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[#8a948f]"
          />
          <input
            type="search"
            value={search}
            onChange={event => setSearch(event.target.value)}
            placeholder="搜索当前章节或未归入的素材"
            className="h-10 w-full rounded-md border border-black/8 bg-[#f7f8f7] pl-9 pr-10 text-sm text-[#202824] outline-none transition-[border-color,box-shadow,background-color] placeholder:text-[#9aa39f] focus:border-[#3c7a67] focus:bg-white focus:shadow-[0_0_0_3px_rgba(60,122,103,0.12)]"
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch('')}
              aria-label="清除搜索"
              title="清除搜索"
              className="absolute right-1 top-1/2 inline-flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-md text-[#7a847f] transition-[background-color,color,transform] hover:bg-black/5 hover:text-[#202824] active:scale-[0.96]"
            >
              <X size={15} />
            </button>
          )}
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto xl:grid-cols-2 xl:overflow-hidden">
        <SourceSection
          title="本章素材"
          count={filteredAssigned.length}
          emptyText={search ? '本章没有匹配的素材' : '本章还没有素材'}
          className="xl:min-h-0 xl:overflow-y-auto xl:border-r xl:border-black/6"
        >
          {filteredAssigned.map(source => (
            <SourceItem
              key={source.utterance_id}
              source={source}
              audioOpen={openAudioId === source.utterance_id}
              onToggleAudio={() => setOpenAudioId(current => (
                current === source.utterance_id ? null : source.utterance_id
              ))}
              actions={(
                <>
                  <label className="sr-only" htmlFor={`move-source-${source.utterance_id}`}>
                    移动到其他章节
                  </label>
                  <select
                    id={`move-source-${source.utterance_id}`}
                    value=""
                    disabled={disabled || !canMoveOrRemove || otherChapters.length === 0}
                    onChange={event => {
                      if (event.target.value) onMove(source.utterance_id, event.target.value);
                    }}
                    className="h-9 min-w-0 max-w-36 rounded-md border border-black/8 bg-white px-2 text-xs text-[#59635e] outline-none focus:border-[#3c7a67] disabled:opacity-40"
                    title={canMoveOrRemove ? '移动到其他章节' : '每章至少保留一段素材'}
                  >
                    <option value="">移动到...</option>
                    {otherChapters.map(target => (
                      <option key={target.id} value={target.id}>{target.title}</option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={() => onRemove(source.utterance_id)}
                    disabled={disabled || !canMoveOrRemove}
                    aria-label="移出本章"
                    title={canMoveOrRemove ? '移出本章' : '每章至少保留一段素材'}
                    className="inline-flex h-10 w-10 items-center justify-center rounded-md text-[#8a5a5a] transition-[background-color,color,transform] hover:bg-[#f8eeee] hover:text-[#7c3f3f] active:scale-[0.96] disabled:opacity-30"
                  >
                    <Trash2 size={15} />
                  </button>
                </>
              )}
            />
          ))}
        </SourceSection>

        <SourceSection
          title="未归入目录"
          count={filteredUncovered.length}
          emptyText={search ? '没有匹配的未归入素材' : '所有素材都已归入目录'}
          className="xl:min-h-0 xl:overflow-y-auto"
        >
          {filteredUncovered.map(source => (
            <SourceItem
              key={source.utterance_id}
              source={source}
              audioOpen={openAudioId === source.utterance_id}
              onToggleAudio={() => setOpenAudioId(current => (
                current === source.utterance_id ? null : source.utterance_id
              ))}
              actions={(
                <>
                  <button
                    type="button"
                    onClick={() => onCreateChapter(source.utterance_id)}
                    disabled={disabled}
                    title="以此素材新建章节"
                    className="inline-flex h-10 items-center justify-center gap-1.5 rounded-md px-2 text-xs font-medium text-[#59635e] transition-[background-color,color,transform] hover:bg-black/5 hover:text-[#29322e] active:scale-[0.96] disabled:opacity-40"
                  >
                    <BookPlus size={14} />
                    新建章节
                  </button>
                  <button
                    type="button"
                    onClick={() => onAssign(source.utterance_id)}
                    disabled={disabled}
                    className="inline-flex h-10 items-center justify-center gap-1.5 rounded-md bg-[#3c7a67] px-3 text-xs font-medium text-white transition-[background-color,transform] hover:bg-[#326956] active:scale-[0.96] disabled:opacity-50"
                  >
                    <Plus size={14} />
                    加入本章
                  </button>
                </>
              )}
            />
          ))}
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
        <span className="ml-auto font-normal text-[#8a948f] tabular-nums">{count}</span>
      </div>
      {hasItems ? (
        <div className="space-y-2 p-3 sm:p-4">{children}</div>
      ) : (
        <p className="px-6 py-12 text-center text-sm text-[#8a948f]">{emptyText}</p>
      )}
    </section>
  );
}

function SourceItem({
  source,
  audioOpen,
  onToggleAudio,
  actions,
}: {
  source: MemoirChapterSourceEditorSource;
  audioOpen: boolean;
  onToggleAudio: () => void;
  actions: React.ReactNode;
}) {
  return (
    <article className="rounded-md bg-white p-3 shadow-[0_1px_3px_rgba(23,43,35,0.08),0_0_0_1px_rgba(0,0,0,0.04)]">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 inline-flex h-6 min-w-6 items-center justify-center rounded bg-[#eef0ef] px-1 text-[10px] font-medium text-[#69736e] tabular-nums">
          {source.source_index}
        </span>
        <p className="min-w-0 flex-1 text-sm leading-6 text-[#37413c] [text-wrap:pretty]">
          {source.text || '暂无文字'}
        </p>
      </div>
      {audioOpen && source.audio_file_id && (
        <audio
          controls
          autoPlay
          preload="metadata"
          src={memoirChapterSourceEditorApi.audioPlaybackUrl(source.audio_file_id)}
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
          {actions}
        </div>
      </div>
    </article>
  );
}
