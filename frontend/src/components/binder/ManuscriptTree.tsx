import { ChevronDown, ChevronRight, FilePlus, FileText, GripVertical } from 'lucide-react';
import { SortableContext, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { DocumentGroup, DocumentMeta, ProjectType } from '../../types';
import { formatDocumentTitle, formatGroupTitle } from '../../domain/projectProfiles';

const STATUS_COLORS: Record<string, string> = {
  draft: 'bg-gray-300',
  revision: 'bg-yellow-400',
  done: 'bg-green-400',
};

interface ManuscriptTreeProps {
  projectType: ProjectType;
  groups: DocumentGroup[];
  currentDocumentId: string | null;
  collapsedGroups: Set<string>;
  editingId: string | null;
  editValue: string;
  onToggleGroup: (groupId: string) => void;
  onStartRename: (id: string, name: string) => void;
  onCommitGroupRename: (groupId: string) => void;
  onCommitDocumentRename: (documentId: string) => void;
  onSetEditValue: (value: string) => void;
  onCancelEdit: () => void;
  onGroupContextMenu: (event: React.MouseEvent, groupId: string) => void;
  onDocumentContextMenu: (event: React.MouseEvent, documentId: string) => void;
  onCreateDocument: (groupId: string) => void;
  onSelectDocument: (documentId: string) => void;
}

export function ManuscriptTree({
  projectType,
  groups,
  currentDocumentId,
  collapsedGroups,
  editingId,
  editValue,
  onToggleGroup,
  onStartRename,
  onCommitGroupRename,
  onCommitDocumentRename,
  onSetEditValue,
  onCancelEdit,
  onGroupContextMenu,
  onDocumentContextMenu,
  onCreateDocument,
  onSelectDocument,
}: ManuscriptTreeProps) {
  return (
    <SortableContext items={groups.map((group) => group.id)} strategy={verticalListSortingStrategy}>
      {groups.map((group, groupIndex) => (
        <SortableGroup
          key={group.id}
          projectType={projectType}
          group={group}
          groupIndex={groupIndex}
          isCollapsed={collapsedGroups.has(group.id)}
          isEditing={editingId === group.id}
          editValue={editValue}
          currentDocumentId={currentDocumentId}
          editingId={editingId}
          onToggle={() => onToggleGroup(group.id)}
          onStartRename={() => onStartRename(group.id, group.name)}
          onCommitRename={() => onCommitGroupRename(group.id)}
          onSetEditValue={onSetEditValue}
          onCancelEdit={onCancelEdit}
          onContextMenu={(event) => onGroupContextMenu(event, group.id)}
          onCreateDocument={() => onCreateDocument(group.id)}
          onSelectDocument={onSelectDocument}
          onDocumentContextMenu={onDocumentContextMenu}
          onDocumentStartRename={onStartRename}
          onCommitDocumentRename={onCommitDocumentRename}
        />
      ))}
    </SortableContext>
  );
}

interface SortableGroupProps {
  projectType: ProjectType;
  group: DocumentGroup;
  groupIndex: number;
  isCollapsed: boolean;
  isEditing: boolean;
  editValue: string;
  currentDocumentId: string | null;
  editingId: string | null;
  onToggle: () => void;
  onStartRename: () => void;
  onCommitRename: () => void;
  onSetEditValue: (value: string) => void;
  onCancelEdit: () => void;
  onContextMenu: (event: React.MouseEvent) => void;
  onCreateDocument: () => void;
  onSelectDocument: (documentId: string) => void;
  onDocumentContextMenu: (event: React.MouseEvent, documentId: string) => void;
  onDocumentStartRename: (documentId: string, name: string) => void;
  onCommitDocumentRename: (documentId: string) => void;
}

function SortableGroup({
  projectType,
  group,
  groupIndex,
  isCollapsed,
  isEditing,
  editValue,
  currentDocumentId,
  editingId,
  onToggle,
  onStartRename,
  onCommitRename,
  onSetEditValue,
  onCancelEdit,
  onContextMenu,
  onCreateDocument,
  onSelectDocument,
  onDocumentContextMenu,
  onDocumentStartRename,
  onCommitDocumentRename,
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
            <RenameInput
              value={editValue}
              onChange={onSetEditValue}
              onCommit={onCommitRename}
              onCancel={onCancelEdit}
            />
          ) : (
            <span
              className="flex-1 text-sm font-medium text-gray-600 truncate"
              onDoubleClick={(event) => {
                event.stopPropagation();
                onStartRename();
              }}
            >
              {formatGroupTitle(projectType, groupIndex, group.name)}
            </span>
          )}
        </div>
        <button
          onClick={(event) => {
            event.stopPropagation();
            onCreateDocument();
          }}
          className="opacity-0 group-hover/ghdr:opacity-100 p-0.5 rounded hover:bg-gray-200 text-gray-400"
          title="新建文档"
        >
          <FilePlus size={12} />
        </button>
      </div>

      {!isCollapsed && (
        <SortableContext items={(group.documents || []).map((doc) => doc.id)} strategy={verticalListSortingStrategy}>
          <div>
            {(group.documents || []).map((doc, docIndex) => (
              <SortableDocument
                key={doc.id}
                projectType={projectType}
                doc={doc}
                docIndex={docIndex}
                isSelected={doc.id === currentDocumentId}
                isEditing={editingId === doc.id}
                editValue={editValue}
                onSelect={() => onSelectDocument(doc.id)}
                onContextMenu={(event) => onDocumentContextMenu(event, doc.id)}
                onStartRename={() => onDocumentStartRename(doc.id, doc.name)}
                onCommitRename={() => onCommitDocumentRename(doc.id)}
                onSetEditValue={onSetEditValue}
                onCancelEdit={onCancelEdit}
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

interface SortableDocumentProps {
  projectType: ProjectType;
  doc: DocumentMeta;
  docIndex: number;
  isSelected: boolean;
  isEditing: boolean;
  editValue: string;
  onSelect: () => void;
  onContextMenu: (event: React.MouseEvent) => void;
  onStartRename: () => void;
  onCommitRename: () => void;
  onSetEditValue: (value: string) => void;
  onCancelEdit: () => void;
}

function SortableDocument({
  projectType,
  doc,
  docIndex,
  isSelected,
  isEditing,
  editValue,
  onSelect,
  onContextMenu,
  onStartRename,
  onCommitRename,
  onSetEditValue,
  onCancelEdit,
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
        <RenameInput
          value={editValue}
          onChange={onSetEditValue}
          onCommit={onCommitRename}
          onCancel={onCancelEdit}
        />
      ) : (
        <span className="flex-1 text-sm truncate">
          {formatDocumentTitle(projectType, docIndex, doc.name)}
        </span>
      )}
      <div className={`w-2 h-2 rounded-full flex-shrink-0 ${STATUS_COLORS[doc.status] || 'bg-gray-300'}`} />
      <span className="text-[10px] text-gray-400 flex-shrink-0 w-8 text-right">
        {doc.word_count > 0 ? (doc.word_count >= 1000 ? `${(doc.word_count / 1000).toFixed(1)}k` : doc.word_count) : ''}
      </span>
    </div>
  );
}

interface RenameInputProps {
  value: string;
  onChange: (value: string) => void;
  onCommit: () => void;
  onCancel: () => void;
}

function RenameInput({ value, onChange, onCommit, onCancel }: RenameInputProps) {
  return (
    <input
      value={value}
      onChange={(event) => onChange(event.target.value)}
      onBlur={onCommit}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onCommit();
        if (event.key === 'Escape') onCancel();
      }}
      autoFocus
      className="flex-1 text-sm px-1 py-0 border border-pink-300 rounded outline-none bg-white"
      onClick={(event) => event.stopPropagation()}
    />
  );
}
