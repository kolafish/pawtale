import { useState } from 'react';
import {
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import type { Project } from '../../types';

interface UseBinderDragArgs {
  project: Project;
  onReorderGroups: (items: { id: string; sort_order: number }[]) => void;
  onReorderDocuments: (items: { id: string; group_id: string; sort_order: number }[]) => void;
}

export function useBinderDrag({
  project,
  onReorderGroups,
  onReorderDocuments,
}: UseBinderDragArgs) {
  const [activeId, setActiveId] = useState<string | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
  );

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
      const oldIndex = project.groups.findIndex((group) => group.id === active.id);
      const newIndex = project.groups.findIndex((group) => group.id === over.id);
      if (oldIndex === -1 || newIndex === -1) return;

      const reordered = [...project.groups];
      const [moved] = reordered.splice(oldIndex, 1);
      reordered.splice(newIndex, 0, moved);
      onReorderGroups(reordered.map((group, index) => ({ id: group.id, sort_order: index })));
      return;
    }

    if (activeData?.type !== 'document') return;

    const sourceGroupId = activeData.groupId as string;
    const targetGroupId = overData?.type === 'document' ? (overData.groupId as string) : (over.id as string);
    const sourceGroup = project.groups.find((group) => group.id === sourceGroupId);
    const targetGroup = project.groups.find((group) => group.id === targetGroupId);
    if (!sourceGroup?.documents || !targetGroup?.documents) return;

    const activeDoc = sourceGroup.documents.find((doc) => doc.id === active.id);
    if (!activeDoc) return;

    if (sourceGroupId === targetGroupId) {
      const oldIndex = sourceGroup.documents.findIndex((doc) => doc.id === active.id);
      const newIndex = sourceGroup.documents.findIndex((doc) => doc.id === over.id);
      if (oldIndex === -1 || newIndex === -1) return;

      const reordered = [...sourceGroup.documents];
      const [moved] = reordered.splice(oldIndex, 1);
      reordered.splice(newIndex, 0, moved);
      onReorderDocuments(reordered.map((doc, index) => ({
        id: doc.id,
        group_id: targetGroupId,
        sort_order: index,
      })));
      return;
    }

    const newTargetDocs = [...targetGroup.documents];
    const insertIndex = overData?.type === 'document'
      ? newTargetDocs.findIndex((doc) => doc.id === over.id)
      : newTargetDocs.length;

    newTargetDocs.splice(
      insertIndex >= 0 ? insertIndex : newTargetDocs.length,
      0,
      { ...activeDoc, group_id: targetGroupId },
    );

    onReorderDocuments([
      ...sourceGroup.documents
        .filter((doc) => doc.id !== active.id)
        .map((doc, index) => ({ id: doc.id, group_id: sourceGroupId, sort_order: index })),
      ...newTargetDocs.map((doc, index) => ({ id: doc.id, group_id: targetGroupId, sort_order: index })),
    ]);
  };

  return {
    activeId,
    sensors,
    handleDragStart,
    handleDragEnd,
  };
}
