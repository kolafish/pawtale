import { useState } from 'react';
import {
  ChevronDown, ChevronRight, FileText, FolderPlus, FilePlus,
  GripVertical, User, Map, StickyNote, Link2, Plus, Trash2, Sparkles,
} from 'lucide-react';
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  closestCenter,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import { SortableContext, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { Project, DocumentMeta, ReferenceItemBrief } from '../types';
import { getGroupLabel, getDocumentLabel } from '../utils/numberUtils';

interface BinderProps {
  project: Project;
  currentDocumentId: string | null;
  currentReferenceId: string | null;
  onSelectDocument: (docId: string) => void;
  onSelectReference: (refId: string) => void;
  onCreateGroup: (name: string) => void;
  onRenameGroup: (groupId: string, name: string) => void;
  onDeleteGroup: (groupId: string) => void;
  onCreateDocument: (groupId: string) => void;
  onRenameDocument: (docId: string, name: string) => void;
  onDeleteDocument: (docId: string) => void;
  onReorderGroups: (items: { id: string; sort_order: number }[]) => void;
  onReorderDocuments: (items: { id: string; group_id: string; sort_order: number }[]) => void;
  onCreateReference: (type: string) => void;
  onRenameReference: (refId: string, name: string) => void;
  onDeleteReference: (refId: string) => void;
  onAnalyzeProject: () => void;
}

const STATUS_COLORS: Record<string, string> = {
  draft: 'bg-gray-300',
  revision: 'bg-yellow-400',
  done: 'bg-green-400',
};

const SECTION_CONFIG = [
  { key: 'manuscript', label: '手稿', icon: FileText },
  { key: 'character', label: '人物', icon: User },
  { key: 'outline', label: '大纲', icon: Map },
  { key: 'notes', label: '设定', icon: StickyNote },
] as const;

type SectionKey = typeof SECTION_CONFIG[number]['key'];

// ── SortableGroup (unchanged) ──────────────────────

interface SortableGroupProps {
  group: { id: string; name: string; documents: DocumentMeta[] };
  groupIndex: number;
  isCollapsed: boolean;
  isEditing: boolean;
  editValue: string;
  currentDocumentId: string | null;
  editingDocId: string | null;
  docEditValue: string;
  onToggle: () => void;
  onStartRename: (name: string) => void;
  onCommitRename: () => void;
  onSetEditValue: (value: string) => void;
  onCancelEdit: () => void;
  onContextMenu: (e: React.MouseEvent) => void;
  onCreateDocument: () => void;
  onSelectDocument: (docId: string) => void;
  onDocContextMenu: (e: React.MouseEvent, docId: string) => void;
  onDocStartRename: (docId: string, name: string) => void;
  onDocCommitRename: (docId: string) => void;
  onDocSetEditValue: (value: string) => void;
  onDocCancelEdit: () => void;
}

function SortableGroup({
  group, groupIndex, isCollapsed, isEditing, editValue,
  currentDocumentId, editingDocId, docEditValue,
  onToggle, onStartRename, onCommitRename, onSetEditValue, onCancelEdit,
  onContextMenu, onCreateDocument, onSelectDocument,
  onDocContextMenu, onDocStartRename, onDocCommitRename, onDocSetEditValue, onDocCancelEdit,
}: SortableGroupProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: group.id,
    data: { type: 'group' },
  });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  return (
    <div ref={setNodeRef} style={style}>
      <div
        className="flex items-center gap-1 px-2 py-1.5 hover:bg-gray-100 cursor-pointer group/ghdr"
        onContextMenu={onContextMenu}
      >
        <div {...attributes} {...listeners} className="cursor-grab active:cursor-grabbing">
          <GripVertical size={14} className="text-gray-300" />
        </div>
        <div onClick={onToggle} className="flex items-center gap-1 flex-1 min-w-0">
          {isCollapsed ? (
            <ChevronRight size={14} className="text-gray-400 flex-shrink-0" />
          ) : (
            <ChevronDown size={14} className="text-gray-400 flex-shrink-0" />
          )}
          {isEditing ? (
            <input
              value={editValue}
              onChange={(e) => onSetEditValue(e.target.value)}
              onBlur={onCommitRename}
              onKeyDown={(e) => {
                if (e.key === 'Enter') onCommitRename();
                if (e.key === 'Escape') onCancelEdit();
              }}
              autoFocus
              className="flex-1 text-sm px-1 py-0 border border-pink-300 rounded outline-none bg-white"
              onClick={(e) => e.stopPropagation()}
            />
          ) : (
            <span
              className="flex-1 text-sm font-medium text-gray-600 truncate"
              onDoubleClick={(e) => { e.stopPropagation(); onStartRename(group.name); }}
            >
              {getGroupLabel(groupIndex)} {group.name}
            </span>
          )}
        </div>
        <button
          onClick={(e) => { e.stopPropagation(); onCreateDocument(); }}
          className="opacity-0 group-hover/ghdr:opacity-100 p-0.5 rounded hover:bg-gray-200 text-gray-400"
          title="新建文档"
        >
          <FilePlus size={12} />
        </button>
      </div>

      {!isCollapsed && (
        <SortableContext items={(group.documents || []).map((d) => d.id)} strategy={verticalListSortingStrategy}>
          <div>
            {(group.documents || []).map((doc, docIndex) => (
              <SortableDocument
                key={doc.id}
                doc={doc}
                docIndex={docIndex}
                isSelected={doc.id === currentDocumentId}
                isEditing={editingDocId === doc.id}
                editValue={docEditValue}
                onSelect={() => onSelectDocument(doc.id)}
                onContextMenu={(e) => onDocContextMenu(e, doc.id)}
                onStartRename={() => onDocStartRename(doc.id, doc.name)}
                onCommitRename={() => onDocCommitRename(doc.id)}
                onSetEditValue={onDocSetEditValue}
                onCancelEdit={onDocCancelEdit}
              />
            ))}
            {(group.documents || []).length === 0 && (
              <div
                onClick={onCreateDocument}
                className="pl-7 pr-2 py-2 text-xs text-gray-400 hover:text-pink-500 cursor-pointer"
              >
                + 新建文档
              </div>
            )}
          </div>
        </SortableContext>
      )}
    </div>
  );
}

