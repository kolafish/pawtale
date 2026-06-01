import { useState } from 'react';
import { ChevronDown, ChevronRight, FileText, FolderPlus, Sparkles } from 'lucide-react';
import { DndContext, DragOverlay, closestCenter } from '@dnd-kit/core';
import type { Project } from '../types';
import { defaultGroupName, getProjectProfile, type ReferenceSectionKey } from '../domain/projectProfiles';
import { ManuscriptTree } from './binder/ManuscriptTree';
import { ReferenceSections } from './binder/ReferenceSections';
import { useBinderDrag } from './binder/useBinderDrag';

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
  onAnalyzeProject?: () => void;
}

type SectionKey = 'manuscript' | ReferenceSectionKey;
type ContextMenuState = { id: string; type: 'group' | 'doc'; x: number; y: number } | null;

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
  const [contextMenu, setContextMenu] = useState<ContextMenuState>(null);
  const profile = getProjectProfile(project.type);
  const drag = useBinderDrag({ project, onReorderGroups, onReorderDocuments });

  const toggleSection = (key: SectionKey) => {
    setCollapsedSections((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const toggleGroup = (groupId: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupId)) next.delete(groupId);
      else next.add(groupId);
      return next;
    });
  };

  const startRename = (id: string, currentName: string) => {
    setEditingId(id);
    setEditValue(currentName);
    setContextMenu(null);
  };

  const cancelRename = () => setEditingId(null);

  const commitRename = (id: string, type: 'group' | 'doc') => {
    const trimmed = editValue.trim();
    if (trimmed) {
      if (type === 'group') onRenameGroup(id, trimmed);
      else onRenameDocument(id, trimmed);
    }
    setEditingId(null);
  };

  const commitReferenceRename = (referenceId: string) => {
    const trimmed = editValue.trim();
    if (trimmed) onRenameReference(referenceId, trimmed);
    setEditingId(null);
  };

  const openContextMenu = (event: React.MouseEvent, id: string, type: 'group' | 'doc') => {
    event.preventDefault();
    event.stopPropagation();
    setContextMenu({ id, type, x: event.clientX, y: event.clientY });
  };

  const contextItem = contextMenu
    ? contextMenu.type === 'group'
      ? project.groups.find((group) => group.id === contextMenu.id)
      : project.groups.flatMap((group) => group.documents).find((doc) => doc.id === contextMenu.id)
    : null;

  return (
    <DndContext
      sensors={drag.sensors}
      collisionDetection={closestCenter}
      onDragStart={drag.handleDragStart}
      onDragEnd={drag.handleDragEnd}
    >
      <div
        className="w-[250px] flex-shrink-0 h-full flex flex-col bg-gray-50 border-r border-gray-200 select-none"
        onClick={() => setContextMenu(null)}
      >
        <div className="px-3 py-2 border-b border-gray-200 flex items-center justify-between">
          <span className="text-sm font-semibold text-gray-700 truncate">{project.title}</span>
          <div className="flex items-center gap-1">
            {onAnalyzeProject && (
              <button
                onClick={onAnalyzeProject}
                className="p-1 rounded hover:bg-purple-100 text-gray-400 hover:text-purple-500"
                title="AI 分析项目"
              >
                <Sparkles size={14} />
              </button>
            )}
            <button
              onClick={() => {
                const name = prompt('分组名称', defaultGroupName(project.type, project.groups.length));
                if (name?.trim()) onCreateGroup(name.trim());
              }}
              className="p-1 rounded hover:bg-gray-200 text-gray-500"
              title="新建分组"
            >
              <FolderPlus size={14} />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto py-1">
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
              <span className="flex-1 text-xs font-semibold text-gray-500 uppercase tracking-wider">
                {profile.manuscriptLabel}
              </span>
            </div>
            {!collapsedSections.has('manuscript') && (
              <ManuscriptTree
                projectType={project.type}
                groups={project.groups}
                currentDocumentId={currentDocumentId}
                collapsedGroups={collapsedGroups}
                editingId={editingId}
                editValue={editValue}
                onToggleGroup={toggleGroup}
                onStartRename={startRename}
                onCommitGroupRename={(groupId) => commitRename(groupId, 'group')}
                onCommitDocumentRename={(documentId) => commitRename(documentId, 'doc')}
                onSetEditValue={setEditValue}
                onCancelEdit={cancelRename}
                onGroupContextMenu={(event, groupId) => openContextMenu(event, groupId, 'group')}
                onDocumentContextMenu={(event, documentId) => openContextMenu(event, documentId, 'doc')}
                onCreateDocument={onCreateDocument}
                onSelectDocument={onSelectDocument}
              />
            )}
          </div>

          <div className="mx-2 my-1 border-t border-gray-200" />

          <ReferenceSections
            profile={profile}
            references={project.references || []}
            currentReferenceId={currentReferenceId}
            collapsedSections={collapsedSections}
            editingId={editingId}
            editValue={editValue}
            onToggleSection={toggleSection}
            onSelectReference={onSelectReference}
            onStartRename={startRename}
            onCommitRename={commitReferenceRename}
            onSetEditValue={setEditValue}
            onCancelEdit={cancelRename}
            onCreateReference={onCreateReference}
            onDeleteReference={onDeleteReference}
          />
        </div>

        <div className="px-3 py-2 border-t border-gray-200 text-xs text-gray-400">
          {project.total_word_count.toLocaleString()} 字
          {project.word_count_target ? ` / ${project.word_count_target.toLocaleString()} 目标` : ''}
        </div>

        {contextMenu && (
          <div
            className="fixed z-50 bg-white rounded-lg shadow-lg border border-gray-200 py-1 min-w-[120px]"
            style={{ left: contextMenu.x, top: contextMenu.y }}
            onClick={(event) => event.stopPropagation()}
          >
            <button
              onClick={() => {
                if (contextItem) startRename(contextMenu.id, contextItem.name);
              }}
              className="w-full text-left px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100"
            >
              重命名
            </button>
            {contextMenu.type === 'group' && (
              <button
                onClick={() => {
                  onCreateDocument(contextMenu.id);
                  setContextMenu(null);
                }}
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
        {drag.activeId ? (
          <div className="bg-white border border-gray-300 rounded px-2 py-1 shadow-lg text-sm">
            拖拽中...
          </div>
        ) : null}
      </DragOverlay>
    </DndContext>
  );
}
