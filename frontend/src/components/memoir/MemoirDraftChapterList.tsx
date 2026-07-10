import { CheckCircle2, CircleDashed, FileText } from 'lucide-react';

import type {
  MemoirBookChapterDraft,
  MemoirChapterSourceEditorChapter,
} from '../../types';


interface MemoirDraftChapterListProps {
  chapters: MemoirChapterSourceEditorChapter[];
  draftsByChapterId: Map<string, MemoirBookChapterDraft>;
  selectedChapterId: string | null;
  onSelect: (chapterId: string) => void;
}

export function MemoirDraftChapterList({
  chapters,
  draftsByChapterId,
  selectedChapterId,
  onSelect,
}: MemoirDraftChapterListProps) {
  return (
    <aside className="flex h-full min-h-0 flex-col bg-white">
      <div className="border-b border-black/6 px-4 py-4">
        <h1 className="text-base font-semibold text-[#202824]">章节稿</h1>
        <p className="mt-1 text-xs text-[#7a847f] tabular-nums">
          已确认 {chapters.filter(chapter => draftsByChapterId.get(chapter.id)?.status === 'confirmed').length}/{chapters.length} 章
        </p>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto divide-y divide-black/5">
        {chapters.map((chapter, index) => {
          const draft = draftsByChapterId.get(chapter.id);
          const selected = chapter.id === selectedChapterId;
          const pendingCount = draft?.pending_source_count ?? chapter.pending_source_count;
          const status = draft?.status ?? null;
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
              <span className={`mt-0.5 inline-flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md ${
                status === 'confirmed'
                  ? 'bg-[#e5f1eb] text-[#2d705b]'
                  : draft
                    ? 'bg-[#f4efe5] text-[#8a682f]'
                    : 'bg-[#eef0ef] text-[#8a948f]'
              }`}>
                {status === 'confirmed' ? (
                  <CheckCircle2 size={16} />
                ) : draft ? (
                  <FileText size={16} />
                ) : (
                  <CircleDashed size={16} />
                )}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[11px] font-medium text-[#8a948f] tabular-nums">
                  第 {index + 1} 章
                </span>
                <span className="mt-1 block truncate text-sm font-semibold text-[#29322e]">
                  {chapter.title}
                </span>
                <span className="mt-1 block text-[11px] text-[#7a847f]">
                  {status === 'confirmed' ? '已确认' : draft ? '编辑中' : '未生成'}
                  {pendingCount > 0 ? ` · ${pendingCount} 段素材待补写` : ''}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </aside>
  );
}