// ── SortableDocument (unchanged) ───────────────────

interface SortableDocumentProps {
  doc: DocumentMeta;
  docIndex: number;
  isSelected: boolean;
  isEditing: boolean;
  editValue: string;
  onSelect: () => void;
  onContextMenu: (e: React.MouseEvent) => void;
  onStartRename: () => void;
  onCommitRename: () => void;
  onSetEditValue: (value: string) => void;
  onCancelEdit: () => void;
}

function SortableDocument({
  doc, docIndex, isSelected, isEditing, editValue,
  onSelect, onContextMenu, onStartRename, onCommitRename, onSetEditValue, onCancelEdit,
}: SortableDocumentProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: doc.id,
    data: { type: 'document', groupId: doc.group_id },
  });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      onClick={onSelect}
      onContextMenu={onContextMenu}
      onDoubleClick={onStartRename}
      className={`flex items-center gap-2 pl-7 pr-2 py-1.5 cursor-pointer group/doc ${
        isSelected ? 'bg-pink-50 text-pink-700' : 'text-gray-600 hover:bg-gray-100'
      }`}
    >
      <div {...attributes} {...listeners} className="cursor-grab active:cursor-grabbing">
        <GripVertical size={12} className="text-gray-300" />
      </div>
      <FileText size={13} className="flex-shrink-0 opacity-50" />
      {isEditing ? (
        <input
          value={editValue}
          onChange={(e) => onSetEditValue(e.target.value)}
          onBlur={onCommitRename}
          onKeyDown={(e) => {
            if (e.key === 'Enter') onCommitRename();
            if (e.key === 'Escape') onCancelEdit();
          }}
          autoFocus
          className="flex-1 text-sm px-1 py-0 border border-pink-300 rounded outline-none bg-white"
          onClick={(e) => e.stopPropagation()}
        />
      ) : (
        <span className="flex-1 text-sm truncate">
          {getDocumentLabel(docIndex)} {doc.name}
        </span>
      )}
      <div className={`w-2 h-2 rounded-full flex-shrink-0 ${STATUS_COLORS[doc.status] || 'bg-gray-300'}`} />
      <span className="text-[10px] text-gray-400 flex-shrink-0 w-8 text-right">
        {doc.word_count > 0 ? (doc.word_count >= 1000 ? `${(doc.word_count / 1000).toFixed(1)}k` : doc.word_count) : ''}
      </span>
    </div>
  );
}

