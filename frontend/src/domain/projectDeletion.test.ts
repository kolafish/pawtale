import { describe, expect, it } from 'vitest';
import { isProjectDeletionConfirmationValid } from './projectDeletion';

describe('project deletion confirmation', () => {
  it('requires the complete project title', () => {
    expect(isProjectDeletionConfirmationValid('我的回忆录', '我的回忆录')).toBe(true);
    expect(isProjectDeletionConfirmationValid('我的回忆录', '回忆录')).toBe(false);
    expect(isProjectDeletionConfirmationValid('我的回忆录', '')).toBe(false);
  });

  it('ignores accidental surrounding whitespace without accepting an empty title', () => {
    expect(isProjectDeletionConfirmationValid('  我的回忆录  ', ' 我的回忆录 ')).toBe(true);
    expect(isProjectDeletionConfirmationValid('   ', '   ')).toBe(false);
  });
});
