import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import {
  ArrowDown,
  ArrowUp,
  Check,
  GripVertical,
  Pencil,
  Trash2,
  X,
} from 'lucide-react';
import { useState } from 'react';

import type { MemoirChapterSourceEditorChapter } from '../../types';


interface MemoirChapterListProps {
  chapters: MemoirChapterSourceEditorChapter[];
  selectedChapterId: string | null;
  disabled: boolean;
  onSelect: (chapterId: string) => void;
  onMove: (chapterId: string, targetIndex: number) => void;
  onRename: (chapterId: string, title: string) => void;
  onDelete: (chapter: MemoirChapterSourceEditorChapter) => void;
}

export function MemoirChapterList({
  chapters,
  selectedChapterId,
  disabled,
  onSelect,
  onMove,
  onRename,
  onDelete,
}: MemoirChapterListProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [titleDraft, setTitleDraft] = useState('');
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const startEditing = (chapter: MemoirChapterSourceEditorChapter) => {
    setEditingId(chapter.id);
    setTitleDraft(chapter.title);
  };

  const finishEditing = (chapter: MemoirChapterSourceEditorChapter) => {
    const title = titleDraft.trim();
    if (title && title !== chapter.title) onRename(chapter.id, title);
    setEditingId(null);
  };

  const handleDragEnd = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id || disabled) return;
    const targetIndex = chapters.findIndex(chapter => chapter.id === over.id);
    if (targetIndex >= 0) onMove(String(active.id), targetIndex);
  };

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <SortableContext items={chapters.map(chapter => chapter.id)} strategy={verticalListSortingStrategy}>
        <div className="divide-y divide-black/5">
          {chapters.map((chapter, index) => (
            <SortableChapterRow
              key={chapter.id}
              chapter={chapter}
              index={index}
              total={chapters.length}
              selected={chapter.id === selectedChapterId}
              disabled={disabled}
              editing={chapter.id === editingId}
              titleDraft={titleDraft}
              onTitleDraftChange={setTitleDraft}
              onSelect={() => onSelect(chapter.id)}
              onMoveUp={() => onMove(chapter.id, index - 1)}
              onMoveDown={() => onMove(chapter.id, index + 1)}
              onStartEditing={() => startEditing(chapter)}
              onFinishEditing={() => finishEditing(chapter)}
              onCancelEditing={() => setEditingId(null)}
              onDelete={() => onDelete(chapter)}
            />
          ))}
        </div>
      </SortableContext>
    </DndContext>
  );
}

interface SortableChapterRowProps {
  chapter: MemoirChapterSourceEditorChapter;
  index: number;
  total: number;
  selected: boolean;
  disabled: boolean;
  editing: boolean;
  titleDraft: string;
  onTitleDraftChange: (value: string) => void;
  onSelect: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onStartEditing: () => void;
  onFinishEditing: () => void;
  onCancelEditing: () => void;
  onDelete: () => void;
}

function SortableChapterRow({
  chapter,
  index,
  total,
  selected,
  disabled,
  editing,
  titleDraft,
  onTitleDraftChange,
  onSelect,
  onMoveUp,
  onMoveDown,
  onStartEditing,
  onFinishEditing,
  onCancelEditing,
  onDelete,
}: SortableChapterRowProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: chapter.id,
    disabled: disabled || editing,
  });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.55 : 1,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`group relative bg-white transition-[background-color,box-shadow] ${
        selected ? 'z-[1] bg-[#edf5f1] shadow-[inset_3px_0_0_#3c7a67]' : 'hover:bg-[#fafbfa]'
      }`}
    >
      <div className="flex min-h-[82px] items-center gap-1 px-2 py-2">
        <button
          type="button"
          {...attributes}
          {...listeners}
          disabled={disabled || editing}
          aria-label={`拖动第 ${index + 1} 章`}
          title="拖动调整顺序"
          className="inline-flex h-10 w-8 flex-shrink-0 cursor-grab items-center justify-center rounded-md text-[#9aa39f] transition-[background-color,color,transform] hover:bg-black/5 hover:text-[#46504b] active:cursor-grabbing active:scale-[0.96] disabled:cursor-default disabled:opacity-40"
        >
          <GripVertical size={16} />
        </button>

        {editing ? (
          <div className="min-w-0 flex-1 px-1 py-2">
            <input
              value={titleDraft}
              onChange={event => onTitleDraftChange(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') onFinishEditing();
                if (event.key === 'Escape') onCancelEditing();
              }}
              maxLength={80}
              autoFocus
              className="h-9 w-full rounded-md border border-[#3c7a67] bg-white px-2 text-sm font-medium text-[#202824] outline-none shadow-[0_0_0_3px_rgba(60,122,103,0.12)]"
            />
          </div>
        ) : (
          <button
            type="button"
            onClick={onSelect}
            className="min-w-0 flex-1 rounded-md px-1 py-2 text-left outline-none focus-visible:shadow-[0_0_0_2px_rgba(60,122,103,0.35)]"
          >
            <span className="block text-[11px] font-medium text-[#8a948f] tabular-nums">
              第 {index + 1} 章
            </span>
            <span className="mt-1 block truncate text-sm font-semibold text-[#29322e]">
              {chapter.title}
            </span>
            <span className="mt-1 block text-[11px] text-[#7a847f] tabular-nums">
              {chapter.source_refs.length} 段素材
              {chapter.pending_source_count > 0 ? ` · ${chapter.pending_source_count} 段待补写` : ''}
            </span>
          </button>
        )}

        <div className={`flex flex-shrink-0 items-center ${editing ? '' : 'lg:opacity-0 lg:transition-opacity lg:group-hover:opacity-100 lg:group-focus-within:opacity-100'}`}>
          {editing ? (
            <>
              <IconButton label="保存标题" onClick={onFinishEditing} disabled={!titleDraft.trim() || disabled}>
                <Check size={15} />
              </IconButton>
              <IconButton label="取消修改" onClick={onCancelEditing}>
                <X size={15} />
              </IconButton>
            </>
          ) : (
            <>
              <IconButton label="上移章节" onClick={onMoveUp} disabled={disabled || index === 0}>
                <ArrowUp size={15} />
              </IconButton>
              <IconButton label="下移章节" onClick={onMoveDown} disabled={disabled || index === total - 1}>
                <ArrowDown size={15} />
              </IconButton>
              <IconButton label="修改标题" onClick={onStartEditing} disabled={disabled}>
                <Pencil size={14} />
              </IconButton>
              <IconButton label="删除章节" onClick={onDelete} disabled={disabled || total <= 1} danger>
                <Trash2 size={14} />
              </IconButton>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function IconButton({
  label,
  children,
  onClick,
  disabled = false,
  danger = false,
}: {
  label: string;
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex h-10 w-8 items-center justify-center rounded-md transition-[background-color,color,transform] active:scale-[0.96] disabled:opacity-30 ${
        danger
          ? 'text-[#8a5a5a] hover:bg-[#f8eeee] hover:text-[#7c3f3f]'
          : 'text-[#7a847f] hover:bg-black/5 hover:text-[#29322e]'
      }`}
    >
      {children}
    </button>
  );
}