// ── Reference Section Item ─────────────────────────

function ReferenceRow({
  ref_item,
  isSelected,
  isEditing,
  editValue,
  onSelect,
  onStartRename,
  onCommitRename,
  onSetEditValue,
  onCancelEdit,
  onDelete,
}: {
  ref_item: ReferenceItemBrief;
  isSelected: boolean;
  isEditing: boolean;
  editValue: string;
  onSelect: () => void;
  onStartRename: () => void;
  onCommitRename: () => void;
  onSetEditValue: (v: string) => void;
  onCancelEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      onClick={onSelect}
      onDoubleClick={onStartRename}
      className={`flex items-center gap-2 pl-5 pr-2 py-1.5 cursor-pointer group/ref ${
        isSelected ? 'bg-pink-50 text-pink-700' : 'text-gray-600 hover:bg-gray-100'
      }`}
    >
      {isEditing ? (
        <input
          value={editValue}
          onChange={(e) => onSetEditValue(e.target.value)}
          onBlur={onCommitRename}
          onKeyDown={(e) => {
            if (e.key === 'Enter') onCommitRename();
            if (e.key === 'Escape') onCancelEdit();
          }}
          autoFocus
          className="flex-1 text-sm px-1 py-0 border border-pink-300 rounded outline-none bg-white"
          onClick={(e) => e.stopPropagation()}
        />
      ) : (
        <span className="flex-1 text-sm truncate">{ref_item.name}</span>
      )}
      {ref_item.global_id && (
        <Link2 size={10} className="text-blue-400 flex-shrink-0" />
      )}
      <button
        onClick={(e) => { e.stopPropagation(); onDelete(); }}
        className="opacity-0 group-hover/ref:opacity-100 p-0.5 text-gray-400 hover:text-red-500"
      >
        <Trash2 size={11} />
      </button>
    </div>
  );
}

// ── Main Binder ────────────────────────────────────

