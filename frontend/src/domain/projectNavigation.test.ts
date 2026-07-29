import { describe, expect, it } from 'vitest';

import { getInitialProjectDocumentId } from './projectNavigation';

describe('getInitialProjectDocumentId', () => {
  it('does not open the default writing document for memoir material projects', () => {
    expect(getInitialProjectDocumentId({
      type: 'memoir',
      groups: [
        {
          id: 'group-1',
          name: '回忆片段',
          sort_order: 0,
          documents: [
            {
              id: 'doc-1',
              group_id: 'group-1',
              name: '第一次记录',
              status: 'draft',
              sort_order: 0,
              word_count: 0,
              content_revision: 0,
              updated_at: '2026-07-08T00:00:00',
            },
          ],
        },
      ],
    })).toBeNull();
  });

  it('opens the first document for writing projects', () => {
    expect(getInitialProjectDocumentId({
      type: 'novel',
      groups: [
        {
          id: 'group-1',
          name: '第一卷',
          sort_order: 0,
          documents: [
            {
              id: 'doc-1',
              group_id: 'group-1',
              name: '第一章',
              status: 'draft',
              sort_order: 0,
              word_count: 0,
              content_revision: 0,
              updated_at: '2026-07-08T00:00:00',
            },
          ],
        },
      ],
    })).toBe('doc-1');
  });
});
