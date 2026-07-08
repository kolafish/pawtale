import type { Project } from '../types';

export function getInitialProjectDocumentId(project: Pick<Project, 'type' | 'groups'>): string | null {
  if (project.type === 'memoir') {
    return null;
  }

  return project.groups?.[0]?.documents?.[0]?.id ?? null;
}