export function Binder({
  project,
  currentDocumentId,
  currentReferenceId,
  onSelectDocument,
  onSelectReference,
  onCreateGroup,
  onRenameGroup,
  onDeleteGroup,
  onCreateDocument,
  onRenameDocument,
  onDeleteDocument,
  onReorderGroups,
  onReorderDocuments,
  onCreateReference,
  onRenameReference,
  onDeleteReference,
  onAnalyzeProject,
}: BinderProps) {
  const [collapsedSections, setCollapsedSections] = useState<Set<SectionKey>>(new Set());
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const [contextMenu, setContextMenu] = useState<{ id: string; type: 'group' | 'doc'; x: number; y: number } | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } })
  );

  if (!project || !project.groups) {
    return (
      <div className="w-[250px] flex-shrink-0 h-full flex flex-col bg-gray-50 border-r border-gray-200">
        <div className="px-3 py-2 border-b border-gray-200">
          <span className="text-sm font-semibold text-gray-700">加载中...</span>
        </div>
      </div>
    );
  }

  // Group references by section
  const refs = project.references || [];
  const characterRefs = refs.filter((r) => r.type === 'character');
  const outlineRefs = refs.filter((r) => r.type === 'outline');
  const noteRefs = refs.filter((r) => r.type === 'location' || r.type === 'note');

  const toggleSection = (key: SectionKey) => {
    setCollapsedSections((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const toggleGroup = (gid: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(gid)) next.delete(gid);
      else next.add(gid);
      return next;
    });
  };

  const startRename = (id: string, currentName: string) => {
    setEditingId(id);
    setEditValue(currentName);
    setContextMenu(null);
  };

  const commitRename = (id: string, type: 'group' | 'doc') => {
    const trimmed = editValue.trim();
    if (trimmed) {
      if (type === 'group') onRenameGroup(id, trimmed);
      else onRenameDocument(id, trimmed);
    }
    setEditingId(null);
  };

  const commitRefRename = (refId: string) => {
    const trimmed = editValue.trim();
    if (trimmed) onRenameReference(refId, trimmed);
    setEditingId(null);
  };

  const handleContextMenu = (e: React.MouseEvent, id: string, type: 'group' | 'doc') => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({ id, type, x: e.clientX, y: e.clientY });
  };

  const handleDragStart = (event: DragStartEvent) => {
    setActiveId(event.active.id as string);
  };

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    setActiveId(null);

    if (!over || active.id === over.id) return;

    const activeData = active.data.current;
    const overData = over.data.current;

    if (activeData?.type === 'group' && overData?.type === 'group') {
      const oldIndex = project.groups.findIndex((g) => g.id === active.id);
      const newIndex = project.groups.findIndex((g) => g.id === over.id);
      if (oldIndex === -1 || newIndex === -1) return;
      const reordered = [...project.groups];
      const [moved] = reordered.splice(oldIndex, 1);
      reordered.splice(newIndex, 0, moved);
      onReorderGroups(reordered.map((g, idx) => ({ id: g.id, sort_order: idx })));
    }

    if (activeData?.type === 'document') {
      const sourceGroupId = activeData.groupId as string;
      const targetGroupId = overData?.type === 'document' ? (overData.groupId as string) : (over.id as string);
      const sourceGroup = project.groups.find((g) => g.id === sourceGroupId);
      const targetGroup = project.groups.find((g) => g.id === targetGroupId);
      if (!sourceGroup || !targetGroup) return;
      if (!sourceGroup.documents || !targetGroup.documents) return;
      const activeDoc = sourceGroup.documents.find((d) => d.id === active.id);
      if (!activeDoc) return;

      if (sourceGroupId === targetGroupId) {
        const oldIndex = sourceGroup.documents.findIndex((d) => d.id === active.id);
        const newIndex = sourceGroup.documents.findIndex((d) => d.id === over.id);
        if (oldIndex === -1 || newIndex === -1) return;
        const reordered = [...sourceGroup.documents];
        const [moved] = reordered.splice(oldIndex, 1);
        reordered.splice(newIndex, 0, moved);
        onReorderDocuments(reordered.map((d, idx) => ({ id: d.id, group_id: targetGroupId, sort_order: idx })));
      } else {
        const newDocs = [...targetGroup.documents];
        const insertIndex = overData?.type === 'document'
          ? newDocs.findIndex((d) => d.id === over.id)
          : newDocs.length;
        newDocs.splice(insertIndex >= 0 ? insertIndex : newDocs.length, 0, { ...activeDoc, group_id: targetGroupId });
        const allUpdates = [
          ...sourceGroup.documents
            .filter((d) => d.id !== active.id)
            .map((d, idx) => ({ id: d.id, group_id: sourceGroupId, sort_order: idx })),
          ...newDocs.map((d, idx) => ({ id: d.id, group_id: targetGroupId, sort_order: idx })),
        ];
        onReorderDocuments(allUpdates);
      }
    }
  };

  // Render a reference section
  const renderRefSection = (
    sectionKey: SectionKey,
    label: string,
    Icon: typeof User,
    items: ReferenceItemBrief[],
    createTypes: string[],
  ) => {
    const isCollapsed = collapsedSections.has(sectionKey);
    return (
      <div key={sectionKey}>
        <div
          className="flex items-center gap-1.5 px-2 py-1.5 hover:bg-gray-100 cursor-pointer group/sec"
          onClick={() => toggleSection(sectionKey)}
        >
          {isCollapsed ? (
            <ChevronRight size={14} className="text-gray-400" />
          ) : (
            <ChevronDown size={14} className="text-gray-400" />
          )}
          <Icon size={13} className="text-gray-400" />
          <span className="flex-1 text-xs font-semibold text-gray-500 uppercase tracking-wider">{label}</span>
          <span className="text-[10px] text-gray-400 mr-0.5">{items.length}</span>
          {createTypes.length === 1 ? (
            <button
              onClick={(e) => { e.stopPropagation(); onCreateReference(createTypes[0]); }}
              className="opacity-0 group-hover/sec:opacity-100 p-0.5 rounded hover:bg-gray-200 text-gray-400"
            >
              <Plus size={12} />
            </button>
          ) : (
            <div className="relative opacity-0 group-hover/sec:opacity-100">
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  // For notes section: show dropdown or just create 'note' by default
                  onCreateReference('note');
                }}
                className="p-0.5 rounded hover:bg-gray-200 text-gray-400"
              >
                <Plus size={12} />
              </button>
            </div>
          )}
        </div>
        {!isCollapsed && (
          <div>
            {items.map((ref_item) => (
              <ReferenceRow
                key={ref_item.id}
                ref_item={ref_item}
                isSelected={ref_item.id === currentReferenceId}
                isEditing={editingId === ref_item.id}
                editValue={editValue}
                onSelect={() => onSelectReference(ref_item.id)}
                onStartRename={() => startRename(ref_item.id, ref_item.name)}
                onCommitRename={() => commitRefRename(ref_item.id)}
                onSetEditValue={setEditValue}
                onCancelEdit={() => setEditingId(null)}
                onDelete={() => onDeleteReference(ref_item.id)}
              />
            ))}
            {items.length === 0 && (
              <div
                onClick={() => onCreateReference(createTypes[0])}
                className="pl-5 pr-2 py-2 text-xs text-gray-400 hover:text-pink-500 cursor-pointer"
              >
                + 新建{label}
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
    >
      <div
        className="w-[250px] flex-shrink-0 h-full flex flex-col bg-gray-50 border-r border-gray-200 select-none"
        onClick={() => setContextMenu(null)}
      >
        {/* Header */}
        <div className="px-3 py-2 border-b border-gray-200 flex items-center justify-between">
          <span className="text-sm font-semibold text-gray-700 truncate">{project.title}</span>
          <div className="flex items-center gap-1">
            <button
              onClick={onAnalyzeProject}
              className="p-1 rounded hover:bg-purple-100 text-gray-400 hover:text-purple-500"
              title="AI 分析项目"
            >
              <Sparkles size={14} />
            </button>
            <button
              onClick={() => {
                const nextIndex = project.groups.length;
                const defaultName = getGroupLabel(nextIndex).replace('第', '').replace('卷', '');
                const name = prompt('分组名称', defaultName);
                if (name?.trim()) onCreateGroup(name.trim());
              }}
              className="p-1 rounded hover:bg-gray-200 text-gray-500"
              title="新建分组"
            >
              <FolderPlus size={14} />
            </button>
          </div>
        </div>

        {/* Sections */}
        <div className="flex-1 overflow-y-auto py-1">
          {/* Manuscript Section */}
          <div>
            <div
              className="flex items-center gap-1.5 px-2 py-1.5 hover:bg-gray-100 cursor-pointer"
              onClick={() => toggleSection('manuscript')}
            >
              {collapsedSections.has('manuscript') ? (
                <ChevronRight size={14} className="text-gray-400" />
              ) : (
                <ChevronDown size={14} className="text-gray-400" />
              )}
              <FileText size={13} className="text-gray-400" />
              <span className="flex-1 text-xs font-semibold text-gray-500 uppercase tracking-wider">手稿</span>
            </div>
            {!collapsedSections.has('manuscript') && (
              <SortableContext items={project.groups.map((g) => g.id)} strategy={verticalListSortingStrategy}>
                {project.groups.map((group, groupIndex) => (
                  <SortableGroup
                    key={group.id}
                    group={group}
                    groupIndex={groupIndex}
                    isCollapsed={collapsedGroups.has(group.id)}
                    isEditing={editingId === group.id}
                    editValue={editValue}
                    currentDocumentId={currentDocumentId}
                    editingDocId={editingId}
                    docEditValue={editValue}
                    onToggle={() => toggleGroup(group.id)}
                    onStartRename={(name) => startRename(group.id, name)}
                    onCommitRename={() => commitRename(group.id, 'group')}
                    onSetEditValue={setEditValue}
                    onCancelEdit={() => setEditingId(null)}
                    onContextMenu={(e) => handleContextMenu(e, group.id, 'group')}
                    onCreateDocument={() => onCreateDocument(group.id)}
                    onSelectDocument={onSelectDocument}
                    onDocContextMenu={(e, docId) => handleContextMenu(e, docId, 'doc')}
                    onDocStartRename={(docId, name) => startRename(docId, name)}
                    onDocCommitRename={(docId) => commitRename(docId, 'doc')}
                    onDocSetEditValue={setEditValue}
                    onDocCancelEdit={() => setEditingId(null)}
                  />
                ))}
              </SortableContext>
            )}
          </div>

          {/* Divider */}
          <div className="mx-2 my-1 border-t border-gray-200" />

          {/* Reference Sections */}
          {renderRefSection('character', '人物', User, characterRefs, ['character'])}
          {renderRefSection('outline', '大纲', Map, outlineRefs, ['outline'])}
          {renderRefSection('notes', '设定', StickyNote, noteRefs, ['note', 'location'])}
        </div>

        {/* Bottom summary */}
        <div className="px-3 py-2 border-t border-gray-200 text-xs text-gray-400">
          {project.total_word_count.toLocaleString()} 字
          {project.word_count_target ? ` / ${project.word_count_target.toLocaleString()} 目标` : ''}
        </div>

        {/* Context menu (manuscript only) */}
        {contextMenu && (
          <div
            className="fixed z-50 bg-white rounded-lg shadow-lg border border-gray-200 py-1 min-w-[120px]"
            style={{ left: contextMenu.x, top: contextMenu.y }}
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => {
                const item = contextMenu.type === 'group'
                  ? project.groups.find((g) => g.id === contextMenu.id)
                  : project.groups.flatMap((g) => g.documents).find((d) => d.id === contextMenu.id);
                if (item) startRename(contextMenu.id, item.name);
              }}
              className="w-full text-left px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100"
            >
              重命名
            </button>
            {contextMenu.type === 'group' && (
              <button
                onClick={() => { onCreateDocument(contextMenu.id); setContextMenu(null); }}
                className="w-full text-left px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100"
              >
                新建文档
              </button>
            )}
            <button
              onClick={() => {
                if (contextMenu.type === 'group') onDeleteGroup(contextMenu.id);
                else onDeleteDocument(contextMenu.id);
                setContextMenu(null);
              }}
              className="w-full text-left px-3 py-1.5 text-sm text-red-500 hover:bg-red-50"
            >
              删除
            </button>
          </div>
        )}
      </div>

      <DragOverlay>
        {activeId ? (
          <div className="bg-white border border-gray-300 rounded px-2 py-1 shadow-lg text-sm">
            拖拽中...
          </div>
        ) : null}
      </DragOverlay>
    </DndContext>
  );
}
