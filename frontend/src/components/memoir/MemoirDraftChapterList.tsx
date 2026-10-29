import { FileText } from 'lucide-react';
import type { MemoirBookPlanChapter } from '../../types';

interface MemoirDraftChapterListProps {
  chapters: MemoirBookPlanChapter[];
  selectedChapterId: string | null;
  onSelect: (chapterId: string) => void;
}

export function MemoirDraftChapterList({
  chapters,
  selectedChapterId,
  onSelect,
}: MemoirDraftChapterListProps) {
  return (
    <aside className="flex h-full min-h-0 flex-col bg-white">
      <div className="border-b border-black/6 px-4 py-4">
        <h1 className="text-base font-semibold text-[#202824]">章节稿</h1>
        <p className="mt-1 text-xs text-[#69736e] tabular-nums">
          共 {chapters.length} 章
        </p>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto divide-y divide-black/5">
        {chapters.map((chapter, index) => {
          const selected = chapter.id === selectedChapterId;
          return (
            <button
              key={chapter.id}
              type="button"
              onClick={() => onSelect(chapter.id)}
              aria-current={selected ? 'page' : undefined}
              className={`group flex min-h-[86px] w-full items-start gap-3 px-4 py-3 text-left outline-none transition-[background-color,box-shadow,transform] active:scale-[0.995] ${
                selected
                  ? 'z-[1] bg-[#edf5f1] shadow-[inset_3px_0_0_#3c7a67]'
                  : 'hover:bg-[#fafbfa] focus-visible:shadow-[inset_0_0_0_2px_rgba(60,122,103,0.35)]'
              }`}
            >
              <span className="mt-0.5 inline-flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md bg-[#eef0ef] text-[#69736e]">
                <FileText size={16} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[11px] font-medium text-[#69736e] tabular-nums">
                  第 {index + 1} 章
                </span>
                <span className="mt-1 block truncate text-sm font-semibold text-[#29322e]">
                  {chapter.title}
                </span>
                <span className="mt-1 block text-[11px] text-[#69736e]">
                  {chapter.sections.length} 小节
                  {chapter.include_in_book === false ? ' · 不收入本书' : ''}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </aside>
  );
}
