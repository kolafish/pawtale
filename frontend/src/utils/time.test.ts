import { describe, expect, it } from 'vitest';

import { formatBeijingDateTimeShort } from './time';

describe('formatBeijingDateTimeShort', () => {
  it('treats backend timestamps without timezone as UTC before showing Beijing time', () => {
    expect(formatBeijingDateTimeShort('2026-07-01T07:38:00')).toBe('07-01 15:38');
  });

  it('keeps explicit timezone offsets authoritative', () => {
    expect(formatBeijingDateTimeShort('2026-07-01T07:38:00+00:00')).toBe('07-01 15:38');
    expect(formatBeijingDateTimeShort('2026-07-01T15:38:00+08:00')).toBe('07-01 15:38');
  });
});
