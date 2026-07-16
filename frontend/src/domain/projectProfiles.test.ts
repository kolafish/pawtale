import { describe, expect, it } from 'vitest';

import { formatProjectStructure } from './projectProfiles';
import type { ProjectListItem } from '../types';

function project(overrides: Partial<ProjectListItem>): ProjectListItem {
  return {
    id: 'project-1',
    type: 'novel',
    title: '项目',
    can_delete: true,
    total_word_count: 0,
    group_count: 1,
    document_count: 1,
    updated_at: '2026-07-08T00:00:00',
    ...overrides,
  };
}

describe('formatProjectStructure', () => {
  it('labels memoir projects as material editing entries instead of empty writing documents', () => {
    expect(formatProjectStructure(project({ type: 'memoir', document_count: 1 }))).toBe('访谈素材编辑');
  });

  it('keeps writing project structure labels', () => {
    expect(formatProjectStructure(project({ type: 'novel', group_count: 2, document_count: 8 }))).toBe('2 卷 · 8 章');
    expect(formatProjectStructure(project({ type: 'article', document_count: 3 }))).toBe('3 篇');
  });
});
