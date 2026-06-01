import { BookOpen, ChevronDown, ChevronRight, Link2, Map, Plus, StickyNote, Trash2, User } from 'lucide-react';
import type { ProjectProfile, ProjectReferenceSection, ReferenceSectionKey } from '../../domain/projectProfiles';
import type { ReferenceItemBrief } from '../../types';

const REFERENCE_SECTION_ICONS: Record<ReferenceSectionKey, typeof User> = {
  character: User,
  outline: Map,
  notes: StickyNote,
};

interface ReferenceSectionsProps {
  profile: ProjectProfile;
  references: ReferenceItemBrief[];
  currentReferenceId: string | null;
  collapsedSections: Set<string>;
  editingId: string | null;
  editValue: string;
  onToggleSection: (sectionKey: ReferenceSectionKey) => void;
  onSelectReference: (referenceId: string) => void;
  onStartRename: (referenceId: string, name: string) => void;
  onCommitRename: (referenceId: string) => void;
  onSetEditValue: (value: string) => void;
  onCancelEdit: () => void;
  onCreateReference: (type: string) => void;
  onDeleteReference: (referenceId: string) => void;
}

export function ReferenceSections({
  profile,
  references,
  currentReferenceId,
  collapsedSections,
  editingId,
  editValue,
  onToggleSection,
  onSelectReference,
  onStartRename,
  onCommitRename,
  onSetEditValue,
  onCancelEdit,
  onCreateReference,
  onDeleteReference,
}: ReferenceSectionsProps) {
  return (
    <>
      {profile.referenceSections.map((section) => (
        <ReferenceSection
          key={section.key}
          section={section}
          items={references.filter((reference) => section.types.includes(reference.type))}
          currentReferenceId={currentReferenceId}
          isCollapsed={collapsedSections.has(section.key)}
          editingId={editingId}
          editValue={editValue}
          onToggle={() => onToggleSection(section.key)}
          onSelectReference={onSelectReference}
          onStartRename={onStartRename}
          onCommitRename={onCommitRename}
          onSetEditValue={onSetEditValue}
          onCancelEdit={onCancelEdit}
          onCreateReference={onCreateReference}
          onDeleteReference={onDeleteReference}
        />
      ))}
    </>
  );
}

interface ReferenceSectionProps {
  section: ProjectReferenceSection;
  items: ReferenceItemBrief[];
  currentReferenceId: string | null;
  isCollapsed: boolean;
  editingId: string | null;
  editValue: string;
  onToggle: () => void;
  onSelectReference: (referenceId: string) => void;
  onStartRename: (referenceId: string, name: string) => void;
  onCommitRename: (referenceId: string) => void;
  onSetEditValue: (value: string) => void;
  onCancelEdit: () => void;
  onCreateReference: (type: string) => void;
  onDeleteReference: (referenceId: string) => void;
}

function ReferenceSection({
  section,
  items,
  currentReferenceId,
  isCollapsed,
  editingId,
  editValue,
  onToggle,
  onSelectReference,
  onStartRename,
  onCommitRename,
  onSetEditValue,
  onCancelEdit,
  onCreateReference,
  onDeleteReference,
}: ReferenceSectionProps) {
  const Icon = REFERENCE_SECTION_ICONS[section.key] ?? BookOpen;

  return (
    <div>
      <div
        className="flex items-center gap-1.5 px-2 py-1.5 hover:bg-gray-100 cursor-pointer group/sec"
        onClick={onToggle}
      >
        {isCollapsed ? (
          <ChevronRight size={14} className="text-gray-400" />
        ) : (
          <ChevronDown size={14} className="text-gray-400" />
        )}
        <Icon size={13} className="text-gray-400" />
        <span className="flex-1 text-xs font-semibold text-gray-500 uppercase tracking-wider">{section.label}</span>
        <span className="text-[10px] text-gray-400 mr-0.5">{items.length}</span>
        <button
          onClick={(event) => {
            event.stopPropagation();
            onCreateReference(section.createType);
          }}
          className="opacity-0 group-hover/sec:opacity-100 p-0.5 rounded hover:bg-gray-200 text-gray-400"
        >
          <Plus size={12} />
        </button>
      </div>

      {!isCollapsed && (
        <div>
          {items.map((reference) => (
            <ReferenceRow
              key={reference.id}
              reference={reference}
              isSelected={reference.id === currentReferenceId}
              isEditing={editingId === reference.id}
              editValue={editValue}
              onSelect={() => onSelectReference(reference.id)}
              onStartRename={() => onStartRename(reference.id, reference.name)}
              onCommitRename={() => onCommitRename(reference.id)}
              onSetEditValue={onSetEditValue}
              onCancelEdit={onCancelEdit}
              onDelete={() => onDeleteReference(reference.id)}
            />
          ))}
          {items.length === 0 && (
            <div
              onClick={() => onCreateReference(section.createType)}
              className="pl-5 pr-2 py-2 text-xs text-gray-400 hover:text-pink-500 cursor-pointer"
            >
              + 新建{section.label}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

interface ReferenceRowProps {
  reference: ReferenceItemBrief;
  isSelected: boolean;
  isEditing: boolean;
  editValue: string;
  onSelect: () => void;
  onStartRename: () => void;
  onCommitRename: () => void;
  onSetEditValue: (value: string) => void;
  onCancelEdit: () => void;
  onDelete: () => void;
}

function ReferenceRow({
  reference,
  isSelected,
  isEditing,
  editValue,
  onSelect,
  onStartRename,
  onCommitRename,
  onSetEditValue,
  onCancelEdit,
  onDelete,
}: ReferenceRowProps) {
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
          onChange={(event) => onSetEditValue(event.target.value)}
          onBlur={onCommitRename}
          onKeyDown={(event) => {
            if (event.key === 'Enter') onCommitRename();
            if (event.key === 'Escape') onCancelEdit();
          }}
          autoFocus
          className="flex-1 text-sm px-1 py-0 border border-pink-300 rounded outline-none bg-white"
          onClick={(event) => event.stopPropagation()}
        />
      ) : (
        <span className="flex-1 text-sm truncate">{reference.name}</span>
      )}
      {reference.global_id && (
        <Link2 size={10} className="text-blue-400 flex-shrink-0" />
      )}
      <button
        onClick={(event) => {
          event.stopPropagation();
          onDelete();
        }}
        className="opacity-0 group-hover/ref:opacity-100 p-0.5 text-gray-400 hover:text-red-500"
      >
        <Trash2 size={11} />
      </button>
    </div>
  );
}
