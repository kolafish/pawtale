import { useState } from 'react';
import { ChevronDown, ChevronRight, FileText, FolderPlus, FilePlus } from 'lucide-react';
import type { Project } from '../types';

interface BinderProps {
  project: Project;
  currentDocumentId: string | null;
  onSelectDocument: (docId: string) => void;
  onCreateGroup: (name: string) => void;
  onRenameGroup: (groupId: string, name: string) => void;
  onDeleteGroup: (groupId: string) => void;
  onCreateDocument: (groupId: string) => void;
  onRenameDocument: (docId: string, name: string) => void;
  onDeleteDocument: (docId: string) => void;
}

const STATUS_COLORS: Record<string, string> = {
  draft: 'bg-gray-300',
  revision: 'bg-yellow-400',
  done: 'bg-green-400',
};

export function Binder({
  project,
  currentDocumentId,
  onSelectDocument,
  onCreateGroup,
  onRenameGroup,
  onDeleteGroup,
  onCreateDocument,
  onRenameDocument,
  onDeleteDocument,
}: BinderProps) {
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const [contextMenu, setContextMenu] = useState<{ id: string; type: 'group' | 'doc'; x: number; y: number } | null>(null);

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
    if (trimmed && trimmed !== editingId) {
      if (type === 'group') onRenameGroup(id, trimmed);
      else onRenameDocument(id, trimmed);
    }
    setEditingId(null);
  };

  const handleContextMenu = (e: React.MouseEvent, id: string, type: 'group' | 'doc') => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({ id, type, x: e.clientX, y: e.clientY });
  };

  return (
    <div
      className="w-[250px] flex-shrink-0 h-full flex flex-col bg-gray-50 border-r border-gray-200 select-none"
      onClick={() => setContextMenu(null)}
    >
      {/* Header */}
      <div className="px-3 py-2 border-b border-gray-200 flex items-center justify-between">
        <span className="text-sm font-semibold text-gray-700 truncate">{project.title}</span>
        <button
          onClick={() => {
            const name = prompt('分组名称', '新分组');
            if (name?.trim()) onCreateGroup(name.trim());
          }}
          className="p-1 rounded hover:bg-gray-200 text-gray-500"
          title="新建分组"
        >
          <FolderPlus size={14} />
        </button>
      </div>

      {/* Tree */}
      <div className="flex-1 overflow-y-auto py-1">
        {project.groups.map((group) => (
          <div key={group.id}>
            {/* Group header */}
            <div
              className="flex items-center gap-1 px-2 py-1.5 hover:bg-gray-100 cursor-pointer group/ghdr"
              onClick={() => toggleGroup(group.id)}
              onContextMenu={(e) => handleContextMenu(e, group.id, 'group')}
              onDoubleClick={() => startRename(group.id, group.name)}
            >
              {collapsedGroups.has(group.id) ? (
                <ChevronRight size={14} className="text-gray-400 flex-shrink-0" />
              ) : (
                <ChevronDown size={14} className="text-gray-400 flex-shrink-0" />
              )}
              {editingId === group.id ? (
                <input
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  onBlur={() => commitRename(group.id, 'group')}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') commitRename(group.id, 'group');
                    if (e.key === 'Escape') setEditingId(null);
                  }}
                  autoFocus
                  className="flex-1 text-sm px-1 py-0 border border-pink-300 rounded outline-none bg-white"
                  onClick={(e) => e.stopPropagation()}
                />
              ) : (
                <span className="flex-1 text-sm font-medium text-gray-600 truncate">{group.name}</span>
              )}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onCreateDocument(group.id);
                }}
                className="opacity-0 group-hover/ghdr:opacity-100 p-0.5 rounded hover:bg-gray-200 text-gray-400"
                title="新建文档"
              >
                <FilePlus size={12} />
              </button>
            </div>

            {/* Documents */}
            {!collapsedGroups.has(group.id) && (
              <div>
                {group.documents.map((doc) => (
                  <div
                    key={doc.id}
                    onClick={() => onSelectDocument(doc.id)}
                    onContextMenu={(e) => handleContextMenu(e, doc.id, 'doc')}
                    onDoubleClick={() => startRename(doc.id, doc.name)}
                    className={`flex items-center gap-2 pl-7 pr-2 py-1.5 cursor-pointer group/doc ${
                      doc.id === currentDocumentId
                        ? 'bg-pink-50 text-pink-700'
                        : 'text-gray-600 hover:bg-gray-100'
                    }`}
                  >
                    <FileText size={13} className="flex-shrink-0 opacity-50" />
                    {editingId === doc.id ? (
                      <input
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        onBlur={() => commitRename(doc.id, 'doc')}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') commitRename(doc.id, 'doc');
                          if (e.key === 'Escape') setEditingId(null);
                        }}
                        autoFocus
                        className="flex-1 text-sm px-1 py-0 border border-pink-300 rounded outline-none bg-white"
                        onClick={(e) => e.stopPropagation()}
                      />
                    ) : (
                      <span className="flex-1 text-sm truncate">{doc.name}</span>
                    )}
                    <div className={`w-2 h-2 rounded-full flex-shrink-0 ${STATUS_COLORS[doc.status] || 'bg-gray-300'}`} />
                    <span className="text-[10px] text-gray-400 flex-shrink-0 w-8 text-right">
                      {doc.word_count > 0 ? (doc.word_count >= 1000 ? `${(doc.word_count / 1000).toFixed(1)}k` : doc.word_count) : ''}
                    </span>
                  </div>
                ))}
                {group.documents.length === 0 && (
                  <div
                    onClick={() => onCreateDocument(group.id)}
                    className="pl-7 pr-2 py-2 text-xs text-gray-400 hover:text-pink-500 cursor-pointer"
                  >
                    + 新建文档
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Bottom summary */}
      <div className="px-3 py-2 border-t border-gray-200 text-xs text-gray-400">
        {project.total_word_count.toLocaleString()} 字
        {project.word_count_target ? ` / ${project.word_count_target.toLocaleString()} 目标` : ''}
      </div>

      {/* Context menu */}
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
  );
}
