import type { MemoirBookPlanChapter } from '../../types';

interface MemoirChapterListProps {
  chapters: MemoirBookPlanChapter[];
  selectedChapterId: string | null;
  onSelect: (chapterId: string) => void;
}

export function MemoirChapterList({ chapters, selectedChapterId, onSelect }: MemoirChapterListProps) {
  return (
    <div className="divide-y divide-black/5">
      {chapters.map((chapter, index) => (
        <button
          key={chapter.id}
          type="button"
          onClick={() => onSelect(chapter.id)}
          aria-current={chapter.id === selectedChapterId ? 'page' : undefined}
          className={`flex min-h-[82px] w-full items-center px-4 py-3 text-left outline-none transition-[background-color,box-shadow] ${
            chapter.id === selectedChapterId
              ? 'bg-[#edf5f1] shadow-[inset_3px_0_0_#3c7a67]'
              : 'bg-white hover:bg-[#fafbfa] focus-visible:shadow-[inset_0_0_0_2px_rgba(60,122,103,0.35)]'
          }`}
        >
          <span className="min-w-0 flex-1">
            <span className="block text-[11px] font-medium text-[#8a948f] tabular-nums">第 {index + 1} 章</span>
            <span className="mt-1 block truncate text-sm font-semibold text-[#29322e]">{chapter.title}</span>
            <span className="mt-1 block text-[11px] text-[#7a847f] tabular-nums">
              {chapter.include_in_book === false ? '不收入本书 · ' : ''}
              {chapter.sections.length} 小节 · {chapter.sections.reduce((count, section) => count + section.material_count, 0)} 段素材
            </span>
          </span>
        </button>
      ))}
    </div>
  );
}
